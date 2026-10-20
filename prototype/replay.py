"""Replay a CSV export through the same validation and scoring the API uses.

    python -m prototype.replay data/raw_applications.csv [--out results.csv]

Prints aggregate counts only (no tax IDs or company names). With --out, writes one row per
input with the decision or rejection reason, so a ruleset change can be diffed against a
previous results file.
"""

import argparse
import csv
import sys
from collections import Counter

from pydantic import ValidationError

from prototype.risk_scoring_prototype import RULESET_VERSION, ScoreRequest, score_application

MISSING = {"", "null", "na", "n/a"}
FIELDS = ("employees", "annual_revenue", "requested_amount")


def clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value.lower() in MISSING else value


def replay_row(row: dict) -> tuple[str, str]:
    """Return (outcome, detail): outcome is high_risk, low_risk or rejected."""
    payload = {"application_id": clean(row.get("application_id")) or ""}
    payload.update({f: clean(row.get(f)) for f in FIELDS})
    try:
        result = score_application(ScoreRequest.model_validate(payload))
    except ValidationError as exc:
        # Field names and error types only; never the submitted values.
        reasons = sorted({f"{'.'.join(map(str, e['loc']))}:{e['type']}" for e in exc.errors()})
        return "rejected", ";".join(reasons)
    return result.decision, ";".join(result.risk_factors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path")
    parser.add_argument("--out", help="write per-row results to this CSV")
    args = parser.parse_args(argv)

    outcomes, reasons, rows = Counter(), Counter(), []
    with open(args.csv_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            outcome, detail = replay_row(row)
            outcomes[outcome] += 1
            if outcome == "rejected":
                reasons.update(detail.split(";"))
            else:
                reasons.update(f for f in detail.split(";") if f)
            rows.append((row.get("application_id", ""), outcome, detail))

    total = sum(outcomes.values())
    print(f"ruleset {RULESET_VERSION}: {total} rows")
    for name in ("low_risk", "high_risk", "rejected"):
        print(f"  {name}: {outcomes[name]}")
    print("reasons:")
    for reason, count in reasons.most_common():
        print(f"  {reason}: {count}")

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["application_id", "outcome", "detail", "ruleset_version"])
            writer.writerows((a, o, d, RULESET_VERSION) for a, o, d in rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
