# Working Capital Risk Scoring

Start here when returning to the project. The challenge brief is [Brief.pdf](Brief.pdf); the current architecture and trade-offs are in [DESIGN.md](DESIGN.md).

## Current state

The prototype is now a FastAPI service with `POST /score`. It preserves the original two rules and validates requests before scoring. The notebook, [analysis/data_exploration.ipynb](analysis/data_exploration.ipynb), documents the data profile, storage recommendation, HTTP contract, and deployment/operations plan.

The service is a local working slice, not production-ready. It does not persist applications or scores, authenticate callers, return a ruleset version, or include production monitoring. The notebook's code cells have not been run in the VS Code notebook kernel; they were validated from Python. Run the notebook top-to-bottom to populate visible outputs.

## Run locally

From the repository folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn prototype.risk_scoring_prototype:app --reload
```

Open `http://127.0.0.1:8000/docs` to try the API. Example request:

```json
{"application_id":"A-123","employees":10,"annual_revenue":1000000,"requested_amount":100000}
```

The response contains `decision` and `risk_factors`. Invalid requests, including zero or negative employees/revenue/request amounts, receive HTTP 422.

## Run in Docker

With Docker Desktop's Linux engine running:

```powershell
docker build -t working-capital-risk .
docker run --rm -p 8000:8000 working-capital-risk
```

Then open `http://127.0.0.1:8000/docs`. The Docker build context excludes `data/`, `analysis/`, and the brief. Docker was installed in the development environment, but its engine was unavailable, so an image build has not been verified.

## Project map

- `prototype/risk_scoring_prototype.py`: FastAPI app, request model, and scoring rules.
- `data/raw_applications.csv`: source export; contains sensitive identifiers, do not upload or log it.
- `analysis/data_exploration.ipynb`: brief sections 1-4 and the exploratory profile.
- `DESIGN.md`: concise architecture and operating decisions.
- `Dockerfile`, `requirements.txt`, `.dockerignore`: local container setup.

## Resume checklist

1. ~~Add automated tests~~ Done: `python -m pip install -r requirements-dev.txt` then `python -m pytest` (20 tests in `tests/`).
2. Confirm with the data source whether repeated application IDs are duplicates or revisions; do not deduplicate until clarified.
3. Add Postgres persistence for applications and append-only score history, including request ID and ruleset/image versions.
4. Add authentication, structured privacy-safe logs, and monitoring before exposing the endpoint beyond local development.
5. Start Docker Desktop and build/test the image. Provision AWS only when ready to deploy; Colab is not required.
6. This folder is not currently a Git repository. Create a private repository and commit the current checkpoint before making larger changes.

## Last verified

2026-10-20: 20 pytest tests pass. Testing found that a `NaN` payload caused an HTTP 500 (the 422 error body echoed the unserializable input); fixed with a validation handler that also omits submitted values from error bodies.

Earlier: the API passed FastAPI TestClient checks and a live local HTTP check: valid requests returned 200 and invalid employee counts returned 422. The notebook's Python cells ran from Python, and key profile totals were asserted; their outputs are not saved in the notebook. Docker image build remains unverified because the Docker engine was unavailable. No AWS resources have been provisioned.
