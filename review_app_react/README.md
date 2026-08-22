# review_app_react — the FRD → STTM agent's one surface

FastAPI backend (`backend/`) + Vite/React frontend (`frontend/`), deployed
as a **Databricks App**. One surface since 2026-08-21, reshaped 2026-08-22:
**Select FRD**.

## What a user does

1. **Select FRD.** The picker is the *corpus index* — what the SharePoint
   sync (`notebooks/00_sharepoint_sync.py`, scheduled job
   `frd_sttm_sharepoint_sync`, or the panel's "Sync from SharePoint now…")
   has landed in Unity Catalog and paired (exact name match first,
   deterministic similarity second).
   - **FRDs without an STTM** → `Generate STTM` → billed-run confirmation
     (~1 model call, ~$0.15) → the real 01→04 pipeline → results.
   - **FRDs already mapped** → `View STTM` (the approved workbook from the
     reference volume + its SharePoint link) → optional two-step
     *regenerate anyway* (golden-pair eval against the existing STTM, which
     is never touched).
   - `Rebuild index…` re-pairs whatever the volumes already hold, no
     SharePoint needed (unwired tenant, smoke fixtures).
2. **Results** — extraction summary → template decision → the stage-03
   ambiguity gate → **Human-in-the-loop review** (every gated question as a
   card: pick a candidate / none of these / free text; saved into the
   run's contract; `Apply resolutions & re-render` re-runs stage 04 only —
   no second model call) → verdict + accuracy-vs-reference → rendered
   mappings → `Download workbook (.xlsx)` and the hand-off note.
3. **Hand-off is the reviewer's, not the app's.** Download, make final edits
   in Excel, upload the workbook to the SharePoint STTM folder yourself.
   The app never writes to SharePoint; the next sync pulls the upload into
   `sttm_reference` and pairs it with its FRD.

## Modes (`STTM_APP_MODE`)

- `local` (default; laptops, tests): runs spawn `notebooks/01..04` as
  subprocesses with `demo_<ts>` env insulation (suffixed schema/volumes,
  provider pinned to `anthropic`, mock stripped); the sync and re-render
  run the same code in-process / as a subprocess.
- `databricks` (the deployed App): the container never runs notebooks.
  Runs trigger the bundle job `frd_sttm_pipeline` via the Jobs API
  (`backend/jobs_runner.py`); "Sync now" triggers `frd_sttm_sharepoint_sync`
  and then mirrors `frd_raw` + `sttm_reference` down; re-render triggers
  `frd_sttm_render` over the same run suffix. Artifacts land natively in
  Unity Catalog and are mirrored to the container as a rehydratable cache.

## Guardrails

- Run suffixes are backend-generated (`demo_<YYYYmmdd_HHMMSS>`, uniquified);
  every output knob embeds them, so curated volumes/tables are unreachable
  from any UI-triggered write.
- One run at a time (409); one sync at a time (409); one re-render per set
  at a time (409). Billed run, sync and re-render are each explicit clicks.
- `ANTHROPIC_API_KEY`: presence boolean to the frontend only; from the
  environment or the repo `.env` locally, the secret scope in Databricks.
  Never sent to the frontend, never logged.
- **No write path to SharePoint exists** (no publish endpoint, no upload
  method on the Graph client) — read-only by construction.
- Resolutions: structural pick required when an item has candidates
  (client and server), free text only for candidate-less items.

## Local startup

```bash
# from the repo root; .venv has fastapi/uvicorn via `pip install -e ".[ui,local,dev]"`
cd review_app_react/frontend && npm install && npm run build
cd ../backend && DATABRICKS_APP_PORT=8020 ../../.venv/bin/python app.py
# dev loop instead: `../../.venv/bin/uvicorn app:app --reload --port 8000`
#                   and `npm run dev` (proxies /api to :8000)
```

A fresh clone has no documents: `python tools/make_synthetic_smoke_fixture.py`
then "Rebuild index…" in the UI gives you a synthetic corpus to click
through. No SharePoint config → the picker still lists the volumes; only
"Sync now" is disabled.

## Databricks Apps deployment

- **Deploy from the repo ROOT.** The backend reaches outside this folder
  for `src/frdsttm` (imported at startup), `notebooks/` and
  `local_dev_fixtures/`, so the App's source-code path must be the whole
  repo: `app.yaml` and the Apps `requirements.txt` live at the repo root
  (the root `requirements.txt` just includes `review_app_react/requirements.txt`,
  which stays the single manifest and must stay in parity with
  `pyproject.toml` — Apps installs it, NOT `pyproject.toml`).

  ```bash
  cd frontend && npm install && npm run build && cd ../..   # dist/ must exist
  databricks bundle deploy -t <target>                       # the three jobs
  databricks sync . /Workspace/Users/<you>/frd-to-sttm-agent --exclude '.venv' --exclude 'node_modules'
  databricks apps create frd-sttm-agent                      # once
  databricks apps deploy frd-sttm-agent --source-code-path /Workspace/Users/<you>/frd-to-sttm-agent
  ```
- Workspace prerequisites (verify on deploy day, not assumed — listed in
  `app.yaml` too): the three bundle jobs deployed and their AS-DEPLOYED
  names in `STTM_DEMO_JOB_NAME` / `STTM_SYNC_JOB_NAME` /
  `STTM_RENDER_JOB_NAME` (dev-mode targets prefix names; or pin the
  `*_JOB_ID`s); volumes `frd_raw`, `sttm_reference`, `demo_raw`,
  `sttm_out_app`; secret scopes `sttm_agent/anthropic_api_key` and
  `sttm_agent/sharepoint_client_secret` (mapped into the App as an Apps
  secret resource, never a literal value); the App's service principal able
  to run the jobs, READ `frd_raw`/`sttm_reference`, read/write
  `demo_raw`/`sttm_out_app`.
- Still unproven from this checkout (2026-08-22): the deploy itself, SSE
  through the Apps proxy, the Jobs-API paths against a real workspace, and
  whether Apps packages only the source-code path (the reason for the
  repo-root deploy above).
