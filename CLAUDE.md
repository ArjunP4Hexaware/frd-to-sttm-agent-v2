# FRD-to-STTM Agent — working notes

> **TEMPORARY EXCEPTION 2026-08-25 → Friday 2026-08-28 (Arjun):
> development happens on the personal MacBook Air this week**, because
> Claude Code tokens in the Hexaware environment are exhausted until
> Friday. The Windows-only decision below is suspended, not reversed —
> it resumes Friday. While here: (1) there is NO venv on this machine
> (`~/.virtualenvs/` is empty; the repo-root `.venv` is gone) — recreate
> it OUTSIDE `~/Desktop` per the setup section before running anything;
> (2) the real client pair on this machine must live OUTSIDE the repo and
> be reached via `STTM_LOCAL_SOURCE_DIR` — see the 2026-08-25 finding
> under "Fixtures & data rules"; (3) carry commits back through `origin`,
> not by copying files between laptops.

> **Where the work happens — back to Windows-only, for good this time
> (Arjun, 2026-08-24, later the same day).** Earlier today this file
> described a SPLIT: client documents stayed on this Hexaware Windows
> laptop while architecture/synthetic-document work also happened on
> Arjun's personal MacBook Air. That split is now retired — **all
> Hexaware-related development work happens on this Windows laptop**
> (`C:\Users\2000198467\Desktop\frd-to-sttm-agent`) from here forward, not
> just the client-document half of it. This is a standing decision about
> where the work happens generally, not a one-off for this repo.
>
> Consequence: the MacBook-Air-specific material the split pull brought in
> — the iCloud/venv-hidden-`.pth` caveat, `~/.virtualenvs/frdsttm`, running
> `tools/make_local_source_fixture.py` against `~/Desktop/...` — describes
> that same-day detour and does not apply going forward. It is left below
> as history, not as a live setup path.
>
> The umbrella folder is present on this machine, beside this repo.

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

**ARCHITECTURE DECISION 2026-08-25 (Arjun) — THREE INPUTS, not one. The
agent must consume the FRD + the vendor data dictionary + the client's
naming/coding standards document. "FRD → STTM" as pitched is retired; the
real shape is (FRD + dictionary + standards) → STTM, with the FRD as the
spine that ties the other two together.** Reached after the 2026-08-24
presentation, by reading both real FRD/STTM pairs side by side:

