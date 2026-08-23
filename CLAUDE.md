# FRD-to-STTM Agent — working notes

> **Read first, every session:** read
> `frd-to-sttm-master-context-document.md` (repo root, tracked in git) in
> its entirety before working in this repo — it is this agent's single
> get-up-to-speed document (priority + Monday checklist, pipeline, template
> architecture, proven-vs-unproven state, decisions log). On the original
> dev Mac, ALSO read
> `../amerihealth-agents/amerihealth-project-master-context-document.md`
> for program-wide context (all three agents) — that file is not in any git
> repo; it lives in the umbrella folder, which this repo was moved OUT of on
> 2026-08-22 (it now sits beside it, not inside it). This CLAUDE.md remains
> authoritative for this repo where the derived documents disagree with it.

## Purpose & pipeline position

Consumes an approved Functional Requirements Document (.docx) and produces
a Source-to-Target Mapping (STTM) workbook plus a machine-readable
feed-level mapping contract, via four Databricks notebooks: ingest →
extract (Anthropic structured outputs) → contract build (validation,
grounding audit, ambiguity gating) → render/eval. It is the **first
agent** in the client's three-agent AI-in-Engineering program:
**FRD→STTM** → CodeGen → Code Review.

**Scope cut 2026-08-21:** the program dropped from five agents to three —
BRD→FRD and SQL Optimization are no longer in scope. Where this file names
those repos below, it is preserving the *reason* a design choice was made;
none of it is a live sync obligation any more.

**Where this runs:** Hexaware builds, ACFC rebuilds. This repo is the
Hexaware-side reference implementation; the production agent gets rebuilt
inside ACFC's own environment with Claude Code, using this as the
blueprint. Nothing here deploys to ACFC directly — so a decision that is
not written down, or output that cannot be re-derived from the contracts
and config, does not survive the hand-off.

**Current priority (set 2026-08-21):** ship this agent as a **live
Databricks App in the Hexaware environment by Monday 2026-08-24** —
`review_app_react/` deployed with live (billed) runs working, not
replay-only. The three deploy blockers are the last entries under "Known
gaps" below; verify each rather than assuming it still holds.

Two hand-off contracts matter:

- **Upstream (versioned, now a FROZEN INPUT):** `01_frd_ingest` parses the
  IS-Methodology labels an FRD renderer emits ("In Scope", "Assumptions,
  Constraints & Dependencies", `Project ID: NNNNNNN`, req-id families
  `BR|REQ|FR|SRQ|SIR|NFR|MDST`, the `TBD — pending client input…`
  placeholder routed to open items), loaded from the versioned
  `contracts/frd_label_contract.json` (v1.0.0) via
  `frdsttm.label_contract` (fails loudly if missing/unversioned; no
  hardcoded fallback).

  *Historical:* these labels came from the BRD→FRD agent, and the file was
  kept byte-identical in both repos with a cross-repo drift check in that
  repo's suite. **BRD→FRD left the program on 2026-08-21**, so there is no
  second copy to sync and no drift check to satisfy. Treat the contract as
  a frozen description of the FRD documents this agent must parse: change
  it only when a real input document stops matching it, bump `version`
  when you do, and re-verify against `local_dev_fixtures/frd_raw/` rather
  than against another repo.
