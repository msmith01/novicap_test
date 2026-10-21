# Progress log

Newest entry first. One entry per working message: what was done, what was verified, what is next.

## Step 14 — 2026-10-21: leaner Streamlit app (7 tabs -> 4)

**Done**
- Rewrote `streamlit_app/app.py`: tabs are now Answers to the brief, Data, Try the API, Rollout & monitoring.
  - Answers to the brief: one table mapping each brief bullet to a short answer; DESIGN.md, coverage checklist, open questions and demo video are expanders.
  - Data (new, live from `data/raw_applications.csv`): metrics, findings/what-the-pipeline-does/push-back table, replay of all 196 rows (158 low, 13 high, 25 rejected, matches the earlier replay).
  - Try the API: FastAPI-docs layout (POST badge, X-API-Key, Examples dropdown, editable JSON body, Execute, curl, server response, schema and responses tables, OpenAPI expander); kept the stored-scores table and metrics at the bottom.
  - Rollout & monitoring: merged Rollout lab and Recorded stack evidence, plus a short "what is logged".
  - Removed the Overview, Demo video, Design and Tools tabs (content folded in or dropped).
- Verified with `streamlit.testing.AppTest`: no exceptions, 4 tabs, Execute returns 200 for the healthy example and 422 for zero employees.

**Next:** commit and push (Streamlit Cloud redeploys from `main`), then user checks the live app.

## Step 13 — 2026-10-21: app deployed, private by default

- User clicked Deploy before setting the Python version (fixable later in app Settings → Advanced).
- `https://novicaptest-hjntij9h9rqqzmjwqplp8h.streamlit.app/` exists and redirects to the Streamlit sign-in page (private by default), so I cannot load it to verify the tabs. Next: user confirms it runs and invites the interviewer by email, or makes it public.

## Step 12 — 2026-10-21: Streamlit deploy form filled

- User filled the Community Cloud form: `msmith01/novicap_test`, `main`, `streamlit_app/app.py`, URL `novicaptest-hjntij9h9rqqzmjwqplp8h.streamlit.app`. Told them to pick Python 3.12/3.13 under Advanced settings and click Deploy. Waiting for the live URL/result.

## Step 11 — 2026-10-21: pushed to GitHub

- Push to private `msmith01/novicap_test` succeeded (`main`, new branch). Token was passed per-command via http.extraheader, not stored in git config.
- Next: user creates the app at share.streamlit.io (repo `msmith01/novicap_test`, branch `main`, file `streamlit_app/app.py`); then verify the live app. The PROGRESS.md edit for this step is uncommitted.

## Step 10 — 2026-10-21: token page confirms the cause

- User pasted the token's settings page: repo access is `msmith01/novicap_test` (correct), but "code" (= Contents) is Read-only. Only workflows, repository creation, codespaces secrets and GitHub App access are Read and write. Asked user to set Contents to Read and write. Nothing pushed.

## Step 9 — 2026-10-21: token can now see the repo but not write

- Retried: API now returns the repo (private: true), so repo access was granted. Push still 403 "Write access to repository not granted": the token's *Contents* permission is read-only or unset. Nothing pushed.
- Needed from user: Contents → Read and write on the token (or push from own terminal).

## Step 8 — 2026-10-21: repo private, token still can't reach it

**Done**
- User made `msmith01/novicap_test` private. Rechecked: API now returns 404 for it with our token, and `/user/repos` lists only public repos, so the fine-grained token has not been granted this repo. Push still 403 ("Write access to repository not granted"). Nothing pushed.

**Needed from user:** edit the token (github.com/settings/personal-access-tokens) → Repository access → select `novicap_test` → Contents: read and write. Or push from own terminal.

## Step 7 — 2026-10-21: pivot to Streamlit Community Cloud; GitHub push blocked

**Done**
- README now records that Hugging Face blocked free Gradio/Docker Spaces (402) and that the live host is Streamlit Community Cloud, with the trade-off (free but sleeps; production would be VPS/AWS).
- `app.py` already supports Cloud (path fallback to the repo root), main file `streamlit_app/app.py`, requirements in `streamlit_app/requirements.txt`.
- Committed locally (docs, build_space.py Docker output, PROGRESS.md).
- Found `msmith01/novicap_test` is PUBLIC and empty. Tried to set it private and push: both denied (403, "Resource not accessible by personal access token"). Nothing was pushed, so nothing is exposed.

**Needed from user:** GitHub token with Contents (read/write) and Administration (write) on that repo, or make it private by hand in repo Settings; then sign in at share.streamlit.io with GitHub.

**Next:** push, deploy on Community Cloud, set sharing to private/viewer list, test.

## Step 6 — 2026-10-21: Space upload attempted; blocked by Hugging Face plan

