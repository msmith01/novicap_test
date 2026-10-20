import pytest
from fastapi.testclient import TestClient

from prototype.risk_scoring_prototype import RULESET_VERSION, create_app
from prototype.storage import ScoreRecord, Application, make_session_factory

KEY = "test-key"
sessions = make_session_factory("sqlite://")
client = TestClient(create_app(session_factory=sessions, api_keys=[KEY], image_version="test-image"), headers={"X-API-Key": KEY})


def payload(**overrides):
    body = {
        "application_id": "A-123",
        "employees": 10,
        "annual_revenue": 1_000_000,
        "requested_amount": 100_000,
    }
    body.update(overrides)
    return body


def score(**overrides):
    return client.post("/score", json=payload(**overrides))


def test_low_risk_response_shape():
    response = score()
    assert response.status_code == 200
    body = response.json()
    assert body.pop("request_id") == response.headers["X-Request-ID"]
    assert body == {
        "application_id": "A-123",
        "decision": "low_risk",
        "risk_factors": [],
        "ruleset_version": RULESET_VERSION,
    }


def test_revenue_per_employee_threshold():
    # exactly 40,000 per employee is not flagged; just below is
    assert score(employees=25).json()["risk_factors"] == []
    body = score(employees=26).json()
    assert body["decision"] == "high_risk"
    assert body["risk_factors"] == ["revenue_per_employee_below_floor"]


def test_requested_amount_threshold():
    # exactly 20% of revenue is not flagged; just above is
    assert score(requested_amount=200_000).json()["risk_factors"] == []
    body = score(requested_amount=200_001).json()
    assert body["decision"] == "high_risk"
    assert body["risk_factors"] == ["requested_amount_above_revenue_ceiling"]


def test_both_rules_triggered():
    body = score(employees=100, requested_amount=500_000).json()
    assert body["decision"] == "high_risk"
    assert body["risk_factors"] == [
        "revenue_per_employee_below_floor",
        "requested_amount_above_revenue_ceiling",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"employees": 0},
        {"employees": -5},
        {"employees": 1.5},
        {"annual_revenue": 0},
        {"annual_revenue": -1},
        {"requested_amount": 0},
        {"requested_amount": -100},
        {"application_id": ""},
        {"application_id": "   "},
        {"employees": "ten"},
    ],
)
def test_invalid_values_rejected(overrides):
    assert score(**overrides).status_code == 422


@pytest.mark.parametrize("field", ["application_id", "employees", "annual_revenue", "requested_amount"])
def test_missing_field_rejected(field):
    body = payload()
    del body[field]
    assert client.post("/score", json=body).status_code == 422


def test_unknown_field_rejected():
    assert score(tax_id="12-3456789").status_code == 422


def test_non_finite_numbers_rejected():
    raw = '{"application_id":"A","employees":10,"annual_revenue":NaN,"requested_amount":1}'
    response = client.post("/score", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_score_is_persisted_with_versions():
    response = score(application_id="P-1", employees=100)
    request_id = response.json()["request_id"]
    with sessions() as session:
        record = session.query(ScoreRecord).filter_by(request_id=request_id).one()
        application = session.get(Application, record.application_id)
    assert application.source_application_id == "P-1"
    assert record.decision == "high_risk"
    assert record.risk_factors == ["revenue_per_employee_below_floor"]
    assert record.ruleset_version == RULESET_VERSION
    assert record.image_version == "test-image"


def test_repeated_source_ids_are_kept_as_separate_rows():
    score(application_id="DUP-1")
    score(application_id="DUP-1")
    with sessions() as session:
        assert session.query(Application).filter_by(source_application_id="DUP-1").count() == 2


def test_rejected_requests_are_not_persisted():
    before = _count()
    score(employees=0)
    assert _count() == before


def _count():
    with sessions() as session:
        return session.query(ScoreRecord).count()


def test_auth_required():
    anon = TestClient(client.app)
    assert anon.post("/score", json=payload()).status_code == 401
    assert anon.post("/score", json=payload(), headers={"X-API-Key": "wrong"}).status_code == 401


def test_auth_unconfigured_fails_closed():
    unconfigured = TestClient(create_app(session_factory=sessions, api_keys=[]))
    assert unconfigured.post("/score", json=payload(), headers={"X-API-Key": "anything"}).status_code == 503


def test_healthz_is_open():
    assert TestClient(client.app).get("/healthz").json() == {"status": "ok"}


def test_persistence_failure_returns_503_and_no_decision(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def boom(*a, **k):
        raise OperationalError("insert", {"annual_revenue": 1}, Exception("db down"))

    monkeypatch.setattr("prototype.risk_scoring_prototype.save_score", boom)
    response = score()
    assert response.status_code == 503
    assert "decision" not in response.json()


def test_logs_exclude_request_values(caplog):
    import logging

    logging.getLogger("risk_scoring").propagate = True
    with caplog.at_level(logging.INFO, logger="risk_scoring"):
        score(application_id="SECRET-ID", annual_revenue=987654.32)
    text = caplog.text
    assert "SECRET-ID" not in text and "987654" not in text
    assert "request_id" in text and "latency_ms" in text


def test_migration_matches_models_and_is_idempotent(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from prototype.storage import Base, migrate

    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    migrate(engine)
    migrate(engine)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def _metrics_client():
    app = create_app(session_factory=make_session_factory("sqlite://"), api_keys=[KEY], image_version="t")
    return TestClient(app, headers={"X-API-Key": KEY})


def test_metrics_count_decisions_and_errors_without_leaking_values():
    c = _metrics_client()
    c.post("/score", json=payload(application_id="SECRET-ID-1"))
    c.post("/score", json=payload(employees=1000))  # low revenue per employee -> high_risk
    c.post("/score", json=payload(employees=0))  # 422
    text = c.get("/metrics").text
    assert f'score_decisions_total{{decision="low_risk",ruleset_version="{RULESET_VERSION}"}} 1.0' in text
    assert f'score_decisions_total{{decision="high_risk",ruleset_version="{RULESET_VERSION}"}} 1.0' in text
    assert f'score_risk_factors_total{{factor="revenue_per_employee_below_floor",ruleset_version="{RULESET_VERSION}"}} 1.0' in text
    assert 'http_requests_total{method="POST",route="/score",status="422"} 1.0' in text
    assert "input_revenue_per_employee_count 2.0" in text
    assert "SECRET-ID-1" not in text


def test_metrics_requires_api_key():
    c = _metrics_client()
    assert c.get("/metrics", headers={"X-API-Key": "wrong"}).status_code == 401


def test_metrics_count_persistence_failure():
    from sqlalchemy.exc import OperationalError

    c = _metrics_client()
    import prototype.risk_scoring_prototype as mod

    original = mod.save_score
    mod.save_score = lambda *a, **k: (_ for _ in ()).throw(OperationalError("x", {}, Exception("db")))
    try:
        assert c.post("/score", json=payload()).status_code == 503
    finally:
        mod.save_score = original
    assert "score_persistence_failures_total 1.0" in c.get("/metrics").text
