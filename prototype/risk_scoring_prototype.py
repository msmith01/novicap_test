"""HTTP endpoint for the working-capital risk heuristic.

Run locally with:
    API_KEYS=dev-key python -m uvicorn prototype.risk_scoring_prototype:app --reload

Configuration (environment):
    API_KEYS        comma-separated accepted X-API-Key values; if unset, /score returns 503
    DATABASE_URL    SQLAlchemy URL (default sqlite:///./scores.db; use Postgres in deployment)
    IMAGE_VERSION   build/image identifier recorded with each score
"""

import hmac
import json
import logging
import os
import time
import uuid
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from prototype.storage import make_session_factory, save_score


RULESET_VERSION = "2026-10-v1"  # bump on any change to the rules or thresholds below
REVENUE_PER_EMPLOYEE_RISK_FLOOR = 40_000  # below this, flag as risky
REQUESTED_TO_REVENUE_RISK_CEILING = 0.20  # above this, flag as risky

logger = logging.getLogger("risk_scoring")


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    application_id: str = Field(min_length=1)
    employees: int = Field(gt=0)
    annual_revenue: float = Field(gt=0, allow_inf_nan=False)
    requested_amount: float = Field(gt=0, allow_inf_nan=False)


class ScoreResponse(BaseModel):
    application_id: str
    decision: Literal["high_risk", "low_risk"]
    risk_factors: list[str]
    ruleset_version: str = RULESET_VERSION
    request_id: str | None = None


def score_application(request: ScoreRequest) -> ScoreResponse:
    risk_factors = []
    if request.annual_revenue / request.employees < REVENUE_PER_EMPLOYEE_RISK_FLOOR:
        risk_factors.append("revenue_per_employee_below_floor")
    if request.requested_amount / request.annual_revenue > REQUESTED_TO_REVENUE_RISK_CEILING:
        risk_factors.append("requested_amount_above_revenue_ceiling")

    return ScoreResponse(
        application_id=request.application_id,
        decision="high_risk" if risk_factors else "low_risk",
        risk_factors=risk_factors,
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


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    api_keys: list[str] | None = None,
    image_version: str | None = None,
) -> FastAPI:
    _configure_logging()
    sessions = session_factory or make_session_factory()
    image = image_version or os.environ.get("IMAGE_VERSION", "dev")
    keys = api_keys if api_keys is not None else [
        k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()
    ]

    app = FastAPI(title="Working Capital Risk Scoring")

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
            _log(
                event="request",
                request_id=request.state.request_id,
                method=request.method,
                path=request.url.path,
                status=status,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                decision=getattr(request.state, "decision", None),
                risk_factors=getattr(request.state, "risk_factors", None),
                ruleset_version=RULESET_VERSION if hasattr(request.state, "decision") else None,
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

    @app.post("/score", response_model=ScoreResponse, dependencies=[Depends(require_api_key)])
    def score(request: ScoreRequest, http_request: Request, session: Session = Depends(get_session)):
        result = score_application(request)
        result.request_id = http_request.state.request_id
        try:
            save_score(
                session,
                source_application_id=request.application_id,
                employees=request.employees,
                annual_revenue=request.annual_revenue,
                requested_amount=request.requested_amount,
                request_id=result.request_id,
                decision=result.decision,
                risk_factors=result.risk_factors,
                ruleset_version=RULESET_VERSION,
                image_version=image,
            )
        except SQLAlchemyError as exc:
            # Never return a decision that was not recorded. Log the type only:
            # SQLAlchemy error text can include bound parameter values.
            _log(event="persistence_failed", request_id=result.request_id, error=type(exc).__name__)
            raise HTTPException(status_code=503, detail="could not record score; retry")
        http_request.state.decision = result.decision
        http_request.state.risk_factors = result.risk_factors
        return result

    return app


app = create_app()
