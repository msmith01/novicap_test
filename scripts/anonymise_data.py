"""Write the hosted app's copy of the export: the same 196 rows without the identifying columns.

    python scripts/anonymise_data.py

data/raw_applications.csv stays out of the repo (it holds company names and tax IDs). The Streamlit Data tab
only needs the numeric and categorical columns, so it ships this copy instead.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DROP = ["company_name", "tax_id", "notes"]

raw = pd.read_csv(ROOT / "data" / "raw_applications.csv", dtype=str, keep_default_na=False)
out = ROOT / "streamlit_app" / "evidence" / "applications_anonymised.csv"
raw.drop(columns=DROP).to_csv(out, index=False)
print(f"wrote {out} ({len(raw)} rows, dropped {', '.join(DROP)})")