**Done**
- `.env` now holds HF_TOKEN, HF_SPACE and GH_TOKEN (git-ignored). Nothing sensitive in docs or code.
- Deferred Discord alerting documented as "what I would implement" in DESIGN.md (Monitoring), README resume checklist, BRIEF_COVERAGE.md and the Alertmanager note in `streamlit_app/app.py`.
- Kept Streamlit (already built). HF no longer allows `sdk: streamlit` for new Spaces, so `scripts/build_space.py` now emits a Docker Space (Dockerfile + `sdk: docker`, port 7860).
- Verified locally: app boots under `streamlit.testing.AppTest` with no exceptions; health endpoint ok.
- Uploaded `dist/space` to `mattonline/Novicap` (private). Result: CONFIG_ERROR "ZeroGPU is only available on Gradio SDK"; the Space is on ZeroGPU hardware and downgrading to cpu-basic returns 402 (needs PRO).
- Creating a new Docker Space also returns 402: free accounts can only host Static Spaces; Gradio/Docker on cpu-basic need PRO.

**Blocked:** Space can't run Python on this free account. Options: HF PRO (~$9/mo), static Space (HTML/recorded evidence only), or self-host on the VPS.

## Step 5 — 2026-10-21: HF token stored, Space identified

**Done**
- Space is `mattonline/Novicap` (https://huggingface.co/spaces/mattonline/Novicap). It looks like a fresh, empty Gradio-template Space, so nothing of ours is uploaded yet.
- Wrote `HF_TOKEN` and `HF_SPACE` to `.env` (confirmed git-ignored by `.gitignore:6`). Token was not echoed or committed.
- GitHub token was also supplied; NOT stored. Will use it only for `gh auth`/push after the user confirms.

**Needed from user:** OK to upload `dist/space` (switches Space SDK from gradio to streamlit; publishes demo.mp4 + DESIGN.md); public vs private; GitHub private repo name.

**Next:** rebuild `dist/space` without `__pycache__`, test locally, upload with the token, check the Space builds.

## Step 4 — 2026-10-21: checked whether a Space was already uploaded

**Done**
- User believes a first draft is already on Hugging Face. Looked for evidence on this machine: no huggingface.co / hf.space / HF_TOKEN reference in the repo, no HF token cache, no `huggingface-cli`/`hf` on PATH, and the only git remote is GitHub `origin` (msmith01/novicap_test).
- `dist/space` exists locally (app.py, README.md, requirements.txt, evidence/, prototype/), so a built package is ready to upload. It also contains `__pycache__` dirs that should not be uploaded.

**Needed from user:** the Space URL if one exists (it may have been uploaded via the web UI or another session), otherwise an HF username and a write token.

**Next:** verify the live Space against `dist/space`, or create and upload it.

## Step 3 — 2026-10-21: summarised the work for the user

**Done**
- Read memory and this log, then gave the user a recap of the project and the Streamlit "production" question. No code changes.

**Next:** unchanged. The hosting decision is still open, then run the app locally and rebuild `dist/space`.

## Step 2 — 2026-10-21: clarified what the deliverable is

**Done**
- Read the "Deliverables" table in BRIEF_COVERAGE.md: the brief asks for working code, a Dockerfile, DESIGN.md and a "left out / next steps" section. All exist.
- The Streamlit/Hugging Face Space is an extra, not a brief deliverable: a viewable showcase for the interviewer.
- `scripts/build_space.py` builds `dist/space` (Streamlit SDK Space) containing the scorer, recorded stack evidence and docs.

**Decision needed from user:** hosting model for the showcase (HF Space vs. self-hosted vs. repo-only). See chat.

**Next:** run app locally (old Step 2), then act on the hosting decision.

## Step 1 — 2026-10-21: recovered state after reconnect

**Done**
- Reconstructed state from memory, `git log` and the README. Working tree was clean at the start, so nothing was lost uncommitted.
- Last commit `effe21b` already contains the Streamlit showcase: `streamlit_app/app.py` (271 lines), `streamlit_app/requirements.txt`, `streamlit_app/evidence/stack_snapshot.json`, `scripts/capture_evidence.py`, `scripts/build_space.py` (builds `dist/space` for a Hugging Face Space), plus `BRIEF_COVERAGE.md`.
- Created this log.

**Not yet verified**
- Whether the Streamlit app runs locally, and whether `dist/space` is current.
- Hugging Face Space has not been deployed (no evidence of it).

**Next**
1. Run the Streamlit app locally and check every page.
2. Rebuild `dist/space` and test it.
3. Deploy to a Hugging Face Space (needs your HF account/token — will ask first).

**Still blocked on you (unchanged):** `gh auth login` and push; Discord webhook; optional ElevenLabs/VPS/data-owner answers.
