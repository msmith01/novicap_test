"""Record the short, captioned product tour (streamlit_app/evidence/demo.mp4 and demo.mp4 at the repo root).

Starts the real FastAPI service on localhost, drives its /docs page and operator console in headless
Chromium, and uses rows from data/raw_applications.csv for every request. Nothing is mocked and no
terminal output appears in the video. The replay figures on the closing card are the HTTP status codes
and decisions returned by the service for all 196 rows, not typed-in numbers.

    python -m pip install playwright imageio-ffmpeg httpx pandas && python -m playwright install chromium
    python scripts/record_demo.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import imageio_ffmpeg
import pandas as pd
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
KEY = "demo-key"
SIZE = {"width": 1280, "height": 720}
RAW = pd.read_csv(ROOT / "data" / "raw_applications.csv", dtype=str, keep_default_na=False)

# One real row for each outcome the tour shows.
LOW, HIGH, MISSING = "RAW-0073", "RAW-0043", "RAW-0156"

CAPTION_JS = """(text) => {
  let el = document.getElementById('tour-caption');
  if (!el) {
    el = document.createElement('div'); el.id = 'tour-caption';
    el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:14px 28px;' +
      'background:rgba(15,23,42,.92);color:#fff;font:600 22px/1.35 system-ui,Segoe UI,sans-serif;text-align:center';
    document.body.appendChild(el);
  }
  el.textContent = text;
}"""

CARD = """<!doctype html><meta charset=utf-8><style>
body{{margin:0;height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center;
background:#0f172a;color:#fff;font-family:system-ui,Segoe UI,sans-serif;text-align:center}}
h1{{font-size:44px;margin:0 0 14px}}p{{font-size:24px;margin:6px 40px;color:#cbd5e1}}
.row{{display:flex;gap:28px;margin:28px 0 8px}}.k{{background:#1e293b;border-radius:14px;padding:22px 36px}}
.k b{{display:block;font-size:56px}}.k span{{font-size:20px;color:#94a3b8}}
.low b{{color:#5fd39a}}.high b{{color:#ff8b82}}.rej b{{color:#f0b35a}}</style>{body}"""


def row_body(app_id: str) -> dict:
    r = RAW[RAW.application_id == app_id].iloc[0]
    return {
        "application_id": r.application_id,
        "employees": int(r.employees) if r.employees.strip() else None,
        "annual_revenue": float(r.annual_revenue),
        "requested_amount": float(r.requested_amount),
        "source_system": r.source_system,
    }


def start_server(db: Path) -> subprocess.Popen:
    env = {**os.environ, "API_KEYS": KEY, "DATABASE_URL": f"sqlite:///{db.as_posix()}",
           "ACTIVE_RULESET": "2026-10-v1", "SHADOW_RULESET": "2026-11-v2-candidate",
           "DEMO_VIDEO_PATH": str(db) + ".none"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "prototype.risk_scoring_prototype:app",
                             "--port", str(PORT), "--log-level", "error"], cwd=ROOT, env=env, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(f"{BASE}/healthz", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("API did not start")


def replay_over_http() -> dict:
    """Send every raw row to the running service exactly as exported (strings included)."""
    counts = {"low_risk": 0, "high_risk": 0, "rejected": 0}
    with httpx.Client(base_url=BASE, headers={"X-API-Key": KEY}) as c:
        for r in RAW.itertuples():
            resp = c.post("/score", json={
                "application_id": r.application_id, "employees": r.employees,
                "annual_revenue": r.annual_revenue, "requested_amount": r.requested_amount,
                "source_system": r.source_system or None})
            counts["rejected" if resp.status_code == 422 else resp.json()["decision"]] += 1
    return counts


def caption(page, text: str, hold: float) -> None:
    page.evaluate(CAPTION_JS, text)
    page.wait_for_timeout(int(hold * 1000))


def card(page, body: str, text: str, hold: float) -> None:
    page.set_content(CARD.format(body=body))
    caption(page, text, hold)


def swagger_score(page, body: dict, text: str, hold: float) -> None:
    caption(page, text, 0)
    op = page.locator("#operations-default-score_score_post")
    if "is-open" not in (op.get_attribute("class") or ""):
        op.locator(".opblock-summary-control").click()
        page.locator("button.try-out__btn").click()
        page.locator('input[placeholder="x-api-key"]').fill(KEY)
    area = page.locator("textarea.body-param__text")
    area.fill("")
    area.press_sequentially(json.dumps(body, indent=2), delay=4)
    page.locator("button.execute").click()
    page.locator(".live-responses-table td.response-col_status").last.wait_for()
    page.evaluate("document.querySelector('.live-responses-table').scrollIntoView({block: 'start'})")
    page.wait_for_timeout(int(hold * 1000))


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="tour-"))
    videos = work / "video"
    server = start_server(work / "tour1.db")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome")
            ctx = browser.new_context(viewport=SIZE, record_video_dir=str(videos), record_video_size=SIZE)
            page = ctx.new_page()
            page.goto(f"{BASE}/healthz")  # recording only starts on a first navigation
            page.wait_for_timeout(300)

            card(page, "<h1>Working Capital Risk Scoring</h1><p>A FastAPI service, shown with rows from "
                       "<b>data/raw_applications.csv</b></p>",
                 "The tour: score, reject bad data, store, roll out safely", 3)

            page.goto(f"{BASE}/docs")
            page.locator("#operations-default-score_score_post").wait_for()
            page.evaluate("document.body.style.zoom = '1.0'")
            caption(page, "POST /score sits behind an API key; GET /healthz is open", 3)
            swagger_score(page, row_body(LOW), f"{LOW} (10 employees, 1.44M revenue): low_risk, no factors", 5)
            swagger_score(page, row_body(HIGH), f"{HIGH} (19 employees, 3.2K revenue): high_risk, both rules triggered", 5)
            swagger_score(page, row_body(MISSING),
                          f"{MISSING} has no employee count: 422, never imputed, submitted values not echoed", 5)

            # Fresh database for the console scene so its totals equal the file's replay exactly.
            server.terminate(); server.wait()
            server = start_server(work / "tour2.db")
            counts = replay_over_http()

            page.goto(f"{BASE}/#key={KEY}")
            page.locator("#rolloutStats .stat").first.wait_for()
            page.wait_for_timeout(600)
            caption(page, f"Every decision is stored: the console reads {counts['low_risk'] + counts['high_risk']} scored rows back from the database", 4)
            page.locator("#recent").scroll_into_view_if_needed()
            caption(page, "Each row keeps its ruleset and image version, so a decision can be replayed later", 4)
            page.evaluate("window.scrollTo(0, 0)")
            caption(page, "A candidate ruleset runs in shadow mode first; rollback is one config change", 4)

            card(page,
                 "<h1>All 196 rows of the export, through the live API</h1>"
                 f"<div class=row><div class='k low'><b>{counts['low_risk']}</b><span>low risk</span></div>"
                 f"<div class='k high'><b>{counts['high_risk']}</b><span>high risk</span></div>"
                 f"<div class='k rej'><b>{counts['rejected']}</b><span>rejected (422)</span></div></div>"
                 "<p>Rejected rows have a missing or non-positive employee count. Nothing is imputed.</p>",
                 "The same app is running live below: try it, and watch it connect to the API", 5)
            page.close()
            video_path = Path(page.video.path())
            ctx.close()
            browser.close()
        if sum(counts.values()) != len(RAW):
            raise RuntimeError(f"unexpected replay counts {counts}")
    finally:
        server.terminate()

    out = ROOT / "streamlit_app" / "evidence" / "demo.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(video_path), "-an", "-vf", "fps=25,format=yuv420p",
                    "-c:v", "libx264", "-crf", "30", "-preset", "slow", "-movflags", "+faststart", str(out)],
                   check=True, capture_output=True)
    shutil.copyfile(out, ROOT / "demo.mp4")
    shutil.rmtree(work, ignore_errors=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB); replay {counts}")


if __name__ == "__main__":
    main()
