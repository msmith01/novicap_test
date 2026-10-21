"""Streamlit showcase of the working-capital risk scoring project.

The scoring API, rulesets, canary/shadow logic, persistence and Prometheus metrics run live inside this
app (FastAPI driven through its test client, SQLite in memory). Postgres, Prometheus and Alertmanager
cannot run on a free host, so the "Recorded stack evidence" tab shows a timestamped snapshot taken from
the real docker compose stack by scripts/capture_evidence.py.
"""

import json
import os
import random
import re
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")  # the module-level app in the scorer must not write a file

HERE = Path(__file__).resolve().parent
for root in (HERE, HERE.parent):  # repo checkout (Streamlit Cloud) or assembled Space
    if (root / "prototype").is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))

import pandas as pd
import streamlit as st
from fastapi.testclient import TestClient

from prototype.risk_scoring_prototype import ScoreRequest, create_app, score_application
from prototype.storage import make_session_factory

KEY = "showcase"
HEADERS = {"X-API-Key": KEY}
ACTIVE, CANDIDATE = "2026-10-v1", "2026-11-v2-candidate"
REPO_URL = os.environ.get("REPO_URL", "")

st.set_page_config(page_title="Working Capital Risk Scoring", page_icon="📊", layout="wide")


def find(name: str) -> Path | None:
    for base in (HERE / "evidence", HERE, HERE.parent):
        if (base / name).is_file():
            return base / name
    return None


def read_text(name: str) -> str:
    path = find(name)
    return path.read_text(encoding="utf-8") if path else f"*{name} is not bundled in this deployment.*"


def plain_links(markdown: str) -> str:
    """Relative repo links would be dead on this host; keep the text."""
    return re.sub(r"\[([^\]]+)\]\((?!https?://)[^)]*\)", r"\1", markdown)


@st.cache_resource
def get_client() -> TestClient:
    app = create_app(
        session_factory=make_session_factory("sqlite://"),
        api_keys=[KEY],
        image_version="streamlit-showcase",
        active_ruleset=ACTIVE,
        canary_ruleset=CANDIDATE,
        canary_percent=50,
        shadow_ruleset=CANDIDATE,
    )
    return TestClient(app)


client = get_client()


def overview() -> dict:
    return client.get("/console/api/overview", headers=HEADERS).json()


@st.cache_data
def snapshot() -> dict | None:
    path = find("stack_snapshot.json")
    return json.loads(path.read_text(encoding="utf-8")) if path else None


st.title("Working Capital Risk Scoring")
st.caption(
    "A rules-based risk score turned into a validated, persisted, observable service. "
    "Parts of this page are live; each tab says which."
)
if REPO_URL:
    st.sidebar.link_button("Source repository", REPO_URL)
st.sidebar.markdown(
    "**Live in this app:** FastAPI, Pydantic, SQLAlchemy, Alembic, canary and shadow rulesets, Prometheus metrics.\n\n"
    "**Recorded from the real stack:** Postgres, Prometheus, Alertmanager.\n\n"
    "**In the repo only:** Terraform, CI, Caddy, Discord routing."
)
if st.sidebar.button("Reset live database"):
    get_client.clear()
    st.rerun()

tabs = st.tabs(["Overview", "Try the API", "Rollout lab", "Recorded stack evidence", "Demo video", "Design", "Tools used"])

# ---------------------------------------------------------------- Overview
with tabs[0]:
    st.subheader("What the brief asked, and what exists")
    st.markdown(plain_links(read_text("BRIEF_COVERAGE.md")))

