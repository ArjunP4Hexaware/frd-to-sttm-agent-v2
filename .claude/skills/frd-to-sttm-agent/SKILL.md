---
name: frd-to-sttm-agent
description: Read this when (a) designing an agent — for any client or document family — that must turn two approved input documents (a prose requirements document plus a structured data dictionary) into a reviewable, standards-conformant mapping artefact where every cell must be traceable to a source and nothing may be guessed; OR (b) working in or asking about the `frd-to-sttm-agent-v2` repository itself — the FRD-to-STTM agent that turns an FRD (.docx) plus a vendor data dictionary (.xlsx) into a Source-to-Target Mapping workbook for the AmeriHealth Caritas program (the four volumes, the extract/assess/answers/render run, the question kinds, the review app on Databricks Apps, the bundle job `frd_sttm_pipeline`, the eval against an approved STTM, or how the unified agent console reaches it). Covers the concrete implementation (Part A) and the reusable pattern (Part B). The pre-2026-08-27 system (four-stage pipeline, label contract, SharePoint sync, CodeGen contract JSON) is in git history, not here.
---

# frd-to-sttm-agent — implementation + reusable pattern

Two parts. **Part A** is the agent as rebuilt on 2026-08-27 (Arjun), the
version that runs today. **Part B** abstracts the design so another team
can lift it into an unrelated project.

> **Import note.** Only this SKILL.md travels into a client workspace —
> `README.md`, `CLAUDE.md`, `docs/` are reachable only from a local
> checkout. Part A is written to stand on its own; every pointer to
> another repo file is for local Claude Code work, never a build
> dependency.

> **History note.** The previous SKILL.md (in git history before commit
> `620f187`) specified a much larger system — a four-stage pipeline with a
> label contract, SharePoint sync, run-insulation clones, template scoring
> and a CodeGen contract JSON. The 2026-08-27 rebuild deliberately dropped
> all of it (see "Deliberately left out" below). Do not resurrect any of
> it from memory of the old file; read git history if a piece is needed.

---

## Part A — This agent, concretely

### 1. What it does, and the one rule

FRD (`.docx`; also `.pdf`, `.md`, `.txt`) + vendor data dictionary (VDD,
`.xlsx`) → STTM workbook (`STTM_<name>.xlsx`) in ACFC's own layout, plus a
markdown run report. It is the **first agent** of the three-agent
AI-in-Engineering program at AmeriHealth Caritas (FRD→STTM → CodeGen →
Code Review); the CodeGen agent reads the rendered workbook through its
own deterministic `extract-sttm` tool — this agent no longer emits a
contract JSON for it.

**Every cell in the STTM comes from one of three places, and the agent
says which:**

| cell | source |
|---|---|
| source column name, type, length, required, PHI, description, allowed values, sample, segment | the vendor data dictionary |
| file pattern, format, delimiter, frequency, landing path, target tables, validation rules, recycle rule | the FRD |
| catalog and schema per layer (when the FRD is silent), stage default type, standard type promoted from the vendor type, audit columns | `standards/*.json` — the transcription of ACFC's two standards documents (`EDO Data Engineering Naming Standards.docx`, `EDO Data Engineering Coding Standards.docx`) |
| target column name | the source column name, **as-is** |

Anything else is a **question** for the reviewer or a **blocker**. The
agent never fills a cell it cannot source. Neither standards document
states a column-naming rule, so names are carried as the vendor wrote
them — a confirmed rule would become config, never code.

### 2. Inputs and pairing

Two documents per feed. Either:

- **paired by name in the volumes** — `FRD_<x>.docx` ↔ `VDD_<x>.xlsx`
  (`DICT_` accepted). Pairing is an exact name match, never similarity: a
  wrongly paired dictionary puts another vendor's columns on a source; or
- **declared by a reviewer** — both files uploaded together
  (`POST /api/runs/upload`, multipart `frd` + `vdd`, added 2026-08-31).
  They keep their real names, live under `output_sttms/<run_id>/inputs/`,
  are recorded on the run as `inputs`, and **never enter the corpus
  volumes**. A person saying "these two belong together" is stronger
  evidence than a matching name; the machine still never guesses.
  `GET /api/runs/<id>/inputs/frd|vdd` is the way back to what was
  uploaded; `POST /api/runs {doc_id, from_run}` re-runs such a pair.

