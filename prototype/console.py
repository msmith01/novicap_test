"""Operator console: a single page plus a few read endpoints that show the running system.

Everything except the HTML page itself requires the same X-API-Key as /score, so the page can be
public without exposing data. The page reads the key from the URL fragment (never sent to the
server) or a text box.

Optional environment:
    PROMETHEUS_URL     e.g. http://prometheus:9090; enables the monitoring panel
    ALERTMANAGER_URL   e.g. http://alertmanager:9093; enables the alert panel and test alert
    DEMO_VIDEO_PATH    path to the narrated demo (default /app/demo/demo.mp4)
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from prototype.storage import Application, ScoreRecord

PAGE = Path(__file__).parent / "console.html"
RECENT_LIMIT = 15


def _fetch(url: str, payload: list | None = None, timeout: float = 2.0):
    """GET (or POST JSON) and decode JSON. Returns (data, error) so one dead service never breaks the page."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return (json.loads(body) if body else None), None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return None, type(exc).__name__


def _metric_total(registry, name: str) -> float:
    return sum(
        sample.value
        for family in registry.collect()
        for sample in family.samples
        if sample.name == name
    )


def register_console(
    app: FastAPI,
    *,
    sessions: sessionmaker[Session],
    require_api_key: Callable,
    get_session: Callable,
    registry,
    config: dict,
) -> None:
    prometheus = os.environ.get("PROMETHEUS_URL", "").rstrip("/")
    alertmanager = os.environ.get("ALERTMANAGER_URL", "").rstrip("/")
    video = Path(os.environ.get("DEMO_VIDEO_PATH", "/app/demo/demo.mp4"))
    router = APIRouter(prefix="/console/api", dependencies=[Depends(require_api_key)], include_in_schema=False)

    @router.get("/overview")
    def overview(request: Request, session: Session = Depends(get_session)) -> dict:
        by_decision = dict(session.execute(select(ScoreRecord.decision, func.count()).group_by(ScoreRecord.decision)).all())
        by_ruleset = [
            {"ruleset": r, "decision": d, "count": c}
            for r, d, c in session.execute(
                select(ScoreRecord.ruleset_version, ScoreRecord.decision, func.count()).group_by(
                    ScoreRecord.ruleset_version, ScoreRecord.decision
                )
            )
        ]
        recent = [
            {
                "application_id": a.source_application_id,
                "employees": a.employees,
                "annual_revenue": a.annual_revenue,
                "requested_amount": a.requested_amount,
                "source_system": a.source_system,
                "decision": s.decision,
                "risk_factors": s.risk_factors,
                "ruleset_version": s.ruleset_version,
                "image_version": s.image_version,
                "request_id": s.request_id,
                "scored_at": s.scored_at.isoformat() if s.scored_at else None,
            }
            for s, a in session.execute(
                select(ScoreRecord, Application)
                .join(Application, Application.id == ScoreRecord.application_id)
                .order_by(ScoreRecord.scored_at.desc())
                .limit(RECENT_LIMIT)
            )
        ]
        schema = {"dialect": session.bind.dialect.name, "revision": None, "server_version": None}
        try:
            schema["revision"] = session.execute(text("select version_num from alembic_version")).scalar()
            if schema["dialect"] == "postgresql":
                schema["server_version"] = session.execute(text("show server_version")).scalar()
            else:
                schema["server_version"] = session.execute(text("select sqlite_version()")).scalar()
        except SQLAlchemyError:
            pass
        shadow_decisions = _metric_total(registry, "shadow_decisions_total")
        return {
            "config": config,
            "totals": {"scored": sum(by_decision.values()), "by_decision": by_decision},
            "by_ruleset": by_ruleset,
            "recent": recent,
            "schema": schema,
            "shadow": {
                "decisions": shadow_decisions,
                "disagreements": _metric_total(registry, "shadow_disagreements_total"),
                "failures": _metric_total(registry, "shadow_failures_total"),
            },
            "persistence_failures": _metric_total(registry, "score_persistence_failures_total"),
            "behind_proxy": "x-forwarded-for" in request.headers,
        }

    @router.get("/monitoring")
    def monitoring() -> dict:
        result: dict = {"prometheus": {"configured": bool(prometheus)}, "alertmanager": {"configured": bool(alertmanager)}}
        if prometheus:
            targets, err = _fetch(f"{prometheus}/api/v1/targets")
            rules, rerr = _fetch(f"{prometheus}/api/v1/rules?type=alert")
            result["prometheus"].update(
                reachable=err is None and rerr is None,
                targets=[
                    {"job": t["labels"].get("job"), "health": t["health"], "last_error": t.get("lastError", "")}
                    for t in (targets or {}).get("data", {}).get("activeTargets", [])
                ],
                rules=[
                    {"name": r["name"], "state": r["state"], "severity": r.get("labels", {}).get("severity"),
                     "summary": r.get("annotations", {}).get("summary", "")}
                    for g in (rules or {}).get("data", {}).get("groups", [])
                    for r in g["rules"]
                ],
            )
        if alertmanager:
            status, err = _fetch(f"{alertmanager}/api/v2/status")
            alerts, _ = _fetch(f"{alertmanager}/api/v2/alerts")
            result["alertmanager"].update(
                reachable=err is None,
                version=((status or {}).get("versionInfo") or {}).get("version"),
                active_alerts=[
                    {"name": a["labels"].get("alertname"), "severity": a["labels"].get("severity"),
                     "state": a["status"]["state"], "starts_at": a["startsAt"]}
                    for a in (alerts or [])
                ],
            )
        return result

    @router.post("/test-alert")
    def test_alert() -> JSONResponse:
        """Fire a short-lived alert through Alertmanager so the Discord route can be shown working."""
        if not alertmanager:
            return JSONResponse(status_code=503, content={"detail": "ALERTMANAGER_URL is not configured"})
        now = datetime.now(timezone.utc)
        alert = {
            "labels": {"alertname": "ConsoleTestAlert", "severity": "ticket"},
            "annotations": {"summary": "Test alert sent from the operator console"},
            "startsAt": now.isoformat(),
            "endsAt": (now + timedelta(minutes=2)).isoformat(),
        }
        _, err = _fetch(f"{alertmanager}/api/v2/alerts", payload=[alert])
        if err:
            return JSONResponse(status_code=502, content={"detail": f"Alertmanager unreachable ({err})"})
        return JSONResponse(content={"status": "sent", "alertname": "ConsoleTestAlert"})

    app.include_router(router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def console_page() -> HTMLResponse:
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    @app.get("/demo.mp4", include_in_schema=False)
    def demo_video():
        if not video.is_file():
            return JSONResponse(status_code=404, content={"detail": "demo video not bundled"})
        return FileResponse(video, media_type="video/mp4")