- **Downstream (round trip RUN on synthetic documents 2026-08-22 — works
  in the sheet-per-table dialect):** `03_contract_build` emits
  `<doc_id>.contract.json`, which the CodeGen agent consumes as its FRD
  feed contract. CodeGen ALSO requires a workbook-derived **STTM mapping
  contract JSON**, produced by
  `codegen extract-sttm --workbook X.xlsx --frd-contract Y.json --out Z.json`
  in the code-gen-agent repo.

  **Round trip as of 2026-08-22 (evening):** both synthetic smoke documents
  went 01→03→04 (template mode, `EXCLUDE_OWN_REFERENCE=0`) and then through
  `codegen extract-sttm` unpatched → `EXTRACTED … 1 feed(s) (5 fields)`,
  with `value_spec` carrying the FRD rule on the row it names and the audit
  columns peeled. What made it work, all in 04 + `reference_workbooks`:
  (a) `render_sheet_per_table` now emits a **`Comment`** column (→ CodeGen
  `value_spec`) carrying each validation rule on the row(s) whose source
  column the rule names, and a **`Recycle Flag`** column beyond the standard
  band with the recycle rule as `Y ( <verbatim> )` on the row it names; a
  rule naming no rendered column goes to FILE_DETAILS › File Description
  (human-visible) and is never pinned to a guessed row — placement is
  recorded in `_provenance.rule_placement` + the phase5 report;
  (b) trailing **audit rows** (source `NA`) are flagged by the dictionary
  parser and derived from the TEMPLATE's target column/datatype instead of
  the 1:1 rule (a client convention, not an FRD fact; the old render wrote
  stage ColumnName `NA`, which CodeGen rejects). The synthetic fixture
  carries audit rows + states project name / delimiter / load strategy so
  the round trip needs no patching.
  Still true: `render_single_sheet` (CAQH dialect) is incompatible with the
  FLAT-only extractor outright (different band/header dialect); the rules
  now land in its `Business Rule` column + metadata block for humans.
  Still UNPROVEN on real documents — and CodeGen's `FrdContract` is
  stricter than ours (non-null `project_name`, `load_strategy` ∈
  {'Truncate and Load','Append'}, a delimiter for `txt`): a real FRD that
  does not state one of those fails at CodeGen's validation step, loudly,
  before the workbook is read.

## Repo layout

