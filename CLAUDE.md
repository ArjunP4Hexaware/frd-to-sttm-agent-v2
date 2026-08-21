# FRD-to-STTM Agent — working notes

> **Read first, every session:** read
> `../amerihealth-project-master-context-document.md` in its entirety before
> working in this repo. It is the program-wide master context document
> (scope, timeline, all three agents, open gaps, and known-stale claims in
> this file). This file remains authoritative for this repo specifically.

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
- **Downstream (tool now exists; round trip UNVALIDATED):**
  `03_contract_build` emits `<doc_id>.contract.json`, which the CodeGen
  agent consumes as its FRD feed contract — that half works. CodeGen ALSO
  requires a workbook-derived **STTM mapping contract JSON**. As of
  2026-08-21 a committed tool does produce it —
  `codegen extract-sttm --workbook X.xlsx --frd-contract Y.json --out Z.json`
  in the code-gen-agent repo. (An earlier claim here that "no committed
  tool anywhere in the program produces it" was correct when written and
  is now stale.)

  **But nobody has run `04_sttm_render` output through that extractor**,
  and the shapes do not fully line up. `render_sheet_per_table` matches
  the extractor's sheet names (`FILE_DETAILS` / `VERSION_HISTORY` /
  `MAPPING-*`), row-1 band labels and row-2 header synonyms — clearly
  designed to. It emits **no `Comment` column** (→ `value_spec` empty) and
  **no `Recycle Flag` column** (→ the verbatim validation text is
  dropped), and that text is CodeGen's entire Layer-2 input, so a naive
  round trip loses the rules *silently*. `render_single_sheet` (CAQH
  dialect) is incompatible outright: no `MAPPING-` prefix, band label
  `Source Layout` vs the expected `Source File Layout`, different header
  dialect (`Field Name` / `Comments` / `Business Rule` / `Catalog`). The
  extractor is FLAT-only. Do not describe this hand-off as working until
  someone runs it end to end.

## Repo layout

```
notebooks/01..04_*.py   the four core entry points — dual-mode (plain local
                        scripts OR Databricks notebook tasks; IS_DATABRICKS
                        detection, widgets ↔ env vars, Spark ↔ deltalake)
notebooks/00_sharepoint_fetch.py    SharePoint library → frd_raw (read)
notebooks/05_sharepoint_publish.py  rendered/*.sttm.xlsx → SharePoint (write)
notebooks/_models.py, _local_tables.py, _mock_extractions.py,
  _contract_build.py, _sharepoint.py   thin re-export SHIMS — the real code
                        lives in src/frdsttm/; %run and local imports both
                        hit these
src/frdsttm/            models.py (FrdIngestionSpec, GatedAmbiguity,
                        HumanResolution), contract_build.py (enrich /
                        grounding_audit / gating), label_contract.py
                        (shared-label-contract loader), local_tables.py,
                        mock_extractions.py, live_extraction.py,
                        sharepoint.py (Microsoft Graph transport)
contracts/frd_label_contract.json   the versioned FRD label contract — now a
                        frozen input, no longer mirrored anywhere (see
                        "Upstream" above)
tests/                  114 tests, offline (no LLM/network/Spark);
                        run `pytest` for the live count rather than
                        trusting a number written down here
schema/sttm_extraction_schema.json   the extraction contract (mirrors models)
databricks.yml + resources/frd_sttm_job.yml   asset bundle, job frd_sttm_pipeline
review_app_react/       FastAPI + Vite/React review app + client demo
                        (Databricks App; its root requirements.txt is the
                        Apps deploy manifest). Three tabs: gated-ambiguity
                        review, mock upload flow (orchestration.py,
                        unchanged), and the client demo (backend/demo.py:
                        live 01→04 runs with backend-generated demo_<ts>
                        suffix insulation + zero-call replay of saved
                        artifact sets). See review_app_react/README.md.
local_dev_fixtures/     frd_raw/, sttm_reference/ inputs; outputs land here
demo_frd.docx / demo_sttm.xlsx   the tracked anonymized demo pair
tools/                  anonymization mapping + applier (mandated fixture path)
```

## Setup / run / test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,dev]"        # deps from pyproject.toml

