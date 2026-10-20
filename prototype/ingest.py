"""Ingestion step: normalise the raw export into a clean CSV plus a data-quality report.

    python -m prototype.ingest data/raw_applications.csv --out clean.csv

Cleaning is limited to unambiguous normalisation (null tokens, date format, label spellings,
tax-ID punctuation, exact duplicate rows). Nothing is imputed: a missing employee count stays
missing, so the scoring service still rejects the row. Suspicious-but-valid values are flagged
in a `flags` column, not changed. Prints aggregate counts only.
"""

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import date, datetime

MISSING = {"", "null", "na", "n/a"}

# Observed spellings -> canonical label. Unknown spellings are kept as-is and counted.
SECTORS = {
    "food manufacturing": "food_manufacturing", "food manuf.": "food_manufacturing",
    "food & beverage mfg": "food_manufacturing",
    "it services": "it_services", "information technology": "it_services", "it / software": "it_services",
    "retail": "retail", "retail / wholesale": "retail", "retail trade": "retail",
    "construction": "construction", "building & construction": "construction",
    "business consulting": "consultancy", "consultancy": "consultancy", "management consultancy": "consultancy",
}
REGIONS = {"cataluna": "cataluna", "catalunya": "cataluna"}  # same region, two spellings

OUT_FIELDS = [
    "application_id", "tax_id", "sector", "region", "incorporation_date", "employees",
    "annual_revenue", "annual_ebitda", "requested_amount", "source_system", "flags",
]


def clean(value: str | None) -> str:
    value = (value or "").strip()
    return "" if value.lower() in MISSING else value


def parse_date(value: str) -> str | None:
    """ISO or dd/mm/yyyy (the export's slash dates have day-first: days reach 28, months stop at 11)."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        return parsed.isoformat() if parsed <= date.today() else None
    return None


def normalise_row(row: dict) -> tuple[dict, list[str]]:
    flags = []
    out = {k: clean(row.get(k)) for k in OUT_FIELDS if k != "flags"}
    out["tax_id"] = re.sub(r"[\s-]", "", out["tax_id"]).upper()

    sector = out["sector"].lower()
    if sector in SECTORS:
        out["sector"] = SECTORS[sector]
    elif sector:
        flags.append("unknown_sector")
    region = out["region"].lower()
    out["region"] = REGIONS.get(region, region.replace(" ", "_"))

    if out["incorporation_date"]:
        parsed = parse_date(out["incorporation_date"])
        if parsed is None:
            flags.append("bad_date")
        out["incorporation_date"] = parsed or ""
    else:
        flags.append("missing_date")

    if not out["annual_ebitda"]:
        flags.append("missing_ebitda")
    try:
        employees, revenue = int(out["employees"]), float(out["annual_revenue"])
        if employees > 10_000:
            flags.append("employees_implausible")
        if employees > 0 and revenue / employees < 1_000:
            flags.append("revenue_per_employee_implausible")
    except ValueError:
        pass
    return out, flags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path")
    parser.add_argument("--out", required=True, help="write the cleaned CSV here")
    args = parser.parse_args(argv)

    seen, cleaned, counts = set(), [], Counter()
    with open(args.csv_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            counts["rows_in"] += 1
            key = tuple((k, (v or "").strip()) for k, v in sorted(row.items()))
            if key in seen:
                counts["exact_duplicates_dropped"] += 1
                continue
            seen.add(key)
            out, flags = normalise_row(row)
            counts.update(f"flag:{f}" for f in flags)
            out["flags"] = ";".join(flags)
            cleaned.append(out)

    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_FIELDS)
        writer.writeheader()
        writer.writerows(cleaned)

    counts["rows_out"] = len(cleaned)
    counts["ids_still_repeated"] = sum(n > 1 for n in Counter(r["application_id"] for r in cleaned).values())
    for name, n in sorted(counts.items()):
        print(f"{name}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
