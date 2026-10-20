"""HTTP endpoint for the working-capital risk heuristic.

Run locally with:
    python -m uvicorn prototype.risk_scoring_prototype:app --reload
"""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


REVENUE_PER_EMPLOYEE_RISK_FLOOR = 40_000  # below this, flag as risky
REQUESTED_TO_REVENUE_RISK_CEILING = 0.20  # above this, flag as risky


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


app = FastAPI(title="Working Capital Risk Scoring")


@app.exception_handler(RequestValidationError)
def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Omit the echoed input: NaN is not JSON-serializable (would 500) and
    # rejected payloads may contain sensitive fields.
    detail = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.post("/score", response_model=ScoreResponse)
def score(request: ScoreRequest) -> ScoreResponse:
    return score_application(request)
