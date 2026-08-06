# FRD → STTM Agent (Databricks)

Generates source-to-target mapping (STTM) documents from Functional
Requirements Documents (FRDs) on Databricks, with the LLM step running through
the Anthropic Python SDK (`client.messages.parse()` structured outputs) rather
than a manually-configured Agent Bricks endpoint — the extraction schema and
prompt live in this repo.

## Pipeline

```
FRD .docx (UC volume: frd_raw)
  -> notebooks/01_frd_ingest.py     docx -> markdown -> frd_documents Delta table
  -> notebooks/02_extract.py        Anthropic SDK structured outputs against
                                    schema/sttm_extraction_schema.json ->
                                    one extraction JSON per doc in sttm_out/extractions/
  -> notebooks/03_contract_build.py pydantic validation + regex enrichment
                                    + grounding audit + ambiguity gating
                                    -> feed-level mapping contract JSON
  -> notebooks/04_sttm_render.py    source-dictionary cross-check resolves
                                    gated ambiguities; derives stage/standard
                                    mappings; renders client STTM .xlsx;
                                    cell-level eval vs reference workbook
```

Division of labor: the LLM reads prose and scattered requirement tables;
deterministic code owns validation, regex-able facts, grounding checks,
attribution, and rendering. Every extracted string is audited against the
source document; genuine ambiguities are gated for review, never guessed.

## Running

1. Unity Catalog setup: schema `sttm_agent` with volumes `frd_raw`,
   `sttm_reference`, `sttm_out`. Upload FRDs to `frd_raw`, reference STTM
   workbooks to `sttm_reference`.
2. Databricks secret setup (one-time): create scope `sttm_agent` and store the
   Anthropic API key as `anthropic_api_key`:
   ```
   databricks secrets create-scope sttm_agent
   databricks secrets put-secret sttm_agent anthropic_api_key
   ```
3. Run `01_frd_ingest` (serverless). Widgets default to
   `soham_workspace.sttm_agent`.
4. Run `02_extract`. It reads `frd_documents.content`, calls Claude via the
   Anthropic SDK with `schema/sttm_extraction_schema.json` enforced
   server-side (via the shared Pydantic model in `frdsttm.models`), and
   writes one extraction JSON per document to `sttm_out/extractions/`.
5. Run `03_contract_build`, then `04_sttm_render`.

Known gaps and cautions: `CLAUDE.md`.

Client documents, extractions, contracts, and rendered workbooks stay in
Unity Catalog volumes — see `.gitignore`.

## Orchestrated run (Databricks Job / Asset Bundle)

The steps above still work exactly as described, and are still the fastest
way to debug a single stage (re-run just `03_contract_build` after fixing a
contract issue, for example, without re-running ingestion or the LLM call).
For running the full pipeline end-to-end in one shot, a [Databricks Asset
Bundle](https://docs.databricks.com/en/dev-tools/bundles/index.html)
(`databricks.yml` + `resources/frd_sttm_job.yml`) defines a single Job,
`frd_sttm_pipeline`, that chains all four notebooks as tasks with explicit
dependencies:

```
ingest -> extract -> contract_build -> render
```

Each task invokes its notebook with `base_parameters` matching that
notebook's actual `dbutils.widgets` — the shared ones (`catalog`, `schema`,
the parsed-documents table, the output volume, the contracts table) are
promoted to job-level parameters (`{{job.parameters.<name>}}`) so a single
job run only needs to set each value once, even though the notebooks don't
all name them the same way (see `databricks.yml`'s variable descriptions for
the specific name mismatches). All tasks run on serverless compute — no job
cluster is defined. Task-level retries are explicitly `max_retries: 0`:
`02_extract` and `03_contract_build` are deliberately written to fail loudly
on deterministic errors (schema violations, refusals, truncation) rather
than being silently retried by the orchestration layer; the only retries
that happen are the Anthropic SDK client's own internal retries for
transient API errors, configured separately via the `max_retries`
base_parameter passed into `02_extract`.

From the repo root, with the Databricks CLI authenticated
(`databricks auth login` or an existing profile):

