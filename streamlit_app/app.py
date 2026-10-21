"""Streamlit showcase of the working-capital risk scoring project.

The scoring API, rulesets, canary/shadow logic, persistence and Prometheus metrics run live inside this
app (FastAPI driven through its test client, SQLite in memory). Postgres, Prometheus and Alertmanager
cannot run on a free host, so the monitoring section shows a timestamped snapshot taken from the real
docker compose stack by scripts/capture_evidence.py.
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



def find_data() -> Path | None:
    for base in (HERE.parent, HERE):
        if (base / "data" / "raw_applications.csv").is_file():
            return base / "data" / "raw_applications.csv"
    return None




@st.cache_data
def load_raw() -> pd.DataFrame | None:
    path = find_data()
    return pd.read_csv(path, dtype=str, keep_default_na=False) if path else None


def replay_counts(df: pd.DataFrame) -> dict:
    """Run every raw row through the same request model and scorer the API uses."""
    counts = {"low_risk": 0, "high_risk": 0, "rejected (422)": 0}
    for r in df.itertuples():
        try:
            request = ScoreRequest(application_id=r.application_id, employees=r.employees,
                                   annual_revenue=r.annual_revenue, requested_amount=r.requested_amount,
                                   source_system=r.source_system or None)
            counts[score_application(request, ACTIVE).decision] += 1
        except Exception:
            counts["rejected (422)"] += 1
    return counts


def schema_table() -> pd.DataFrame:
    schema = ScoreRequest.model_json_schema()
    rows = []
    for name, prop in schema["properties"].items():
        kind = prop.get("type") or " | ".join(o.get("type", "?") for o in prop.get("anyOf", []))
        rules = [f"{k} {v}" for k, v in prop.items() if k in ("exclusiveMinimum", "minLength", "pattern")]
        rows.append({"field": name, "type": kind, "required": "yes" if name in schema["required"] else "no",
                     "validation": ", ".join(rules) or "-"})
    return pd.DataFrame(rows)


st.title("Working Capital Risk Scoring")
st.caption("A rules-based risk score turned into a validated, stored, observable service. "
           "Live sections run the real FastAPI app in this process; recorded sections are marked.")
if REPO_URL:
    st.sidebar.link_button("Source repository", REPO_URL)
st.sidebar.markdown(
    "**Live here:** FastAPI, Pydantic, SQLAlchemy, canary and shadow rulesets, Prometheus metrics.\n\n"
    "**Recorded from the real stack:** Postgres, Prometheus, Alertmanager.\n\n"
    "**Repo only:** Terraform, CI, Caddy VPS compose."
)
if st.sidebar.button("Reset live database"):
    get_client.clear()
    st.rerun()

tab_brief, tab_data, tab_api, tab_ops = st.tabs(["Answers to the brief", "Data", "Try the API", "Rollout & monitoring"])

# ---------------------------------------------------------------- Answers to the brief
with tab_brief:
    st.markdown(
        """
