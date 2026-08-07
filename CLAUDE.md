# FRD-to-STTM Agent — working notes

## Purpose & pipeline position

Consumes an approved Functional Requirements Document (.docx) and produces
a Source-to-Target Mapping (STTM) workbook plus a machine-readable
feed-level mapping contract, via four Databricks notebooks: ingest →
extract (Anthropic structured outputs) → contract build (validation,
grounding audit, ambiguity gating) → render/eval. It is the **second
agent** in the client's five-agent AI-in-Engineering
program: BRD→FRD → **FRD→STTM** → CodeGen → Code Review (SQL Optimization
is standalone). Two hand-off contracts matter:

- **Upstream (versioned):** `01_frd_ingest` parses the IS-Methodology
  labels the BRD→FRD agent's renderer emits ("In Scope", "Assumptions,
  Constraints & Dependencies", `Project ID: NNNNNNN`, req-id families
  `BR|REQ|FR|SRQ|SIR|NFR|MDST`, the `TBD — pending client input…`
  placeholder routed to open items). Both sides load these from the
  shared, versioned `contracts/frd_label_contract.json` (v1.0.0),
  committed **byte-identically to both repos** and loaded via
  `frdsttm.label_contract` (fails loudly if missing/unversioned; no
  hardcoded fallback). Any contract change bumps `version` and must land
  as identical files in both repos in the same change set — the upstream
  repo's round-trip suite byte-compares the two copies and fails on
  drift. Never edit one side alone.
- **Downstream (the pipeline's biggest known gap):** `03_contract_build`
  emits `<doc_id>.contract.json`, which the CodeGen agent consumes as its
  FRD feed contract — that half works. But CodeGen ALSO requires a
  workbook-derived **STTM mapping contract JSON**, and **no committed tool
  anywhere in the program produces it** — the existing one was made
  out-of-repo, and CodeGen's CAQH copy is synthetic. Until a
  workbook→mapping-contract extractor exists (or `04_sttm_render` emits it
  alongside the .xlsx), that hand-off is manual and unreproducible.

## Repo layout

```
notebooks/01..04_*.py   the four entry points — dual-mode (plain local
                        scripts OR Databricks notebook tasks; IS_DATABRICKS
                        detection, widgets ↔ env vars, Spark ↔ deltalake)
notebooks/_models.py, _local_tables.py, _mock_extractions.py,
  _contract_build.py    thin re-export SHIMS — the real code lives in
                        src/frdsttm/; %run and local imports both hit these
src/frdsttm/            models.py (FrdIngestionSpec, GatedAmbiguity,
                        HumanResolution), contract_build.py (enrich /
                        grounding_audit / gating), label_contract.py
                        (shared-label-contract loader), local_tables.py,
                        mock_extractions.py
contracts/frd_label_contract.json   the shared FRD label contract (see
                        "Upstream" above; identical copy in brd-to-frd-agent)
tests/                  41 pure-function tests (no LLM/network/Spark)
schema/sttm_extraction_schema.json   the extraction contract (mirrors models)
databricks.yml + resources/frd_sttm_job.yml   asset bundle, job frd_sttm_pipeline
review_app_react/       FastAPI + Vite/React review app (Databricks App;
                        its root requirements.txt is the Apps deploy manifest)
local_dev_fixtures/     frd_raw/, sttm_reference/ inputs; outputs land here
demo_frd.docx / demo_sttm.xlsx   the tracked anonymized demo pair
tools/                  anonymization mapping + applier (mandated fixture path)
```

## Setup / run / test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,dev]"        # deps from pyproject.toml

pytest                               # 41 tests, offline

python notebooks/01_frd_ingest.py                        # parse demo_frd
STTM_MOCK_EXTRACTION=1 python notebooks/02_extract.py    # zero-cost mock
python notebooks/03_contract_build.py                    # gate: PASS_WITH_FLAGS
python notebooks/04_sttm_render.py                       # render + eval (94.1% on demo)
```

Without `STTM_MOCK_EXTRACTION=1`, `02_extract` makes real billed Anthropic
calls (`ANTHROPIC_API_KEY` env var locally; secret scope
`sttm_agent/anthropic_api_key` in Databricks). Bundle:
`databricks bundle validate|deploy|run frd_sttm_pipeline -t dev` — verify
the CLI targets the intended workspace first, and override the
dev-only `soham_workspace.sttm_agent` defaults per target.

## Config doctrine

- Runtime knobs are notebook widgets with env-var fallbacks of the same
  (uppercased) names; the bundle promotes shared ones to job parameters.
  Note the deliberate widget-name mismatch: `01` calls the documents table
  `table`, `02`/`03` call it `docs_table` — `databricks.yml` maps them;
  don't "fix" one side alone.
- No secrets in the repo, ever. Databricks reads the Anthropic key from
  the secret scope with an env-var fallback; with no key present,
  `02_extract` fails fast naming both remedies.
- Provider seam: `STTM_LLM_PROVIDER` unset preserves default behavior
  (`STTM_MOCK_EXTRACTION` decides mock vs Anthropic); `mock` / `anthropic`
  select explicitly — the program is Anthropic-only as model vendor.
  Setting a live provider while
  `STTM_MOCK_EXTRACTION` is also set RAISES rather than silently picking.
  Mock mode is gated on `not IS_DATABRICKS` — a real workspace run can
  never silently skip extraction.
- The grounding audit is the quality gate (strict fields verbatim,
  advisory prose token-overlap). Never relax it to make a run pass.

## Branching model

All development happens on `staging`. `main` is the deployment branch;
`staging` merges to `main` only after testing.

## Fixtures & data rules

Only the anonymized demo pair (`demo_frd.docx`, `demo_sttm.xlsx`) is
tracked. **Real client documents must NEVER enter this repo** — extractions,
contracts, and rendered workbooks live in UC volumes (or gitignored
`local_dev_fixtures/`). The anonymization tooling in `tools/` is the
mandated path for any new fixture material. Mock extraction specs copy real
facts from the demo FRD's parsed markdown — they prove plumbing, not
extraction quality.

## Known gaps / cautions

- **No committed workbook→STTM-mapping-contract extractor** (see Purpose
  above) — the program pipeline's biggest gap.
- **The upstream label contract is versioned** in
  `contracts/frd_label_contract.json` (shared with brd-to-frd-agent — see
  Purpose above), but the cross-repo byte-identity check lives in the
  BRD→FRD repo's suite and still skips there without its
  `reference/frd-sttm-agent` checkout; this repo's own tests only pin its
  local copy.
- Ambiguity ids are stable hashes of kind+text+context and are the join
  key for saved human resolutions; they were migrated once
  (`scripts/migrate_ambiguity_ids.py`) — changing the id scheme again
  requires migrating the review apps' stored decisions the same way.
- `%run ./_models` / `%run ./_contract_build` (Databricks) and
  `from _models import ...` (local) both resolve through the shims in
  `notebooks/` — edit `src/frdsttm/`, never the shims. The shim path has
  been verified locally end-to-end; a bundle-deployed Databricks run with
  the shims has not yet been executed.
- The review app's Databricks Apps deploy has not been executed from this
  checkout; the app's root `requirements.txt` (not `pyproject.toml`) is
  what Apps installs.