- **The FRD carries the feed-level frame only.** File names/patterns,
  schema + table per layer, load strategy, frequency, the landing FOLDER
  (Structural Metadata › ADLS Location) and the DQ / recycle rules.
  It names ~2 columns; its own STTM has ~410 column rows.
  **CORRECTED 2026-08-26, then SOFTENED the same day when a THIRD real
  FRD arrived (`standards/FRD_STG_STD_SFMC_..._1005310.docx`, commit
  `9159447`): 2 of 3 real FRDs state the landing folder**, and the path
  has a derivable shape, `mftlanding/inbound/<domain>/<subdomain>/<vendor>`
  (SFMC: `mftlanding/inbound/member/outreach/salesforce_marketing_cloud`).
  SD's empty cell is an authoring omission, not the norm — so a missing
  ADLS Location is INFERABLE, and must be gated rather than filled
  silently. CAQH gives `mftlanding/inbound/member/tpl/caqh`
  (relative; no storage account or container, so it resolves only through
  the `STTM_LANDING_ROOTS` config of §7) and its STTM's `File Location`
  cell agrees verbatim. The SD FRD's `ADLS Location` row is EMPTY, and the
  adjacent `Inbound/outbound File Folder Path` says only `Inbound` — a
  direction, not a path. Consequence for the probe: it fills NOTHING for
  either document today — SD has headered files but no path to find them,
  CAQH has a path but a headerless file, so the probe yields a field count
  and no names. Build it as the §7 VALIDATOR (which the CAQH FRD demands:
  "Process should validate the source metadata as per Source Dictionary"),
  never as a source of field names.
  It points at the STTM for the field list ("refer to the mapping
  document" ×6) — circular. No prompt closes that gap. The sharpest
  instance, worth quoting in a room: the SD FRD's Structural Metadata
  table has a row literally named **`Source Data Dictionary`**, and its
  value is "File and field descriptions are mentioned in the mapping
  document" — the slot designed to hold the dictionary contains a pointer
  back to the STTM. It is also exactly where a `DICT_` reference belongs
  once vendors supply one. **Confirmed BOILERPLATE 2026-08-26:** the SFMC
  FRD's `Source Data Dictionary` row carries that sentence word-for-word
  too. Two of three real FRDs. It is the blank template's default text —
  the FRD process has a dictionary slot nobody ever fills.
- **The vendor data dictionary is where every source-side cell comes
  from**: column names, positions, types, lengths, null rules, valid
  ranges, descriptions, PHI. The STTM's `Description` / `Comment` /
  `Sample Value` / `DataType` columns are a transcription of it. It CANNOT
  be reconstructed from a file: descriptions and rules are never in the
  data, and one real feed is a headerless multi-record-type pipe file
  whose field names exist only in the vendor spec. The agent must never
  invent a field name or description — a blank + gated item, not a guess
  (the grounding-audit doctrine, applied to the source side).
- **The standards document is where every target-side rule comes from**,
  and the two real STTMs prove it varies by feed: one keeps source names
  lowercase as-is, the other renames to uppercase snake with a `TPL_`
  prefix and three fixed audit columns; stage is always `String` with
  numerics promoted to `Decimal(10,2)` / `Int` in standard; catalog per
  layer. Neither FRD states these rules. Today the agent borrows them
  implicitly from the matched template STTM — which is why it only looks
  right when regenerating an already-mapped FRD (self-referential).
- **A landing-zone probe is a FALLBACK and a validator, not an input.**
  Given `mftlanding` → `abfss://…` resolved by a one-line environment
  config (the FRD does not need to change), the agent can read a headered
  file with CODE (pyarrow/pandas schema inference, never the LLM over raw
  rows — PHI) to fill names / types / samples when no dictionary was
  attached, and to check a dictionary against a real file when both exist.
  It fills nothing for headerless files and never fills meaning. Whether a
  file is even present at STTM-authoring time is an open question for the
  BSAs.

Consequences and what was settled LATER THE SAME DAY (2026-08-25, Arjun):
- **The standards are TWO client documents — a NAMING standards doc and an
  ENGINEERING standards doc — and they are VERSIONED CONFIG, not a corpus
  kind and not a per-run upload.** Precedent: `contracts/frd_label_contract
  .json` + `frdsttm.label_contract` (loads at start-up, fails loudly if
  missing/unversioned, no hard-coded fallback). Target:
  `contracts/naming_standards.json` + `contracts/engineering_standards.json`,
  a `frdsttm.standards` loader modelled on `label_contract`, and
  `standards_sha256` in every run's provenance like `system_prompt_sha256`.
  NOT rules baked into Python (invisible, silently stale, not re-derivable
  in the ACFC rebuild). Arjun has both documents; they are client documents
  and never enter git — the config is a transcription of them. NEXT STEP:
  Arjun drops them in `sample_documents/` (`.gitignore` line 2 covers
  `*.xlsx`; check `*.docx`), then decide together which sections become
  which config keys.
- **Vendor dictionaries live in SharePoint → corpus kind `DICT_<name>.xlsx`**
  harvested by the sync beside `FRD_` / `STTM_`, paired by stem, shown in
  the picker; per-run upload only as the email fallback.
- **A worked-example dictionary exists:** `sample_documents/DICT_Medicare
  Expansion-MIDS - Socially Determined.xlsx` (built 2026-08-25 from the
  approved STTM's SOURCE band as a stand-in for the vendor's real spec;
  gitignored by `*.xlsx`; leaves with the Friday purge). Shape: `FILES`
  sheet + one sheet per file with `Position | Field Name | Data Type |
  Length | Required | Description | Allowed Values / Range | Example Value
  | PHI/PII`; 399 fields (86/267/46); contains NOTHING the client decides.
  Its README sheet states the provenance caveat (no lengths; "String" on
  every community-file field).
- **The golden STTM is not rule-typed (found building the dictionary):** in
  the community-risk sheet all 90 standard-layer `Decimal(10,2)` cells are
  exactly the rows whose SAMPLE had a fractional part (0 of 177 `String`
  rows did); a `_pct` column with sample `0` stayed `String`. So type
  promotion comes from the vendor type or a name rule, never the sample,
  and the golden-pair eval must report those rows as disagreements, not
  agent errors.
- A missing dictionary is a gated ambiguity naming the file, never a
  sparse render; `_provenance` records which input each cell came from.
- Still open: whether a sample file is normally in the landing zone when
  the STTM is written (decides how often the probe fills anything).
See the matching entry under "Designed, not built" and the full design in
docs/THREE_INPUT_ARCHITECTURE.md (2026-08-25; §5 standards, §6 ingress,
§8a the worked example, §11 the standards documents read).

**`standards/` — the client documents arrived 2026-08-26 (commit
`9159447`). FOUR files, only TWO of them standards.** Full read in
docs/THREE_INPUT_ARCHITECTURE.md §11. Note they are CLIENT DOCUMENTS and
are TRACKED, like `sample_documents/` — they go in the same Friday purge
(see "Fixtures & data rules"), and `standards/` is not in `.gitignore`
today either.

- `EDO Data Engineering Naming Standards.docx` — the naming input. 14
  vocabulary tables: data layers (STAGE→`STG`, Standard→`STD`), domains
  (MEMBER→`MBR`, CLAIMS→`CLM`), load strategy (Truncate & Load→`TRUNC`,
  Append→`INSRT`, Update Else Insert→`UPSRT`, Extracts→`EXTR`), product
  codes (Data Lake→`DLK`), frequency, region/LOB, pipeline type. **These
  decode the cells 04 borrows from the template today:** CAQH's catalog
  `PR_DLK` is the Data Lake product code, its schema `STG_MBR` is stage +
  member. Catalog and schema per layer are DERIVABLE, not copied.
- `EDO Data Engineering Coding Standards.docx` — the engineering input;
  mostly ADF/Databricks build practice (CodeGen's). Three lines are ours:
  *"Standard table should be created with proper datatype as per the
  source column datatype"* (independent confirmation that type promotion
  is a function of the SOURCE type, never the sample), the recycle-flag
  null check, and rejects → reject table + Production Support email.
- **NEITHER document contains the target COLUMN naming rule (upper snake,
  the `TPL_` prefix) or the audit columns** (`SRC_FILE_NAME`,
  `REC_CREATION_TIME`, `REC_UPDATED_TIME`) — grepped. They stop at
  object level. So §5's plan holds for catalogs/schemas/tables/load
  strategy and has NO written source for the column-level half. Ask where
  that is documented before deciding it is undocumented. One lead, one
  data point: `TPL_` matches CAQH's own `Sub-Domain = TPL`.
- `FRD_Enhanced_Metadata_Template (1).docx` — NOT a standard: the blank
  FRD BSAs author into, and so the canonical structure `01_frd_ingest`
  parses. Its Structural Metadata block is a FIXED 11 rows (Object/data
  Format, Target Schema, Target Table Name, Domain and Subdomain, Load
  Strategy STG/STD/Consumption, Archive Schedule, Source Data Dictionary,
  ADLS Location, Inbound File Folder Path) — the FRD's half of the
  division of labour, and where the `DICT_` reference belongs. **Gotcha:
  the requirement id `MDST231070` is HARDCODED IN THE TEMPLATE** and is
  identical in every real FRD — a template artifact, never a project id.
- `FRD_STG_STD_SFMC_..._1005310.docx` — NOT a standard: a THIRD real FRD
  (Salesforce Marketing Cloud, csv, `stg_mbr`→`mbr`). It is what softened
  the landing-path correction above, and it exposed a real CodeGen defect:
  its `Load Strategy STD` is **`Upsert`**, which CodeGen's `FrdContract`
  enum (`{'Truncate and Load','Append'}`) rejects before the workbook is
  read. The naming standards sanction `Update Else Insert`, so the ENUM is
  wrong, not the FRD — widen it on the CodeGen side.

**FRD picker for the 2026-08-24 demo (decided 2026-08-23, Arjun) — no code
change needed.** Both FRDs in the Hexaware SharePoint library must stay
selectable/generatable through the demo regardless of mapped status. This
is already true of the picker as it exists today: `GET
/api/demo/corpus/frds` (`corpus_routes.py`) returns every FRD with a
`paired` flag and never removes a mapped one from the response, and
`CorpusPanel.tsx` renders mapped FRDs in their own "FRDs already mapped"
section with a "View STTM" action plus a two-step regenerate-anyway
control that re-enters the same billed-run gate as a fresh generation.
There is no dedicated allow-list or override for specific documents (and,
checked 2026-08-23, none exists anywhere in the repo) — none is needed,
because the permissive behavior is already the default, not an exception
carved out for these two documents.

**Post-demo plan is a new restriction to build, not a reversion (Arjun,
2026-08-23).** The picker's current behavior — showing mapped FRDs with
view + regenerate-anyway, never hiding them — has been the only behavior
this codebase has ever had; there is no prior stricter mode to "revert" to.
The target state after 2026-08-24 is to add one: mapped FRDs stop being
offered for (re)generation and surface only their approved STTM. That gate
does not exist yet — see the corresponding bullet under "Designed, not
built" below once you're ready to scope it; treat it as new work on
`corpus_routes.py` (exclude/flag mapped FRDs out of the generatable set
server-side) and `CorpusPanel.tsx` (drop or lock the regenerate-anyway
control client-side), not as flipping a flag.

**Architecture decision 2026-08-22 (late evening, Arjun) — IMPLEMENTED the
same evening:** the SharePoint → Unity Catalog sync runs **when the app starts
up** (and on "Sync now"), not on a cron schedule. On start-up the backend
lists the library, imports every FRD and STTM that is not already in the
volumes, re-pairs and re-indexes. The library's naming convention is now
known: **every FRD is `FRD_<name>.docx`, every STTM is `STTM_<name>.xlsx`**,
so listing can filter by prefix and pairing is `FRD_X` ↔ `STTM_X` by the
stem after the prefix (similarity stays the fallback). How it is built:
(1) `corpus_routes.start_sync_on_startup()`, called from `app.py`'s FastAPI
lifespan — non-blocking daemon thread, the same worker as "Sync now", state
visible in the Corpus panel (`trigger: "startup"`); local mode with a wired
tenant → the sync in-process, local mode with NO tenant → a network-free
reindex, databricks mode → the sync job via the Jobs API then the mirror;
`STTM_SYNC_ON_STARTUP=0` disables it. (2) `resources/frd_sttm_sync_job.yml`
has NO `schedule` (the `sync_cron` / `sync_schedule_pause_status` variables
are gone); the job is the databricks-mode execution target only, or a hand
run. (3) `similarity.name_key` strips role tokens at both ends, so
`FRD_X` ↔ `STTM_X` name-pairs (the older `.sttm.xlsx` / `… STTM.xlsx`
shapes still do too; similarity last). (4) `sync.sync_from_sharepoint`
takes `frd_prefix` / `reference_prefix` — the notebook widgets
`frd_name_prefix` / `sttm_name_prefix` and the app env vars
`STTM_FRD_NAME_PREFIX` / `STTM_STTM_NAME_PREFIX` default them to `FRD_` /
`STTM_` (blank disables); files without the prefix are COUNTED in the
summary (`frd_ignored` / `reference_ignored`), never silently dropped.
Tests: `test_sync.py` (name_key + prefix filter), `test_corpus_routes.py`
(four start-up cases + the lifespan wiring). Unproven live like everything
SharePoint (no tenant yet).

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
  **Template fill (2026-08-22, later the same night):** 04 no longer
  writes from a hard-coded header list when a template matched — it renders
  INTO the lead template workbook's own layout (`reference_workbooks.layout_of`
  → `render_into_template` / `render_into_single_sheet_template`): sheets,
  band labels, headers, widths and styles are kept; the template's data rows
  (another FRD's) are removed; ours are written under the SAME headers via
  the logical roles the parser already recovers; a template column the
  contract knows nothing about stays BLANK and is listed in
  `_provenance.template_fill.unfilled_columns` (the ad-lib is the shape,
  never a cell); unused template sheets are removed, extra feeds get a copy
  of the lead sheet; FILE_DETAILS / VERSION_HISTORY are kept from the
  template or created minimal + flagged (CodeGen requires both). A third
  client dialect therefore needs no code. The two built-in renderers
  (`render_sheet_per_table` / `render_single_sheet`) remain ONLY as the
  freeform fallback (no template matched). Tests:
  tests/test_render_template_fill.py (an unseen dialect, a spare-sheet
  reuse, copy/remove, the CAQH metadata block, the freeform fallback).
  Consequence for the round trip: the output now mirrors the template, so a
  template that lacks a column CodeGen requires yields a workbook CodeGen
  rejects — loudly, and the synthetic fixture's templates were made
  CodeGen-complete (Sample Value / PHI Field / Mandatory Field, no Catalog
  in the stage band, FILE_DETAILS + VERSION_HISTORY) for that reason.
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
                        run on app START-UP + "Sync now" — no schedule
                        (2026-08-22 late); in databricks mode the app triggers
                        the job frd_sttm_sharepoint_sync; incremental via
                        sync_manifest.json; FRD_*/STTM_* prefix filter)