```
notebooks/01..04_*.py   the four core entry points — dual-mode (plain local
                        scripts OR Databricks notebook tasks; IS_DATABRICKS
                        detection, widgets ↔ env vars, Spark ↔ deltalake)
notebooks/00_sharepoint_sync.py     SharePoint FRD + STTM folders → frd_raw +
                        sttm_reference volumes → corpus_index.json (READ;
                        scheduled job frd_sttm_sharepoint_sync; incremental
                        via sync_manifest.json; also the app's "Sync now")
notebooks/00_sharepoint_fetch.py    SharePoint library → frd_raw (read; the
                        pipeline job's own fetch for a stand-alone full run)
                        — there is NO publish notebook: the repo never writes
                        to SharePoint (2026-08-22; 05_sharepoint_publish removed)
notebooks/_models.py, _local_tables.py, _mock_extractions.py,
  _contract_build.py, _sharepoint.py   thin re-export SHIMS — the real code
                        lives in src/frdsttm/; %run and local imports both
                        hit these
src/frdsttm/            models.py (FrdIngestionSpec, GatedAmbiguity,
                        HumanResolution), contract_build.py (enrich /
                        grounding_audit / gating), label_contract.py
                        (shared-label-contract loader), local_tables.py,
                        mock_extractions.py, live_extraction.py,
                        sharepoint.py (Microsoft Graph transport, READ-ONLY),
                        sync.py (SharePoint → volumes → index, incremental;
                        ONE implementation for the job and the app);
                        since 2026-08-22 (docs/TEMPLATE_ARCHITECTURE.md):
                        frd_parsing.py + reference_workbooks.py (the 01/04
                        parsers, factored verbatim behind new shims),
                        similarity.py (deterministic FRD↔STTM scoring,
                        thresholds), corpus.py (corpus_index.json),
                        exemplars.py (retrieved-exemplar prompt blocks)
context/                FRD_to_STTM_Agent_Architecture.pptx (the two-slide
                        ACFC-style deck; built by scripts/build_architecture_deck.py
                        from scripts/deck_assets/),
                        FRD_to_STTM_Agent_System_Architecture.pptx (ONE slide,
                        "Signal Dark" — design philosophy + theme spec in
                        scripts/deck_assets/ONEPAGER_DESIGN.md; built by
                        scripts/build_architecture_onepager.py — regenerate it
                        there, never edit the .pptx by hand; python-pptx +
                        pillow are deck-building deps, not runtime; the
                        background is a seeded flow-field the script draws), and
                        FRD_to_STTM_Agent_Screens.html (self-contained
                        walkthrough of every review-app screen, captured
                        2026-08-22 against a synthetic corpus). No client
                        documents — ever.
contracts/frd_label_contract.json   the versioned FRD label contract — now a
                        frozen input, no longer mirrored anywhere (see
                        "Upstream" above)
tests/                  offline (no LLM/network/Spark); 195 as of
                        2026-08-22 (late evening; +17 in test_render_rules.py
                        for rule placement + audit rows) — run `pytest` for
                        the live count rather than trusting a number here
schema/sttm_extraction_schema.json   the extraction contract (mirrors models)
databricks.yml + resources/frd_sttm_job.yml   asset bundle, job frd_sttm_pipeline
resources/frd_sttm_sync_job.yml   the SCHEDULED sync job (cron in databricks.yml
                        `sync_cron`, paused on the dev target)
resources/frd_sttm_render_job.yml the render-only job (stage 04 over an existing
                        run suffix) that the human-in-the-loop re-render triggers
                        in databricks mode — no re-extraction, nothing billed
app.yaml + requirements.txt   the Databricks App manifest, AT THE REPO ROOT
                        (2026-08-22 evening): the App deploys from the repo root
                        because the backend imports src/frdsttm and reaches
                        notebooks/ + local_dev_fixtures/; the root requirements
                        only includes review_app_react/requirements.txt (the
                        single manifest, kept in parity with pyproject.toml)
review_app_react/       FastAPI + Vite/React review app (Databricks App; its
                        requirements.txt is the Apps deploy manifest, included
                        by the repo-root one).
                        ONE surface since 2026-08-21 (the earlier tab
                        layouts, incl. the rendered gated-ambiguity review
                        tab, were removed; those components remain in the
                        tree unrendered and their backend stays for tests):
                        the "Select FRD" flow — since 2026-08-22 (evening)
                        the picker is the CORPUS INDEX the sync keeps in
                        Unity Catalog (GET /api/demo/corpus/frds): unmapped
                        FRDs get "Generate STTM" (billed-run gate); mapped
                        FRDs present their approved STTM from the reference
                        volume (download + SharePoint link) with a two-step
                        regenerate-anyway. The results view carries the
                        HUMAN-IN-THE-LOOP panel (2026-08-22 evening): every
                        gated item as a GatedItemCard (pick / none-of-these /
                        free text → POST /api/demo/artifacts/{set}/resolutions,
                        merged into the run's v1 contract exactly like the
                        legacy flow) and "Apply resolutions & re-render"
                        (POST .../rerender: stage 04 only — subprocess
                        locally, the frd_sttm_render job in databricks mode).
                        No SharePoint lookup on the
                        request path; NO publish control anywhere — the
                        reviewer uploads the finished workbook to the
                        library's STTM folder themselves and the next sync
                        pairs it (the results view says exactly where). The
                        Corpus panel's "Sync now"/"Rebuild index" start one
                        background sync (POST /api/demo/corpus/sync, 202 +
                        polled state). Runs are MODE-SWITCHED on STTM_APP_MODE:
                        local = 01→04 subprocesses with demo_<ts> env
                        insulation; databricks (the deployed App) = trigger
                        the bundle job via the Jobs API with the same
                        insulation as job parameters (backend/jobs_runner.py)
                        so artifacts land natively in UC and survive a
                        restart. Artifact-set replay remains the shared
                        results renderer ("Past runs"). The mock upload flow
                        (orchestration.py) and upload endpoints stay in the
                        backend, UI-less, for tests and local dev. In
                        databricks mode "Sync now" triggers the sync JOB and
                        then mirrors frd_raw + sttm_reference down to the
                        container; reads re-mirror lazily after
                        STTM_CORPUS_REFRESH_SECONDS so scheduled ticks show
                        up without app involvement.
                        See review_app_react/README.md.
local_dev_fixtures/     gitignored, NOT in the checkout since 2026-08-22 -- frd_raw/,
                        sttm_reference/ inputs and run outputs land here when you
                        supply them locally; nothing FRD/STTM-shaped is tracked
tools/                  anonymization mapping + applier (mandated fixture path)
```