# ---------------------------------------------------------------- Try the API
with tabs[1]:
    st.subheader("Score an application (live)")
    st.caption("This sends a real `POST /score` through the FastAPI app: auth, validation, scoring, persistence, metrics.")
    presets = {
        "Healthy": (10, 1_000_000, 100_000),
        "Thin revenue per employee": (100, 1_000_000, 100_000),
        "Over-asking": (10, 1_000_000, 500_000),
        "Both rules trip": (100, 1_000_000, 500_000),
        "Invalid: zero employees": (0, 1_000_000, 100_000),
    }
    preset = st.selectbox("Preset", list(presets))
    emp0, rev0, req0 = presets[preset]
    with st.form(f"score-{preset}"):
        c1, c2 = st.columns(2)
        app_id = c1.text_input("Application ID", f"DEMO-{random.randint(1000, 9999)}")
        source = c2.selectbox("Source system", ["manual_entry", "legacy_crm", "new_partner_feed"])
        c3, c4, c5 = st.columns(3)
        employees = c3.number_input("Employees", value=emp0, step=1)
        revenue = c4.number_input("Annual revenue", value=float(rev0), step=10_000.0)
        requested = c5.number_input("Requested amount", value=float(req0), step=10_000.0)
        sent = st.form_submit_button("POST /score")
    if sent:
        body = {
            "application_id": app_id, "employees": int(employees), "annual_revenue": revenue,
            "requested_amount": requested, "source_system": source,
        }
        response = client.post("/score", json=body, headers=HEADERS)
        if response.is_success:
            out = response.json()
            (st.error if out["decision"] == "high_risk" else st.success)(
                f"**{out['decision']}**  ·  factors: {', '.join(out['risk_factors']) or 'none'}  ·  ruleset {out['ruleset_version']}"
            )
            st.json({"request": body, "response": out, "X-Request-ID": response.headers["X-Request-ID"]})
        else:
            st.warning(f"HTTP {response.status_code}: rejected, nothing stored and nothing imputed")
            st.json(response.json())
    st.markdown("**Stored scores** (append-only `score_history` joined to `applications`)")
    rows = overview()["recent"]
    if rows:
        st.dataframe(pd.DataFrame(rows).drop(columns=["image_version"]), width="stretch", hide_index=True)
    else:
        st.info("Nothing scored yet.")
    with st.expander("Live Prometheus metrics (`GET /metrics`)"):
        text = client.get("/metrics", headers=HEADERS).text
        st.code("\n".join(l for l in text.splitlines() if l.startswith(("score_", "shadow_", "http_requests_total"))), language="text")

# ---------------------------------------------------------------- Rollout lab
with tabs[2]:
    st.subheader("Roll out a new ruleset safely (live)")
    st.markdown(
        f"Active `{ACTIVE}`. The candidate `{CANDIDATE}` raises the requested-amount ceiling from 20% to 25% of revenue. "
        "It runs in **shadow** for every request (scored, recorded, never returned) and as a **50% canary** "
        "that decides for half of application IDs, stable per ID."
    )
    n = st.slider("Synthetic applications to send", 50, 500, 200, step=50)
    if st.button("Send synthetic traffic"):
        rng = random.Random()
        before = overview()["shadow"]
        diffs, progress = [], st.progress(0.0)
        for i in range(n):
            employees = max(1, int(rng.lognormvariate(2.7, 0.9)))
            revenue = employees * rng.lognormvariate(11.2, 0.6)
            requested = revenue * rng.lognormvariate(-2.1, 0.7)
            body = {"application_id": f"LAB-{rng.randrange(10**8)}", "employees": employees,
                    "annual_revenue": round(revenue), "requested_amount": round(requested), "source_system": "new_partner_feed"}
            client.post("/score", json=body, headers=HEADERS)
            old = score_application(ScoreRequest(**body), ACTIVE).decision
            new = score_application(ScoreRequest(**body), CANDIDATE).decision
            if old != new:
                diffs.append({"application_id": body["application_id"], "ratio": round(requested / revenue, 3), "v1": old, "v2": new})
            progress.progress((i + 1) / n)
        after = overview()["shadow"]
        st.session_state["lab"] = {
            "n": n, "diffs": diffs,
            "shadow_runs": after["decisions"] - before["decisions"],
            "shadow_disagreements": after["disagreements"] - before["disagreements"],
        }
    lab = st.session_state.get("lab")
    if lab:
        a, b, c = st.columns(3)
        a.metric("Requests sent", lab["n"])
        b.metric("Shadow runs recorded", int(lab["shadow_runs"]))
        c.metric("Shadow disagreements", int(lab["shadow_disagreements"]),
                 f"{lab['shadow_disagreements'] / max(lab['shadow_runs'], 1):.1%} of decisions", delta_color="off")
        st.markdown("**Replay diff**: applications whose decision would change if the candidate were promoted")
        if lab["diffs"]:
            st.dataframe(pd.DataFrame(lab["diffs"]), width="stretch", hide_index=True)
        else:
            st.info("No decisions would change in this sample.")
    split = pd.DataFrame(overview()["by_ruleset"])
    if not split.empty:
        st.markdown("**Decisions by ruleset arm** (all live traffic so far)")
        st.bar_chart(split.pivot_table(index="ruleset", columns="decision", values="count", aggfunc="sum", fill_value=0))
    st.caption("Rollback is changing `ACTIVE_RULESET`; an unknown ruleset name stops startup. Promotion and abort are manual.")