notebooks/00_sharepoint_fetch.py    SharePoint library → frd_raw (read; the
                        pipeline job's own fetch for a stand-alone full run)
                        — there is NO publish notebook: the repo never writes
                        to SharePoint (2026-08-22; 05_sharepoint_publish removed)
notebooks/90_uc_governance.py     Unity Catalog governance set-up (2026-08-23):
                        creates the audit volume, COMMENTs + TAGs every
                        table/volume the agent touches (owner, steward,
                        sensitivity, data class, retention) — run BY HAND via
                        `bundle run frd_sttm_uc_governance` by someone with
                        APPLY TAG; local mode prints the plan. Idempotent.
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
                        local_folder.py (2026-08-24: a local directory behind
                        the SAME client duck type, so the folder stand-in
                        reuses the sync engine rather than forking it),
                        sync.py (source → volumes → index, incremental;
                        ONE implementation for the job and the app);
                        since 2026-08-22 (docs/TEMPLATE_ARCHITECTURE.md):
                        frd_parsing.py + reference_workbooks.py (the 01/04
                        parsers, factored verbatim behind new shims),
                        similarity.py (deterministic FRD↔STTM scoring,
                        thresholds), corpus.py (corpus_index.json),
                        exemplars.py (retrieved-exemplar prompt blocks);
                        dictionary.py (2026-08-27: the VENDOR DATA DICTIONARY
                        parser — the third input's ingestion half. FILES sheet +
                        one field sheet per file; headers are FOUND not assumed;
                        STRUCTURE raises (no FILES sheet, no locatable header),
                        CONTENT gates (a missing field sheet yields an EMPTY
                        column list plus a named problem, never a guess); the
                        issued template's worked example rows are DROPPED and
                        COUNTED so a vendor who returns the template unedited
                        cannot look fine; unknown columns are kept in `extra`);
                        standards.py (2026-08-26: the two standards contracts —
                        abbreviate/schema_for/catalog_for/normalize_load_strategy/
                        promote_type/column_convention + standards_sha256)
context/                THE DECK HOME. **All three decks below were restyled
                        2026-08-27 (Arjun) into the ACFC HOUSE STYLE and are
                        built from scripts/acfc_theme.py — one palette, one set
                        of primitives, three build scripts. The "Signal" theme
                        (scripts/deck_assets/ONEPAGER_DESIGN.md) is RETIRED for
                        context/ and that file is kept as the record of the old
                        system, not as live guidance.** The style was sampled
                        LIVE from amerihealthcaritas.com, not guessed: #003DA5
                        primary blue, #0A2458 navy, #2D2D2D ink, #69B3E7 sky,
                        #F0F0F0 band, and #D2202F flag red taken from the logo
                        mark and used ONLY as the attention colour. Type is
                        Segoe UI (the site sets the licensed Area Normal; Segoe
                        is the nearest face shipping with Windows Office, so a
                        deck renders true with nothing to install). Geometry:
                        SQUARE CORNERS EVERYWHERE, no shadows, flat bands — the
                        site's buttons are 0px-radius outlined rectangles and
                        that is the loudest signal in their system. **The ACFC MARK
                        is CLEARED FOR USE (Arjun, 2026-08-27)** and is the
                        masthead device on all three decks and in the app —
                        `scripts/deck_assets/logos/acfc.png` (full colour) and
                        `acfc_white.png` (knockout), placed by
                        `acfc_theme.acfc_logo()`. Two hard-won placement rules,
                        both learned by rendering: never below ~0.9in / 90px
                        wide (the "Care is the heart of our work" line turns to
                        mud, and a half-legible logo reads as carelessness, not
                        branding), and never in colour on navy. A knockout was
                        tried in the solution deck's doctrine bar and in the
                        app footer and REMOVED from both for exactly that
                        reason — one placement done properly beats two.
                        `flag_bars` is kept for slots too small for the lockup,
                        not as a stand-in for a mark we may now use.
                        QA loop that found five overflowing slides on the first
                        pass and is worth repeating after any edit: build →
                        `soffice --headless --convert-to pdf` → pymupdf
                        `get_text("blocks")` → flag any block whose y1 clears
                        the footer rule. python-pptx renders no auto-fit, so
                        overflow is invisible until you render.

                        FRD-and-Dictionary-Input-Requirements.pptx (NEW
                        2026-08-27; scripts/build_input_requirements_deck.py) —
                        **THREE SLIDES, HARD CAP (Arjun): nobody presents 14.**
                        It was cut from 14 by one rule — keep what a room cannot
                        reconstruct for itself — and the cut material moved into
                        SPEAKER NOTES rather than being deleted, so the deck
                        reads in three slides and still answers a follow-up. If
                        a slide comes back, another has to leave. (1) the three
                        inputs + the one ask; (2) the FRD's eleven Structural
                        Metadata rows with measured fill rates; (3) both VDD
                        sheets, column by column, marked for what a data file
                        could and could not tell us. FOR THE CLIENT'S BSAs: what
                        to put in the FRD
                        (the same eleven Structural Metadata rows, no template
                        change) and what the vendor data dictionary must
                        contain. Its force is that every number is measured,
                        not asserted — per-row fill rates across the three real
                        FRDs, the 0/3 that name a dictionary, the 399/399 and
                        22/115 column-rule figures from
                        contracts/naming_standards.json. Slide 13 is the ask:
                        target column naming is inferred per feed and the
                        missing input is READ ACCESS to
                        information_schema.columns, not another document.
                        CAUTION: this deck is DERIVED from client documents
                        (verbatim template row names, three FRDs' fill state).
                        No person names or e-mails appear on any slide, but it
                        belongs in the same conversation as the sample_documents
                        / standards Friday purge — decide deliberately, do not
                        let it ride on "it's only a deck".

                        FRD-to-STTM-Agent-Solution-Architecture.pptx (ONE slide;
                        scripts/build_architecture_onepager.py) — REDESIGNED
                        2026-08-27 on TWO axes: the ACFC style above, AND the
                        architecture. Band A is now THREE INPUTS (FRD ·
                        DICT_<feed>.xlsx · contracts/*.json) plus the approved
                        STTM as template+golden, each carrying a status chip —
                        the standards contracts say LIVE, the vendor dictionary
                        says DESIGNED · NOT BUILT, because it is (grepped: no
                        DICT_ handling exists outside scripts/ and templates/).
                        Band B is the real change: THREE INTERCHANGEABLE
                        LOADERS behind one duck type (SharePoint — not yet
                        wired; local folder; direct volume push — in use today)
                        all landing in Unity Catalog, and the slide states
                        plainly that **the review app reads Unity Catalog and
                        SharePoint is not on the request path** (verified in
                        corpus_routes: IS_DATABRICKS_APP short-circuits to
                        jobs_runner.mirror_corpus and _source_client is never
                        consulted there).

                        FRD-to-STTM-Agent-Data-Governance-Architecture.pptx (ONE
                        slide for a NON-TECHNICAL reader;
                        scripts/build_governance_onepager.py) — same 2026-08-27
                        double redesign. Still reads LEFT→RIGHT on purpose (four
                        stations in document order, audit bar beneath, Collibra
                        register column on the right — Arjun 2026-08-23: NOT the
                        solution deck's top-down bands). Updated: station 01 now
                        says three inputs enter UNITY CATALOG whichever loader
                        brought them, and station 03's provenance includes
                        standards_sha256. Added a "still open — for the client,
                        not for code" block so the slide cannot be read as
                        claiming docs/AI_GOVERNANCE.md §8 is settled.

                        FRD_to_STTM_Agent_Architecture.pptx (the older two-slide
                        deck; scripts/build_architecture_deck.py) — NOT restyled;
                        still Signal-themed. Restyle or retire it before showing
                        it beside the three above.
                        FRD_to_STTM_Agent_Screens.html (REBUILT 2026-08-27 by
                        scripts/build_screens_doc.py, replacing the 2026-08-22
                        version which predated both the ACFC restyle and the
                        dictionary work) — the SCREEN CATALOGUE: every page a
                        reviewer can reach, the state that produces it, what
                        they can do there, and the backend call behind it. One
                        self-contained file, every shot inlined as a data URI.
                        RULE: every image is a REAL capture of the running app
                        against the synthetic corpus. States needing a billed
                        call or a wired tenant (run-in-progress, the HITL review
                        panel, sync running/failed) are listed in a final
                        section, NAMED not illustrated — a catalogue that
                        quietly includes invented screens is worse than one with
                        honest gaps. Regenerate: drive the app in a browser,
                        then `python scripts/build_screens_doc.py <shots dir>`.
                        No RAW client documents — ever.
templates/DICT_TEMPLATE_v1.3.xlsx   the blank vendor data dictionary issued to a
                        vendor, built by scripts/build_dict_template.py. FILES
                        sheet + one field sheet per file; the first 9 columns of
                        each are frozen so ONE parser reads both this and the
                        worked example. Gitignored by `.gitignore` line 2
                        (`*.xlsx`) — regenerate it, never hunt for it in git.
                        **v1.3 (2026-08-27) added the two columns that stood
                        between "a filled-in dictionary" and "an STTM
                        generatable from the FRD + VDD ALONE":**
                        `Null Representation` (FILES) — the FRD's reject rules
                        say "when MEMBER_ID is NULL" without saying what null
                        LOOKS LIKE in a delimited file (empty field? the text
                        NULL? spaces?), and guessing either rejects good records
                        or loads bad ones; and `Key / Uniqueness` (field
                        sheets) — an Upsert load strategy needs a match key, and
                        the FRD's own Business Key / Primary Key rows read "NA"
                        on every real document, so the vendor is the only one
                        who knows. Watch the EXAMPLE ROWS when adding a column:
                        they are positional lists, and inserting a header
                        without inserting its value shifts every later cell by
                        one (which silently un-marked a template example row and
                        made the blank template parse as a real file).

scripts/build_vdd_from_sttm.py   derives a WORKED VDD from an approved STTM's
                        SOURCE band — the band that is itself a transcription of
                        the vendor's spec. Replaces the hand-built SD example
                        (2026-08-25), which could not be reproduced; both worked
                        examples now come from a command. Reproduces it exactly:
                        3 files, 399 columns (86/267/46). Three rules earned the
                        hard way: read only the SOURCE band (both workbooks have
                        a SECOND `DataType` in the STAGE band — reading past the
                        boundary takes the TARGET type as the vendor's); drop
                        audit rows (source "NA" — those are the CLIENT's
                        columns and must never appear in a document a VENDOR
                        fills in); and assign file names GLOBALLY best-first,
                        never greedily in sheet order, with a tie refusing to
                        decide. **The output is a worked example, NOT evidence,
                        and it is CIRCULAR for evaluation** — a dictionary
                        derived from an STTM must never score a run against that
                        same STTM. The README sheet says so inside the workbook.
contracts/frd_label_contract.json   the versioned FRD label contract — now a
                        frozen input, no longer mirrored anywhere (see
                        "Upstream" above)
contracts/naming_standards.json + contracts/engineering_standards.json
                        (BUILT 2026-08-26) the client's two standards
                        documents transcribed into versioned config, loaded by
                        src/frdsttm/standards.py on the label_contract model
                        (fails loudly, no fallback). See the Standards section
                        below for what is STATED vs OBSERVED vs UNSOURCED.
tests/                  offline (no LLM/network/Spark); 234 as of
                        2026-08-23 (+21 governance: identity, audit trail,
                        provenance, UC plan) —
                        run `pytest` for the live count rather than trusting
                        a number here
schema/sttm_extraction_schema.json   the extraction contract (mirrors models)
databricks.yml + resources/frd_sttm_job.yml   asset bundle, job frd_sttm_pipeline
resources/frd_sttm_sync_job.yml   the sync job — NO schedule (2026-08-22 late):
                        the databricks-mode target of the app's start-up sync
                        and "Sync now", or a hand `bundle run`
resources/frd_sttm_render_job.yml the render-only job (stage 04 over an existing
                        run suffix) that the human-in-the-loop re-render triggers
                        in databricks mode — no re-extraction, nothing billed
resources/frd_sttm_governance_job.yml  the governance set-up job (90_uc_governance),
                        hand-run, no schedule, not triggered by the app
docs/THREE_INPUT_ARCHITECTURE.md  the 2026-08-25 design: FRD + vendor
                        dictionary + standards doc; target data flow, the
                        source-layout and standards contracts, ingress options,
                        the probe, gating/provenance rules, client questions,
                        build order. DESIGN ONLY — nothing built.
docs/AI_GOVERNANCE.md   the governance record (2026-08-23): AI asset/model
                        card, data flow, data inventory + classification,
                        controls map (each control → code → how to verify),
                        the provenance chain, audit-trail SQL, what to register
                        in the client's Collibra, and the OPEN human decisions
                        (owner/steward, Anthropic data path/BAA, retention,
                        access model, git-history purge). Keep it current.
review_app_react/backend/identity.py + audit.py   (2026-08-23) who-is-calling
                        (Databricks Apps X-Forwarded-* headers; 401 without them
                        in databricks mode) and the append-only audit trail (one
                        JSON per event in volume STTM_AUDIT_VOLUME, fail-closed);
                        see the Governance section below
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
tools/                  anonymization mapping + applier (mandated fixture path);
                        make_synthetic_smoke_fixture.py (synthetic VOLUMES);
                        make_local_source_fixture.py (2026-08-24: the
                        synthetic SOURCE FOLDER upstream of them, so the
                        local-folder path is testable with no client
                        documents and no Graph access);
                        push_local_source_to_volumes.py (2026-08-24: laptop
                        folder -> UC volumes, creating them if absent -- the
                        bridge a DEPLOYED App needs, since it cannot read
                        your filesystem)
```

## Setup / run / test

```bash
python -m venv .venv && source .venv/bin/activate
# On the MacBook Air the venv must live OUTSIDE ~/Desktop (iCloud hides it
# and Python 3.14 then ignores the editable-install .pth — see the
# "Where the work happens" note at the top):
#     python3 -m venv ~/.virtualenvs/frdsttm && source ~/.virtualenvs/frdsttm/bin/activate
pip install -e ".[local,dev,ui]"     # deps from pyproject.toml; ui gives the
                                     # review-app backend its FastAPI tests

pytest                               # offline; run it for the live count

# Review app against a LOCAL documents folder (the 2026-08-24 SharePoint
# stand-in), with synthetic documents:
python tools/make_local_source_fixture.py ~/Desktop/frd-to-sttm-demo-document
cd review_app_react/frontend && npm install && npm run build && cd -
set -a; . ./.env; set +a             # STTM_LOCAL_SOURCE_DIR points at the folder
python review_app_react/backend/app.py                   # http://127.0.0.1:8000

# HEXAWARE LAPTOP ONLY -- the machine with the real client documents. A
# DEPLOYED App cannot read that folder (it runs inside Databricks), so push
# the folder into the volumes it DOES read. Creates missing volumes via the
# SDK; no warehouse, no job, no cluster, so no compute cost.
databricks auth login --host https://adb-7405616719878880.0.azuredatabricks.net
python tools/push_local_source_to_volumes.py \
    ~/Desktop/frd-to-sttm-agent-documents --all-volumes --dry-run   # inspect
python tools/push_local_source_to_volumes.py \
    ~/Desktop/frd-to-sttm-agent-documents --all-volumes             # then push

# Deploy the App itself (the --include is required; see review_app_react/README.md)
cd review_app_react/frontend && npm install && npm run build && cd -
databricks sync . /Workspace/Users/<you>/frd-to-sttm-agent \
    --exclude '.venv' --exclude 'node_modules' --exclude 'local_dev_fixtures' \
    --include 'review_app_react/frontend/dist/**'
databricks apps start frd-sttm-review-app     # deploy needs it RUNNING first
databricks apps deploy frd-sttm-review-app \
    --source-code-path /Workspace/Users/<you>/frd-to-sttm-agent
# ... and when finished, STOP it -- Apps bill per hour of running compute:
databricks apps stop frd-sttm-review-app

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
`sttm_agent/anthropic_api_key` in Databricks) — UNLESS
`STTM_LLM_PROVIDER=databricks`, which needs neither (2026-08-24). Bundle:
`databricks bundle validate|deploy|run frd_sttm_pipeline -t dev` (and
`run frd_sttm_sharepoint_sync` for the sync) — verify the CLI targets the
intended workspace first, and override the dev-only
`arjun_workspace.sttm_agent` defaults per target (was `soham_workspace` —
see "Deploy blockers" below; that catalog is not accessible from Arjun's
identity at all). The sync job's schedule
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
  (`STTM_MOCK_EXTRACTION` decides mock vs Anthropic); `mock` / `anthropic` /
  `databricks` select explicitly. **The program is still Anthropic-only as
  model VENDOR** — `databricks` (added 2026-08-24) serves the same Claude
  models through this workspace's Foundation Model APIs, so what changes is
  the front door, the credential and who is billed, not the vendor. It needs
  NO Anthropic key and NO secret scope: the workspace credential
  authenticates. Both providers are built by ONE function,
  `frdsttm.live_extraction.build_live_client`, and both return the same
  `anthropic.Anthropic` type, so `extract_live` and everything downstream is
  provider-agnostic. Model ids map 1:1 (`claude-opus-5` ->
  `databricks-claude-opus-5`) and the banner prints the resolved name.
  The default model is `claude-opus-5` (2026-08-24) and `app.yaml` pins
  `STTM_LLM_PROVIDER=databricks` + `MODEL=claude-opus-5`, so the deployed App
  needs no Anthropic key.
  Setting a live provider while
  `STTM_MOCK_EXTRACTION` is also set RAISES rather than silently picking.
  Mock mode is gated on `MOCK_AVAILABLE` — false when `IS_DATABRICKS`
  (notebook task) OR `IS_DATABRICKS_APP` (`STTM_APP_MODE=databricks`, the
  deployed App, where `dbutils` is absent from a subprocess so the first flag
  alone would miss it). A workspace run can never silently skip extraction;
  in the App an explicit mock request raises rather than being ignored.
- The grounding audit is the quality gate (strict fields verbatim,
  advisory prose token-overlap). Never relax it to make a run pass.

## Standards contracts (BUILT 2026-08-26 — src/frdsttm/standards.py)

The client's naming + engineering standards are now versioned config, not
an implicit borrow from whichever reference workbook matched. Loaded at
import like `label_contract`: missing or unversioned raises
`StandardsError` naming the path, no hardcoded fallback.
`standards_sha256()` (over canonical JSON of BOTH contracts) belongs in
every run's provenance beside `system_prompt_sha256` — NOT yet wired into
02/04's manifests, which is the next step.

**Every value carries its confidence, and the API enforces it:**

- **STATED** (in a client document) — the load-strategy / layer / domain /
  product / frequency vocabularies; type promotion from the SOURCE
  datatype; the recycle null-check; rejects → reject table + Production
  Support email; the run-control table columns.
- **OBSERVED** (inferred from the mapped STTMs) — schema per layer
  (`STG_{domain}` / `{domain}`), stage default `String`, the vendor-type →
  standard-type table. `derivation_status()` returns this so
  `_provenance` can show a reviewer which is which.
- **OBSERVED_SINGLE_PAIR** — the catalogs. `PR_DLK` and `PR_STD` come from
  ONE workbook and are not even built the same way (`PR_` + a PRODUCT code
  vs `PR_` + a LAYER abbreviation), so `catalog_for()` answers only for
  `stage`/`standard` and returns None for any other layer rather than
  generalising.
- **PARTIALLY_SOURCED** — the target COLUMN rules. Neither document states
  them, so v1.0.0 gated the lot; **v1.1.0 (2026-08-26) split them by
  MEASURING each against the two mapped STTMs** rather than asserting:
  - `as_is` → **DERIVABLE**. Target column == source column on **399 of 399**
    non-audit SD rows. `column_convention("as_is")` needs no opt-in —
    applying a rule that reproduces the workbook exactly is a measurement,
    not a guess.
  - `prefixed_upper_snake` → **NOT_DERIVABLE**. `TPL_` + UPPER_SNAKE(source)
    reproduces **22 of 115** CAQH rows; 46 distinct word substitutions and 58
    of the 93 misses change the WORD COUNT. **These are not abbreviations —
    they are an existing warehouse vocabulary**: `TPL_MEME_ID`,
    `TPL_SBSB_IND`, `GRP` are Facets column names, and the CAQH FRD names
    Facets as the incumbent. So the missing input is a TERM CATALOG, not a
    rule document — `information_schema.columns` over the existing
    `PR_STD`/`PR_DLK` tables, which needs read access rather than a client
    meeting. Still gated until that exists.
  - **Audit columns moved OUT of the conventions** — they are consistent
    across BOTH pairs once segment role is accounted for. `core` (3:
    SRC_FILE_NAME / REC_CREATION_TIME / REC_UPDATED_TIME) on every table of
    both workbooks in both layers; `LOB` on **data-bearing tables only**
    (CAQH's DTL and all three SD tables, absent from CAQH's HDR/TRL — a line
    of business is meaningless on a control record); `FILE_TYPE`
    OBSERVED_SINGLE_PAIR, off by default. **A note in this repo previously
    claimed the two workbooks disagreed (3 vs 4). They do not** — that came
    from reading only CAQH's header segment.
  `confirmed_by` is still null; set it only when the client confirms, never
  to make a run quieter. `default_convention` is deliberately null —
  defaulting would reintroduce the template borrow, and which convention a
  given FRD uses is an OPEN CLIENT QUESTION.

**A `None` return is a real answer meaning GATE, never a reason to invent.**
`abbreviate("domains", "sdoh")` is None because the SD FRD's own domain is
absent from the client's table — a real gap, not a typo. Callers fall
through to the FRD's stated value and gate.

**WIRED INTO THE PIPELINE 2026-08-26 (steps 1 + 2):**

- **Provenance.** `standards_sha256()` is on 02's `extraction_meta` sidecar
  beside `system_prompt_sha256`/`schema_sha256`, and on 04's `frd_sttm_runs`
  row (new nullable column `standards_sha256`; `mergeSchema` carries older
  tables forward). 04 also writes `_provenance.standards` (both versions, the
  hash, and `column_rules_sourced`) onto the v2 contract.
- **04 consumes the contracts.** New `apply_standards_targets(contract,
  feed_match)` runs immediately BEFORE `derive_field_mappings` (ordering is
  load-bearing — the per-field rows must carry the filled values, and a test
  pins it). Three rules:
  1. **the FRD always wins**, per ATTRIBUTE not per layer — a stated schema
     with an unstated catalog fills only the catalog;
  2. every fill is recorded in `_provenance.standards_fill` with the
     contract's own confidence (`OBSERVED` for schema,
     `OBSERVED_SINGLE_PAIR` for the catalogs) so a reviewer can tell a client
     rule from an inference this repo made;
  3. an underivable value stays **NULL and is gated** (`kind:
     "standards_gap"`, options = the client's own domain vocabulary), never
     invented. The rows still render — a gated cell, never a sparse workbook.

  Verified end-to-end on the synthetic smoke: those FRDs state catalog and
  schema, so nothing is filled and nothing is gated — rule 1 holding on a
  real run, not just in tests.

**NOT done yet:** the review app's `run_manifest.json` does not carry
`standards_sha256`; `column_convention()` is still unused by 04, so target
COLUMN names and audit rows continue to come from the template — that is the
next step and it is blocked on the client confirming the column rules (see
UNSOURCED above).

Tests: `tests/test_standards.py` (56) + `tests/test_render_standards.py` (15);
suite now 362 passed / 4 skipped.

## TWO INPUTS — the standards are IN the agent (Arjun, 2026-08-27)

**The agent is given exactly two documents per feed: the FRD and the vendor
data dictionary.** Everything else it brings itself. State it that way
everywhere — deck, app, docs — because the earlier "three inputs" framing was
read in the room as "three things you must supply", which is both wrong and
the opposite of the point.

- The client's **naming + engineering standards are versioned config the agent
  ships with** (`contracts/naming_standards.json`, `engineering_standards.json`,
  loaded by `frdsttm.standards`, hashed into every run as `standards_sha256`).
  Nobody attaches them to a feed. They were only ever an "input" in the sense
  that the agent must know them — which is what transcribing them into
  contracts settled on 2026-08-26.
- The **approved-STTM corpus** is likewise the agent's own: it maintains it,
  and uses it as layout template + golden eval. Not something handed over.
- So the decks draw both INSIDE the platform, under "BUILT INTO THE AGENT",
  and the input row has exactly two tiles.

## DOES THE VDD TEMPLATE WORK? — MEASURED 2026-08-27

Asked directly, answered by counting every column of both real STTMs and
asking which input supplies it. **The template is DONE. It is not what stands
between the agent and a generated STTM.**

| feed | convention | VDD | standards | FRD | not covered |
|---|---|---|---|---|---|
| SD (indiv risk) | `as_is` | 12 | 2 | 3 | **0** |
| CAQH | `prefixed_upper_snake` | 17 | 4 | 6 | **1 — `ColumnName`** |

* **The source side is fully covered by the template**, on both feeds and both
  dialects. Every source cell a real STTM carries — name, type, length,
  required, description, allowed values, sample, PHI, position, segment,
  fixed-width offsets, primary key — has a column in v1.3.
* **SD-style feeds are 100% generatable from FRD + VDD**, because `as_is`
  makes the target column name a copy of the source name (measured 399/399).
* **CAQH-style feeds are covered except the target COLUMN NAME**, which
  appears twice (stage band and standard band) and is the only cell neither
  document supplies.

**No addition to the template can close that gap, and adding one would be a
mistake.** The target column name is a TARGET-side decision — `TPL_MEME_ID`
is what ACFC's own warehouse calls a member key. Asking a vendor "what should
ACFC call this column?" is a question they cannot answer and should not be
handed. The fix is a term catalog from the warehouse
(`information_schema.columns` over `PR_STD`/`PR_DLK`, or the approved-STTM
corpus the agent already syncs) — see the column-rules section above.

So: stop iterating on the template. Issue it.

## THE ELIGIBILITY RULE — what may be generated (Arjun, 2026-08-27)

**An FRD may be run only when it has a matching vendor data dictionary AND
does not already have an approved STTM.** One rule, one implementation:
`frdsttm.corpus.eligibility_for` / `eligibility_of`, materialised into the
corpus index as `eligibility` + `generatable` (index v4). Three verdicts,
exactly one generatable:

| verdict | VDD | STTM | generatable |
|---|---|---|---|
| `ready` | yes | no | **yes** |
| `mapped` | either | yes | no |
| `no_dictionary` | no | no | no |

`mapped` beats `no_dictionary` deliberately: an FRD that already has an STTM
has nothing to wait for, and telling a reviewer to chase a vendor for a
mapped feed is noise.

- **Enforced SERVER-SIDE**, in `demo.start_run` → `_check_eligible`, not only
  by hiding a button. Hiding a control is presentation; this is a rule about
  spending money and about not regenerating over a system of record. A caller
  with the URL gets a 400 naming the reason.
- **The gate's SCOPE is narrow, and two defaults are deliberately opposite.**
  `_check_eligible` lets through an absent corpus index (local dev, the
  offline smoke) AND a doc_id the index does not list (an uploaded or
  hand-staged document — "has a dictionary and no STTM" is not a question you
  can ask about a document the corpus never paired). It therefore reads
  `index["eligibility"].get(doc_id)` directly, NOT `eligibility_of`, which is
  conservative by design so the picker never renders a live button for an
  unknown doc. Tightening the gate to use `eligibility_of` breaks the upload
  path and eleven tests — it was tried on 2026-08-27. A CORRUPT index still
  raises.
- **The picker LISTS all three groups** — Ready to map / Waiting on a vendor
  data dictionary / Already mapped — and only the first has a live button.
  Hiding the others would leave a reviewer wondering where their document
  went; each row says what it is waiting on.
- **"Regenerate this mapping anyway" is GONE.** It existed for the 2026-08-24
  demo, where regenerating an already-mapped FRD was the only way to show the
  pipeline end to end — and the accuracy figure it produced was
  self-referential anyway, because the approved workbook was also the
  template. This closes the "hide mapped FRDs" item that sat under "Designed,
  not built".
- Verified on the real pair: SD (has a VDD, no STTM) → `ready`; CAQH (has an
  STTM, no VDD) → `mapped`, and `POST /api/demo/runs` refuses it.

## Volumes — `vdd_raw` beside `frd_raw`

`vdd_raw` (was `dict_source` for a few hours on 2026-08-27) holds the vendor
dictionaries. Named to sit beside `frd_raw`: **two raw source documents, two
raw volumes.** It is NOT `sttm_reference`, which is globbed for TEMPLATE
workbooks — a VDD is not one.

**Naming was reviewed and KEPT (2026-08-27)** against a proposal to move to
`frds` / `vdds` / `reference_sttms` / `output_sttms`. Two arguments carried:
(1) the current set is already `<document type>_<role>` throughout —
`frd_raw`, `vdd_raw`, `sttm_reference`, `sttm_out_app` — so the `sttm_*`
family SORTS TOGETHER in `SHOW VOLUMES`, which the proposal would split;
(2) `_raw` is the client's own layer vocabulary (RAW / STAGE / STANDARD in
the EDO naming standard), so it speaks their language, and a plural noun
loses that. Cost also matters: `frd_raw` and `sttm_reference` already exist
with data and grants in the workspace, and `sttm_out_*` is load-bearing for
run insulation (`demo._SET_DIR_RE` pins `sttm_out_(demo|live_e2e)_...`).

**File naming: `VDD_<feed>.xlsx` is the convention, `DICT_` a live alias.**
`similarity.name_key` strips both, so they key identically and two spellings
of one feed are AMBIGUOUS rather than double-paired. The alias exists because
the template already issued to vendors says `DICT_`, and invalidating a
document someone was asked to fill in is worse than carrying two spellings.

## SharePoint scope — one site, one library, three folders

Worth stating because it gets asked: the client **already** reads from ONE
NAMED SITE, not the tenant. `SharePointConfig.site_path` is REQUIRED
(`load_config` refuses without it and rejects a path not starting with `/`),
resolution is `GET /sites/{host}:{site_path}` → a drive by display name → a
folder by path. Nothing can enumerate the tenant, search across sites, or walk
out of the configured library — which is also why read-only `Sites.Selected`
on the one site is a sufficient grant. `vdd_folder` joined `frd_folder` /
`reference_folder` as a first-class field on 2026-08-27: three named folders
on one named library on one named site. Blank `vdd_folder` means VDD sync is
OFF, and with the eligibility rule that means nothing is generatable — say so
when configuring a new environment.

## The vendor data dictionary — INGESTION BUILT 2026-08-27

The third input is now **ingested end to end**; it is not yet **consumed by
the pipeline**. Hold that line when describing it: `DICT_` workbooks sync,
parse, pair, index and surface in the app, and 02/03/04 still do not read
them. The next step is a source layout from `dictionary.source_layout()`
reaching 02 as structured input, which is where the source-side columns stop
coming from the matched template.

What was built, and the rule each piece encodes:

- **`src/frdsttm/dictionary.py`** — the parser. Two rules do the work:
  **structure raises, content gates.** No FILES sheet, or a header row that
  cannot be located in the first 12 rows, raises `DictionaryError` (an
  unreadable dictionary must never masquerade as an empty one). Anything the
  vendor merely left blank — a missing field sheet, no descriptions, no PHI
  flag — is a `problems` entry, and the caller gates. `_yn` is **tri-state**:
  a blank PHI flag is `None`, never `False`, because "the vendor did not say"
  and "the vendor said no" are different facts and only one is safe to act on.
- **The returned-template trap.** The issued template's worked example rows
  parse perfectly, which is exactly what makes them dangerous: a vendor who
  returns the template unedited would hand back a dictionary of examples that
  looks complete. Rows carrying the marker `delete once replaced` are dropped
  and REPORTED, so a reviewer sees "this vendor returned the template".
  A trailing prose row (real workbooks carry an assumptions line under the
  table) is recognised as a note, not a file — but only when EVERY other core
  cell is blank, so a half-filled real row is never swallowed.
- **Pairing is EXACT NAME ONLY** (`similarity.pair_dictionaries`), deliberately
  unlike STTM pairing. A wrongly-guessed template still gives the render a
  layout; a wrongly-guessed dictionary puts another vendor's column names,
  types and PHI flags on this feed. That is fabrication, not a degraded
  answer, so an ambiguous or absent key leaves the FRD without a dictionary.
- **Its own volume, `dict_source`** (`STTM_DICT_VOLUME`). NOT `sttm_reference`:
  `corpus.parse_reference_dir` globs every `.xlsx` there as a TEMPLATE
  workbook, and a dictionary is not one.
- **Corpus index v3** — `dictionaries` (summary only: counts, file patterns,
  problem kinds, sha256 — never the 399 rows, so the index stays cheap to load
  on every request), `dictionary_pairs`, `unpaired_dictionaries`,
  `ambiguous_dictionaries`, `dictionary_errors`. The version bump is
  deliberate rather than additive-and-silent: a stage reading a v2 index would
  see no dictionaries and build the source side from the template alone —
  precisely the implicit borrow this input exists to remove.
- **Sync**: a third kind through the SAME `_sync_folder`, so prefix filter,
  incremental manifest, atomic writes and departed-file cleanup are one
  implementation for three kinds. `STTM_SHAREPOINT_DICT_FOLDER` **blank is the
  default and means OFF** — two folders listed, exactly as before. A blank
  STRING knob is meaningful here; see the blank-env-var caution below.
- **App**: `GET /api/demo/corpus/frds` carries a dictionary block on EVERY FRD
  (`has_dictionary: false` is the gating state, not a missing field);
  `GET /api/demo/corpus/dictionaries/{name}` serves one, allow-listed by the
  index exactly as the reference download is. `mirror_corpus` tolerates an
  ABSENT `dict_source` (it will not exist in a workspace deployed before
  2026-08-27) and reports `dictionary: None` so "no volume" stays
  distinguishable from "empty volume" — every other volume still raises.
- **Governance**: `dict_source` is tagged in `90_uc_governance` and joins
  `READ_VOLUMES`. A BSA who may read the FRD may read the dictionary that
  describes the same feed's columns — "may run == may read" would be broken
  by granting one and not the other.

Tests: `tests/test_dictionary.py` (28) + dictionary cases appended to
`test_sync.py` and `test_corpus_routes.py`. Suite 400 passed / 4 skipped.
Every fixture is SYNTHETIC and built in-process — these tests must keep
passing after the `sample_documents/` purge.

**Verified on the real pair:** the SD FRD pairs to its dictionary (3 files,
399 fields, 86/267/46); the CAQH FRD reports `None`, which is the correct
gating state because no dictionary for it exists.

## Results are addressable — `?set=&doc=` (2026-08-27)

Found while cataloguing the screens, and it was a real gap rather than a
documentation one: the results view was reachable ONLY as the tail of a run in
that browser tab. Navigate away, or come back after an App restart, and a
completed — BILLED — run could not be looked at again, even though its artifact
set was sitting in the volume. `DemoFlow` now reads `?set=&doc=` on mount and
writes it with `history.replaceState` when the results phase is entered, so a
result can be bookmarked, sent to a colleague, or reopened tomorrow.

Deliberately NOT a router: one query pair, nothing else in the app is
addressable, and a bare URL still opens the picker. Do not grow this into
client-side routing without a reason — the app is one surface.

## The review-app UI — ACFC HOUSE STYLE, rebuilt 2026-08-27

**Standing rule (Arjun, 2026-08-27): the frontend must read essentially the
same in the Hexaware environment and in ACFC's rebuild.** The backend will
differ; the UI is the half that has to survive the move, and matching UIs are
"half the battle". So: every rule is expressed in the leaf tokens in
`index.css`, and **nothing in the shell branches on environment, catalog,
tenant or mode**. The one exception is `.acfc-envchip`, which changes its TEXT
and never its box — same chip, same place, either side. Do not add a
mode-conditional layout; put the difference in a value.

The COLOUR was already right (sampled from the live site 2026-08-21). What was
missing was the client's LAYOUT language, without which the app read as a
generic admin panel. Added in `index.css` as `.acfc-*` primitives and used by
`App.tsx` / `AgentHeader.tsx` / `CorpusPanel.tsx` / `DemoFlow.tsx`:

- a **masthead** — 3px brand rule along the top of the page, white band, the
  flag device, headline in brand blue, utility chip right, hairline beneath;
- **full-bleed bands** with a centred `.acfc-container` (76rem — wider than the
  old `max-w-5xl`; the rows carry long file names plus several chips);
- **`.acfc-row`** document rows with a left accent edge instead of floating
  cards, **`.acfc-stat`** square outlined tiles with the numeral in brand blue,
  **`.acfc-chip`** square outlined status chips, **`.acfc-panel`** with a square
  accent edge (the same container the decks use, so app and slides read as one
  system), **`.acfc-notice`** one-line config warnings (they were two tall
  alert boxes owning the first screen), and a **navy footer band**;
- **`--brand-red` `#d2202f`**, sampled from the LOGO MARK, is the attention
  colour and nothing else: "the agent will not guess this". Never decorative.
- **The ACFC LOGO is deliberately never used.** Matching a client's palette on
  a vendor's app is ordinary practice; putting their mark on it is not ours to
  decide. `.acfc-flag` evokes the flag without reproducing it.

The third input is visible where the decisions are made: a chip on every
picker row (red `no vendor dictionary`, or `dictionary · N columns · N gaps`
linking to the download), a `with a dictionary — N / total` stat tile, an
unpaired-dictionary panel, and — most importantly — a notice on the
already-mapped screen, because that is where a reviewer decides whether to
spend a billed regeneration and needs to know the source side is ungrounded
BEFORE spending it, not after.

QA loop worth repeating: `npm run build`, run the backend against the
synthetic fixture, and LOOK at it in a browser. The first render of this shell
had the "/ of" denominator wrapping to its own line because `.acfc-stat span`
caught a nested span — invisible in the source, obvious on screen.

## SharePoint / Microsoft Graph (added 2026-08-21; READ-ONLY 2026-08-22; sync on app start-up decided 2026-08-22 late — see the decision block at the top)

**Local folder stands in for the library for the 2026-08-24 demo (Venu,
2026-08-24).** Graph access did not arrive in time, so the FRD/STTM pairs sit
in a folder on the reviewer's machine and the app is pointed at it with
`STTM_LOCAL_SOURCE_DIR`. **This is a source swap, not a second pipeline.**
`src/frdsttm/local_folder.py` implements only the client duck type
`sync_from_sharepoint` already documents (`list_documents(suffixes=, folder=)`
+ `download_item(item_id)`), so the entire downstream half — prefix split,
incremental manifest, atomic writes, departed-file cleanup, reindex, name
pairing — is the same code on the same path. Consequences worth knowing:

- **the naming convention still decides everything.** `FRD_<name>.<ext>` and
  `STTM_<name>.xlsx`, `<name>` matching exactly, is what splits the two kinds
  AND what pairs them. A mismatch does not fail — it falls through to
  similarity scoring and can land as "unmapped".
- `item_id` is the file name and `etag` is `<size>-<mtime_ns>`, so editing a
  document in the folder re-syncs it and deleting one removes the synced copy.
- the folder is **read-only** to this repo, matching the never-writes-to-
  SharePoint rule it stands in for.
- **precedence: local folder first, then SharePoint** — in
  `corpus_routes._source_client`, mirrored (not re-decided) by the
  `/api/demo/sharepoint/config` probe's new `source` field. Setting the
  variable is deliberate; a stale tenant in `.env` must not outrank it.
  Unset it to go back to Graph — nothing else changes.

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
  `sttm_reference` and pairs it with its FRD (name first — the library's
  convention is `FRD_<name>.docx` ↔ `STTM_<name>.xlsx`, so the stem after the
  prefix is the key; the older `<doc_id>.sttm.xlsx` / `<doc> STTM.xlsx`
  patterns the code matches today become fallbacks — similarity last).
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
  incremental. TRIGGER: app START-UP (`corpus_routes.start_sync_on_startup`
  via `app.py`'s lifespan) and "Sync now" — not a schedule;
  `resources/frd_sttm_sync_job.yml` has none (`max_concurrent_runs: 1` kept
  so two instances cannot race on the manifest). `sync_mode=reindex`
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
  API has no fine-tuning surface. Settled; do not re-open.
- **Hide mapped FRDs from the generate flow — NOT BUILT, planned for after
  the 2026-08-24 demo (Arjun).** Today `GET /api/demo/corpus/frds` /
  `CorpusPanel.tsx` show mapped FRDs alongside unmapped ones (view +
  two-step regenerate-anyway) — see the note under "Current priority"
  above for why that stays unchanged through the demo. The target
  afterward: mapped FRDs are no longer offered for (re)generation at all,
  surfacing only their approved STTM. Needs a concrete design (does
  regenerate-anyway disappear entirely, or move behind a reviewer-only
  control?) before it's built.
- **Three-input architecture (FRD + vendor data dictionary + standards
  doc) — DECIDED 2026-08-25, NOT BUILT.** The full rationale is the
  decision block under "Purpose & pipeline position". What it means in
  code, at the level that is settled: (a) a dictionary parser beside
  `frd_parsing.py` producing a per-file source layout (name, position,
  type, length, null rule, allowed values, description, PHI) as a
  structured input to `02_extract`, so the source-side STTM columns are
  grounded in the dictionary the way feed facts are grounded in the FRD;
  (b) a standards input that decides target naming, type promotion, audit
  columns and catalog per layer — replacing the implicit borrow from the
  template STTM (`reference_workbooks`), which stays as the LAYOUT source
  only; (c) an optional landing-zone probe (code, not LLM; headered files
  only; fills names/types/samples, never descriptions) behind a
  `mftlanding` → storage-root config, used when no dictionary is attached
  and as a layout check when one is; (d) missing dictionary → gated
  ambiguity per file, never a sparse render; (e) `_provenance` records
  which input each cell came from. NOT settled — decide before building:
  the exact config keys for the two standards documents (read them first);
  what the probe does when the folder is empty at authoring time. Settled
  the same day: `DICT_` is a sync-harvested corpus kind; the standards are
  versioned config under `contracts/`, not corpus documents.

## Governance (added 2026-08-23 — docs/AI_GOVERNANCE.md is the full record)

The client runs Collibra; the governance pass made the agent *describable
and auditable* without changing what it does. The rules that now hold:

- **Every governed action has a named actor and an audit event, or it does
  not happen.** Governed = start run, record resolution, re-render, download
  a workbook (the HAND-OFF — the repo never writes to SharePoint, so the
  download is where a generated STTM leaves the governed boundary), start a
  sync, upload. `identity.py` reads the Databricks Apps forwarded headers
  (`X-Forwarded-Email` / `-Preferred-Username` / `-User`); in databricks
  mode a request without them is a 401 (`STTM_REQUIRE_IDENTITY=0` is the
  operator-only escape hatch → actor `unknown`); local mode records the OS
  user with `source: local`. `audit.py` writes ONE JSON FILE PER EVENT to
  `/Volumes/<cat>/<sch>/<STTM_AUDIT_VOLUME>/events/` (+ a local mirror)
  BEFORE the action; a failed volume write is the caller's 502 and the
  action is not performed. `GET /api/demo/audit` lists; `read_files(...,
  format => 'json')` queries. Never document content in an event. The
  `X-Forwarded-Access-Token` header is never read.
- **Provenance is hashes and ids, never file names.** `frd_documents.
  content_sha256` (01) = the corpus index's = the run manifest's `frd_sha256`
  (02's `extraction_meta` sidecar carries it too); `frd_sttm_runs.
  rendered_sha256` (04) = `workbook.downloaded.sha256`. `extractions/<doc>.
  extraction_meta.json` (02) records provider, model, `system_prompt_sha256`,
  `schema_sha256`, usage, exemplars, SDK version, job run id — written for
  mock runs too. `<set>/run_manifest.json` (app) records who/which bytes/
  mode/job run/outcome and is uploaded next to the artifacts in databricks
  mode. `HumanResolution.resolved_by` is the actor (was always None).
- **`frd_sttm_runs` is APPEND + mergeSchema, never overwrite** — it is the
  render log. New columns: `run_label`, `triggered_by`, `job_run_id`,
  `provider`, `model`, `system_prompt_sha256`, `input/output_tokens`,
  `frd_sha256`, `rendered_sha256`, `standards_sha256` (2026-08-26 — which
  revision of the client's standards decided this render's target side).
  `triggered_by` / `run_label` are job
  parameters on BOTH job ymls (the app overrides them per run; a hand run
  records `manual`), `job_run_id` is `{{job.run_id}}`; local subprocess
  mode passes `TRIGGERED_BY` / `RUN_LABEL` env vars — `_param` reads both.
  The Jobs API rejects undeclared parameters: add a parameter to
  `jobs_runner.job_parameters` / `RENDER_JOB_PARAMETERS` ONLY together with
  the yml.
- **Classification is a separate, hand-run, APPLY-TAG step**
  (`90_uc_governance` / `frd_sttm_uc_governance`), not a pipeline
  side-effect; defaults read as placeholders (`UNASSIGNED — set
  data_owner`). Do not move tagging into 01/03/04.
- **Deploy prerequisite added:** volume `sttm_audit` exists (the governance
  job creates it) with READ+WRITE VOLUME for the app's service principal —
  without it every governed action in the deployed App is a 502 by design.
- **Access model (decided 2026-08-23, Arjun): may RUN == may READ.** The
  client's BSAs are ONE Entra-synced group; `90_uc_governance` with
  `reviewer_group` + `app_name` grants it USE CATALOG/SCHEMA + READ VOLUME
  on `frd_raw` / `sttm_reference` / `sttm_out_app` / `sttm_audit` and
  CAN USE on the App (SDK `apps.update_permissions`; CLI fallback printed).
  Never WRITE VOLUME / MANAGE to users (owner + steward only); never
  `demo_raw`, secret scopes or jobs (the app SP does those). View = run, no
  second tier. Do not key app access on MANAGE — it is the admin privilege.
- **Open human decisions live in docs/AI_GOVERNANCE.md §8** (owner/steward,
  Anthropic data path vs the client's BAA posture, retention, access model,
  git-history purge). Do not "resolve" them in code; record the decision.
- Tests: `tests/test_governance.py` (+ `tests/conftest.py` points the audit
  store at tmp for the whole suite — no test writes into
  local_dev_fixtures/).

## Branching model

All development happens on `staging`. `main` is the deployment branch;
`staging` merges to `main` only after testing.

**`deploy-demo` (added 2026-08-23, Arjun) is a third, narrow-purpose
branch — not part of normal development.** Databricks Git folders have no
build step, so the review app's Databricks Git folder in the workspace is
pointed at this branch specifically so it has a built
`review_app_react/frontend/dist/` to serve; `dist/` stays gitignored on
`staging`/`main` as before. Rebuilding means: `cd review_app_react/frontend
&& npm install && npm run build`, then `git add -f
review_app_react/frontend/dist` and commit to `deploy-demo` again — it is
NOT kept in sync automatically, and it is never merged into `staging` or
`main`.

## Fixtures & data rules

**REGRESSION FOUND 2026-08-25 — real client documents are TRACKED again
and PUSHED.** Commit `5f43813` ("Add sample_documents folder") on
`staging` — and only `staging`, as of 2026-08-25; `main` / `deploy-demo`
do not contain it — added the two real FRD/STTM pairs under
`sample_documents/`, and `origin/staging` has them. That is the exact
material the 2026-08-23 history purge removed. **Purge DEFERRED to Friday
2026-08-28 (Arjun, 2026-08-25): Arjun was told these two pairs contain no
sensitive material, so they stay tracked this week as the working
fixtures.** Still do it Friday — the no-client-documents rule is about
the repo, not about this pair. The fix: `git filter-repo --invert-paths --path
sample_documents/`, force-push `staging`, add `sample_documents/` to
`.gitignore` (it is NOT there today), keep the files in
`~/Desktop/frd-to-sttm-agent-documents/` and reach them via
`STTM_LOCAL_SOURCE_DIR`. Anyone who cloned `staging` after `5f43813` must
re-clone. Delete this paragraph once done and record the purge date in
the paragraph below.

**No FRD or STTM material is tracked in this repo — as of 2026-08-22.** The
formerly tracked anonymized demo pair (`demo_frd.docx`, `demo_sttm.xlsx`),
the `local_dev_fixtures/` inputs, and the demo replay set
(`local_dev_fixtures/sttm_out_live_e2e_20260807b/`, the post-fix live E2E run
described in docs/LIVE_E2E_2026-08-07.md) were deleted from the working tree
and `git rm`'d on Arjun's instruction that no client documents — raw or
derived — live in the repository. **PURGED FROM GIT HISTORY 2026-08-23**
(`git filter-repo --invert-paths` over `demo_frd.docx`, `demo_sttm.xlsx`,
`local_dev_fixtures/`; `main` + `staging` force-pushed; every commit SHA
changed — any clone made before then must be re-cloned, not pulled; the
old root commits remain fetchable from GitHub by raw SHA until GitHub
Support purges them on request). Consequences: a fresh clone has no preloaded FRD, no offline
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

- **Two files configure the model, and the JOB wins (2026-08-24).** A deployed
  App does NOT extract in its own container — it triggers the bundle job
  (`jobs_runner.py`), so a live run is governed by the `base_parameters` in
  `resources/frd_sttm_job.yml`, not by `app.yaml`'s `env:`. Both are set to
  `sttm_llm_provider: databricks` + `model: claude-opus-5`; **change both or
  the job silently wins.** `app.yaml`'s copies cover anything that ever runs
  in-container (the subprocess path, today mock-only) and document intent
  where people look first. Found because the job passed no provider at all,
  which would have sent a deployed live run down the Anthropic path demanding
  a key that no longer needs to exist.
- **`frd_sttm_job.yml` pinned `max_tokens: "16000"` until 2026-08-24** — the
  value R3 replaced (it capped out around 15-20 feeds,
  docs/LIVE_E2E_2026-08-07.md) while `02_extract`'s own default had been
  raised to 64000. A workspace job run would silently have reintroduced the
  truncation. Now 64000 in both. When changing a notebook default, check
  whether the job resource pins the old one.

- **Stale extraction JSONs can silently change the gate result (found
  2026-08-24).** `03_contract_build` matches extraction files to `doc_id`, and
  two DIFFERENTLY NAMED files can map to the SAME doc_id -- e.g.
  `synthetic_claim_intake.json` (pre-written by
  `tools/make_synthetic_smoke_fixture.py`) and
  `FRD_synthetic_claim_intake.json` (written by a real `02_extract` run over
  the `FRD_`-prefixed corpus). With both present, 03 reported PASS/PASS; with
  only the fresh pair present it reported PASS_WITH_FLAGS/PASS. The stale file
  won, and nothing said so. **Clear `sttm_out/extractions/` before a run whose
  result you intend to trust**, and read `<doc_id>.extraction_meta.json`
  (provider, model, stop_reason, usage, extraction_sha256) to confirm which
  artifact produced the contract. This is a live demo-day hazard, not just a
  local-fixtures one.

- **A deployed Databricks App cannot read your laptop (2026-08-24).** The
  local documents folder (`STTM_LOCAL_SOURCE_DIR`, `frdsttm.local_folder`) is
  a LOCAL-MODE source: it works when the review app runs on your machine. The
  deployed App runs inside Databricks and reads Unity Catalog, so
  `corpus_routes._source_client` is not even consulted there
  (`IS_DATABRICKS_APP` short-circuits every sync path to `jobs_runner`). To
  get folder documents in front of a deployed App, run
  `tools/push_local_source_to_volumes.py`, which stages the folder through
  the pipeline's OWN sync and uploads the result -- including
  `corpus_index.json`, which matters: the app's read path mirrors the volumes
  with the SDK (`_mirror_from_uc` -> `jobs_runner.mirror_corpus`), needing no
  job and no warehouse, so the corpus shows up with none of the bundle jobs
  deployed.
- **Only the audit volume is ever created by code.** `90_uc_governance.py`
  does `CREATE VOLUME IF NOT EXISTS` for `sttm_audit` alone; `frd_raw`,
  `sttm_reference`, `demo_raw` and `sttm_out_app` are assumed to exist and are
  only commented/tagged/granted. Verified in the Hexaware workspace
  2026-08-24: `frd_raw` and `sttm_reference` EXIST; `demo_raw`,
  `sttm_out_app` and `sttm_audit` are MISSING -- which is exactly the error
  the deployed App surfaced on its first run. `push_local_source_to_volumes.py
  --all-volumes` creates the missing ones via the SDK (no warehouse, so no
  compute cost).
- **`databricks sync` honours `.gitignore` AND rewrites notebook-headed .py
  files.** Two separate traps, both hit on 2026-08-24: gitignored build output
  (`review_app_react/frontend/dist/`) never reaches the workspace unless
  re-included with `--include`, and any `.py` whose FIRST line is
  `# Databricks notebook source` is stored as a NOTEBOOK object with no `.py`
  extension -- invisible to `import`. Do not put that header on anything under
  `src/`. The working deploy command is in review_app_react/README.md.

- **A BLANK env var is "not set", never a value — fixed 2026-08-24, and the
  rule is not yet enforced everywhere.** `.env.example` ships every optional
  knob blank, and the load it documents (`set -a; . ./.env; set +a`) exports
  a blank as `""`. `os.environ.get(NAME, default)` then returns `""` rather
  than the default, so `int("")` / `float("")` raised and following this
  repo's own setup instructions crashed the app at import
  (`STTM_CORPUS_REFRESH_SECONDS`), and `02_extract` on
  `STTM_EXEMPLARS_K` / `STTM_EXEMPLARS`. Fixed at four sites:
  `similarity.thresholds_from`, `corpus_routes._env_int`, and
  `02_extract._int_param` + its `sttm_exemplars` default. **Deliberately NOT
  fixed by a blanket rule in `_param`:** for STRING knobs a blank is
  meaningful and differs from the default — `frd_name_prefix` documents
  "blank disables the filter" — so a sweeping change there would silently
  re-enable prefix filtering. When adding a numeric knob, parse it through a
  blank-tolerant helper; when adding a string one, decide explicitly what
  blank means and say so in `.env.example`. The other `int(os.environ.get(…))`
  sites (`demo.py`, `jobs_runner.py`, `app.py`'s port) are unfixed but safe
  today only because no `STTM_DEMO_*` key ships blank in `.env.example` —
  add one and they break.
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
- **Template retrieval CALIBRATED on the two real pairs (2026-08-25) —
  three changes, all in code, after every real render came out freeform
  (the "sparse workbook" symptom).** (1) Real FRDs do not enumerate columns
  (33 identifiers in the FRD vs 391 columns in its own STTM), so the
  markdown-only features scored 0.11 against the document's OWN workbook;
  04 now scores `merge_features(index features, contract_features(contract))`
  — the extraction's feed keys + stage/standard table names, the vocabulary
  an FRD and its STTM actually share. Own-STTM scores became 0.236 / 0.253,
  cross-pair 0.046 / 0.033. (2) `THRESHOLD_DEFAULTS` re-seeded to
  single 0.15 / amalgam 0.08 (was 0.55 / 0.30); both job ymls pass the same
  values explicitly on the render task. (3) The corpus's exact-name pairing
  is now definitive at render time too: `decide_templates(..., pinned=)` —
  04 pins the own STTM when `pairs[doc_id].matched_by == "name"` AND
  exclude-own is off. Both job ymls set `exclude_own_reference: "0"` for the
  demo (the App regenerates ALREADY-MAPPED FRDs, so the approved STTM is the
  template by identity); the cross-validation default in the notebook is
  unchanged, and with exclude-own ON a pinned workbook is still excluded.
  Consequence to say out loud in the room: with own as template, the
  "accuracy vs golden" figure is self-referential. Not yet done: the
  2-pair corpus cannot exercise amalgam mode; re-calibrate as it grows.
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
current priority stated at the top. None had been executed from this
checkout before 2026-08-23; treat each as unproven until you have seen it
work.

- **CLI connectivity — RESOLVED 2026-08-23.** No org permission to run an
  installer (winget) on this laptop; unblocked with the Databricks CLI's
  portable Windows zip from its official GitHub releases
  (github.com/databricks/cli — not winget), unzipped to
  `C:\Users\2000198467\AppData\Local\databricks-cli\` and that folder added
  to the user PATH — no admin rights needed either way. Auth had a separate
  trap: `databricks auth login` against the workspace ran the full OAuth
  browser flow successfully (confirmed by the browser's own "Authenticated"
  page and a debug-log token exchange) but the CLI's default
  `auth_storage=secure` (Windows Credential Manager / OS keyring) silently
  failed to persist the result — no error surfaced, yet `auth profiles` /
  `current-user me` found nothing afterward. Likely a corporate
  endpoint-protection policy blocking credential-vault writes; revisit if
  this recurs on another machine. Fix: `.databrickscfg`'s `[__settings__]`
  `auth_storage` set to `plaintext` (was `secure`); profile `ahc` (now
  `default_profile`) authenticates cleanly and `databricks bundle validate
  -t dev` succeeds from this laptop. `bundle deploy` has NOT been run as of
  this writing — treat it as the next unproven step, not a done one.
- **Catalog default was wrong, fixed 2026-08-23 (Arjun) — bundle deploy and
  app deploy are STILL unproven.** Verified live: `soham_workspace` (the
  checked-in default in every notebook widget, `databricks.yml`, and
  `app.yaml`) is **not accessible from Arjun's identity at all** —
  `databricks catalogs list` doesn't show it, and `volumes list
  soham_workspace sttm_agent` errors "Catalog 'soham_workspace' is not
  accessible." This was true even in this shared internal dev workspace,
  not just as a future-client-workspace risk. Fixed by switching every
  hardcoded default (all four notebooks + `00_sharepoint_sync`,
  `00_sharepoint_fetch`, `90_uc_governance`, `databricks.yml`, `app.yaml`,
  and the four `review_app_react/backend/*.py` modules that read `CATALOG`)
  from `soham_workspace` to `arjun_workspace` — the catalog this identity
  can actually reach, which already has a `sttm_agent` schema with
  `frd_raw` / `sttm_out` / `sttm_reference` volumes and all three Delta
  tables (`frd_documents`, `frd_contracts`, `frd_sttm_runs`) populated from
  prior manual runs. **Still missing under `arjun_workspace.sttm_agent`:**
  the `demo_raw`, `sttm_out_app`, and `sttm_audit` volumes the live-run and
  governance paths require (see `app.yaml`'s prerequisite comment) — none
  of the three exist yet. **Nothing has ever been deployed to this
  workspace via the bundle**: `databricks jobs list` returns zero jobs, so
  `frd_sttm_pipeline`, `frd_sttm_sharepoint_sync`, `frd_sttm_render`, and
  the governance job all still need their first `bundle deploy`. **The
  registered Databricks App is stale**: `frd-sttm-review-app` (created
  2026-07-31) shows `app_status: UNAVAILABLE`, compute `STOPPED`, and its
  `default_source_code_path` still points at an old workspace copy
  (`/Workspace/Users/.../sttm_review_app`) predating the repo-root move
  (2026-08-22 evening) — it has never been deployed since that
  architecture change and its description ("read-only demo") is left over
  from the pre-2026-08-21 tab layout. Treat creating the three missing
  volumes, the first `bundle deploy`, and redeploying the App from the repo
  root as three separate, still-open action items before Monday, not
  assumptions.
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
- **The sync job has never been deployed (2026-08-22)** — it has no schedule
  (start-up sync decision, implemented the same evening); it is the
  databricks-mode execution path for the app's start-up sync and "Sync now".
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
- **Bundle defaults are dev-only** (`arjun_workspace.sttm_agent`, changed
  from `soham_workspace` 2026-08-23 — see the entry above). Confirm the CLI
  targets the intended workspace and override per target before
  `databricks bundle deploy`.
