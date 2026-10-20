# Design

## Request path

FastAPI exposes `POST /score`. Pydantic validates the request before the scoring function runs; the scorer stays independent of HTTP. The response includes the decision and triggered rules. Each request gets a request ID, is authenticated by API key (a stopgap), and is persisted before the decision is returned; if the write fails the service returns 503 rather than an unrecorded decision. Logs are JSON metadata only (request ID, status, latency, decision, factors), never request values. The ruleset carries an explicit version string. Schema changes go through Alembic migrations applied at startup. The endpoint is still a working slice: no production identity yet. `GET /metrics` (API key required) exposes Prometheus counters and histograms: requests and latency by route and status, decisions and risk factors by ruleset version, persistence failures, and aggregate input-ratio histograms for drift; nothing identifying or raw.

The checked-in Dockerfile packages the API on port 8000 and runs as a non-root user. Build and run it locally with `docker build -t working-capital-risk .` and `docker run --rm -p 8000:8000 -e API_KEYS=dev-key working-capital-risk` (or `docker compose up --build` for the service with Postgres), then try `/docs`. The build context excludes the raw data and notebook. For production, publish the image to ECR and run it on ECS Fargate behind authenticated HTTPS. Keep RDS Postgres private to the service. No AWS account is needed to agree on this design; provisioning comes later.

## Data and decisions

The prototype had never seen the full export, so `python -m prototype.replay data/raw_applications.csv` runs every row through the same validation and scorer as the API. Result (ruleset 2026-10-v1): of 196 rows, 158 low risk, 13 high risk, 25 rejected (22 missing employee count, 3 zero or negative). The service rejects such input with 422 instead of imputing, so a missing value is never silently scored; whether to repair or request it is for the source owner. The same tool, with `--out`, produces per-row results to diff between rulesets before release.

Keep each source CSV as an immutable, encrypted, versioned object in S3. Use Postgres for operational applications and an append-only score history. Each score should capture the validated inputs, decision, risk factors, ruleset and image versions, timestamp, and request ID. This makes a decision reproducible even after rules change.

The export has repeated application IDs, so do not treat that source field as a unique key until the source owner explains whether repeats are revisions or duplicate submissions. Assign an internal application ID and retain the source ID. Restrict access to `tax_id`; exclude it and full financial payloads from logs.

## Safe changes and operations

Give every ruleset an explicit version. Before release, replay a representative historical set and review changed decisions. Run the candidate in shadow mode, then a small canary; keep the prior image and ruleset available for rollback. Keep database changes backward-compatible across releases.

Track request volume and latency, 4xx/5xx errors, validation rejections, decision and risk-factor rates, and shifts in input distributions. Break these down by source and relevant applicant groups, and alert on changes against a baseline. These signals can show operational faults or input drift, but they cannot establish repayment predictive quality. Evaluate that when repayment outcomes mature.

When Data Science changes the formula, replace and test the ruleset while keeping the HTTP contract stable. Replay, compare, shadow, and canary it as above. The storage and request boundaries stay the same.

## Left out, and what I would do next

- **Duplicate application IDs:** four IDs repeat in the export; I stored every submission under an internal ID and did not deduplicate. Ask the source owner whether repeats are duplicates or revisions (see OPEN_QUESTIONS.md).
- **Production identity:** API keys are a stopgap. Move to the platform's identity and keep secrets in a secrets manager.
- **Monitoring:** the metrics exist, but nothing scrapes them yet. Add Prometheus/CloudWatch collection, dashboards, and alerts against baselines, with breakdowns by source system (needs `source_system` in the request).
- **AWS:** ECR, ECS Fargate and RDS are designed, not provisioned. No CI/CD pipeline yet.
- **Release tooling:** `prototype.replay` covers replay and diffing over a CSV, and `--from-db` re-scores stored applications and reports changed decisions; shadow and canary are described, not built.
- **Data issues:** the service rejects bad inputs (422) rather than imputing. Cleaning the export (dates, sector and region labels, missing EBITDA) belongs in an ingestion step I did not build.