pytest                               # 114 tests, offline

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
  Mock mode is gated on `MOCK_AVAILABLE` — false when `IS_DATABRICKS`
  (notebook task) OR `IS_DATABRICKS_APP` (`STTM_APP_MODE=databricks`, the
  deployed App, where `dbutils` is absent from a subprocess so the first flag
  alone would miss it). A workspace run can never silently skip extraction;
  in the App an explicit mock request raises rather than being ignored.
- The grounding audit is the quality gate (strict fields verbatim,
  advisory prose token-overlap). Never relax it to make a run pass.

## SharePoint / Microsoft Graph (added 2026-08-21)

The document library is the program's system of record: FRDs in, STTM
workbooks out. `src/frdsttm/sharepoint.py` is the whole transport.

**It is a seam at the edges, deliberately not inside 01-04.** `01` parses
every supported file in a directory; `04` writes a workbook to a volume.
SharePoint attaches before `01` (`00_sharepoint_fetch`) and after `04`
(`05_sharepoint_publish`). Keeping the network at the edge is what lets
01-04 stay offline and credential-free and keeps the suite network-free.
**Do not "simplify" this by calling Graph from inside 01** — that puts a
token lifetime and a network dependency inside the parsing stage.

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
  admin consent: `Sites.Selected` on the target site is preferred over
  tenant-wide `Files.ReadWrite.All`.
- **Fail-loud, both directions.** Missing config raises naming BOTH remedies
  rather than defaulting. A short download raises rather than leaving a
  truncated .docx for `01`. A missing library lists what the site actually
  has. There is no fallback to a stale local copy in either direction.
- **`05` is separate from `04` on purpose.** `04` re-runs every time a
  reviewer resolves an ambiguity, and a re-render is not automatically a
  re-publish — the human gate sits between them. Running `04` must never
  push a not-yet-approved workbook to the client's library.

  **DECIDED 2026-08-21, NOT YET IMPLEMENTED — the top open work item.**
  Arjun's decision: **publishing must be a manual button the reviewer
  presses in the review app.** Never automatic. As the bundle stands,
  `resources/frd_sttm_job.yml` still wires `sharepoint_publish` with
  `depends_on: render`, so a full job run *can* publish an unapproved
  workbook. To close it: remove that task from the job, and add a
  confirm-gated publish control to the demo tab that publishes one reviewed
  document. The transport, config and fail-loud paths already exist — this
  is wiring plus a button, not new plumbing.
- **Write scope is one folder.** `sharepoint_output_folder` is the only path
  this repo ever writes to. Keep the app registration's write grant scoped
  to it.
- **Review app picker** (`review_app_react/backend/sharepoint_routes.py`):
  a picked document is downloaded into the same demo-uploads directory an
  upload lands in and returned in `GET /api/demo/documents` shape, so it
  starts through the existing validated run path. There is deliberately no
  second "run from SharePoint" execution path to keep in sync. Status codes
  say whose problem it is: 503 not configured, 502 Graph refused, 400 bad
  request, 413 over the demo cap. The picker renders nothing when
  unconfigured.

## Designed, not built (decided 2026-08-21)

Carry these into the next session; none is implemented.

- **Manual publish button** — see the SharePoint section above. Highest
  priority, smallest change.
