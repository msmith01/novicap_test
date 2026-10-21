"""HTTP endpoint for the working-capital risk heuristic.

Run locally with:
    API_KEYS=dev-key python -m uvicorn prototype.risk_scoring_prototype:app --reload

Configuration (environment):
    API_KEYS        comma-separated accepted X-API-Key values; if unset, /score returns 503
    DATABASE_URL    SQLAlchemy URL (default sqlite:///./scores.db; use Postgres in deployment)
    IMAGE_VERSION   build/image identifier recorded with each score
    ACTIVE_RULESET  ruleset that decides (default RULESET_VERSION); set to a prior version to roll back
    CANARY_RULESET  optional ruleset that decides for CANARY_PERCENT % of applications (stable per application_id)
    CANARY_PERCENT  0-100, default 0
    SHADOW_RULESET  optional candidate ruleset scored alongside, recorded in metrics/logs, never returned
"""

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from prototype.console import register_console
from prototype.storage import make_session_factory, save_score


RULESET_VERSION = "2026-10-v1"  # bump on any change to the rules or thresholds below
REVENUE_PER_EMPLOYEE_RISK_FLOOR = 40_000  # below this, flag as risky
REQUESTED_TO_REVENUE_RISK_CEILING = 0.20  # above this, flag as risky


@dataclass(frozen=True)
class Ruleset:
    revenue_per_employee_floor: float
    requested_to_revenue_ceiling: float


# Every shipped ruleset stays here so a rollback is a config change, not a rebuild.
# The candidate is an example of a change to trial in shadow mode before promoting.
RULESETS = {
    RULESET_VERSION: Ruleset(REVENUE_PER_EMPLOYEE_RISK_FLOOR, REQUESTED_TO_REVENUE_RISK_CEILING),
    "2026-11-v2-candidate": Ruleset(REVENUE_PER_EMPLOYEE_RISK_FLOOR, 0.25),
}

REVENUE_PER_EMPLOYEE_BUCKETS = (10_000, 20_000, 40_000, 60_000, 100_000, 250_000, 1_000_000)
REQUESTED_TO_REVENUE_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0)

# Known sources; anything else is stored as sent but reported as "other" in metrics labels
# so a caller cannot create unbounded label cardinality.
KNOWN_SOURCE_SYSTEMS = {"legacy_crm", "manual_entry", "new_partner_feed"}

logger = logging.getLogger("risk_scoring")


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    application_id: str = Field(min_length=1)
    employees: int = Field(gt=0)
    annual_revenue: float = Field(gt=0, allow_inf_nan=False)
    requested_amount: float = Field(gt=0, allow_inf_nan=False)
    source_system: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,40}$")


class ScoreResponse(BaseModel):
    application_id: str
    decision: Literal["high_risk", "low_risk"]
    risk_factors: list[str]
    ruleset_version: str = RULESET_VERSION
    request_id: str | None = None


def score_application(request: ScoreRequest, ruleset_version: str = RULESET_VERSION) -> ScoreResponse:
    rules = RULESETS[ruleset_version]
    risk_factors = []
    if request.annual_revenue / request.employees < rules.revenue_per_employee_floor:
        risk_factors.append("revenue_per_employee_below_floor")
    if request.requested_amount / request.annual_revenue > rules.requested_to_revenue_ceiling:
        risk_factors.append("requested_amount_above_revenue_ceiling")

    return ScoreResponse(
        application_id=request.application_id,
        decision="high_risk" if risk_factors else "low_risk",
        risk_factors=risk_factors,
        ruleset_version=ruleset_version,
    )


def _configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def _log(**fields) -> None:
    # Metadata only. Never log request bodies, tax IDs or financial values.
    logger.info(json.dumps(fields, default=str))


