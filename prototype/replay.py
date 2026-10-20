"""Replay a CSV export through the same validation and scoring the API uses.

    python -m prototype.replay data/raw_applications.csv [--out results.csv]
    python -m prototype.replay --from-db [sqlite:///scores.db]

With --from-db, re-scores every stored application under the current ruleset and reports how
many decisions differ from the stored ones (the release check before shadow/canary).

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


def replay_db(url: str | None) -> int:
    """Re-score stored applications; print aggregate decision changes (no identifying values)."""
    from sqlalchemy import select

    from prototype.storage import Application, ScoreRecord, make_session_factory

    changes, total = Counter(), 0
    with make_session_factory(url)() as session:
        rows = session.execute(
            select(Application, ScoreRecord).join(ScoreRecord, ScoreRecord.application_id == Application.id)
        )
        for app, record in rows:
            total += 1
            new = score_application(
                ScoreRequest(
                    application_id=app.source_application_id,
                    employees=app.employees,
                    annual_revenue=app.annual_revenue,
                    requested_amount=app.requested_amount,
                )
            ).decision
            changes[(record.decision, new)] += 1
    changed = sum(n for (old, new), n in changes.items() if old != new)
    print(f"ruleset {RULESET_VERSION}: {total} stored scores re-scored, {changed} decisions changed")
    for (old, new), n in sorted(changes.items()):
        if old != new:
            print(f"  {old} -> {new}: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", nargs="?")
    parser.add_argument("--from-db", nargs="?", const="", metavar="URL", help="replay stored scores")
    parser.add_argument("--out", help="write per-row results to this CSV")
    args = parser.parse_args(argv)
    if args.from_db is not None:
        return replay_db(args.from_db or None)
    if not args.csv_path:
        parser.error("csv_path or --from-db is required")

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