- **Duplicate-FRD detection.** Add `content_sha256` to `frd_documents` and
  short-circuit when an STTM already exists for that exact content. **Key on
  the content hash, not the filename** — FRDs get revised and
  same-name-new-content is the normal case. And **never silently skip**:
  surface it as a human decision ("an STTM for this exact content exists,
  generated <date>, published <where> — reuse or regenerate?"). Near-duplicates
  should show a diff rather than auto-skipping. Silently declining to produce
  an STTM is exactly the failure mode the rest of this pipeline exists to
  prevent.
- **Unity Catalog as the working store.** Once harvested from SharePoint, FRDs
  and STTMs live in UC. SharePoint remains the system of record for hand-off.
- **Historical corpus for quality.** Harvest ACFC's FRDs + STTMs, pair them
  deterministically, and use them as (1) an eval set — the highest-value use,
  since the 80% UAT gate currently cannot be measured against one golden pair,
  (2) retrieved few-shot exemplars, and (3) a mined data dictionary feeding
  `04_sttm_render`'s existing source-dictionary cross-check. Gated on the
  HIPAA/BAA data-handling review before any real document is harvested.
  **NOT fine-tuning** — the Claude API has no fine-tuning surface, and it would
  move ACFC's conventions into weights that cannot be inspected, cited, or
  corrected, which is the opposite of this repo's grounding doctrine. Full
  reasoning in the master context document §7a. Settled; do not re-open.

## Branching model

All development happens on `staging`. `main` is the deployment branch;
`staging` merges to `main` only after testing.

## Fixtures & data rules

Only the anonymized demo pair (`demo_frd.docx`, `demo_sttm.xlsx`) and the
demo replay set (`local_dev_fixtures/sttm_out_live_e2e_20260807b/`, the
post-fix live E2E run — offline replay for the demo app; see
docs/LIVE_E2E_2026-08-07.md) are tracked. **Real client documents must NEVER enter this repo** — extractions,
contracts, and rendered workbooks live in UC volumes (or gitignored
`local_dev_fixtures/`). The anonymization tooling in `tools/` is the
mandated path for any new fixture material. Mock extraction specs copy real
facts from the demo FRD's parsed markdown — they prove plumbing, not
extraction quality.

## Known gaps / cautions

- **The committed `.venv` is stale.** It was created at
  `/Users/arjunpillai/Desktop/frd-to-sttm-agent/`, before the repo moved under
  the `amerihealth-agents/` umbrella, so every console-script shebang
  (including `.venv/bin/pytest`) points at a path that no longer exists.
  `.venv/bin/python3 -m pytest` works. Recreate the venv when convenient.

- **The workbook→mapping-contract round trip has never been run.** The
  extractor exists in code-gen-agent (`codegen extract-sttm`), but
  `04_sttm_render` output has not been fed through it, and at least two
  columns it reads are not emitted — see Purpose above for the specifics.
  This is still the pipeline's biggest gap; what changed is that it is now
  an integration/validation gap rather than a missing tool.
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

### Deploy blockers — live Databricks App, Hexaware, by 2026-08-24

These are the specific things standing between the current state and the
current priority stated at the top. None has been executed from this
checkout; treat each as unproven until you have seen it work.

- **Databricks Apps deploy has never run from here.** Apps installs the
  app's root `review_app_react/requirements.txt` — **not** `pyproject.toml`.
  A dependency that exists only in `pyproject.toml` will be missing at
  runtime, and the failure surfaces in the deployed app, not locally.
  **Partly addressed 2026-08-21:** the manifest was short by exactly five —
  `anthropic`, `python-docx`, `pypdf`, `openpyxl`, `deltalake` — needed
  because the demo tab spawns `notebooks/0N_*.py` as SUBPROCESSES in the app
  container, where `dbutils` is not a global, so `IS_DATABRICKS` is False and
  they take the LOCAL storage path. Those are now listed and parity with
  `pyproject.toml` (core + local + ui, minus streamlit) is exact in both
  directions. The deploy itself is still unproven.
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
  **Still unverified:** the storage half. Artifacts will land on the
  container's ephemeral filesystem via the local `deltalake` path rather than
  in Unity Catalog, and will not survive a restart.
- **SharePoint has never been run against a real tenant.** The Graph
  transport is fully unit-tested against a stubbed transport (37 tests, all
  offline) and every failure path is exercised, but no live Entra ID app
  registration has been used from this checkout. Unverified until someone
  runs `00_sharepoint_fetch` against a real library: token acquisition,
  `Sites.Selected` consent actually granting what is needed, the site/drive
  resolution shape on a real tenant, and the upload's replace-existing
  behaviour.
- **No bundle-deployed run has exercised the `notebooks/` shims.** The shim
  path is verified *locally* end-to-end only. `%run` resolution in a real
  workspace is the untested half.
- **The live path needs a real key in the workspace.** Databricks reads it
  from secret scope `sttm_agent/anthropic_api_key`, env var locally. Mock
  mode is gated on `not IS_DATABRICKS`, so a workspace run cannot silently
  fall back to mock — it fails instead, which is correct but means the
  secret must be in place before the App can do anything live.
- **Bundle defaults are dev-only** (`soham_workspace.sttm_agent`). Confirm
  the CLI targets the intended workspace and override per target before
  `databricks bundle deploy`.
