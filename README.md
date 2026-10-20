# Working Capital Risk Scoring

Start here when returning to the project. The challenge brief is [Brief.pdf](Brief.pdf); the current architecture and trade-offs are in [DESIGN.md](DESIGN.md).

## Current state

The prototype is now a FastAPI service with `POST /score`. It preserves the original two rules and validates requests before scoring. The notebook, [analysis/data_exploration.ipynb](analysis/data_exploration.ipynb), documents the data profile, storage recommendation, HTTP contract, and deployment/operations plan.

The service is a local working slice, not production-ready. It persists scores, requires an API key and returns a ruleset version, but it has no production-grade identity, and alerting is not yet delivering anywhere. The notebook's code cells have not been run in the VS Code notebook kernel; they were validated from Python. Run the notebook top-to-bottom to populate visible outputs.

## Run locally

From the repository folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:API_KEYS = "dev-key"
python -m uvicorn prototype.risk_scoring_prototype:app --reload
```

Scores are stored in a local SQLite file (`scores.db`) unless `DATABASE_URL` points at Postgres (`postgresql+psycopg://user:pass@host/db`). `/score` requires an `X-API-Key` header matching one of the comma-separated `API_KEYS`; with none configured it returns 503. `GET /healthz` is open. `GET /metrics` (API key required) serves Prometheus metrics. `ACTIVE_RULESET` selects the deciding ruleset (rollback) `SHADOW_RULESET` trials a candidate without affecting responses, and `CANARY_RULESET` + `CANARY_PERCENT` let it decide for a share of applications; see DESIGN.md. `docker compose up` also starts Prometheus on port 9090 with the alert rules in `ops/prometheus/`, and Alertmanager on 9093. AWS infrastructure as code is in `infra/aws/` (validated, not applied); single-VPS deployment is in `deploy/VPS.md`. Alerts go to Discord: copy `ops/alertmanager/discord_webhook.example` to `discord_webhook` and set the URL. The demo narration text is in `demo/narration.txt`.

Open `http://127.0.0.1:8000/docs` (use Authorize with the key) to try the API. Example request:

```json
{"application_id":"A-123","employees":10,"annual_revenue":1000000,"requested_amount":100000}
```

The response contains `decision`, `risk_factors`, `ruleset_version` and `request_id` (also sent as the `X-Request-ID` header). Invalid requests, including zero or negative employees/revenue/request amounts, receive HTTP 422.

## Run in Docker

With Docker Desktop's Linux engine running:

```powershell
docker build -t working-capital-risk .
docker run --rm -p 8000:8000 -e API_KEYS=dev-key working-capital-risk
```

Then open `http://127.0.0.1:8000/docs`. The Docker build context excludes `data/`, `analysis/`, and the brief. The image build and the `docker compose up` stack with Postgres were verified on 2026-10-20. If the build fails with `error getting credentials` in a non-interactive shell, pull `python:3.12-slim` and `postgres:16-alpine` first with `DOCKER_CONFIG` pointing at a folder containing `{}` in `config.json`, then build with `DOCKER_BUILDKIT=0`.

## Project map

- `prototype/risk_scoring_prototype.py`: FastAPI app, auth, logging, request model, and scoring rules.
- `prototype/storage.py`: SQLAlchemy models and `save_score`; applies Alembic migrations on startup.
- `prototype/migrations/`: Alembic migrations (add one per schema change; `alembic -x url=<db> upgrade head` also works from the CLI).
- `prototype/replay.py`: replays a CSV through the API's validation and scorer; prints aggregate counts and optionally writes per-row results.
- `tests/`: pytest suite.
- `data/raw_applications.csv`: source export; contains sensitive identifiers, do not upload or log it.
- `analysis/data_exploration.ipynb`: brief sections 1-4 and the exploratory profile.
- `DESIGN.md`: concise architecture and operating decisions.
- `Dockerfile`, `requirements.txt`, `.dockerignore`: local container setup.

## Resume checklist

Done: automated tests (43; `python -m pip install -r requirements-dev.txt`, then `python -m pytest`); Postgres-backed persistence (applications plus append-only `score_history` with request ID, ruleset and image versions); API-key auth; privacy-safe JSON logs; Prometheus metrics, 8 alert rules and Alertmanager (compose); shadow and canary rulesets with config rollback; ingest and replay tools; Dockerfile and compose stack; Terraform for AWS (validated, not applied); single-VPS compose and guide; narrated demo video; `Brief.pdf` highlighted with the completed points. Local commits are not yet pushed.

Still open, in the order to do them next:

1. **Push to GitHub (blocked on you):** `gh` login for `msmith01` is expired. Run `C:\Users\MattO\scoop\apps\gh\2.96.0\bin\gh.exe auth login -h github.com`, then create a private repo and push; that also runs CI for the first time.
2. **Discord webhook (blocked on you):** put the URL in `ops/alertmanager/discord_webhook` (git-ignored; template is `.example`), restart Alertmanager, and send a test alert. Until then that file holds a placeholder.
3. **Voiceover (optional):** `demo/narration.txt` is the script (one line per scene). The current video uses the Windows Zira voice; if ElevenLabs clips are supplied, re-mux them. The video was generated by a script kept outside the repo; re-creating it means re-running the real commands against the compose stack.
4. **VPS deploy (optional):** follow `deploy/VPS.md`; needs the server and its SSH details, and a DNS name for HTTPS. Untested on a real server.
5. **Duplicate application IDs / missing employee counts** need answers from the data source owner; see [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).
6. **Not built:** dashboards, thresholds tuned on real traffic, automated canary promotion/abort, production identity (API keys are a stopgap), AWS apply.

Local run notes (Windows): `docker compose up --build` can fail with a credential error; build with `DOCKER_BUILDKIT=0 docker build -t candidate_package-api .` using an empty `DOCKER_CONFIG`, then `docker compose up -d --no-build`. Set `API_KEYS` first (the demo uses `demo-key`; `ops/prometheus/api_key` must contain the same value). Optional: `SHADOW_RULESET`, `CANARY_RULESET`, `CANARY_PERCENT` (example ruleset: `2026-11-v2-candidate`).

## Last verified

2026-10-20 (evening): 43 tests pass; compose stack (API, Postgres, Prometheus, Alertmanager) ran with a 50% canary and shadow ruleset; Prometheus scraped the API, loaded 8 rules and connected to Alertmanager; `terraform validate` and `amtool check-config` passed. Discord delivery, the VPS compose file on a real server, and the AWS apply were not exercised.

2026-10-20 (later): Docker image built and the compose stack ran against Postgres 16: `/healthz` 200, `/score` 200 with a row in `score_history`, missing key 401, Alembic at revision `0001`.

2026-10-20: 33 pytest tests pass (replay, metrics, migration/model drift check, scoring thresholds, validation, persistence, auth, 503 on write failure, log privacy). Testing found that a `NaN` payload caused an HTTP 500 (the 422 error body echoed the unserializable input); fixed with a validation handler that also omits submitted values from error bodies.

Earlier: the API passed FastAPI TestClient checks and a live local HTTP check: valid requests returned 200 and invalid employee counts returned 422. The notebook's Python cells ran from Python, and key profile totals were asserted; their outputs are not saved in the notebook.  No AWS resources have been provisioned.