| The brief asks | Short answer | Where |
|---|---|---|
| **Look at the data** | 196 rows; 4 IDs repeated as exact duplicates; 22 missing employee counts and 3 zero or negative; half the EBITDA missing; mixed date formats and label spellings. Cleaned only where unambiguous, nothing imputed. | *Data* tab |
| **How to store it** | Postgres: `applications` plus an append-only `score_history` (inputs, decision, factors, ruleset and image version, request ID). Internal ID with the source ID kept non-unique. Raw CSVs immutable in S3. Alembic migrations. | DESIGN.md |
| **Serve the prototype over HTTP** | FastAPI `POST /score`, Pydantic validation, bad data gets a 422 and is never imputed, score persisted before returning, API key, Dockerfile, pytest. | *Try the API* tab |
| **Version and roll out safely** | Named rulesets, replay and diff, shadow scoring, percentage canary, rollback by changing `ACTIVE_RULESET` (no rebuild). | *Rollout & monitoring* |
| **Know it is misbehaving without outcomes** | Eight Prometheus alerts on errors, unrecorded scores, input and high-risk-share drift, and shadow disagreement. These catch faults and drift, not predictive quality. | *Rollout & monitoring* |
| **Debuggable weeks later** | JSON logs with metadata only (never raw values or `tax_id`); every score row can be replayed from its stored inputs and ruleset. | DESIGN.md |
| **When Data Science sends a new prototype** | Only the ruleset changes. HTTP contract, storage and request boundary stay; new version goes replay, shadow, canary, promote. | *Rollout & monitoring* |
"""
    )
    st.caption("Left out and next steps (real alert targets, tuned thresholds, ingest-to-Postgres load, AWS apply) are listed in DESIGN.md.")
    with st.expander("DESIGN.md (architecture, trade-offs, operations, left out)"):
        st.markdown(plain_links(read_text("DESIGN.md")))
    with st.expander("Full coverage checklist with status per item"):
        st.markdown(plain_links(read_text("BRIEF_COVERAGE.md")))
    with st.expander("Open questions for the data owner"):
        st.markdown(plain_links(read_text("OPEN_QUESTIONS.md")))
    with st.expander("Narrated demo video"):
        video = find("demo.mp4")
        if video:
            st.video(str(video))
        else:
            st.info("demo.mp4 is not bundled in this deployment.")

# ---------------------------------------------------------------- Data
with tab_data:
    raw = load_raw()
    if raw is None:
        st.info("data/raw_applications.csv is not bundled in this deployment.")
    else:
        MISSING = {"", "null", "na", "n/a"}
        missing = lambda col: int(raw[col].str.strip().str.lower().isin(MISSING).sum())
        employees = pd.to_numeric(raw["employees"], errors="coerce")
        dup_rows = int(raw.duplicated().sum())
        slash_dates = int(raw["incorporation_date"].str.contains("/").sum())
        st.subheader("What is in data/raw_applications.csv")
        st.caption("Computed live from the file in the repo.")
        a, b, c, d = st.columns(4)
        a.metric("Rows", len(raw))
        b.metric("Unique application IDs", raw["application_id"].nunique(), f"{dup_rows} exact duplicate rows", delta_color="off")
        c.metric("Missing employees", missing("employees"), f"{int((employees <= 0).sum())} zero or negative", delta_color="off")
        d.metric("Missing EBITDA", missing("annual_ebitda"), f"{missing('annual_ebitda') / len(raw):.0%} of rows", delta_color="off")

        st.markdown("**Findings and what I did about them**")
        st.dataframe(pd.DataFrame([
            ("Repeated application IDs", f"{dup_rows} rows, identical in every column, different source systems", "Exact duplicates dropped in cleaning; the API keeps source IDs non-unique", "Ask: is the ID unique? can a revision reuse it?"),
            ("Employees missing or <= 0", f"{missing('employees')} missing, {int((employees <= 0).sum())} zero or negative", "Rejected with a 422, never imputed (employees is the scoring denominator)", "Why is it missing; which source?"),
            ("EBITDA mostly empty", f"{missing('annual_ebitda')} rows", "Not used by the score, kept null", "Will it be provided? It could enable better rules"),
            ("Incorporation date", f"{missing('incorporation_date')} missing; {slash_dates} in dd/mm/yyyy, the rest ISO", "Normalised to ISO (day-first confirmed by the value ranges)", "Confirm the format, fix at source"),
            ("Sector labels", f"{raw['sector'].str.strip().nunique()} spellings for 5 sectors", "Mapped to canonical labels; unknown ones flagged", "Agree a controlled vocabulary"),
            ("Region labels", "Cataluna and Catalunya for the same region", "Merged", "Same"),
            ("Implausible values", "e.g. 19 employees on 3,225 revenue", "Flagged, not changed", "Confirm units and currency"),
            ("Tax ID punctuation and null tokens", "'null', 'N/A', spaces and dashes", "Normalised; tax ID kept out of logs and responses", "Standard format and null encoding"),
        ], columns=["Issue", "What I found", "What the pipeline does", "Push back on the source"]),
            width="stretch", hide_index=True)

        st.markdown("**Replaying all 196 raw rows through the same validation and scorer the API uses**")
        counts = replay_counts(raw)
        cols = st.columns(3)
        for col, (label, n) in zip(cols, counts.items()):
            col.metric(label, n)
        st.caption("The rejected rows are the ones with missing or non-positive employees, plus rows the request model refuses. "
                   "The full profile is in analysis/data_exploration.ipynb.")
        with st.expander("Raw sector and region spellings"):
            x, y = st.columns(2)
            x.dataframe(raw["sector"].str.strip().value_counts().rename("rows"), width="stretch")
            y.dataframe(raw["region"].str.strip().value_counts().rename("rows"), width="stretch")

# ---------------------------------------------------------------- Try the API
with tab_api:
    st.markdown(
        """