```
databricks bundle validate -t dev   # check the bundle config is well-formed
databricks bundle deploy -t dev     # create/update the job in your workspace
databricks bundle run frd_sttm_pipeline -t dev   # trigger a full pipeline run
```

`validate` only checks the bundle's shape locally against the Jobs API
schema — it doesn't touch the workspace. `deploy` creates or updates the
`frd_sttm_pipeline` job definition in the target workspace (path shown in
`validate`'s output). `run` (or the Databricks UI, once deployed) triggers
an actual run — the job is manual/on-demand only, with no cron schedule,
since documents arrive irregularly and there's no review UI yet to gate a
scheduled run on.

Only one target, `dev`, is defined today, using the same
`soham_workspace.sttm_agent` defaults the notebook widgets already use. The
job has an `email_notifications.on_failure` field wired up with a
placeholder address (`notification_email` variable in `databricks.yml`) —
replace it with a real address before deploying to a shared workspace.

## Local mode

The same four notebooks also run as plain `python notebooks/0N_*.py` scripts
on a laptop, no Databricks connection required. Each notebook detects at
import time whether `dbutils`/`spark` are present (`IS_DATABRICKS =
"dbutils" in globals()`); when they aren't, every `dbutils.widgets` call
falls back to an env var of the same name (uppercased) with the same
default, and every Spark Delta-table read/write falls back to a real local
Delta table (via the `deltalake` package — no JVM/cluster involved) under
`local_dev_fixtures/warehouse/<catalog>/<schema>/<table>/`, written by the
small helper in `frdsttm.local_tables` (shared logic lives in the
`src/frdsttm/` package; thin shims at the old `notebooks/_*.py` paths keep
both execution modes working). `dbutils.secrets` already had
an `ANTHROPIC_API_KEY` env-var fallback in `02_extract.py`; local mode just
uses it. None of the extraction prompt, Pydantic models, grounding-audit
logic, gating rules, mapping-derivation rules, or rendering logic changed —
this is purely a swap of the execution/storage substrate. Inside an actual
Databricks notebook, `dbutils` is always present, so none of this changes
existing Databricks behavior.

Setup (Python 3.11+; dependencies live in `pyproject.toml`):

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,dev]"
export ANTHROPIC_API_KEY=...   # or put it in your shell profile
```

Run the test suite (pure functions only — no LLM, no network, no Spark):

```
pytest
```

Fixtures (`.gitignore`d, same as UC volumes — place them yourself):

```
local_dev_fixtures/
  frd_raw/            # FRD .docx files (input to 01)
  sttm_reference/      # reference STTM .xlsx workbooks (input to 04)