An approved `STTM_<x>.xlsx` in `reference_sttms` marks the FRD as
*mapped* and may be used by **other** feeds' runs as a **layout** (sheets,
band labels, headers, styles). **A run never opens the feed's OWN approved
STTM — for anything, layout included** (Arjun, 2026-08-28). No content is
ever read from any approved workbook; no fitting other workbook → the
built-in layout. The FRD and VDD templates BSAs and vendors fill in are in
`templates/` (`FRD_TEMPLATE.docx`, `VDD_TEMPLATE.xlsx`).

A thin dictionary (few columns, no types) is reported as thin, not read
as a good one (2026-08-31): `vdd_summary.problems` carries the gaps and
the picker shows them.

### 3. Storage — four volumes, one table

Unity Catalog `<catalog>.sttm_agent` (Hexaware dev: `arjun_workspace`):

```
/Volumes/<catalog>/sttm_agent/
  frds/              FRD_<x>.docx
  vdds/              VDD_<x>.xlsx
  reference_sttms/   STTM_<x>.xlsx (approved; layout only) + corpus_index.json
  output_sttms/      <run_id>/run.json · STTM_<x>.xlsx · report.md · inputs/
table  frd_pairing   one row per FRD: paired VDD, approved STTM, generatable, reason
```

No other tables, no audit volume. `output_sttms/<run_id>/run.json` is the
**whole state of a run** — status, the extracted spec, the assessment,
the answers, the render summary. Locally the same four names are folders
under `./local_data`.

### 4. A run — extract · assess · answers · render

Statuses: `extracting` → `ready` | `needs_input` | `cannot_generate` →
(`render`) → `rendered`; `failed` at any step.

1. **extract** (`frd_parsing.py`, `extract.py`, `dictionary.py`) — the
   `.docx` becomes markdown with headings and tables kept; **one Claude
   call** (schema-in-prompt, response validated back into
   `FrdIngestionSpec`: project, in/out of scope, ACD rows, `feeds[]` with
   file patterns, format, delimiter, segments, frequency, SLA, LOBs,
   landing path, stage/standard `TableTarget`s, `validation_rules[]`,
   `recycle_rule`, …) returns the feed-level facts. Trailing commas are
   tolerated and malformed JSON is retried **once**; a still-failing call
   is a `failed` run with a *Run again* button — never a repair loop. The
   VDD is parsed column by column with no model involved; an off-template
   VDD is normalised by Claude and its column names verified
   (`dictionary_repair.py`).
2. **assess** (`completeness.py`) —
   - **grounding**: every identifier the model returned (file patterns,
     tables, schemas, ids) must appear **verbatim** in the FRD or becomes
     an `unverified` question (keep / remove / correct); prose is
     token-overlap checked → `weak_match`. A miss on a field the workbook
     never carries (`requirement_ids`, `sttm_reference`,
     `history_backfill`, …) is a **note**, not a question;
   - **attribution**: a rule attached to several sources becomes an
     `attribution` question **only if** it names a column those sources
     share and the FRD does not say which file. A rule naming every file
     is settled by the FRD, one naming no column stays on all sources,
     one whose column exactly one source has is attributed by the
     dictionary — each a note;
   - **the rule (2026-08-28): a question is asked only when different
     answers would write a different workbook and the documents do not
     settle it.** Off-workbook grounding misses and `project_id`
     conflicts are notes;
   - each source is paired to a VDD file by name tokens; an undecidable
     one → `file_pairing` question; a dictionary with blank types →
     `dictionary_types`;
   - targets per layer: the FRD wins, the standards fill (`schema_for`,
     `catalog_for`), the rest → `target_gap` question. Standard-layer
     type is promoted from the vendor's example value (a decimal point →
     `Decimal(10,2)`), config-switched; stage stays String;
   - **blockers**: `no_sources`, `no_dictionary`, `empty_dictionary`,
     `no_file_pairing` → `cannot_generate`.
