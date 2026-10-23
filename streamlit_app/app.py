"""Five-minute demo of the working-capital risk scoring project: one tab per point in the brief.

The scoring API, rulesets, canary/shadow logic, persistence and Prometheus metrics run live: the real
FastAPI app is served over HTTP by uvicorn on a localhost port inside this process (SQLite in memory) and
every call below goes through that socket with httpx, so the page can show that it is connected. Set
API_BASE_URL (and API_KEY) to point the same page at a deployed API instead. Postgres, Prometheus and
Alertmanager cannot run on a free host, so the monitoring section shows a timestamped snapshot taken from
the real docker compose stack by scripts/capture_evidence.py.
"""

import json
import os
import random
import socket
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")  # the module-level app in the scorer must not write a file

HERE = Path(__file__).resolve().parent
for root in (HERE, HERE.parent):  # repo checkout (Streamlit Cloud) or assembled Space
    if (root / "prototype").is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))

import httpx
import pandas as pd
import streamlit as st
import uvicorn

from prototype.risk_scoring_prototype import ScoreRequest, create_app, score_application
from prototype.storage import Base, make_session_factory

KEY = os.environ.get("API_KEY", "showcase")
HEADERS = {"X-API-Key": KEY}
EXTERNAL_API = os.environ.get("API_BASE_URL", "").rstrip("/")
ACTIVE, CANDIDATE = "2026-10-v1", "2026-11-v2-candidate"
REPO_URL = os.environ.get("REPO_URL", "https://github.com/msmith01/novicap_test")

st.set_page_config(page_title="Working Capital Risk Scoring", page_icon="📊", layout="wide")


def find(name: str) -> Path | None:
    for base in (HERE / "evidence", HERE, HERE.parent):
        if (base / name).is_file():
            return base / name
    return None


class EmbeddedApi:
    """The real FastAPI app, served by uvicorn on a free localhost port in a background thread."""

    def __init__(self) -> None:
        with socket.socket() as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            self.port = probe_socket.getsockname()[1]
        app = create_app(
            session_factory=make_session_factory("sqlite://"),
            api_keys=[KEY],
            image_version="streamlit-showcase",
            active_ruleset=ACTIVE,
            canary_ruleset=CANDIDATE,
            canary_percent=50,
            shadow_ruleset=CANDIDATE,
        )
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):  # up to 10 s for the socket to open
            if self.server.started:
                break
            time.sleep(0.1)
        self.base_url = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


@st.cache_resource
def get_api() -> EmbeddedApi | None:
    return None if EXTERNAL_API else EmbeddedApi()


@st.cache_resource
def get_client() -> httpx.Client:
    api = get_api()
    return httpx.Client(base_url=EXTERNAL_API or api.base_url, timeout=10)


def reset_api() -> None:
    if (api := get_api()) is not None:
        api.stop()
    get_client().close()
    get_client.clear()
    get_api.clear()


client = get_client()
API_URL = str(client.base_url).rstrip("/")


def probe(label: str, method: str, path: str, expect: int, headers: dict | None = None, json_body: dict | None = None) -> dict:
    """One real HTTP round trip to the API, timed, for the connection panel."""
    started = time.perf_counter()
    try:
        response = client.request(method, path, headers=headers, json=json_body)
        got, detail = response.status_code, response
    except httpx.HTTPError as exc:
        got, detail = type(exc).__name__, None
    return {"check": label, "request": f"{method} {path}", "expected": expect, "got": got,
            "ms": round((time.perf_counter() - started) * 1000, 1), "ok": got == expect, "response": detail}


def connection_checks() -> list[dict]:
    return [
        probe("GET /healthz (open)", "GET", "/healthz", 200),
        probe("GET /openapi.json (contract)", "GET", "/openapi.json", 200),
        probe("GET /metrics, no key: refused", "GET", "/metrics", 401),
        probe("GET /metrics, with key: allowed", "GET", "/metrics", 200, HEADERS),
    ]


# Real rows from the export (request fields only; no company name or tax ID). The tour video uses the same rows.
SAMPLES = {
    "RAW-0073: low risk": {"application_id": "RAW-0073", "employees": 10, "annual_revenue": 1441971.18, "requested_amount": 175311.57, "source_system": "legacy_crm"},
    "RAW-0043: high risk, both rules": {"application_id": "RAW-0043", "employees": 19, "annual_revenue": 3225.09, "requested_amount": 280516.32, "source_system": "new_partner_feed"},
    "RAW-0156: employee count missing (422)": {"application_id": "RAW-0156", "employees": None, "annual_revenue": 4411183.42, "requested_amount": 414549.77, "source_system": "legacy_crm"},
}


def overview() -> dict:
    return client.get("/console/api/overview", headers=HEADERS).json()


@st.cache_data
def snapshot() -> dict | None:
    path = find("stack_snapshot.json")
    return json.loads(path.read_text(encoding="utf-8")) if path else None