```

Run the pipeline:

```
python notebooks/01_frd_ingest.py
python notebooks/02_extract.py
python notebooks/03_contract_build.py
python notebooks/04_sttm_render.py   # optional; needs sttm_reference fixtures
```

`02_extract.py` needs `ANTHROPIC_API_KEY` set and makes real (billed) API
calls, same as in Databricks. For zero-cost local testing of everything
*downstream* of extraction — `03_contract_build.py`'s gating logic, the
review app — set `STTM_MOCK_EXTRACTION=1` before running `02_extract.py`
instead:

```
STTM_MOCK_EXTRACTION=1 python notebooks/02_extract.py
```

This skips the Anthropic call entirely and returns a hand-authored
`FrdIngestionSpec` per document from `frdsttm.mock_extractions`,
built by actually reading each sample FRD's real parsed markdown and
copying real facts (file patterns, schemas, table names, business rules)
into the spec — not synthetic placeholder data — and each one deliberately
reproduces a genuine ambiguity from its source FRD so there's something
real for `03_contract_build.py` to gate and the review app to display.
**This is not a quality benchmark** — a mock-mode run only proves the
plumbing works; it says nothing about how well the real LLM extraction
performs (real numbers come from workspace runs against the reference
workbooks, e.g. the demo pair's 94.1% cell-level eval). Local-mode-only by construction — the env var is ignored if
`IS_DATABRICKS` is true, so a real Databricks job run can never accidentally
skip the real extraction call.

Outputs land under `local_dev_fixtures/`, mirroring the real UC volume
layout: `sttm_out/{extractions,contracts,reports,rendered}/` and
`warehouse/<catalog>/<schema>/<table>/` for the three local Delta tables
(`frd_documents`, `frd_contracts`, `frd_sttm_runs`). To point any stage at a
different catalog/schema/table/volume, set the matching uppercased env var
(e.g. `CATALOG`, `SCHEMA`, `DOCS_TABLE`, `OUT_VOLUME`) before running it —
same names, same defaults as the notebook widgets.

To switch a notebook back to Databricks mode, just run it inside a
Databricks notebook (or as a Job task, see `resources/frd_sttm_job.yml`) —
`dbutils`/`spark` being present is the only thing that flips
`IS_DATABRICKS`; nothing else needs to change.

## Review app (gated ambiguities)

`review_app_react/` reads a Phase-4 contract JSON, shows every gated item
from `_provenance.ambiguities` and `_provenance.grounding.advisory_flagged`,
and lets a reviewer resolve each one (pick a parsed candidate, or type a
free-text override). Resolutions are saved back into the same contract JSON
under `_provenance.human_resolutions`, keyed by the structured
`GatedAmbiguity.id` (see `frdsttm.models`). `04_sttm_render.py` reads
this list and applies it ahead of its own dictionary cross-check — a saved
human resolution takes precedence and is never re-decided by the heuristic.
One case is intentionally recorded for audit rather than auto-applied:
free-text prose on a cross-feed attribution ambiguity, which has no
reliable prose-to-field mapping.

It's a FastAPI backend and a React + TypeScript + Vite frontend using
`@databricks/appkit-ui` components (`Card`, `RadioGroup`, `Dialog`-family
primitives, etc. — not the package's opinionated `DataTable`, which is
wired to AppKit's own Node-side query registry and isn't a fit for a plain
FastAPI backend; see `frontend/src/components/` for the primitives used
instead). `backend/data_access.py` is the data-access layer, swappable the
same way `02_extract.py`'s secret lookup is: an `STTM_APP_MODE` env var,
`local` (default) or `databricks`. `local` reads/writes the real contract
JSONs under `local_dev_fixtures/sttm_out/contracts/`; `databricks`
reads/writes the same filenames from a UC volume via the Databricks SDK's
Files API.

(An earlier Streamlit version of this app, `review_app/`, has been retired
in favor of this one — same functionality, on Databricks' current
officially-supported app stack.)

Run locally (two terminals):

```
# terminal 1 -- backend (deps: pip install -e ".[ui]" from the repo root)
cd review_app_react/backend
uvicorn app:app --reload --port 8000

# terminal 2 -- frontend (proxies /api to the backend above)
cd review_app_react/frontend
npm install
npm run dev
```

Open the URL Vite prints (typically `http://localhost:5173`).

To switch the backend to `databricks` mode locally (e.g. to test against a
live workspace once one is reachable), set `STTM_APP_MODE=databricks` plus
`CATALOG`/`SCHEMA`/`OUT_VOLUME` before starting uvicorn, or deploy it as a
Databricks App — `review_app_react/app.yaml` already pins
`STTM_APP_MODE: databricks` for that deployed context.

Before deploying as a Databricks App, build the frontend once
(`cd frontend && npm run build`) so `backend/app.py` has a `frontend/dist/`
to serve as static assets; `review_app_react/app.yaml` then runs
`python backend/app.py` as a single process serving both the API and the
built frontend. Not deployed this session — the workspace account used for
`databricks apps deploy` is still inactive (403 org-cancelled error); this
app has only been run and verified locally.

## Branching strategy

The repo has exactly two branches: `main` and `staging`. All active work
happens directly on `staging` — feature branches are deliberately not
used, so no temporary branches accumulate. `main` is promoted from
`staging` only once something is production-ready. Merges are real merge
commits, never squashed or rebased, so the full history stays intact for
audit. No force operations are used on either branch: no `--force`, no
`--force-with-lease`, and no `-D` branch deletion.
