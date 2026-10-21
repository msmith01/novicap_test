"""Snapshot the running compose stack into streamlit_app/evidence/stack_snapshot.json.

The hosted showcase cannot run Postgres, Prometheus or Alertmanager, so it shows this recording,
labelled with the time it was taken. Aggregates and demo rows only; never run it against real data.

    python scripts/capture_evidence.py --key demo-key
"""

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "streamlit_app" / "evidence" / "stack_snapshot.json"


def get(url: str, key: str | None = None, raw: bool = False):
    request = urllib.request.Request(url, headers={"X-API-Key": key} if key else {})
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode()
    return body if raw else json.loads(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--alertmanager", default="http://localhost:9093")
    args = parser.parse_args()

    overview = get(f"{args.api}/console/api/overview", args.key)
    monitoring = get(f"{args.api}/console/api/monitoring", args.key)
    metrics = get(f"{args.api}/metrics", args.key, raw=True)
    keep = ("score_decisions_total", "shadow_", "http_requests_total", "score_persistence")
    metric_lines = [l for l in metrics.splitlines() if l.startswith(keep)]
    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "stack": "docker compose: api, postgres 16, prometheus, alertmanager",
        "overview": overview,
        "monitoring": monitoring,
        "prometheus_rules": get(f"{args.prometheus}/api/v1/rules?type=alert")["data"],
        "alertmanager_status": get(f"{args.alertmanager}/api/v2/status")["versionInfo"],
        "alertmanager_alerts": get(f"{args.alertmanager}/api/v2/alerts"),
        "metric_samples": metric_lines,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