class Metrics:
    """Prometheus metrics on a per-app registry. Aggregates only: no identifiers or raw values."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "http_requests_total", "HTTP requests", ["method", "route", "status"], registry=self.registry
        )
        self.latency = Histogram(
            "http_request_duration_seconds", "Request latency", ["route"], registry=self.registry
        )
        self.decisions = Counter(
            "score_decisions_total", "Decisions returned", ["decision", "ruleset_version", "source_system"], registry=self.registry
        )
        self.risk_factors = Counter(
            "score_risk_factors_total", "Triggered risk factors", ["factor", "ruleset_version", "source_system"], registry=self.registry
        )
        self.shadow_decisions = Counter(
            "shadow_decisions_total", "Decisions the shadow ruleset would have made", ["decision", "ruleset_version"], registry=self.registry
        )
        self.shadow_disagreements = Counter(
            "shadow_disagreements_total", "Shadow decision differed from the active decision", ["ruleset_version"], registry=self.registry
        )
        self.shadow_failures = Counter(
            "shadow_failures_total", "Shadow scoring raised an error", ["ruleset_version"], registry=self.registry
        )
        self.persistence_failures = Counter(
            "score_persistence_failures_total", "Scores not recorded", registry=self.registry
        )
        self.revenue_per_employee = Histogram(
            "input_revenue_per_employee", "Input drift: revenue per employee",
            buckets=REVENUE_PER_EMPLOYEE_BUCKETS, registry=self.registry,
        )
        self.requested_to_revenue = Histogram(
            "input_requested_to_revenue", "Input drift: requested amount / revenue",
            buckets=REQUESTED_TO_REVENUE_BUCKETS, registry=self.registry,
        )


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    api_keys: list[str] | None = None,
    image_version: str | None = None,
    active_ruleset: str | None = None,
    shadow_ruleset: str | None = None,
    canary_ruleset: str | None = None,
    canary_percent: float | None = None,
) -> FastAPI:
    _configure_logging()
    sessions = session_factory or make_session_factory()
    image = image_version or os.environ.get("IMAGE_VERSION", "dev")
    keys = api_keys if api_keys is not None else [
        k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()
    ]

    active = active_ruleset or os.environ.get("ACTIVE_RULESET") or RULESET_VERSION
    shadow = shadow_ruleset or os.environ.get("SHADOW_RULESET") or None
    canary = canary_ruleset or os.environ.get("CANARY_RULESET") or None
    percent = canary_percent if canary_percent is not None else float(os.environ.get("CANARY_PERCENT", "0") or 0)
    if not 0 <= percent <= 100:
        raise ValueError("CANARY_PERCENT must be between 0 and 100")
    for name in (active, shadow, canary):
        if name is not None and name not in RULESETS:  # fail at startup, not on a request
            raise ValueError(f"unknown ruleset {name!r}; known: {sorted(RULESETS)}")

    def ruleset_for(application_id: str) -> str:
        # Stable per application: the same ID always lands in the same arm, so retries agree.
        if canary and percent > 0:
            bucket = int.from_bytes(hashlib.sha256(application_id.encode()).digest()[:4], "big") % 10_000
            if bucket < percent * 100:
                return canary
        return active

    app = FastAPI(title="Working Capital Risk Scoring")
    metrics = Metrics()

    def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if not keys:
            raise HTTPException(status_code=503, detail="authentication is not configured")
        if x_api_key is None or not any(hmac.compare_digest(x_api_key, k) for k in keys):
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    def get_session():
        with sessions() as session:
            yield session

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable):
        request.state.request_id = str(uuid.uuid4())
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = getattr(request.scope.get("route"), "path", "unmatched")  # template, not raw path
            metrics.requests.labels(request.method, route, str(status)).inc()
            metrics.latency.labels(route).observe(elapsed)
            _log(
                event="request",
                request_id=request.state.request_id,
                method=request.method,
                path=request.url.path,
                status=status,
                latency_ms=round(elapsed * 1000, 1),
                decision=getattr(request.state, "decision", None),
                risk_factors=getattr(request.state, "risk_factors", None),
                source_system=getattr(request.state, "source_system", None),
                ruleset_version=getattr(request.state, "ruleset_version", None),
                shadow_ruleset=shadow if hasattr(request.state, "decision") else None,
                shadow_decision=getattr(request.state, "shadow_decision", None),
            )

    @app.exception_handler(RequestValidationError)
    def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Omit the echoed input: NaN is not JSON-serializable (would 500) and
        # rejected payloads may contain sensitive fields.
        detail = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics", dependencies=[Depends(require_api_key)], include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/score", response_model=ScoreResponse, dependencies=[Depends(require_api_key)])
    def score(request: ScoreRequest, http_request: Request, session: Session = Depends(get_session)):
        decider = ruleset_for(request.application_id)
        result = score_application(request, decider)
        result.request_id = http_request.state.request_id
        try:
            save_score(
                session,
                source_application_id=request.application_id,
                employees=request.employees,
                annual_revenue=request.annual_revenue,
                requested_amount=request.requested_amount,
                source_system=request.source_system,
                request_id=result.request_id,
                decision=result.decision,
                risk_factors=result.risk_factors,
                ruleset_version=decider,
                image_version=image,
            )
        except SQLAlchemyError as exc:
            # Never return a decision that was not recorded. Log the type only:
            # SQLAlchemy error text can include bound parameter values.
            metrics.persistence_failures.inc()
            _log(event="persistence_failed", request_id=result.request_id, error=type(exc).__name__)
            raise HTTPException(status_code=503, detail="could not record score; retry")
        source = request.source_system if request.source_system in KNOWN_SOURCE_SYSTEMS else (
            "other" if request.source_system else "unspecified"
        )
        metrics.decisions.labels(result.decision, decider, source).inc()
        for factor in result.risk_factors:
            metrics.risk_factors.labels(factor, decider, source).inc()
        metrics.revenue_per_employee.observe(request.annual_revenue / request.employees)
        metrics.requested_to_revenue.observe(request.requested_amount / request.annual_revenue)
        http_request.state.ruleset_version = decider
        if shadow:
            # A failing candidate must never affect the decision already made.
            try:
                candidate = score_application(request, shadow)
                http_request.state.shadow_decision = candidate.decision
                metrics.shadow_decisions.labels(candidate.decision, shadow).inc()
                if candidate.decision != result.decision:
                    metrics.shadow_disagreements.labels(shadow).inc()
            except Exception as exc:
                metrics.shadow_failures.labels(shadow).inc()
                _log(event="shadow_failed", request_id=result.request_id, error=type(exc).__name__)
        http_request.state.decision = result.decision
        http_request.state.source_system = request.source_system
        http_request.state.risk_factors = result.risk_factors
        return result

    register_console(
        app,
        sessions=sessions,
        require_api_key=require_api_key,
        get_session=get_session,
        registry=metrics.registry,
        config={
            "image_version": image,
            "active_ruleset": active,
            "shadow_ruleset": shadow,
            "canary_ruleset": canary,
            "canary_percent": percent,
            "rulesets": {
                name: {
                    "revenue_per_employee_floor": r.revenue_per_employee_floor,
                    "requested_to_revenue_ceiling": r.requested_to_revenue_ceiling,
                }
                for name, r in RULESETS.items()
            },
        },
    )
    return app


app = create_app()
