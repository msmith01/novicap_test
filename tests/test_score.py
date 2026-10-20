import pytest
from fastapi.testclient import TestClient

from prototype.risk_scoring_prototype import app

client = TestClient(app)


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
    assert response.json() == {
        "application_id": "A-123",
        "decision": "low_risk",
        "risk_factors": [],
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