def find_data() -> Path | None:
    """The full export when present locally, else the copy without company names and tax IDs."""
    for path in (HERE.parent / "data" / "raw_applications.csv", HERE / "evidence" / "applications_anonymised.csv",
                 HERE / "applications_anonymised.csv"):
        if path.is_file():
            return path
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


SCENARIOS = {  # label: (body, send_key, expected status)
    "Low-risk application": (SAMPLES["RAW-0073: low risk"], True, 200),
    "High-risk application": (SAMPLES["RAW-0043: high risk, both rules"], True, 200),
    "Employee count missing": (SAMPLES["RAW-0156: employee count missing (422)"], True, 422),
    "Zero employees": ({"application_id": "DEMO-1005", "employees": 0, "annual_revenue": 1000000, "requested_amount": 100000}, True, 422),
    "Unexpected field (tax_id)": ({"application_id": "DEMO-1006", "employees": 10, "annual_revenue": 1000000, "requested_amount": 100000, "tax_id": "H12345678"}, True, 422),
    "No API key": (SAMPLES["RAW-0073: low risk"], False, 401),
}


def send(body: dict, with_key: bool) -> httpx.Response:
    return client.post("/score", json=body, headers=HEADERS if with_key else {})


def replay_check(rows: list[dict]) -> pd.DataFrame:
    """Re-score each stored row from its stored inputs and ruleset: the weeks-later debugging path."""
    out = []
    for r in rows:
        again = score_application(ScoreRequest(application_id=r["application_id"], employees=r["employees"],
                                               annual_revenue=r["annual_revenue"], requested_amount=r["requested_amount"],
                                               source_system=r["source_system"]), r["ruleset_version"])
        out.append({"application_id": r["application_id"], "ruleset": r["ruleset_version"], "stored decision": r["decision"],
                    "replayed": again.decision, "match": "✅" if again.decision == r["decision"] else "❌",
                    "request_id": r["request_id"], "scored_at": r["scored_at"]})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- Header
st.title("Working Capital Risk Scoring")
checks = connection_checks()
if all(c["ok"] for c in checks):
    st.success(f"FastAPI live at `{API_URL}` · {len(checks)}/{len(checks)} checks passed")
else:
    st.error(f"API not reachable at `{API_URL}`")
st.sidebar.link_button("Source repository", REPO_URL)
if st.sidebar.button("Reset live database"):
    reset_api()
    st.rerun()
tour = find("demo.mp4")
if tour:
    with st.sidebar.expander("1-minute video tour"):
        st.video(str(tour), muted=True, loop=True)

tab_data, tab_store, tab_api, tab_roll, tab_mon = st.tabs(["1 Data", "2 Storage", "3 API", "4 Rollout", "5 Monitoring"])

# ---------------------------------------------------------------- 1 Data
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
        a, b, c, d = st.columns(4)
        a.metric("Rows", len(raw))
        b.metric("Duplicate rows", dup_rows, f"{raw['application_id'].nunique()} unique IDs", delta_color="off")
        c.metric("Employees missing or ≤ 0", missing("employees") + int((employees <= 0).sum()))
        d.metric("EBITDA missing", f"{missing('annual_ebitda') / len(raw):.0%}")

        st.dataframe(pd.DataFrame([
            ("Repeated IDs", f"{dup_rows} identical rows", "Dropped", "Is the ID unique?"),
            ("Employees missing or ≤ 0", f"{missing('employees')} missing, {int((employees <= 0).sum())} zero/negative", "422, never imputed", "Why missing, which source?"),
            ("EBITDA", f"{missing('annual_ebitda')} empty", "Unused by the score", "Will it be provided?"),
            ("Dates", f"{missing('incorporation_date')} missing, {slash_dates} dd/mm/yyyy", "Normalised to ISO", "Fix format at source"),
            ("Sector labels", f"{raw['sector'].str.strip().nunique()} spellings for 5 sectors", "Mapped", "Controlled vocabulary"),
            ("Region labels", "Cataluna / Catalunya", "Merged", "Same"),
            ("Implausible values", "19 employees, 3,225 revenue", "Flagged, unchanged", "Confirm units"),
            ("Tax ID", "'null', 'N/A', dashes", "Normalised, never logged", "One null encoding"),
        ], columns=["Issue", "Found", "Pipeline", "Push back"]), width="stretch", hide_index=True)

        st.markdown("**All rows replayed through the API's validation and scorer**")
        counts = replay_counts(raw)
        left, right = st.columns([1, 2])
        for label, n in counts.items():
            left.metric(label, n)
        right.bar_chart(pd.Series(counts, name="rows"), horizontal=True)

