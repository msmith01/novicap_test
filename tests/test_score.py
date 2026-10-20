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
    assert f'score_decisions_total{{decision="low_risk",ruleset_version="{RULESET_VERSION}",source_system="unspecified"}} 1.0' in text
    assert f'score_decisions_total{{decision="high_risk",ruleset_version="{RULESET_VERSION}",source_system="unspecified"}} 1.0' in text
    assert f'score_risk_factors_total{{factor="revenue_per_employee_below_floor",ruleset_version="{RULESET_VERSION}",source_system="unspecified"}} 1.0' in text
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


def test_replay_counts_outcomes_and_reasons_without_values(tmp_path, capsys):
    from prototype.replay import main

    src = tmp_path / "in.csv"
    src.write_text(
        "application_id,tax_id,employees,annual_revenue,requested_amount\n"
        "R1,TAXSECRET1,10,1000000,100000\n"
        "R2,TAXSECRET2,1000,1000000,500000\n"
        "R3,TAXSECRET3,null,1000000,100000\n"
        "R4,TAXSECRET4,-1,1000000,100000\n"
    )
    out = tmp_path / "out.csv"
    assert main([str(src), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "low_risk: 1" in text and "high_risk: 1" in text and "rejected: 2" in text
    assert "employees:int_type: 1" in text and "employees:greater_than: 1" in text
    assert "TAXSECRET" not in text and "TAXSECRET" not in out.read_text()
    assert out.read_text().splitlines()[1].startswith("R1,low_risk")


def test_replay_from_db_reports_changed_decisions(tmp_path, capsys):
    from prototype.replay import main
    from prototype.storage import make_session_factory, save_score

    url = f"sqlite:///{tmp_path / 's.db'}"
    with make_session_factory(url)() as session:
        for rid, decision in (("a", "low_risk"), ("b", "low_risk")):
            save_score(
                session, source_application_id=rid, employees=10, annual_revenue=1_000_000,
                requested_amount=100_000, request_id=rid, decision=decision, risk_factors=[],
                ruleset_version="old", image_version="t",
            )
        save_score(
            session, source_application_id="c", employees=10, annual_revenue=1_000_000,
            requested_amount=100_000, request_id="c", decision="high_risk", risk_factors=[],
            ruleset_version="old", image_version="t",
        )
    assert main(["--from-db", url]) == 0
    text = capsys.readouterr().out
    assert "3 stored scores re-scored, 1 decisions changed" in text
    assert "high_risk -> low_risk: 1" in text


def test_source_system_is_stored_and_labelled_with_bounded_cardinality():
    c = _metrics_client()
    assert c.post("/score", json=payload(source_system="legacy_crm")).status_code == 200
    assert c.post("/score", json=payload(source_system="made_up_source")).status_code == 200
    assert c.post("/score", json=payload(source_system="Bad Value!")).status_code == 422
    text = c.get("/metrics").text
    assert 'decision="low_risk",ruleset_version="%s",source_system="legacy_crm"} 1.0' % RULESET_VERSION in text
    assert 'source_system="other"} 1.0' in text
    assert "made_up_source" not in text


def test_ingest_normalises_flags_and_dedupes_without_imputing(tmp_path, capsys):
    import csv

    from prototype.ingest import main

    header = "application_id,tax_id,sector,region,incorporation_date,employees,annual_revenue,annual_ebitda,requested_amount,source_system\n"
    row = "R1,H-123,Food manuf.,Catalunya,03/02/2010,10,1000000,n/a,100000,legacy_crm\n"
    src = tmp_path / "in.csv"
    src.write_text(header + row + row + "R2,H2,Retail ,Madrid,2030-01-01,null,50000,1,100,manual_entry\n")
    out = tmp_path / "out.csv"
    assert main([str(src), "--out", str(out)]) == 0
    assert "exact_duplicates_dropped: 1" in capsys.readouterr().out
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 2
    assert rows[0]["incorporation_date"] == "2010-02-03" and rows[0]["tax_id"] == "H123"
    assert rows[0]["sector"] == "food_manufacturing" and rows[0]["region"] == "cataluna"
    assert "missing_ebitda" in rows[0]["flags"]
    assert rows[1]["employees"] == "" and "bad_date" in rows[1]["flags"]


def _shadow_client(shadow, active=None):
    return TestClient(
        create_app(session_factory=make_session_factory("sqlite://"), api_keys=[KEY], active_ruleset=active, shadow_ruleset=shadow),
        headers={"X-API-Key": KEY},
    )


def test_shadow_ruleset_is_recorded_but_never_returned():
    shadow_client = _shadow_client("2026-11-v2-candidate")
    # 22% of revenue: high_risk under v1 (ceiling 20%), low_risk under the candidate (25%)
    body = shadow_client.post("/score", json=payload(requested_amount=220_000)).json()
    assert body["decision"] == "high_risk"
    assert body["ruleset_version"] == RULESET_VERSION
    text = shadow_client.get("/metrics").text
    assert 'shadow_decisions_total{decision="low_risk",ruleset_version="2026-11-v2-candidate"} 1.0' in text
    assert 'shadow_disagreements_total{ruleset_version="2026-11-v2-candidate"} 1.0' in text


def test_active_ruleset_can_be_switched_for_rollback():
    candidate_client = _shadow_client(None, active="2026-11-v2-candidate")
    body = candidate_client.post("/score", json=payload(requested_amount=220_000)).json()
    assert body["decision"] == "low_risk"
    assert body["ruleset_version"] == "2026-11-v2-candidate"


def test_shadow_failure_does_not_affect_decision(monkeypatch):
    import prototype.risk_scoring_prototype as mod

    real = mod.score_application

    def flaky(request, ruleset_version=RULESET_VERSION):
        if ruleset_version == "2026-11-v2-candidate":
            raise RuntimeError("bad candidate")
        return real(request, ruleset_version)

    monkeypatch.setattr(mod, "score_application", flaky)
    shadow_client = _shadow_client("2026-11-v2-candidate")
    response = shadow_client.post("/score", json=payload())
    assert response.status_code == 200 and response.json()["decision"] == "low_risk"
    assert 'shadow_failures_total{ruleset_version="2026-11-v2-candidate"} 1.0' in shadow_client.get("/metrics").text


def test_unknown_ruleset_fails_at_startup():
    with pytest.raises(ValueError):
        create_app(session_factory=make_session_factory("sqlite://"), api_keys=[KEY], shadow_ruleset="nope")


def _canary_client(percent):
    return TestClient(
        create_app(session_factory=make_session_factory("sqlite://"), api_keys=[KEY],
                   canary_ruleset="2026-11-v2-candidate", canary_percent=percent),
        headers={"X-API-Key": KEY},
    )


def test_canary_splits_traffic_and_is_stable_per_application():
    c = _canary_client(30)
    versions = {}
    for i in range(400):
        versions[i] = c.post("/score", json=payload(application_id=f"C-{i}")).json()["ruleset_version"]
    share = sum(v == "2026-11-v2-candidate" for v in versions.values()) / 400
    assert 0.2 < share < 0.4
    for i in range(0, 400, 40):  # same application, same arm
        assert c.post("/score", json=payload(application_id=f"C-{i}")).json()["ruleset_version"] == versions[i]


def test_canary_zero_and_hundred_percent():
    assert _canary_client(0).post("/score", json=payload()).json()["ruleset_version"] == RULESET_VERSION
    assert _canary_client(100).post("/score", json=payload()).json()["ruleset_version"] == "2026-11-v2-candidate"


def test_invalid_canary_percent_fails_at_startup():
    with pytest.raises(ValueError):
        _canary_client(101)
