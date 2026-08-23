# FRD-to-STTM Agent — Master Context Document

**Last verified: 2026-08-22, late evening — after TEMPLATE FILL (04 renders into the chosen template's own layout; §12 top entry), the start-up-sync + `FRD_`/`STTM_` naming change (IMPLEMENTED, offline-tested), the rule-placement / audit-row fix in 04 and the first successful CodeGen round trip on synthetic documents.** Read this end to end at the start
of any session in this repo (≈8–10 minutes; §§1–3 alone are the 2-minute
version). It is the single get-up-to-speed document for THIS agent only, and
it is **tracked in git deliberately** so it travels with every clone — unlike
the program-wide `amerihealth-project-master-context-document.md`, which
lives in the (non-git) umbrella folder `../amerihealth-agents/` on the
original dev Mac (this repo was moved out of that folder to sit beside it
on 2026-08-22) and must be copied by hand. This is a *derived* document: where it disagrees with
`CLAUDE.md`, a file in `docs/`, or the code, those win — but §12 lists the
places where I verified the other docs were stale.

**Maintenance rule:** if your work changes anything this document states,
update it in the same change set and bump the "Last verified" date.

---

## 1. Thirty-second orientation

This agent turns an approved **Functional Requirements Document** (.docx)
into a client-dialect **Source-to-Target Mapping workbook** (.xlsx) plus a
machine-readable **feed-level mapping contract** (JSON), with every extracted
fact audited against the source text and genuine ambiguities gated to a
human reviewer — never guessed.

Program context (one paragraph, all you need): this is agent 1 of a
**three**-agent AmeriHealth Caritas (ACFC) program — **FRD→STTM → CodeGen →
Code Review** (BRD→FRD and SQL Optimization were cut 2026-08-21). **Hexaware
builds; ACFC rebuilds** — this repo is the Hexaware-side reference
implementation, and anything a rebuilder cannot re-derive from the docs,
contracts, and config is a liability, so non-obvious decisions get written
down with reasons. **Arjun owns this repo**; Soham owns `code-gen-agent`.
The sibling repos are independent gits — never share a venv, never assume
their docs are current about this repo.

Design doctrine in one line: **the LLM proposes, deterministic code audits
and decides, a human resolves** — grounding checks, gates, verdicts,
similarity scores, and template choices are all computed in code; the model
makes exactly one call per document (stage 02 extraction).

## 2. Current priority

**Executive demo Monday 2026-08-24, from the Hexaware environment, full
feature set, no cuts** (Arjun, 2026-08-22 — superseding the earlier
"deploy Monday, present Friday 8/28" framing; the Friday executive
presentation of both agents still stands after it). Everything needed is on
`staging` as of commit `dd2c0eb` + `fe23849`, verified **offline only**.

### The ordered Monday checklist (Hexaware laptop)

1. Clone/pull `staging`. `python -m venv .venv && pip install -e
   ".[local,dev,ui]"`; `cd review_app_react/frontend && npm install && npm
   run build`. Fill `.env` from `.env.example`: the `SHAREPOINT_*` vars
   (tenant, client id/secret, host, site path, library, FRD folder,
   reference/STTM folder — there is no output folder), `ANTHROPIC_API_KEY`.
2. Offline sanity with zero client content:
   `python tools/make_synthetic_smoke_fixture.py`, then run notebooks
   01 → 03 → 04 (02 is skipped by design — §10).
3. **First SharePoint sync** against the 2 real FRD/STTM pairs — it now
   happens when the app STARTS (`start_sync_on_startup`, §12; or "Sync from
   SharePoint now…", or `databricks bundle run frd_sttm_sharepoint_sync`):
   every `FRD_*` / `STTM_*` file not yet in Unity Catalog is imported and
   paired by stem (`FRD_<name>` ↔ `STTM_<name>`; similarity as fallback).
   Zero model calls. Verify both pairs paired and that `frd_ignored` /
   `reference_ignored` in the sync summary are what you expect (a file
   outside the naming convention is counted, not synced). With no tenant
   yet, start-up falls back to a reindex of whatever was hand-placed.
4. **Calibrate `TEMPLATE_SINGLE_MIN`** — with a 2-document corpus and
   exclude-own on, each FRD has exactly ONE eligible template, so this one
   threshold decides single-vs-freeform. Record the outcome in
   `docs/TEMPLATE_ARCHITECTURE.md`.
5. First live regeneration of an FRD whose STTM exists ("FRDs already
   mapped" → View STTM → regenerate → billed confirm). This is the demo's
   money shot: template decision + automatic golden-pair eval against the
   client's real STTM.
6. `databricks bundle run frd_sttm_pipeline` — the gating check for the
   Apps deploy (§11) — and `databricks bundle run frd_sttm_sharepoint_sync`
   (the sync job — deployed as the manual / start-up target, NO schedule
   per the 2026-08-22 late decision) — then **`databricks bundle run
   frd_sttm_uc_governance`** (2026-08-23: creates the `sttm_audit` volume
   and tags every asset; grant the app SP READ+WRITE VOLUME on
   `sttm_audit` — without it every governed action in the deployed App is
   a 502 by design) — then `databricks apps deploy`. After the first live
   run: `GET /api/demo/audit` shows `run.started` / `run.finished` under
   your email, and `frd_sttm_runs` has `triggered_by` = you.
7. In parallel from step 1: chase the **Entra ID app registration**
   (`Sites.Selected` application permission, admin consent, per-site
   grant). External, blocking, and the long pole for everything SharePoint.

## 3. The pipeline

Six dual-mode Python files in `notebooks/` (plain local scripts AND
Databricks notebook tasks — `IS_DATABRICKS = "dbutils" in globals()` decides
widgets-vs-env-vars and Spark-vs-`deltalake`):

```
00_sharepoint_sync    SharePoint FRD + STTM folders → frd_raw + sttm_reference
                      volumes → corpus_index.json. Runs on APP START-UP and
                      "Sync now" (2026-08-22 late; NO schedule — in databricks
                      mode the app triggers the job frd_sttm_sharepoint_sync);
                      incremental (eTag/modified/size via sync_manifest.json);
                      `FRD_*` / `STTM_*` prefix filter, ignored files counted.
                      READ only.
00_sharepoint_fetch   library → frd_raw volume            (network at the edge;
                      the pipeline job's own fetch for a stand-alone run)
01_frd_ingest         docx/pdf/md/txt → markdown → frd_documents Delta   (deterministic)
02_extract            THE one model call per doc → extractions/<doc>.json
                      + retrieved-exemplar block in the prompt when the
                      corpus index exists (2026-08-22)
03_contract_build     validate (extra="forbid") → enrich (regex) → grounding
                      audit (strict verbatim / advisory ≥0.75 overlap) →
                      ambiguity gating → contracts/<doc>.contract.json
                      status ∈ {PASS, PASS_WITH_FLAGS, FAIL} — computed in code
04_sttm_render        TEMPLATE DECISION (single/amalgam/freeform, §4) →
                      dictionary cross-check → derive mappings (audit rows
                      from the template) → render INTO the lead template's
                      own layout (`layout_of` + `render_into_template`,
                      2026-08-22: its sheets/headers/styles, whatever
                      dialect; unfillable template columns blank + reported
                      in `_provenance.template_fill`; built-in renderers only
                      as the freeform fallback) with every FRD rule placed
                      on the row it names (`_provenance.rule_placement`) →
                      eval vs the doc's OWN reference (§4) → contract.v2.json
                      + phase5 report + frd_sttm_runs row
(no 05)               there is NO publish stage anywhere (2026-08-22): the
                      repo never writes to SharePoint — the reviewer uploads
                      the finished STTM; the sync pulls it back in and pairs it
```

Human review attaches between 03 and 04: gated ambiguities
(`attribution` / `disagreement` / `advisory_grounding` — exactly three
kinds) are resolved in the review app (`candidate_pick` / `none_of_these` /
`free_text` — exactly three resolution types, structurally enforced on both
client and server), then 04 re-runs and folds resolutions in, human first,
automatic cross-check second, every non-application recorded with a reason.
Ambiguity ids are stable hashes of kind+text+context — the join key for
saved resolutions; changing the scheme means migrating stored decisions
(`scripts/migrate_ambiguity_ids.py` did it once).

Code layout: notebooks consume `src/frdsttm/` through thin re-export shims
(`notebooks/_*.py`; `%run` in Databricks, plain import locally). **Edit
`src/frdsttm/`, never the shims.** Since 2026-08-22 the 01 parser
(`frd_parsing`) and the 04 workbook-dictionary parser
(`reference_workbooks`) are factored there too — one parser each, shared
with the app's corpus code, no drift.

## 4. The template architecture (2026-08-22)

Full rationale: `docs/TEMPLATE_ARCHITECTURE.md`. The one-paragraph version:

**Every approved FRD→STTM pair is a template.** The SharePoint sync
(`frdsttm/sync.py`; runs on app start-up + "Sync now" — no schedule,
2026-08-22 late) lands the
library's FRDs + STTMs in the volumes, pairs them deterministically (name
first — `FRD_<x>` ↔ `STTM_<x>` by the library's prefix convention; the older
`.sttm.xlsx` patterns and similarity after — `matched_by` on every pair), and writes
`corpus_index.json` (v2, `content_sha256` per document) into the reference
volume.
Generating an STTM retrieves the most similar approved pairs: their
conventions enter the extraction prompt as exemplars (02), and the
best-matching workbook(s) drive the rendered layout and dictionary (04) —
since 2026-08-22 literally: 04 renders INTO the lead template's own
workbook layout (`layout_of` → `render_into_template`), so a third client
dialect needs no code; the two built-in renderers are only the freeform
fallback — in one of exactly three computed modes — **single** (top match ≥
`template_single_min`), **amalgam** (top-k merged, first-wins per table key,
lead workbook's dialect), **freeform** (nothing matched → best-effort render
from the contract alone, FLAGGED, never a silent guess; an empty reference
dir is now a warned freeform state, not 04's old hard assert). A document's
**own** paired STTM is excluded from template candidacy and used as eval
ground truth (name-aligned `evaluate_cross_reference`) — so every
regeneration is an honest, automatic golden-pair eval. The decision +
scored evidence land in `_provenance.template_decision`, the phase5 report,
and the app's results panel.

Why retrieval is plain code (no embeddings vendor): the program is
Anthropic-only as model vendor and the Claude API has no embeddings surface;
at this corpus size, identifier-overlap + token cosine
(`src/frdsttm/similarity.py`) is free, offline-testable, and **explainable**
to SMEs ("84% of this workbook's columns appear in the FRD"). Exemplars
cannot inject facts: 03's grounding audit requires every strict field
verbatim in the *target* FRD, so exemplar-copied facts fail exactly like
invented ones. The sync makes **zero model calls** — ingest-all ≠
extract-all; extraction is billed only when a generation is requested.

Thresholds (`similarity.THRESHOLD_DEFAULTS`, resolved via
`thresholds_from(param)` — widgets in Databricks, same-name env vars
elsewhere): `template_single_min` 0.55, `template_amalgam_min` 0.30,
`template_top_k` 3, `pair_min` 0.35, `pair_high` 0.65. **Seeded on
synthetic fixtures, not calibrated** — §2 step 4.

## 5. The review app (`review_app_react/`)

FastAPI backend + Vite/React frontend, Databricks-Apps-shaped (`app.yaml`;
the Apps deploy installs `review_app_react/requirements.txt`, NOT
`pyproject.toml` — keep them in parity, verified three rounds now, latest
adding `pyarrow`). **ONE surface** since 2026-08-21, **reshaped 2026-08-22
(evening)**: the "Select FRD" flow reads the **corpus index** (what the sync
keeps in Unity Catalog) — "FRDs without an STTM" → Generate STTM →
confirm-gated billed run → results (extraction summary, gate, verdict,
eval, template decision, mappings, download + a hand-off note saying where
to upload); "FRDs already mapped" → View STTM (the approved workbook from
the reference volume + its SharePoint link) → two-step
**regenerate-anyway** into the same gate (golden-pair eval against the
existing STTM, which is never touched). No SharePoint lookup on the request
path; NO publish control — the reviewer uploads the finished workbook to
the library's STTM folder and the next sync pairs it. The corpus panel also
carries "Sync from SharePoint now…" / "Rebuild index…" (background, 202 +
polled state, one at a time) and the pairing stats. **The human-in-the-loop
step is IN the results view** (2026-08-22 evening, previously no rendered
UI after the review tab was removed): every gated item as a GatedItemCard
→ resolutions merged into the run's v1 contract (same format as the legacy
flow) → "Apply resolutions & re-render" re-runs stage 04 only (subprocess
locally; the `frd_sttm_render` job in databricks mode) and refreshes the
view. Nothing is re-extracted; nothing billed.

Runs are mode-switched on `STTM_APP_MODE`:

- `local` (default): 01→04 spawned as subprocesses with `demo_<ts>`
  env insulation (suffixed schema/volumes), provider pinned to `anthropic`,
  mock stripped, key injected from env/`.env`.
- `databricks` (the deployed App): the container never runs notebooks — live
  runs trigger the bundle job `frd_sttm_pipeline` via the Jobs API
  (`backend/jobs_runner.py`) with the same insulation as job parameters;
  artifacts land natively in UC (`sttm_out_app/<suffix>`) and are mirrored
  to container disk as a rehydratable cache. "Sync now" triggers the
  bundle-deployed **sync job** the same way, waits, then mirrors `frd_raw`
  + `sttm_reference` down to the container; reads re-mirror lazily after
  `STTM_CORPUS_REFRESH_SECONDS`. The same sync runs at app START-UP
  (`corpus_routes.start_sync_on_startup` from `app.py`'s lifespan — daemon
  thread, never blocks first paint; `STTM_SYNC_ON_STARTUP=0` disables).

There is no publish path (2026-08-22, supersedes the 2026-08-21 manual
button): the app never writes to SharePoint. The reviewer downloads the
draft, edits/approves, uploads it to the STTM folder as `STTM_<name>.xlsx`;
the next start-up or "Sync now" pairs it with `FRD_<name>.docx`.

Backend route families: `demo.py` (`/api/demo/...` config, documents,
uploads, runs + SSE, artifacts), `sharepoint_routes.py` (config probe +
shared client factory only), `corpus_routes.py` (corpus summary + sync
state, frds, sync/reindex, references/{name}, config),
`orchestration.py`/`app.py` (legacy review +
upload machinery, UI-less but tested). Import-order note: the deployed App
does not pip-install `frdsttm`; `sharepoint_routes` bootstraps `src/` onto
`sys.path` and `corpus_routes`' import order depends on it (marked
load-bearing in the file).

## 6. SharePoint / Microsoft Graph

`src/frdsttm/sharepoint.py` is the whole transport: app-only client
credentials, **standard library only** (`urllib` — zero Apps-manifest
footprint), attached at the edge (00 + the app's "Sync now"), never inside
01–04. **READ-ONLY by construction (2026-08-22):** the client has no upload
method, there is no output folder, and nothing in the repo writes to the
library — so the Entra ID grant only needs `Sites.Selected` READ on the one
site. `src/frdsttm/sync.py` keeps `frd_raw` (FRD folder) and
`sttm_reference` (`SHAREPOINT_REFERENCE_FOLDER`, default = the FRD folder;
also where reviewers upload finished STTMs) in step incrementally. Config
fails loudly naming both remedies (widget/env var AND the secret scope
`sttm_agent/sharepoint_client_secret`); the secret never reaches
`__repr__`/logs. **Never run against a real tenant** — §11.

## 7. Storage

| Where | What |
|---|---|
| UC volume `frd_raw` | source FRDs (the sync lands them here; 00_fetch too) |
| UC volume `sttm_reference` | approved STTM workbooks **+ `corpus_index.json` + `sync_manifest.json`** |
| UC volume `sttm_out` (job: `sttm_out_app/<suffix>`) | extractions/, contracts/, rendered/, reports/ |
| Delta `frd_documents`, `frd_contracts`, `frd_sttm_runs` | stage summaries (04 reads contract JSONs, NOT the Delta — the JSON carries human resolutions, the table predates them) |
| `local_dev_fixtures/` (gitignored) | the local mirror of all of the above; nothing document-shaped is ever tracked |

`frd_sttm_runs` gained `template_mode` and `eval_reference` columns
2026-08-22; `eval_pct = -1.0` / `eval_cells = 0` are the explicit "no eval"
sentinels (freeform with no paired reference).

## 8. Config doctrine

Every knob is a notebook widget with a same-name (uppercased) env-var
fallback; the bundle promotes shared ones to job parameters. A numeric
literal in rule logic is a bug. Notable knobs: `catalog`/`schema` (defaults
are DEV-ONLY `soham_workspace.sttm_agent` — override per target),
`reference_volume`, `exclude_own_reference` (default on; off is for
debugging, never demos), `sttm_exemplars` (`auto`/`on`/`off`) +
`sttm_exemplars_k`, the five similarity thresholds (§4), and the
`STTM_DEMO_*` app knobs. Provider seam: Anthropic-only; `STTM_LLM_PROVIDER`
unset/`anthropic`/`mock`, and every mock-vs-live conflict RAISES rather than
picking — in the deployed App a mock request is an error, never ignored
(a run that looks live but made no call is the worst failure in front of a
client). No secrets in the repo, ever.

## 9. Quality doctrine (do not relax)

- **Grounding audit is the quality gate.** Strict fields (identifiers,
  paths, patterns, tables) must appear verbatim in the FRD → any miss is
  FAIL. Advisory prose needs ≥0.75 token overlap → flags gate for review.
  Never lower a threshold, exclude a field, or accept invented prose to
  make a run pass.
- **Verdicts are computed in code, never by the model** — gate status,
  pairing confidence, template mode, eval percentages.
- **Fail loud, both directions.** No silent mock, no silent live, no
  fallback to stale copies, no fuzzy doc-id matching on any serving
  endpoint, empty-vs-unreadable always distinguishable (200+[] vs 5xx).
- **Fine-tuning is settled: no.** The Claude API has no fine-tuning
  surface, and the template architecture IS the sanctioned alternative
  (eval set + retrieved exemplars + dictionary reuse). Do not re-open.

## 10. Data rules & fixtures

**No FRD/STTM material — raw or derived — is tracked in this repo
(2026-08-22, Arjun).** The anonymized demo pair, all of
`local_dev_fixtures/`, and the tracked replay set were deleted and
`git rm`'d; the `.gitignore` re-includes were removed so nothing
document-shaped can return by default. They remain in git history — purge
undecided. Consequences, all deliberate: a fresh clone has no preloaded FRD,
no reference workbook, no offline replay; replay tests skip.

Replacements that keep development possible with zero client content:

- `tools/make_synthetic_smoke_fixture.py` — fully synthetic FRDs + matching
  reference workbooks + pre-written extraction JSONs whose every strict
  field grounds honestly. Offline smoke = generate, then run 01 → 03 → 04.
- **02 is skipped in that smoke on purpose**: the hand-authored mock specs
  (`src/frdsttm/mock_extractions.py`) are keyed to the REMOVED demo
  documents and raise on anything else (correct — fail loud). That module
  still embeds hand-copied facts from the two source FRDs; it is source
  code, not a document — flagged, kept.
- New fixture material must go through `tools/` anonymization AND a
  deliberate tracking decision. Real client documents NEVER enter the repo;
  they live in UC volumes or gitignored `local_dev_fixtures/`.

## 11. Verification state — proven vs unproven

**Proven:**

- Offline suite: **213 passed / 4 skipped**, zero network/credentials
  (2026-08-22 late evening — run `pytest` for the live count, never trust a
  written one).
- **04 → `codegen extract-sttm` round trip on the synthetic smoke (both
  documents, template mode), unpatched** — 2026-08-22 late evening; §11
  "unproven" item 4 for what is still open.
- Live E2E 2026-08-07 (`docs/LIVE_E2E_2026-08-07.md`, `claude-opus-4-8`):
  1 billed call ≈ $0.15, ~33.5s pipeline, gate PASS, strict grounding
  45/45, 0/50 hallucination sweep, eval 94.1% (3094/3288 cells) — the
  numbers stand; the replay artifacts that recorded them left the repo.
- Template architecture end-to-end on synthetic fixtures (2026-08-22):
  corpus pairing correct; single mode → right template, eval 100% (40/40);
  exclude-own → freeform with an honest 0% cross-eval and per-cell diffs.
- Transport layers fully unit-tested against stubs (Graph, Jobs API, SSE).

**Unproven — treat each as false until seen working:**

1. **SharePoint against a real tenant** (token, consent, site/drive
   resolution). Blocked on the Entra ID registration — which also means
   the start-up sync and prefix pairing are offline-tested only.
2. **Any Databricks execution from this checkout**: the bundle-job run
   through the `%run` shims, the Jobs-API demo path, the Apps deploy, SSE
   through the Apps proxy. First `databricks bundle run frd_sttm_pipeline`
   gates everything.
3. **The template architecture on real documents** + threshold calibration
   (§2 steps 3–5).
4. **The downstream round trip on REAL documents.** On the two synthetic
   smoke documents it now WORKS (2026-08-22 late evening — moved up from
   "never run"): 01→03→04 (template mode) → `codegen extract-sttm` →
   mapping contract, `value_spec` carrying the FRD rule on the row it
   names, audit columns peeled. 04 now emits `Comment` + `Recycle Flag`
   (rule placement recorded in `_provenance.rule_placement`) and derives
   audit rows from the template. Unproven on a real pair; CodeGen's
   `FrdContract` is stricter than ours (non-null `project_name`,
   `load_strategy` literal, delimiter for `txt`) and fails loudly when a
   real FRD omits one. The CAQH single-sheet dialect remains incompatible
   with the FLAT-only extractor (rules land in `Business Rule` + metadata
   for humans).
5. Bundle defaults are dev-only; the job's failure-notification email is a
   placeholder.

Environment quirk worth knowing: on the py3.14 venv the interpreter can
take tens of seconds in C finalizers (deltalake/pyarrow) after a stage's
work is fully done — inflates local per-stage wall clock only.

## 12. Decisions log (newest first; reasons matter more than dates)

- **2026-08-23 (Arjun; built with Claude Code on the personal Mac, deliberate
  one-session override of the no-work-code rule) — GOVERNANCE PASS: the
  agent becomes describable and auditable; behaviour unchanged.** Trigger:
  the client runs Collibra and the agent had no actor, no audit trail, a
  runs table that was overwritten every render, and no extraction
  provenance. What landed (full record in `docs/AI_GOVERNANCE.md`; repo
  CLAUDE.md "Governance" has the rules): (1) `backend/identity.py` — the
  caller is the Databricks Apps forwarded identity; databricks-mode
  requests without it are refused; (2) `backend/audit.py` — one JSON file
  per governed event in volume `sttm_audit` (fail-closed: no event, no
  action) for run start/finish, resolution, re-render, workbook download
  (= the hand-off, with the file's sha256), sync, upload; `GET
  /api/demo/audit`; (3) provenance by hash: `frd_documents.content_sha256`
  (01), `extraction_meta.json` sidecars (02: model, prompt/schema sha,
  usage, exemplars, job run id), `run_manifest.json` per artifact set
  (app), `frd_sttm_runs` now APPEND with `triggered_by / run_label /
  job_run_id / model / tokens / frd_sha256 / rendered_sha256` (04);
  `resolved_by` is finally set; `triggered_by` / `run_label` are job
  parameters on both job ymls; (4) `90_uc_governance` +
  `frd_sttm_uc_governance` — comments + tags on every UC asset, hand-run by
  an APPLY-TAG holder, placeholders that read as placeholders. Why this
  shape: Collibra (and UC itself) can only describe what exists — registry
  without trail is empty; tagging is a data-owner act, not a pipeline
  side-effect; the download is the real boundary crossing because the repo
  is read-only against SharePoint. Explicitly NOT decided in code (§8 of
  the doc): owner/steward names, whether the direct Anthropic API path is
  within the client's BAA posture, retention, the app-level access model,
  the git-history purge. New deploy prerequisite: the `sttm_audit` volume +
  READ/WRITE VOLUME for the app SP. Tests +21 (234), all offline.
- **2026-08-22 late evening (Arjun) — TEMPLATE FILL replaces the two
  hard-coded output dialects.** `reference_workbooks.layout_of` turns the
  parser's header-name observation into a write-side layout descriptor;
  `04.render_into_template` / `render_into_single_sheet_template` open the
  lead template workbook, keep its sheets / bands / headers / widths /
  styles, drop its data rows and write ours under the same headers via the
  logical roles; unknown template columns stay blank and are reported
  (`_provenance.template_fill`); unused sheets removed, extra feeds copied
  from the lead sheet; FILE_DETAILS / VERSION_HISTORY kept or created
  minimal + flagged. Why: the renderer was the last place a client's
  workbook shape was hard-coded; every approved pair already IS the layout.
  "Ad-lib" is allowed only for the shape, never a cell value — the doctrine
  applied to layout. The built-in renderers survive only as the freeform
  fallback. Round trip re-verified: both synthetic docs → `codegen
  extract-sttm` EXTRACTED (fixture templates made CodeGen-complete in the
  process). 213 tests.

- **2026-08-22 late evening (Arjun) — sync on app START-UP, not on a
  schedule; `FRD_` / `STTM_` naming.** Whenever the app starts, it lists
  the SharePoint library, imports every FRD and STTM not already in Unity
  Catalog, re-pairs and re-indexes ("Sync now" stays as the manual
  repeat). The cron schedule goes. Learned the same day: every FRD in the
  library is `FRD_<name>.docx` and every STTM is `STTM_<name>.xlsx`, so
  listing filters by prefix and pairing is by the stem after it (similarity
  remains the fallback). Why: the reviewer opens the app to work, so the
  freshest corpus at that moment is what matters; a background schedule
  adds a job to babysit and a lag to explain. **Implemented the same
  evening** — `start_sync_on_startup` + FastAPI lifespan, no `schedule` in
  the sync job, `name_key` strips prefixes, `sync_from_sharepoint` takes
  the prefixes (widgets / env, default on, ignored files counted); 206
  tests pass. The solution architecture slide shows the new trigger.

- **2026-08-22 late evening (Arjun, pre-hand-off review):** the rendered
  workbook dropped every FRD-stated validation/recycle rule (they lived
  only in the contract JSON — silent loss for the reviewer AND for CodeGen,
  whose Layer-2 input is that text). Fixed in 04: rules are placed on the
  row whose column they name (`Comment` → CodeGen `value_spec`; recycle →
  `Recycle Flag` `Y ( verbatim )`; single-sheet → `Business Rule`), and a
  rule naming no rendered column goes to a feed-level human-visible cell
  rather than a guessed row; every placement in `_provenance.rule_placement`
  + the phase5 report. Trailing `NA` audit rows are now flagged by the
  dictionary parser and derived from the TEMPLATE's targets (the one
  deliberate use of a reference's target side: a client convention, not an
  FRD fact). Result: first-ever successful `04 → codegen extract-sttm`
  round trip, on the synthetic smoke (both docs). Also: repo moved out of
  the umbrella folder (venv rebuilt; umbrella path fixed in CLAUDE.md);
  suite 195 passed / 4 skipped.
- **2026-08-22 evening (Arjun):** make the code match the two-slide
  architecture deck — (a) **automatic SharePoint sync** as a scheduled
  bundle job (`frd_sttm_sharepoint_sync`, `00_sharepoint_sync`,
  `frdsttm/sync.py`; incremental; also the app's "Sync now"); (b) **every
  write path to SharePoint removed** (05 notebook, publish endpoint + UI,
  `upload_file`, output folder) — the reviewer uploads the finished STTM
  themselves, the sync pulls it back and pairs it by name; READ grant only;
  (c) the **picker is the corpus index** (unmapped → generate; mapped →
  present + regenerate-anyway), `content_sha256` per document, `matched_by`
  per pair. Chosen over "keep the slides as target state" — Arjun's call.
  Later the same evening: (d) the **human-in-the-loop review panel** wired
  into the results view + render-only job `frd_sttm_render` (the slides'
  step 3 had no rendered UI); (e) `app.yaml` + Apps `requirements.txt`
  moved to the repo root (deploy from the repo root — the backend imports
  `src/frdsttm`); (f) runs accept every FRD type 01 parses, not only
  `.docx`; (g) the py3.14 interpreter-exit deadlock in 01–04 worked around
  with a guarded local-mode `os._exit(0)`; review_app_react/README.md
  rewritten. Suite 178 passed / 4 skipped.
- **2026-08-22 (Arjun):** template architecture built (§4) — reference
  STTMs imported via the sync; own-STTM excluded from candidacy, used
  for eval; deterministic retrieval, no embeddings vendor;
  regenerate-despite-existing shipped. Monday 2026-08-24 restated as an
  executive demo from the Hexaware environment, full feature set.
- **2026-08-22 (Arjun):** no client documents in any repo, raw or derived —
  the purge described in §10. History purge + force-push still undecided.
- **2026-08-21 (Arjun):** SharePoint-first app, no uploads; existing STTM
  presented, never silently skipped. Publishing is manual (job ends at
  render). Deployed-App runs go through the Jobs API, not container
  subprocesses. Program rescoped to three agents; label contract frozen;
  Genie Code rebuild dropped; fine-tuning ruled out (§9).
- **2026-08-07:** schema-in-prompt + client-side validation transport
  (server-side structured output rejects the schema: "compiled grammar too
  large"); streaming; no repair loop — a schema error is a real signal.
  `max_tokens` 64k.

## 13. Where to look next

| Question | Read |
|---|---|
| Repo conventions, layout, gaps | `CLAUDE.md` (authoritative for this repo) |
| Template architecture rationale | `docs/TEMPLATE_ARCHITECTURE.md` |
| From-scratch rebuild spec | `docs/NATIVE_REBUILD_SPEC.md`, `.claude/skills/frd-to-sttm-agent/SKILL.md` (self-contained; includes the DBU budget) |
| Live-run record + transport defects | `docs/LIVE_E2E_2026-08-07.md` |
| Demo choreography | `docs/DEMO_RUNBOOK.md` (predates the SharePoint-first surface — verify against §5) |
| App deploy prerequisites | `app.yaml` (repo root) comments, `CLAUDE.md` "Deploy blockers", `review_app_react/README.md` "Databricks Apps deployment" |
| What the agent looks like to a user | `context/FRD_to_STTM_Agent_Screens.html` (every screen, 2026-08-22) and `context/FRD_to_STTM_Agent_Architecture.pptx` (the two-slide ACFC-style deck; `scripts/build_architecture_deck.py`) |
| How the agent actually works, on one slide | `context/FRD_to_STTM_Agent_System_Architecture.pptx` (dark Blueprint-palette system diagram, 2026-08-22; regenerate with `scripts/build_architecture_onepager.py`) |
| Program-wide context | `../amerihealth-agents/amerihealth-project-master-context-document.md` — umbrella folder on the original Mac ONLY (this repo sits beside it since 2026-08-22); not in git |
