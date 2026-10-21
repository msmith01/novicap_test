# Brief coverage: "What we'd like to see"

Status key: **Done** = built and run locally; **Partial** = built but not run in a real environment, or with a stated gap. Gaps are detailed in the "Left out" section of [DESIGN.md](DESIGN.md).

## 1. Look at the data
**Status: Done**
- Profiled `data/raw_applications.csv` in [analysis/data_exploration.ipynb](analysis/data_exploration.ipynb) (pandas).
- Findings: 196 rows; 4 application IDs repeated (8 rows, exact duplicates); 22 rows missing employee count, 3 zero or negative; mixed date formats, sector/region spellings, tax-ID punctuation.
- Built [prototype/ingest.py](prototype/ingest.py): normalises, flags implausible values, drops exact duplicates, imputes nothing (196 → 192 rows).
- Push-back for the source owner is in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) (are repeated IDs duplicates or revisions?).

## 2. Decide how to store this
**Status: Done** (the ingest-to-Postgres load is not wired)
- Raw CSVs: immutable, versioned, encrypted S3 objects (design only).
- Postgres holds applications and an append-only score history ([prototype/storage.py](prototype/storage.py)); SQLite is used locally ([scores.db](scores.db)).
- Each score stores validated inputs, decision, risk factors, ruleset version, image version, timestamp and request ID, so decisions are reproducible.
- Internal application ID plus the source ID kept as non-unique, because source IDs repeat.
- Schema managed with Alembic ([prototype/migrations/](prototype/migrations/)); `tax_id` access restricted and kept out of logs.
- Reasoning in [DESIGN.md](DESIGN.md), "Data and decisions".

## 3. Turn the prototype into something that serves a real request
**Status: Done**
- FastAPI `POST /score` with Pydantic validation; the scorer ([prototype/risk_scoring_prototype.py](prototype/risk_scoring_prototype.py)) is independent of HTTP.
- Bad data (missing or non-positive employees) gets a 422 and is never imputed.
- API-key auth (stopgap), a request ID per call, persisted before returning (503 if the write fails).
- [prototype/replay.py](prototype/replay.py) ran the full export through the same path: 158 low risk, 13 high risk, 25 rejected.
- Tests in [tests/test_score.py](tests/test_score.py) (pytest).
- [Dockerfile](Dockerfile) (non-root, port 8000) and [docker-compose.yml](docker-compose.yml) with Postgres, verified 2026-10-20.

## 4. Deploy and operate

### 4a. Version the model and roll out a new one safely
**Status: Done** (promotion and abort are manual)
- Explicit ruleset version strings in a registry (e.g. `2026-10-v1`).
- Replay and diff candidate rulesets before release (`prototype.replay`, with `--from-db` for stored applications).
- `SHADOW_RULESET` scores the candidate alongside live traffic without affecting the decision (tested).
- `CANARY_RULESET` and `CANARY_PERCENT` route a share of applications by hash of the application ID.
- Promote or roll back with `ACTIVE_RULESET`, a config change with no rebuild; an unknown name stops startup.
- Infra: ECS deployment circuit breaker with automatic rollback ([infra/aws/main.tf](infra/aws/main.tf)).

### 4b. Know if scoring is misbehaving without repayment outcomes
**Status: Partial** (thresholds are untuned guesses; no real notification targets)
- Prometheus metrics at `GET /metrics`: latency, errors, decisions and risk factors by ruleset, and input-ratio histograms for drift.
- Eight alerts in [ops/prometheus/alerts.yml](ops/prometheus/alerts.yml): 5xx, unrecorded scores, traffic drop, validation-reject spike, high-risk share drift, revenue-per-employee drift, shadow disagreement, shadow failure.
- Alertmanager ([ops/alertmanager/](ops/alertmanager/)) routes `page` and `ticket` to Discord; the webhook is git-ignored and still a placeholder.
- Stated limit: these catch faults and drift, not predictive quality. That needs repayment outcomes.

### 4c. What to log and track so incidents are debuggable weeks later
**Status: Done**
- JSON logs hold metadata only: request ID, status, latency, decision, factors. Never raw values or `tax_id`.
- The append-only score table records inputs, ruleset and image version and request ID, so any decision can be replayed later.
- Metrics carry the ruleset version and a bounded `source_system` label (migration 0002).

### 4d. What changes when Data Science sends an updated prototype
**Status: Done** (described in DESIGN.md; example only)
- Only the ruleset changes. The HTTP contract, storage and request boundary stay the same.
- Process: add a new version, test it, replay and diff, shadow, canary, promote. `2026-11-v2-candidate` is a worked example of the mechanism, not a real change.

## Deliverables
| Deliverable | Where |
|---|---|
| Working code | [prototype/](prototype/), [tests/](tests/) |
| Dockerfile | [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml) |
| DESIGN.md | [DESIGN.md](DESIGN.md) |
| Left out / next steps | "Left out" section of [DESIGN.md](DESIGN.md) |

## Extras beyond the brief
- Terraform for AWS ([infra/aws/main.tf](infra/aws/main.tf)): validated, never applied.
- Single-VPS deployment with Caddy HTTPS ([docker-compose.vps.yml](docker-compose.vps.yml), [deploy/VPS.md](deploy/VPS.md)).
- CI workflow ([.github/workflows/ci.yml](.github/workflows/ci.yml)): defined, never run.
- Narrated walkthrough: [demo.mp4](demo.mp4).
