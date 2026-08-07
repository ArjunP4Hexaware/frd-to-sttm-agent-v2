# review_app_react — gated-ambiguity review + client-facing demo

FastAPI backend (`backend/`) + Vite/React frontend (`frontend/`), deployable
as a Databricks App (`app.yaml`; the root `requirements.txt` is what Apps
installs). Three feature areas, three tabs:

1. **Existing documents** — review gated ambiguities on promoted contracts,
   resolve candidates, download rendered STTM workbooks.
2. **New upload** — the original mock-pipeline upload flow
   (`backend/orchestration.py`): mock extraction only, demo-fixture-scoped.
   Unchanged; still the default path for tests.
3. **Client demo** (`backend/demo.py`) — the demo story:
   - **Live mode**: pick the preloaded `demo_frd.docx` or upload a .docx
     (gitignored `local_dev_fixtures/demo_uploads/`; prototype — synthetic
     or anonymized documents only) → cost-confirmation dialog (~1 billed
     call, ~$0.15, ~35s) → the real 01→04 pipeline with provider
     `anthropic` → results view. Progress streams over SSE with a polling
     fallback; the full console log persists to `data/live_run_logs/`
     (gitignored).
   - **Replay mode**: pick any saved artifact set → identical results view,
     zero API calls.
   - **Results view** (both modes): extraction summary → the stage-03
     ambiguity gate ("N detected, M auto-confirmed against the data
     dictionary, K awaiting human review") → stage-04 verdict tile →
     eval-vs-golden panel (94.1% on the golden pair; uploads show "no golden
     reference") → per-mapping tables + workbook download.

## Guardrails (live mode)

- The run suffix is backend-generated (`demo_<YYYYmmdd_HHMMSS>`, uniquified),
  never user-supplied. Every output knob embeds it
  (`SCHEMA`/`OUT_VOLUME`/`PREVIEW_VOLUME`/`RAW_VOLUME`), so curated
  baselines (`local_dev_fixtures/sttm_out/`, `fixtures/`, `contracts/`) are
  unreachable from any UI-triggered write path.
- One live run at a time (409 otherwise). Run state is in-memory; artifacts
  and the console log survive a restart (the finished run is then reachable
  via Replay).
- `ANTHROPIC_API_KEY`: presence boolean to the frontend only; sourced from
  the environment or the repo `.env` locally (Databricks: the secret-scope
  path in `notebooks/02_extract.py` remains the route). Never sent to the
  frontend, never logged.
- Uploaded FRDs run through the SAME live pipeline as the preloaded choice —
  no silent mock substitution. Mock stays exclusively on the "New upload"
  tab.

## Replay set

`local_dev_fixtures/sttm_out_live_e2e_20260807b/` is tracked deliberately
(the post-fix live E2E run — see `docs/LIVE_E2E_2026-08-07.md`), so a fresh
clone can replay the demo offline. Future `sttm_out_demo_*` /
`sttm_out_live_e2e_*` sets stay untracked by default; discovery scans for
both families at runtime.

## Local startup

```bash
# backend (from repo root; .venv has fastapi/uvicorn via `pip install -e ".[ui,local,dev]"`)
cd review_app_react/backend
../../.venv/bin/uvicorn app:app --reload --port 8000

# frontend dev server (proxies /api to :8000)
cd review_app_react/frontend
npm install && npm run dev

# OR: single-process serving the built bundle
cd review_app_react/frontend && npm run build
cd ../backend && DATABRICKS_APP_PORT=8020 ../../.venv/bin/python app.py
```

Live mode needs `ANTHROPIC_API_KEY` in the environment or the repo `.env`.
Replay mode needs neither.

## Databricks Apps deployment — known gaps (flagged, not solved)

- The live-run path executes pipeline stages as local subprocesses
  (`python notebooks/0N_*.py`) writing under `local_dev_fixtures/`. In an
  Apps container that works only against ephemeral local disk; a real
  deployment should trigger the bundle job (`frd_sttm_pipeline`) with
  widget parameters and read outputs from UC volumes (data_access.py
  already has a `databricks` mode for contracts; demo.py's artifact scan is
  local-mode only today).
- The API key must reach the app via an Apps secret/env resource — the
  repo-`.env` fallback is local-only.
- SSE through the Apps proxy is expected to work but has not been exercised
  against a real workspace (same status as the rest of this app's deploy).