<style>
.op{display:flex;align-items:center;gap:12px;border:1px solid #49cc90;background:rgba(73,204,144,.1);
border-radius:4px;padding:8px 12px;margin:4px 0 12px}
.op .verb{background:#49cc90;color:#fff;font-weight:700;border-radius:3px;padding:4px 14px;font-size:14px}
.op .path{font-family:monospace;font-weight:700;font-size:16px}
.op .sum{opacity:.75;font-size:14px}
</style>
<div class="op"><span class="verb">POST</span><span class="path">/score</span><span class="sum">Score an application</span></div>
""",
        unsafe_allow_html=True,
    )
    st.caption("Calls go to the real FastAPI app running inside this Streamlit process: auth, validation, scoring, persistence, metrics.")

    left, right = st.columns([3, 2])
    with left:
        api_key = st.text_input("X-API-Key (header, required)", value=KEY, type="password",
                                help="Same idea as Swagger's Authorize button. Try an empty or wrong key to see the 401.")
        presets = {
            "Healthy": {"application_id": "DEMO-1001", "employees": 10, "annual_revenue": 1000000, "requested_amount": 100000, "source_system": "manual_entry"},
            "Thin revenue per employee": {"application_id": "DEMO-1002", "employees": 100, "annual_revenue": 1000000, "requested_amount": 100000, "source_system": "legacy_crm"},
            "Over-asking": {"application_id": "DEMO-1003", "employees": 10, "annual_revenue": 1000000, "requested_amount": 500000, "source_system": "new_partner_feed"},
            "Both rules trip": {"application_id": "DEMO-1004", "employees": 100, "annual_revenue": 1000000, "requested_amount": 500000, "source_system": "manual_entry"},
            "Invalid: zero employees (422)": {"application_id": "DEMO-1005", "employees": 0, "annual_revenue": 1000000, "requested_amount": 100000},
            "Invalid: extra field (422)": {"application_id": "DEMO-1006", "employees": 10, "annual_revenue": 1000000, "requested_amount": 100000, "tax_id": "H12345678"},
        }
        example = st.selectbox("Examples", list(presets))
        body_text = st.text_area("Request body (application/json)", json.dumps(presets[example], indent=2),
                                 height=230, key=f"body-{example}")
        execute = st.button("Execute", type="primary")
    with right:
        st.markdown("**Request body schema**")
        st.dataframe(schema_table(), width="stretch", hide_index=True)
        st.markdown("**Responses**")
        st.dataframe(pd.DataFrame([
            ("200", "Scored and stored: decision, risk_factors, ruleset_version, request_id"),
            ("401", "Missing or invalid X-API-Key"),
            ("422", "Validation failed; nothing stored, nothing imputed"),
            ("503", "Score could not be recorded; no decision is returned"),
        ], columns=["code", "meaning"]), width="stretch", hide_index=True)

    if execute:
        st.markdown("**Server response**")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            st.error(f"Request body is not valid JSON: {exc}")
        else:
            response = client.post("/score", json=payload, headers={"X-API-Key": api_key})
            compact = json.dumps(payload).replace("'", "'\\''")
            st.markdown("Curl")
            st.code(f"curl -X POST 'https://<your-host>/score' \\\n  -H 'X-API-Key: <key>' \\\n  -H 'Content-Type: application/json' \\\n  -d '{compact}'", language="bash")
            c1, c2 = st.columns([1, 4])
            c1.metric("Code", response.status_code)
            c2.json(response.json())
            if response.is_success:
                out = response.json()
                (st.error if out["decision"] == "high_risk" else st.success)(
                    f"{out['decision']}  ·  factors: {', '.join(out['risk_factors']) or 'none'}  ·  ruleset {out['ruleset_version']}")
            else:
                st.warning("Rejected: nothing was stored and no value was guessed.")
            with st.expander("Response headers"):
                st.json({k: v for k, v in response.headers.items() if k.lower() in ("content-type", "x-request-id")})

    with st.expander("GET /healthz and the OpenAPI document"):
        st.code(f"GET /healthz -> {client.get('/healthz').json()}", language="text")
        st.json(client.get("/openapi.json").json(), expanded=False)

    st.markdown("**Stored scores** (append-only `score_history` joined to `applications`, newest first)")
    rows = overview()["recent"]
    if rows:
        st.dataframe(pd.DataFrame(rows).drop(columns=["image_version"]), width="stretch", hide_index=True)
    else:
        st.info("Nothing scored yet. Press Execute above.")
    with st.expander("Live Prometheus metrics (`GET /metrics`)"):
        text = client.get("/metrics", headers=HEADERS).text
        st.code("\n".join(l for l in text.splitlines() if l.startswith(("score_", "shadow_", "http_requests_total"))), language="text")

# ---------------------------------------------------------------- Rollout & monitoring
with tab_ops:
    st.subheader("1. Roll out a new ruleset safely (live)")
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

    st.divider()
    st.subheader("2. Detect bad behaviour without repayment outcomes (recorded)")
    snap = snapshot()
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
            st.caption("Alertmanager had an active alert when recorded: earlier test traffic skewed the high-risk share "
                       "against the 24h baseline, so the drift alert fired. That is the detection working.")
        with st.expander("Rows read back from Postgres, and the canary split"):
            st.dataframe(pd.DataFrame(ov["recent"]).drop(columns=["image_version"]), width="stretch", hide_index=True)
            st.dataframe(pd.DataFrame(ov["by_ruleset"]), width="stretch", hide_index=True)
        with st.expander("Metric samples scraped from /metrics"):
            st.code("\n".join(snap["metric_samples"]), language="text")

    st.subheader("3. What is logged")
    st.markdown(
        "- **JSON logs, metadata only:** request ID, status, latency, decision, risk factors. Never raw values or `tax_id`.\n"
        "- **`score_history` (append-only):** validated inputs, decision, factors, ruleset version, image version, request ID, timestamp, "
        "so any decision can be replayed weeks later.\n"
        "- **Metrics** carry the ruleset version and a bounded `source_system` label."
    )
    st.info("Alert delivery to Discord is deferred on purpose; the webhook is a placeholder. For production I would page on-call via "
            "PagerDuty/Opsgenie, send tickets to chat, add a dead-man's-switch heartbeat, fire a test alert on every deploy and keep "
            "the webhook in a secrets manager (see DESIGN.md, Monitoring).")