3. **answers** — `POST /api/runs/<id>/answers {question_id, value}`,
   recorded on `run.json` with who and when; `apply_answers` folds them
   into the spec and targets at render time. **No second model call.**
4. **render** (`render.py`, `reference_layout.py`) — rows from the VDD
   (+ the standards' audit columns per segment), targets from step 2, FRD
   rules placed on the row whose column they name, written into a chosen
   approved workbook's layout or the built-in one. A single-sheet
   template's other sheets are the template feed's content and are
   dropped. Output name: `STTM_<name>.xlsx`.

Question kinds the UI must label: `unverified`, `weak_match`,
`attribution`, `project_id`, `file_pairing`, `target_gap`,
`dictionary_types`. Every question carries `options[]`, `free_text`, a
`context` object and, once answered, `answer {value, by, at}`.

### 5. Where it runs

- **Databricks App** `frd-sttm-review-app` (`app.yaml`,
  `STTM_APP_MODE=databricks`, deployed from the repo root; Hexaware dev
  workspace `adb-7405616719878880.0`): the FastAPI backend
  (`app/backend/`) mirrors the volumes through the Files API (pulled at
  most once a minute; always rewritten — size is not freshness) and
  triggers the bundle job **`frd_sttm_pipeline`** (`resources/
  pipeline_job.yml`, serverless, `max_retries: 0`) for `task=extract |
  render | reindex`; `STTM_JOB_ID` in `app.yaml` pins the deployed id
  (a dev-target deploy prefixes the name). `notebooks/pipeline.py` only
  wires widgets to `frdsttm.pipeline`. Reindex runs at App start-up —
  nobody presses a button to see their documents. The App bills per
  hour: stop it when not demoing.
- **Locally** (`STTM_APP_MODE=local`, `python app/backend/app.py`, port
  8000 / `DATABRICKS_APP_PORT`): the same backend runs `frdsttm.pipeline`
  in a thread over the four folders.
- **Model**: Claude through the workspace's Foundation Model APIs —
  `STTM_PROVIDER=databricks`, `STTM_MODEL=claude-sonnet-5`
  (`databricks-claude-sonnet-5` endpoint, since 2026-08-28) — no
  Anthropic key anywhere; `STTM_PROVIDER=anthropic` remains for local use.
- **Frontend**: React, `app/frontend/dist` **committed** (the workspace
  serves it without a build step) — `npm run build` before committing a
  UI change. The picker has no Runs section; a finished run is reachable
  by URL (`?run=<run_id>`). The run page keeps polling while the tab is
  in the background.
- **Unified agent console** (repo `unified-agent-console`, Databricks App
  `unified-agent-console` in Soham's workspace `adb-7405617821962942.2`)
  reverse-proxies this backend under `/api/sttm/*` with its own
  service-principal token. As of 2026-09-04 this app is **not deployed in
  that workspace**, so the console shows the STTM side as "not configured
  for this deployment" by design; deploying it there and setting
  `STTM_BACKEND_URL` (plus CAN_USE for the console's SP) lights it up.

### 6. HTTP surface (what any front end may rely on)

```
GET  /api/config                        mode, provider, model, catalog, schema, volumes, data_root
GET  /api/documents                     {built, documents[], unpaired_vdds, unpaired_references, vdd_errors, generated_at}
POST /api/reindex (202) · GET /api/reindex     {state idle|running|done|failed, error, url, finished_at, trigger}
GET  /api/documents/{doc_id}/{frd|vdd|sttm}    the file (docx/xlsx)
POST /api/documents/upload?kind=frd|vdd|sttm   multipart file → its volume, then reindex (201)
GET  /api/runs                          {runs[]}
POST /api/runs {doc_id, from_run?} (201) · POST /api/runs/upload (201; multipart frd+vdd)
GET  /api/runs/{id}                     the run view (status, frd, vdd, assessment, sources, preview, render, live, inputs)
POST /api/runs/{id}/answers             {question_id, value} → {status, questions}
POST /api/runs/{id}/render (202)        → {run_id, live}
GET  /api/runs/{id}/workbook · /report · /inputs/{frd|vdd}
```

Status codes are part of the contract: **409** "a run is already in
progress — one at a time" (`runs.busy()`), **400** a document that cannot
be generated or a bad upload type, **404** an unknown run/document/
question, **502** a broken corpus index. A run the job has not written
yet is served as `extracting` with its `live` block, never a 500.
`_who()` reads `x-forwarded-email` / `-preferred-username` / `-user` for
attribution (`local` otherwise). `python-multipart` is a hard dependency
of the upload routes (bitten 2026-09-01).

### 7. Repo layout

```
src/frdsttm/        the agent — frd_parsing, dictionary(+_repair), extract, completeness,
                    render, reference_layout, standards, corpus, pipeline, models
notebooks/pipeline.py   the Databricks job (task = extract | render | reindex) — wiring only
app/backend/        FastAPI: app.py (routes), runs.py, jobs.py, storage.py, settings.py
app/frontend/       React (dist/ committed)
standards/          the two ACFC standards .docx + naming_standards.json / engineering_standards.json
templates/          FRD_TEMPLATE.docx, VDD_TEMPLATE.xlsx
tools/              push_documents.py (folder → volumes → reindex), build_vdd_template.py,
                    build_vdd_from_sttm.py, eval_against_approved.py
tests/              offline, synthetic documents only (conftest builds them)
docs/               ARCHITECTURE.md, DEMO.md (SD demo runbook), PRESENTATION_SCRIPT.md
sample_documents/   real client pairs (SD, CAQH) as working fixtures — scheduled for purge
scripts/            build_acfc_deck.py, extract_overview_slides.py (ACFC overview deck + one-pagers)
```

All logic lives in `src/frdsttm/`; the notebook and the backend only
wire it. Tests are offline and synthetic. `pytest` from a
`pip install -e ".[app,dev]"` venv.

### 8. Eval against an approved STTM (2026-08-28)

`tools/eval_against_approved.py <agent.xlsx> <approved STTM_*.xlsx>
[--run run.json] [--out eval.xlsx]`: both workbooks go through the same
reader (`reference_layout.parse_reference_workbook`), tables matched by
stage table name, rows keyed by normalised source column (audit rows by
stage column), twelve structured fields scored as `match · question ·
agent_blank · approved_blank · disagree`, with a distinct-disagreement
roll-up; prose is shown side by side, never scored. A disagreement is
not automatically an agent error. Measured on the SD pair: 411/411 on
names, 400/411 on standard types.

### 9. Design constraints a rebuild must respect (hard-won)

1. **Nothing is invented.** A value is from the FRD, the VDD, the
   standards, or it is a question/blocker. A missing dictionary is a
   blocker, never a guess at columns.
2. **Pairing is a name match or a person's declaration — never
   similarity.**
3. **The feed's own approved STTM is never opened, for anything.** Other
   workbooks supply layout only; nothing supplies content. (This removed
   the self-referential loop of learning names from approved workbooks.)
4. **Ask only what changes the workbook.** Every other finding is a note.
5. **Grounding is verbatim.** Identifiers must appear literally in the
   FRD; the reviewer decides keep/remove/correct — the model never
   self-certifies.
6. **One model call per run** (plus at most one JSON retry, plus an
   optional dictionary normalisation). Answers and render make no model
   call.
7. **Standards are data** (`standards/*.json`), not code paths — a
   confirmed convention becomes config.
8. **All logic in `src/frdsttm/`**; tests offline and synthetic; no
   client documents in tests.
9. **Money asks first** (Arjun's rule): a live run, a job start, an App
   start are spend — the demo runbook says when.

### 10. Deliberately left out (2026-08-27 rebuild)

Audit trail, identity, SharePoint sync, run-insulation clones, template
scoring, term catalog, exemplar retrieval, standards "fill" gating, the
CodeGen contract JSON, evals against approved workbooks (later re-added
as the standalone tool in §8). Git history has all of it.

### 11. Open items (do not present as done)

1. **Read the FRD's Structural Metadata table by code** (post-demo #1):
   the template's fixed eleven rows are a table — read them exactly as
   the VDD is, as a template check (a missing row is a question or
   blocker before any model call) and as a second reading beside Claude's
   so a disagreement becomes a question.
2. Purge `sample_documents/` and `standards/*.docx` from git history
   (`git filter-repo`); keep them outside the repo.
3. Re-point the Databricks Git folder / App at `main` once `staging` is
   merged (both branches exist; `main` currently deploys).
4. Deploy the review app in Soham's workspace so the unified console's
   STTM side becomes reachable.

### 12. Key docs — local checkout only

- `README.md` — commands, deploy steps, layout table
- `CLAUDE.md` — working rules, workspace facts, the post-demo list
- `docs/ARCHITECTURE.md` — the source table above, the run, where it runs
- `docs/DEMO.md` — the SD demo click path and fallbacks
- `scripts/build_acfc_deck.py` — regenerates the ACFC overview deck (no
  deck is stored in the repo; documents were removed 2026-08-22)

---

## Part B — The reusable pattern

Lift this when building an agent that must produce a reviewable,
standards-conformant mapping or specification from **two** approved
inputs — one prose (requirements) and one structured (a dictionary,
schema, catalogue) — for any client or domain.

### When it applies

- The output is a **table every cell of which must be traceable** to an
  input document, a standard, or a named human answer.
- One input is prose a model must read; the other is structured and must
  be read **exactly**, never by a model.
- A wrong pairing of the two inputs is catastrophic (another vendor's
  columns on a source), so pairing must be exact or declared.
- Reviewers, not the agent, own ambiguity.

### The essentials

1. **A provenance rule per cell, written down first.** [essential] Decide
   for every output column which input owns it; anything with no owner is
   a question or a blocker. The table in Part A §1 *is* the design.
2. **Model reads prose only; code reads structure.** [essential] One
   schema-validated model call over the prose; the structured input is
   parsed deterministically. Never let the model "help" with the
   structured file except to normalise an off-template one, and then
   verify its output against the original.
3. **Verbatim grounding of every identifier the model returns.**
   [essential] Literal substring in the source or it becomes a reviewer
   question with keep/remove/correct. Prose gets an overlap score and a
   softer question.
4. **Ask only what changes the output.** [essential] A finding that
   cannot alter the artefact is a note. This is what keeps the review
   short enough that people actually do it.
5. **Pairing by exact key or human declaration.** [essential] Never by
   similarity; a declared pair is recorded on the run and stays out of
   the shared corpus.
6. **One durable run record.** [essential] A single JSON is the whole
   state (spec, assessment, answers, render summary), so any step can
   be replayed and the UI is a view over it.
7. **Standards as data.** [essential] Transcribe the client's standards
   into versioned JSON the agent reads; a newly confirmed convention is a
   data change with a source citation.
8. **Never read the target's own approved artefact.** [essential] Other
   approved artefacts may lend layout; none lend content. Otherwise the
   agent learns to copy the answer key.
9. **Status + question vocabularies that a UI can label.** [essential]
   `ready | needs_input | cannot_generate | rendered | failed` and a
   closed set of question kinds, each with options and free-text.
10. **An eval against the approved artefact that uses the same reader
    for both sides.** [essential] Then the eval has no parsing of its own
    and disagreements are diffs, not judgment.

### What is incidental (swap freely)

FRD/VDD/STTM as the artefact names; `.docx`/`.xlsx`; Databricks Apps +
UC volumes + a serverless job as the runtime (folders + a thread work
identically); Claude via Foundation Model APIs as the model; React for
the review UI; the specific ACFC standards.

### Anti-patterns this design rules out

- A repair loop that asks the model again until the output validates.
- Similarity-based pairing "to be helpful".
- Learning naming conventions from approved workbooks.
- Filling a target cell with a default when the FRD is silent.
- Asking the reviewer every question the audit can generate.