## Setup / run / test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,dev,ui]"     # deps from pyproject.toml; ui gives the
                                     # review-app backend its FastAPI tests

pytest                               # offline; run it for the live count

# Offline smoke of 01→03→04 with SYNTHETIC documents (the repo carries no
# FRD/STTM material since 2026-08-22; this replaces the deleted fixtures
# without client content — 02 is skipped, its extraction JSONs pre-written):
python tools/make_synthetic_smoke_fixture.py
SYNC_MODE=reindex python notebooks/00_sharepoint_sync.py # index the fixture (no network)
python notebooks/01_frd_ingest.py
python notebooks/03_contract_build.py                    # both gate PASS
python notebooks/04_sttm_render.py                       # template decision + eval
```

Without `STTM_MOCK_EXTRACTION=1`, `02_extract` makes real billed Anthropic
calls (`ANTHROPIC_API_KEY` env var locally; secret scope
`sttm_agent/anthropic_api_key` in Databricks). Bundle:
`databricks bundle validate|deploy|run frd_sttm_pipeline -t dev` (and
`run frd_sttm_sharepoint_sync` for the sync) — verify the CLI targets the
intended workspace first, and override the dev-only
`soham_workspace.sttm_agent` defaults per target. The sync job's schedule
is PAUSED on the dev target (no tenant there); UNPAUSED by default.

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
  Mock mode is gated on `MOCK_AVAILABLE` — false when `IS_DATABRICKS`
  (notebook task) OR `IS_DATABRICKS_APP` (`STTM_APP_MODE=databricks`, the
  deployed App, where `dbutils` is absent from a subprocess so the first flag
  alone would miss it). A workspace run can never silently skip extraction;
  in the App an explicit mock request raises rather than being ignored.
- The grounding audit is the quality gate (strict fields verbatim,
  advisory prose token-overlap). Never relax it to make a run pass.

## SharePoint / Microsoft Graph (added 2026-08-21; made READ-ONLY + scheduled sync 2026-08-22)

The document library is the program's system of record: FRDs and approved
STTMs live there. `src/frdsttm/sharepoint.py` is the whole transport;
`src/frdsttm/sync.py` is what keeps the volumes in step with it.

**It is a seam at the edges, deliberately not inside 01-04.** `01` parses
every supported file in a directory; `04` writes a workbook to a volume.
SharePoint attaches before `01` only: `00_sharepoint_sync` (continuous) and
`00_sharepoint_fetch` (the pipeline job's own fetch). Keeping the network
at the edge is what lets 01-04 stay offline and credential-free and keeps
the suite network-free. **Do not "simplify" this by calling Graph from
inside 01** — that puts a token lifetime and a network dependency inside
the parsing stage.

- **READ-ONLY BY CONSTRUCTION (decided 2026-08-22 — Arjun; supersedes the
  2026-08-21 "manual publish button").** Nothing in this repo writes to
  SharePoint: `SharePointClient` has no upload method, there is no output
  folder knob, `05_sharepoint_publish.py` and the app's
  `POST /api/demo/sharepoint/publish` + `PublishControl` are gone, and
  `tests/test_sharepoint_routes.py` guards that the router exposes only
  the config probe. The hand-off is a PERSON uploading the reviewed
  workbook to the library's STTM folder; the next sync pulls it into
  `sttm_reference` and pairs it with its FRD (exact name match first —
  `<doc_id>.sttm.xlsx`, `<doc> STTM.xlsx` etc. — similarity second).
  Consequence for the long-pole Entra ID grant: `Sites.Selected` **read**
  on the one site is sufficient. Do not add a write path back "for
  convenience"; the slides, docs and grant all rely on its absence.
- **The sync is the set-up AND the steady state.** `00_sharepoint_sync`
  lists the FRD folder (`sharepoint_frd_folder`) and the STTM folder
  (`sharepoint_reference_folder`, default = the FRD folder), downloads
  only what is new or changed (Graph item id + eTag + modified + size,
  recorded in `sync_manifest.json` next to the corpus index), removes the
  local copy of anything that left the library (only files IT synced — a
  hand-staged file is left alone), then rebuilds `corpus_index.json` with a
  `content_sha256` per document. First tick = bulk load; every later tick =
  incremental. Scheduled by `resources/frd_sttm_sync_job.yml` (`sync_cron`,
  default every 15 min, UTC; `max_concurrent_runs: 1`). `sync_mode=reindex`
  rebuilds the index from the volumes without touching SharePoint — the
  path for an unwired tenant and for the synthetic smoke fixture. Zero
  model calls. Empty library = warning, not failure (a scheduled tick must
  not page anyone); a Graph refusal on the listing = failure; one bad
  download/parse = reported in `skipped`, never fatal.
- **Standard library only** (`urllib.request`). Graph is plain REST; no SDK
  is needed. This is deliberate: the review app deploys as a Databricks App,
  which installs `review_app_react/requirements.txt` rather than
  `pyproject.toml`, so every avoided dependency is one fewer thing that can
  be missing at runtime.
- **App-only client credentials** against an Entra ID app registration.
  Secret from scope `sttm_agent/sharepoint_client_secret`, env var
  `SHAREPOINT_CLIENT_SECRET` locally — same shape as the Anthropic key.
  The secret is excluded from `SharePointConfig.__repr__` so it cannot reach
  a traceback or a log line. Required Graph APPLICATION permission with
  admin consent: `Sites.Selected` (read) on the target site, preferred
  over tenant-wide `Files.Read.All`.
- **Fail-loud, both directions.** Missing config raises naming BOTH remedies
  rather than defaulting. A short download is reported, never written. A
  missing library lists what the site actually has. There is no fallback
  to a stale local copy in either direction.
- **Review app** (`review_app_react/backend/corpus_routes.py` +
  `sharepoint_routes.py`): the picker reads the corpus index only;
  `sharepoint_routes` is down to the tenant probe and the shared client
  factory. Status codes say whose problem it is: 503 not configured, 502
  Graph refused, 400 bad request, 409 a sync is already running. "Sync
  now" is confirm-gated (it rewrites the volumes/index) and runs in a
  background thread: in local mode the sync code in-process, in databricks
  mode the bundle-deployed sync JOB via the Jobs API followed by a mirror
  of both volumes down to the container.

## Designed, not built (decided 2026-08-21; largely BUILT since)

This list has landed — the historical corpus 2026-08-22, and the same
evening the sync + read-only + corpus-driven picker reshape (entries below
record what shipped and what remains). Still genuinely not built: showing
a DIFF for a revised FRD whose STTM predates the revision (the fingerprint
to detect it now exists).

- **Duplicate-FRD detection — reshaped 2026-08-22 (evening).** "Mapped"
  is now decided by the corpus index in Unity Catalog, not by a SharePoint
  name lookup: the picker lists unmapped FRDs for generation and mapped
  FRDs with their approved STTM (presented first, download + link; a
  two-step regenerate-anyway re-enters the billed-run gate and every such
  run is auto-evaled against the existing STTM, which is never touched).
  Pairing is exact name match first, similarity second. Every FRD and
  reference now carries `content_sha256` in the index and the sync detects
  a revised same-name document by eTag/modified/size — what remains is to
  SHOW the reviewer that the paired STTM predates the FRD revision (a
  diff / "stale pair" flag) rather than presenting it silently.
- **Unity Catalog as the working store — BUILT for documents 2026-08-22
  (evening).** The sync keeps `frd_raw` and `sttm_reference` current and
  the index lives in the reference volume; SharePoint remains the system
  of record and the hand-off (reviewer uploads; sync pulls back).
- **Historical corpus for quality — BUILT 2026-08-22** as the template
  architecture (docs/TEMPLATE_ARCHITECTURE.md): the SharePoint sync
  harvests the library's FRDs + STTMs, pairs them deterministically
  (`frdsttm/similarity.py`, verdicts in code), and every approved pair is a
  template — (1) automatic golden-pair eval on every regeneration
  (exclude-own-reference cross-validation), (2) retrieved few-shot
  exemplars in 02's prompt, (3) the matched workbook(s) as 04's dictionary
  + layout. The HIPAA/BAA gate still applies before harvesting real ACFC
  documents in the ACFC environment. **Still NOT fine-tuning** — the Claude
  API has no fine-tuning surface; reasoning in the master context document
  §7a. Settled; do not re-open.

## Branching model

All development happens on `staging`. `main` is the deployment branch;
`staging` merges to `main` only after testing.

## Fixtures & data rules

**No FRD or STTM material is tracked in this repo — as of 2026-08-22.** The
formerly tracked anonymized demo pair (`demo_frd.docx`, `demo_sttm.xlsx`),
the `local_dev_fixtures/` inputs, and the demo replay set
(`local_dev_fixtures/sttm_out_live_e2e_20260807b/`, the post-fix live E2E run
described in docs/LIVE_E2E_2026-08-07.md) were deleted from the working tree
and `git rm`'d on Arjun's instruction that no client documents — raw or
derived — live in the repository. They remain in git history until a purge
is decided. Consequences: a fresh clone has no preloaded FRD, no offline
replay set and no reference workbook; the replay tests in
`tests/test_demo_backend.py` skip (they already guarded on the set being
present); a local run needs you to drop an FRD into
`local_dev_fixtures/frd_raw/` yourself. **Real client documents must NEVER
enter this repo** — extractions, contracts, and rendered workbooks live in UC
volumes (or gitignored `local_dev_fixtures/`). The anonymization tooling in
`tools/` is the mandated path for any new fixture material, and tracking any
such material again is a deliberate decision, not a default (the `.gitignore`
re-include negations were removed for that reason).
`src/frdsttm/mock_extractions.py` still carries hand-copied facts (file
patterns, table names, rule text) from the two source FRDs — it is source
code, not a document, and was left in place; flagged, not silently kept.

## Known gaps / cautions

- **The `.venv` was recreated 2026-08-22** (again — the repo moved from
  `~/Desktop/amerihealth-agents/` to `~/Desktop/` and the venv bakes in
  absolute paths; `source .venv/bin/activate` then silently pointed at the
  old path and `pytest` resolved to a global one with 8 import errors). If
  the folder moves, `rm -rf .venv` and reinstall `.[local,dev,ui]`. The rebuild surfaced that current FastAPI needs `python-multipart`
  at import time for the UploadFile routes; it is now in the `ui` extra AND
  `review_app_react/requirements.txt` (without it the deployed App dies at
  startup, not at first upload).
- **The workbook→mapping-contract round trip has been run on SYNTHETIC
  documents only (2026-08-22).** It works in the sheet-per-table dialect
  (see "Downstream" above for exactly what changed). Not yet run on a real
  FRD/STTM pair; in freeform mode (no template) there are no derived fields
  and hence no audit rows, so CodeGen rejects that workbook — loudly. To
  repeat the check: run the synthetic smoke with `EXCLUDE_OWN_REFERENCE=0`,
  then `codegen extract-sttm` from the code-gen-agent checkout (its own
  venv; the one on this Mac had to be rebuilt after a folder move).
- **The label contract's cross-repo obligation is retired.**
  `contracts/frd_label_contract.json` stays versioned and is still the only
  source of the labels `01_frd_ingest` parses, but with BRD→FRD out of the
  program (2026-08-21) there is no second copy and no external drift check.
  This repo's tests pin the local copy — that is now the whole story, not a
  partial one.
- Ambiguity ids are stable hashes of kind+text+context and are the join
  key for saved human resolutions; they were migrated once
  (`scripts/migrate_ambiguity_ids.py`) — changing the id scheme again
  requires migrating the review apps' stored decisions the same way.
- `%run ./_models` / `%run ./_contract_build` (Databricks) and
  `from _models import ...` (local) both resolve through the shims in
  `notebooks/` — edit `src/frdsttm/`, never the shims.

- **Mock extraction is keyed to the REMOVED demo documents (2026-08-22).**
  `mock_extractions.py`'s specs match only the old demo_frd/CAQH doc ids and
  content; on any other corpus a mock 02 run raises rather than inventing a
  spec (correct — fail loud), which is why the synthetic offline smoke
  skips 02 by pre-writing extraction JSONs. Live extraction is unaffected.
- **Template thresholds are seeded, not calibrated (2026-08-22).**
  `similarity.THRESHOLD_DEFAULTS` were set against the synthetic smoke
  fixture. With a 2-document corpus and exclude-own on, each FRD has ONE
  eligible template candidate, so `template_single_min` alone decides
  single-vs-freeform — calibrate on the two real Hexaware pairs before the
  demo and record the outcome in docs/TEMPLATE_ARCHITECTURE.md.
- **Interpreter exit DEADLOCK on the py3.14 venv — worked around
  (2026-08-22 evening).** What was logged as a "slow exit" turned into a
  hard hang on `03_contract_build` (>13 min at 0% CPU after all work was
  written and flushed; the same file exits instantly under `runpy`, so it
  is interpreter-SHUTDOWN finalization in deltalake/pyarrow, not the
  stage). Since the review app's local-mode runner waits on process exit,
  that is a failed demo run. Each of 01–04 now ends with a guarded
  local-mode cell: flush stdout/stderr, `os._exit(0)` — never reached in
  Databricks (`IS_DATABRICKS`), and nothing in these stages relies on
  atexit handlers. If you add a stage, copy the cell. `pyarrow` itself is
  an explicit `[local]` dependency — deltalake 1.x stopped depending on it
  while `frdsttm.local_tables` still imports it.

### Deploy blockers — live Databricks App, Hexaware, by 2026-08-24

These are the specific things standing between the current state and the
current priority stated at the top. None has been executed from this
checkout; treat each as unproven until you have seen it work.

- **Databricks Apps deploy has never run from here.** Apps installs the
  repo-root `requirements.txt`, which includes
  `review_app_react/requirements.txt` — **not** `pyproject.toml`. Deploy
  from the REPO ROOT (`app.yaml` lives there since 2026-08-22 evening;
  rationale in it and in review_app_react/README.md).
  A dependency that exists only in `pyproject.toml` will be missing at
  runtime, and the failure surfaces in the deployed app, not locally.
  **Partly addressed 2026-08-21:** the manifest was short by exactly five —
  `anthropic`, `python-docx`, `pypdf`, `openpyxl`, `deltalake` — needed
  because the demo tab spawns `notebooks/0N_*.py` as SUBPROCESSES in the app
  container, where `dbutils` is not a global, so `IS_DATABRICKS` is False and
  they take the LOCAL storage path. Those are now listed and parity with
  `pyproject.toml` (core + local + ui, minus streamlit) is exact in both
  directions; a third round (2026-08-22) added `pyarrow` to both — deltalake
  1.x dropped it while `frdsttm.local_tables` still imports it directly. The deploy itself is still unproven.
- **`IS_DATABRICKS` reads False inside the Apps container.** A spawned
  subprocess has no `dbutils` global. **The mock half is fixed (2026-08-21):**
  `02_extract` now also derives `IS_DATABRICKS_APP` from
  `STTM_APP_MODE=databricks` (set in `app.yaml`), and `MOCK_AVAILABLE` is
  false when either is true. In the App a mock request **raises** rather than
  being ignored — both `STTM_MOCK_EXTRACTION=1` and `STTM_LLM_PROVIDER=mock`
  — because there the variable had to be set deliberately, so the operator
  believes mock is on, and a run that looks live while returning
  hand-authored specs is the worst possible failure in front of a client.
  The notebook-task branch keeps its silent-ignore behaviour (there the var
  is laptop leftovers, not an instruction). The demo tab was already safe —
  `demo.py::_subprocess_env` pops the var and pins the provider — this is
  defence in depth one layer down, for a direct notebook run in the container.
  **The storage half is RESOLVED BY DESIGN (2026-08-21, Arjun's call):** in
  the deployed App the demo tab no longer runs notebooks in the container at
  all — it triggers the bundle job via the Jobs API
  (`backend/jobs_runner.py`), so the notebooks run as real workspace tasks
  (`IS_DATABRICKS` True) and artifacts land natively in Unity Catalog. The
  container's local artifact copy is a mirror/cache, rehydrated from the
  `sttm_out_app` volume after a restart. The subprocess path remains
  local-mode only.
- **The Jobs-API demo path has never run against a real workspace.** Fully
  unit-tested offline (stubbed SDK), but unproven live, and it stacks on the
  also-unproven bundle-run/shim path below. Workspace prerequisites the
  Monday deploy must establish, in the order they will bite: the bundle job
  deployed with `STTM_DEMO_JOB_NAME` matching its AS-DEPLOYED name (dev-mode
  targets prefix it, e.g. `[dev <user>] frd_sttm_pipeline` — or pin
  `STTM_DEMO_JOB_ID`); volumes `demo_raw`, `sttm_out_app`, and
  `sttm_reference` — since 2026-08-22 an empty reference dir is a WARNED
  freeform render rather than 04's old hard failure, but the demo needs the
  sync (job or the app's "Sync now") to have populated `frd_raw` +
  `sttm_reference` + `corpus_index.json`; secret scope
  `sttm_agent/anthropic_api_key`; the app service principal able to run the
  job and read/write those volumes — plus, since 2026-08-22 evening,
  `STTM_SYNC_JOB_NAME` / `STTM_RENDER_JOB_NAME` for the sync and
  render-only jobs. All are listed in `app.yaml`'s comment.
- **SharePoint has never been run against a real tenant.** The Graph
  transport and the sync are fully unit-tested against stubs (all
  offline) and every failure path is exercised, but no live Entra ID app
  registration has been used from this checkout. Unverified until someone
  runs `00_sharepoint_sync` against a real library: token acquisition,
  `Sites.Selected` READ consent actually granting what is needed, the
  site/drive resolution shape and eTag format on a real tenant. (There is
  no upload behaviour left to verify.)
- **The scheduled sync job has never been deployed (2026-08-22).**
  `resources/frd_sttm_sync_job.yml` parses and the notebook runs locally in
  both modes, but `databricks bundle validate` could not be run from this
  checkout (expired CLI token) and no workspace has executed it. The
  deployed App's "Sync now" needs `STTM_SYNC_JOB_NAME` to match the job's
  AS-DEPLOYED name (or `STTM_SYNC_JOB_ID`), and the service principal
  needs READ on `frd_raw` + `sttm_reference` to mirror them.
- **No bundle-deployed run has exercised the `notebooks/` shims.** The shim
  path is verified *locally* end-to-end only. `%run` resolution in a real
  workspace is the untested half. **Now on the demo critical path:** the
  deployed App's live runs execute the bundle job, so the first
  `databricks bundle run frd_sttm_pipeline` is the gating verification for
  the whole Friday demo — do it before wiring anything else on Monday.
- **The live path needs a real key in the workspace.** Databricks reads it
  from secret scope `sttm_agent/anthropic_api_key`, env var locally. Mock
  mode is gated on `not IS_DATABRICKS`, so a workspace run cannot silently
  fall back to mock — it fails instead, which is correct but means the
  secret must be in place before the App can do anything live.
- **Bundle defaults are dev-only** (`soham_workspace.sttm_agent`). Confirm
  the CLI targets the intended workspace and override per target before
  `databricks bundle deploy`.