# ---------------------------------------------------------------- Recorded evidence
with tabs[3]:
    snap = snapshot()
    st.subheader("Recorded from the real docker compose stack")
    if not snap:
        st.info("No snapshot bundled. Run `python scripts/capture_evidence.py --key <key>` against the running stack.")
    else:
        st.warning(f"Recording, not live. Captured {snap['captured_at']} from: {snap['stack']}.")
        ov = snap["overview"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Scores in Postgres", ov["totals"]["scored"])
        c2.metric("Postgres", ov["schema"].get("server_version") or "n/a")
        c3.metric("Alembic revision", ov["schema"].get("revision") or "n/a")
        c4.metric("Alertmanager", snap["alertmanager_status"].get("version", "n/a"))
        st.markdown("**Prometheus alert rules**")
        rules = [r for g in snap["prometheus_rules"]["groups"] for r in g["rules"]]
        st.dataframe(pd.DataFrame([{
            "alert": r["name"], "severity": r["labels"].get("severity"), "state": r["state"],
            "summary": r["annotations"].get("summary"),
        } for r in rules]), width="stretch", hide_index=True)
        firing = snap["alertmanager_alerts"]
        if firing:
            st.markdown("**Alertmanager had active alerts**")
            st.dataframe(pd.DataFrame([{"alert": a["labels"]["alertname"], "severity": a["labels"].get("severity"),
                                        "state": a["status"]["state"], "since": a["startsAt"]} for a in firing]),
                         width="stretch", hide_index=True)
            st.caption("Earlier test traffic skewed the high-risk share against the 24h baseline, so the drift alert fired.")
        st.markdown("**Rows read back from Postgres**")
        st.dataframe(pd.DataFrame(ov["recent"]).drop(columns=["image_version"]), width="stretch", hide_index=True)
        st.markdown("**Canary split in Postgres**")
        st.dataframe(pd.DataFrame(ov["by_ruleset"]), width="stretch", hide_index=True)
        with st.expander("Metric samples scraped from /metrics"):
            st.code("\n".join(snap["metric_samples"]), language="text")
        st.info("Alertmanager routes to a Discord webhook, but that webhook was a placeholder when this was recorded, so Discord delivery has not been exercised.")

# ---------------------------------------------------------------- Demo video
with tabs[4]:
    st.subheader("Narrated walkthrough")
    video = find("demo.mp4")
    if video:
        st.video(str(video))
        st.caption("Generated by running the real commands against the compose stack; the script is in demo/narration.txt.")
    else:
        st.info("demo.mp4 is not bundled in this deployment.")

# ---------------------------------------------------------------- Design
with tabs[5]:
    st.subheader("Design decisions and open questions")
    with st.expander("DESIGN.md", expanded=True):
        st.markdown(plain_links(read_text("DESIGN.md")))
    with st.expander("OPEN_QUESTIONS.md (for the data owner)"):
        st.markdown(plain_links(read_text("OPEN_QUESTIONS.md")))

# ---------------------------------------------------------------- Tools
with tabs[6]:
    st.subheader("Tools used, and how much of each you can see here")
    st.dataframe(pd.DataFrame([
        ("FastAPI + Pydantic", "HTTP contract, validation, auth", "Live in this app (Try the API)"),
        ("SQLAlchemy + Alembic", "Append-only score history, migrations on startup", "Live (SQLite here)"),
        ("prometheus-client", "Latency, decisions, drift histograms, shadow counters", "Live (metrics expander)"),
        ("Canary + shadow rulesets", "Safe rollout and config-only rollback", "Live (Rollout lab)"),
        ("PostgreSQL 16", "System of record", "Recorded snapshot"),
        ("Prometheus + 8 alert rules", "Fault and drift detection without repayment outcomes", "Recorded snapshot"),
        ("Alertmanager", "Severity routing", "Recorded snapshot; Discord delivery not exercised"),
        ("Docker + Compose", "Image and local stack", "Verified locally; shown via the demo video"),
        ("pytest", "Thresholds, validation, auth, persistence failure, log privacy", "In the repo"),
        ("GitHub Actions", "CI workflow", "In the repo; defined, not yet run"),
        ("Caddy + VPS compose", "HTTPS single-host deployment", "In the repo; not run on a real server"),
        ("Terraform (AWS)", "Production design as code", "In the repo; validated, never applied"),
        ("Jupyter + pandas", "Data profile of the export", "In the repo"),
    ], columns=["Tool", "Used for", "What you can see"]), width="stretch", hide_index=True)