# ---------------------------------------------------------------- 2 Storage
with tab_store:
    st.markdown("**Schema**: `applications` 1 → many `score_history` (append-only)")
    for col, table in zip(st.columns(2), Base.metadata.sorted_tables):
        col.markdown(f"`{table.name}`")
        col.dataframe(pd.DataFrame([{"column": c.name, "type": str(c.type),
                                     "key": "PK" if c.primary_key else ("FK" if c.foreign_keys else ("unique" if c.unique else ""))}
                                    for c in table.columns]), width="stretch", hide_index=True)
    st.markdown("**Score three real rows, read them back, replay them**")
    if st.button("Score and store RAW-0073, RAW-0043, RAW-0156", type="primary"):
        codes = [send(SCENARIOS[k][0], True).status_code for k in ("Low-risk application", "High-risk application", "Employee count missing")]
        st.caption(f"HTTP status codes {codes}: the 422 stores nothing.")
    stored = [r for r in overview()["recent"] if r["application_id"].startswith("RAW-")]
    if stored:
        st.dataframe(replay_check(stored), width="stretch", hide_index=True)
    else:
        st.info("Nothing stored yet.")

# ---------------------------------------------------------------- 3 API
with tab_api:
    left, right = st.columns([2, 3])
    with left:
        label = st.selectbox("Scenario", list(SCENARIOS))
        body, with_key, expected = SCENARIOS[label]
        body_text = st.text_area("POST /score", json.dumps(body, indent=2), height=220, key=f"body-{label}")
        st.caption("X-API-Key sent" if with_key else "X-API-Key omitted")
        execute = st.button("Execute", type="primary")
    with right:
        if execute:
            try:
                response = send(json.loads(body_text), with_key)
            except json.JSONDecodeError as exc:
                st.error(f"Invalid JSON: {exc}")
            else:
                st.metric("Status", response.status_code)
                st.json(response.json())
    if st.button("Run all six scenarios"):
        results = []
        for name, (b, k, want) in SCENARIOS.items():
            started = time.perf_counter()
            r = send(b, k)
            results.append({"": "✅" if r.status_code == want else "❌", "scenario": name, "expected": want, "got": r.status_code,
                            "decision": r.json().get("decision", "") if r.is_success else "",
                            "ms": round((time.perf_counter() - started) * 1000, 1)})
        st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)

# ---------------------------------------------------------------- 4 Rollout
with tab_roll:
    st.caption(f"Active `{ACTIVE}` · candidate `{CANDIDATE}` runs in shadow on every request and as a 50% canary.")
    if st.button("Send 200 synthetic applications", type="primary"):
        rng = random.Random()
        before = overview()["shadow"]
        diffs, progress = [], st.progress(0.0)
        for i in range(200):
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
            progress.progress((i + 1) / 200)
        after = overview()["shadow"]
        st.session_state["lab"] = {"diffs": diffs, "runs": after["decisions"] - before["decisions"],
                                   "disagree": after["disagreements"] - before["disagreements"]}
    lab = st.session_state.get("lab")
    if lab:
        a, b, c = st.columns(3)
        a.metric("Shadow runs", int(lab["runs"]))
        b.metric("Decisions that would change", int(lab["disagree"]), f"{lab['disagree'] / max(lab['runs'], 1):.1%}", delta_color="off")
        split = pd.DataFrame(overview()["by_ruleset"])
        if not split.empty:
            c.bar_chart(split.pivot_table(index="ruleset", columns="decision", values="count", aggfunc="sum", fill_value=0))
        st.dataframe(pd.DataFrame(lab["diffs"]), width="stretch", hide_index=True)
    st.markdown("**When Data Science sends a new prototype**")
    st.dataframe(pd.DataFrame([
        ("Ruleset (scoring function)", "Changes: new named version"),
        ("Rollout", "Replay diff → shadow → canary → promote; rollback = set `ACTIVE_RULESET`"),
        ("HTTP contract, validation, storage", "Unchanged"),
        ("score_history", "Unchanged; every row keeps its ruleset version"),
    ], columns=["Part", "Effect"]), width="stretch", hide_index=True)

# ---------------------------------------------------------------- 5 Monitoring
with tab_mon:
    snap = snapshot()
    if snap:
        ov = snap["overview"]
        a, b, c, d = st.columns(4)
        a.metric("Scores in Postgres", ov["totals"]["scored"])
        b.metric("Postgres", ov["schema"].get("server_version") or "n/a")
        c.metric("Alembic", ov["schema"].get("revision") or "n/a")
        d.metric("Alertmanager", snap["alertmanager_status"].get("version", "n/a"))
        rules = [r for g in snap["prometheus_rules"]["groups"] for r in g["rules"]]
        st.dataframe(pd.DataFrame([{"alert": r["name"], "severity": r["labels"].get("severity"), "state": r["state"],
                                    "summary": r["annotations"].get("summary")} for r in rules]), width="stretch", hide_index=True)
        st.caption(f"Recorded {snap['captured_at']} from the docker compose stack. The firing alert is the drift alert reacting to test traffic.")
    st.markdown("**Live `/metrics`**")
    text = client.get("/metrics", headers=HEADERS).text
    st.code("\n".join(l for l in text.splitlines() if l.startswith(("score_", "shadow_", "http_requests_total"))), language="text")
