# Design

## Request path

FastAPI exposes `POST /score`. Pydantic validates the request before the scoring function runs; the scorer stays independent of HTTP. The response includes the decision and triggered rules. Each request gets a request ID, is authenticated by API key (a stopgap), and is persisted before the decision is returned; if the write fails the service returns 503 rather than an unrecorded decision. Logs are JSON metadata only (request ID, status, latency, decision, factors), never request values. The ruleset carries an explicit version string. The endpoint is still a working slice: no migrations, production identity, or monitoring yet.

The checked-in Dockerfile packages the API on port 8000 and runs as a non-root user. Build and run it locally with `docker build -t working-capital-risk .` and `docker run --rm -p 8000:8000 working-capital-risk`, then try `/docs`. The build context excludes the raw data and notebook. For production, publish the image to ECR and run it on ECS Fargate behind authenticated HTTPS. Keep RDS Postgres private to the service. No AWS account is needed to agree on this design; provisioning comes later.

## Data and decisions

Keep each source CSV as an immutable, encrypted, versioned object in S3. Use Postgres for operational applications and an append-only score history. Each score should capture the validated inputs, decision, risk factors, ruleset and image versions, timestamp, and request ID. This makes a decision reproducible even after rules change.

The export has repeated application IDs, so do not treat that source field as a unique key until the source owner explains whether repeats are revisions or duplicate submissions. Assign an internal application ID and retain the source ID. Restrict access to `tax_id`; exclude it and full financial payloads from logs.

## Safe changes and operations

Give every ruleset an explicit version. Before release, replay a representative historical set and review changed decisions. Run the candidate in shadow mode, then a small canary; keep the prior image and ruleset available for rollback. Keep database changes backward-compatible across releases.

Track request volume and latency, 4xx/5xx errors, validation rejections, decision and risk-factor rates, and shifts in input distributions. Break these down by source and relevant applicant groups, and alert on changes against a baseline. These signals can show operational faults or input drift, but they cannot establish repayment predictive quality. Evaluate that when repayment outcomes mature.

When Data Science changes the formula, replace and test the ruleset while keeping the HTTP contract stable. Replay, compare, shadow, and canary it as above. The storage and request boundaries stay the same.
