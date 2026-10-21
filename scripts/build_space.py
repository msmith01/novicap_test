"""Assemble a self-contained Hugging Face Space (Streamlit SDK) in dist/space.

    python scripts/build_space.py
    # then upload dist/space to a Space; see README "Showcase app"

The Space needs app.py at its root, so the scorer package, docs, evidence and video are copied next to it.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "space"

FRONT_MATTER = """---
title: Working Capital Risk Scoring
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: streamlit
sdk_version: 1.54.0
app_file: app.py
pinned: false
short_description: Risk scoring service, rollout and ops showcase
---

Interview showcase for a working-capital risk scoring service: live API, canary and shadow rollout,
and recorded evidence from the full Postgres, Prometheus and Alertmanager stack.
Source: see the repository linked from the app sidebar (set the REPO_URL variable on the Space).
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    app = ROOT / "streamlit_app"
    shutil.copy(app / "app.py", OUT / "app.py")
    shutil.copy(app / "requirements.txt", OUT / "requirements.txt")
    shutil.copytree(app / "evidence", OUT / "evidence")
    shutil.copytree(ROOT / "prototype", OUT / "prototype",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "ingest.py", "replay.py"))
    for name in ("DESIGN.md", "OPEN_QUESTIONS.md", "BRIEF_COVERAGE.md", "demo.mp4"):
        shutil.copy(ROOT / name, OUT / "evidence" / name)
    (OUT / "README.md").write_text(FRONT_MATTER, encoding="utf-8")
    print(f"built {OUT}: {sum(1 for p in OUT.rglob('*') if p.is_file())} files")


if __name__ == "__main__":
    main()
