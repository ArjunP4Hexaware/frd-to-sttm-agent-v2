---
name: frd-sttm-agent
description: Read this when rebuilding the FRD-to-STTM agent from nothing inside the ACFC (AmeriHealth Caritas) Databricks workspace with Genie Code, or when asked how the agent works in detail. The repository does not travel into ACFC, so this file is the whole specification — contract, every module's behavior with its load-bearing strings, the Databricks deployment with exact grants, the verbatim artifacts (both ACFC standards transcriptions, the extraction model, both prompts, the manifests), and the acceptance tests. Built in two phases to keep token spend low. Start at section 0.
---

# FRD-to-STTM agent — rebuild specification for Genie Code

## 0. Read this first — how to build without burning tokens

**Phases.** Phase 1 is the working agent: volumes in, questions, workbook out,
App. Phase 2 adds the expensive extras. Do not start Phase 2 until Phase 1
passes section E. Items marked **P2** are Phase 2.

**Context discipline.**
1. Read sections 0, A, and the B section for the module you are writing. Do
   not re-read the whole file. Sections are self-contained.
2. Copy section D blocks into files directly. Never print them back, never
   `cat` a standards file, never paste file contents into chat.
3. Write each file once, complete. No exploratory partial files.
4. After each module run only its test: `pytest tests/test_<module>.py -q`.
   Run the full suite once at the end of each phase.
5. Keep `BUILD_STATUS.md` at the repo root: one line per file, `todo | written |
   tests pass`. Update it after every file. A new session resumes from it and
   from this section, nothing else.
6. Report line counts and test summaries, not file contents.
7. Say "billed" before the first job run and the first model call.
8. A deviation from this file is reported as a deviation, never as done.

**Build order.** D6 manifests and `pyproject.toml` → D1, D2 into `standards/`
→ D3 as `src/frdsttm/models.py` → B5 `standards.py` → B1 `frd_parsing.py` →
B2 `dictionary.py` → B4 `extract.py` → B8 `reference_layout.py` (Phase 1 needs
only `loose_tokens`, `_nl`, and `parse_reference_workbook`) → B7 `render.py`
(built-in layouts only) → B6 `completeness.py` → B9 `corpus.py` → B10
`pipeline.py` → B14 tests → B11 notebook → B12 backend → B13 UI → C deploy.
Then Phase 2: B3, B7.4, B10 uploaded pairs, B12 upload routes, B15 eval.

**Costs.** App bills per hour while running. Each job run bills serverless
compute. Each extraction bills one model call. Stop the App when done.

---

## A. Contract

**Purpose.** FRD (Functional Requirements Document, `.docx`; `.pdf/.md/.txt`
accepted) plus VDD (vendor data dictionary, `.xlsx`) → STTM (Source-to-Target
Mapping) workbook. The FRD gives the feed frame: files, formats, landing
path, target tables per layer, load strategy, data-quality rules. The VDD
gives every source column. The agent extracts, records what it found, asks
only what the documents do not settle, and renders.

**The rule.** Every cell comes from one of three places, and the agent
records which: the VDD (source column facts), the FRD (feed facts and
rules), or `standards/*.json` (catalog and schema when the FRD is silent,
stage default type, standard type promotion, audit columns). Target column
name = source column name as-is. Anything else is a question or a blocker.
A question is asked only when different answers would write a different
workbook and the documents do not settle it.

**Hard rules.**
1. Pairing by name (`FRD_<x>` ↔ `VDD_<x>`/`DICT_<x>`; `STTM_<x>` marks it
   mapped) or by a reviewer's declaration. Never by similarity.
2. A run never opens the feed's own approved STTM, not even for layout.
3. Nothing invented. Missing dictionary = blocker.
4. Four volumes, one table. No audit or log tables. Run history is
   `output_sttms/<run_id>/run.json`.
5. All logic in `src/frdsttm/`. Notebook and backend only wire it. The same
   functions run locally over four folders.
6. Standards are versioned JSON loaded loudly with no fallback.
7. One model call per run, over the FRD text. The VDD is parsed by code.
8. Tests are offline and synthetic.

**Storage.** `<catalog>.sttm_agent` volumes `frds`, `vdds`, `reference_sttms`
(also holds `corpus_index.json`), `output_sttms` (one dir per run: `run.json`,
`report.md`, `STTM_<x>.xlsx`, P2 `inputs/`). Table `frd_pairing`, one row per
FRD, rewritten by reindex.

**Topology.** Job `frd_sttm_pipeline`: one serverless notebook task,
`task` ∈ `extract | render | reindex`, deployed by a bundle, runs as the
deploying user (who needs volume read, `output_sttms` write, endpoint query).
App `frd-sttm-review-app`: FastAPI + static UI, reads volumes via the Files
API, triggers the job via the Jobs API. Its service principal needs exactly
`USE_CATALOG`, `USE_SCHEMA`, `READ_VOLUME` on `frds`/`vdds`/`reference_sttms`,
`READ_VOLUME`+`WRITE_VOLUME` on `output_sttms`, `CAN_MANAGE_RUN` on the job.
Model `databricks-claude-sonnet-5` through the Anthropic SDK pointed at
`<host>/serving-endpoints/anthropic` with the workspace credential.

**Statuses.** `extracting` → `ready | needs_input | cannot_generate` →
`rendered`; `failed` anywhere.

**Layout to generate.**
```
pyproject.toml requirements.txt app.yaml databricks.yml resources/pipeline_job.yml   (D6)
notebooks/pipeline.py                                                              (B11)
src/frdsttm/{__init__,standards,models,frd_parsing,dictionary,extract,
             reference_layout,render,completeness,corpus,pipeline}.py  P2: dictionary_repair.py
standards/{naming_standards,engineering_standards}.json                            (D1,D2)
app/backend/{settings,storage,jobs,runs,app}.py                                    (B12)
app/frontend/dist/index.html                                                       (B13)
tools/push_documents.py   P2: tools/eval_against_approved.py                        (B15)
tests/conftest.py + test modules                                                   (B14)
BUILD_STATUS.md
```
Python ≥ 3.11. Deps: `anthropic>=0.60 pydantic>=2 openpyxl python-docx pypdf
databricks-sdk`; app: `fastapi uvicorn[standard] python-multipart`; dev:
`pytest httpx`.

**Config** (read only in `app/backend/settings.py`; defaults in brackets):
`STTM_APP_MODE` [`local`] (`databricks` = volumes via Files API, pipeline as
the job) · `STTM_LOCAL_DATA` [`<repo>/local_data`] · `CATALOG` · `SCHEMA`
[`sttm_agent`] · `STTM_FRDS_VOLUME` `STTM_VDDS_VOLUME` `STTM_REFERENCE_VOLUME`
`STTM_OUTPUT_VOLUME` [the four names] · `STTM_PROVIDER` [`databricks`] ·
`STTM_MODEL` [`claude-sonnet-5`; code prefixes `databricks-`] · `STTM_JOB_NAME`
[`frd_sttm_pipeline`] · `STTM_JOB_ID` [empty; required after a dev-mode
deploy] · `STTM_BIND_HOST` [`0.0.0.0`] · `DATABRICKS_APP_PORT` [8000]. Job
parameters: `task catalog schema run_id doc_id provider model triggered_by
frd_file vdd_file` plus the volume names and `pairing_table` as notebook
base parameters.

---

## B. Modules

Conventions: "norm" = B6.1 unless named. `None` from a standards lookup means
ask, never invent. Every parser is deterministic and offline. Strings in
code font are load-bearing.

### B1. `frd_parsing.py`

Constants: `SUPPORTED_SUFFIXES = {".md",".markdown",".txt",".docx",".pdf"}`;
`REQ_ID_FAMILIES = ("BR","REQ","FR","SRQ","SIR","NFR","MDST")`;
`PROJECT_ID_LINE_RE = r"project\s*id\s*:?\s*(\d{6,8})"`;
`PROJECT_ID_DIGITS_RE = r"(\d{6,8})"`; requirement marker: `^\s*((?:<families>)[-\s]?\d+)\b[\s:.—–-]*(.*)$`
case-insensitive → `**<ID> — <rest>**` (id upper, `[-\s]+`→`-`) or `**<ID>**`;
skip styles `^(toc\b|toc header$)`.

`docx_to_markdown`: walk the body in order (paragraphs, tables, and inside
`w:sdt`/`w:sdtContent`). Heading level: style `Title`→1; `Heading N`→min(N+1,6);
a style containing "heading" plus an ordinal word first…sixth/1st…6th →
ordinal+1 capped 6; else paragraph or style `w:outlineLvl`+2 capped 6. Heading
→ blank, `#`×level + text, blank. Style containing "list"/"bullet" → `- text`.
Other → marker or text, then blank. Tables: merged cells (same `_tc`) emit
empty; cell = text plus nested tables (depth ≤ 2) flattened as rows joined
`; `; escape `|`→`\|`, newlines→space; header row, `| --- |` row, body, padded
to max width, blank lines around. Collapse 3+ newlines to 2; strip; add `\n`.
`pdf_to_markdown`: pypdf page text line by line with markers. Text files:
UTF-16 on BOM else `utf-8-sig`, `cp1252`, `utf-8` replace; normalise EOL.

`parse_frd(path)` → `{doc_id: stem, source_file, content, content_sha256
(file bytes), project_id (first 6–8 digits in the name or None),
heading_count (# lines), table_count ("| ---" lines)}`. `FrdParseError` when
content < 500 chars or a `.docx` has zero headings.

### B2. `dictionary.py`

VDD shape: **FILES** sheet, one row per file; one **field sheet** per FILES
row named by its Field Sheet cell, one row per column. Ignore sheets
`readme, read me, instructions, change log, changelog` and any whose norm
name starts `fields template`. Header norm: lower, `[^a-z0-9]+`→space,
collapse. Header row = within the first 12 rows, the row carrying every
required key with the most known keys; none → `DictionaryError`. No FILES
sheet → `DictionaryError`. Unknown columns kept in `extra` as `col_<n>`. Rows
whose Notes contains `delete once replaced` are dropped and counted.

Aliases (key: spellings). FILES — `file_name_pattern`: file name pattern, file
name, file pattern · `file_title`: file title, title · `format`: format, object
data format · `delimiter`: delimiter, delimeter · `header_row`: header row, has
header row · `encoding` · `cadence`: delivery cadence, cadence, frequency ·
`description`: content description, description · `field_sheet`: field sheet,
fields sheet, sheet · `multi_record`: multi record type, multi record ·
`record_type_field` · `record_type_values` · `expected_field_count`: expected
field count, field count · `null_representation`: null representation, null
value, missing value · `text_qualifier` · `line_ending` · `trailer_record`:
trailer control record, trailer record · `notes`: notes, note. Required:
`file_name_pattern`, `field_sheet`.
Fields — `position`: position, ordinal, seq, sequence · `name`: field name,
column name, name · `datatype`: data type, datatype, type · `length`: length,
size · `required`: required y n, required, mandatory, nullable · `description`:
description, definition · `allowed_values`: allowed values range, allowed
values, valid values, domain · `example`: example value, example, sample
value, sample · `phi`: phi pii y n, phi pii, phi, pii · `key`: key uniqueness,
key, primary key, unique key · `segment`: segment, record type ·
`business_name` · `precision` · `scale` · `format`: format pattern, format ·
`default`: default value, default · `start_position` · `end_position` ·
`notes`: notes, note. Required: `name`. First spelling wins on collision.

Coercion: trimmed text, empty→`None`. Y/N: `y yes true t 1`→True,
`n no false f 0`→False, else `None`; **None is never False**. Ints via
`int(float(s))`.

`parse_dictionary_workbook(path)` → `{workbook, files, fields: {sheet: [...]},
n_files, n_fields, problems}`. File records: FILES keys + `extra`;
`header_row`, `multi_record`, `trailer_record` as bool/None. Field records:
field keys + `extra` + `file` (owning pattern); ints for positions; bool/None
for `required`, `phi`.
Problems `{kind, file, sheet?, detail, columns?}`: `file_without_pattern`;
`files_row_ignored` (title, format, delimiter, field sheet all blank = prose);
`template_example_rows`; `no_field_sheet_named`; `field_sheet_missing`
(and `fields[sheet]=[]`); `sheet_not_listed` (a sheet no FILES row names);
`field_sheet_unreadable` (→ `[]`); `field_sheet_empty`; `missing_descriptions`,
`missing_datatypes`, `missing_required_flags`, `missing_phi_flags` (each with
`columns`); `missing_segments` (multi-record file, a column without segment);
`field_count_mismatch`.
`parse_dictionary_dir(dir)` → `{dictionaries, errors}`; skips `~$` files.
`source_layout(parsed)` → `{pattern: [columns]}`.

### B3. `dictionary_repair.py` — P2

Fallback when B2 raises or reads zero columns. Workbook as text: per sheet
`=== SHEET: <title> ===`, one line per non-empty row, cells joined ` | `, ≤ 800
rows/sheet, cells cut at 200 chars. Prompts and models in D5. Stream; reject
`refusal`/`max_tokens`; validate. Guard: a returned column name absent from
the whitespace-collapsed lower-cased text is dropped → problem
`model_columns_not_in_workbook` with names. Output in B2's shape, problem
`normalised_by_model` first, unknown cells `None`, `normalised_by_model: True`.
Returns `(parsed, {model, input_tokens, output_tokens, dropped})`.

### B4. `extract.py`

`DEFAULT_MODEL="claude-sonnet-5"`, `DEFAULT_MAX_TOKENS=64000`,
`DATABRICKS_MODEL_PREFIX="databricks-"`, `DATABRICKS_ANTHROPIC_PATH="/serving-endpoints/anthropic"`.
`databricks_model_name(n)` prefixes unless present. `build_client(provider)`:
`anthropic` → `anthropic.Anthropic(max_retries=2)`; `databricks` → `cfg =
WorkspaceClient().config; anthropic.Anthropic(api_key="unused",
base_url=cfg.host.rstrip("/") + PATH, default_headers=cfg.authenticate(),
max_retries=2)`. `build_prompt(content)` = D4 user prompt with
`json.dumps(FrdIngestionSpec.model_json_schema(), indent=2)` (schema in prompt
on purpose: server-side structured output rejects it as too large).
`clean_json_text`: strip; if starts with ``` take the text after the first
fence, drop a leading `json`; remove `,(\s*[}\]])` trailing commas. `parse_response`:
`json.loads` failure → `MalformedJson`; then `model_validate_json`; a
`ValidationError` → `RuntimeError` listing `loc: msg` per error. `extract(client,
doc_id, content, model, max_tokens)`: `client.messages.stream(model=, max_tokens=,
system=SYSTEM_PROMPT, messages=[{"role":"user","content":prompt}])`, final
message; `stop_reason` `refusal`/`max_tokens` → `RuntimeError` (the latter:
"raise max_tokens"); text blocks joined; `MalformedJson` retried once, schema
mismatch never. Returns `(spec, {model, stop_reason, attempts, input_tokens,
output_tokens, system_prompt_sha256, schema_sha256 (by-alias schema, sorted
keys), extracted_at})`.

### B5. `standards.py`

Load both JSON files at import into `NAMING`, `ENGINEERING`, `NAMING_VERSION`,
`ENGINEERING_VERSION`; `StandardsError` on missing file, missing `version`,
wrong `kind`. `REPO_ROOT` = two parents above the package (`standards/` sits
beside `src/`). `standards_sha256()` = sha256 of `{"naming","engineering"}`
dumped sorted, compact, `ensure_ascii=False`. `abbreviate(vocab, term)`: key =
term upper with whitespace collapsed in `vocabularies[vocab].values`, else
`None`. `known_terms(vocab)` sorted tuple. `normalize_load_strategy` =
`abbreviate("load_strategies", …)`. `schema_for(layer, domain)`:
`derivations.schema[layer].pattern.format(domain=abbreviate("domains", domain))`
or `None`. `catalog_for(layer)`: `derivations.catalog[layer]` if str.
`audit_columns(layer, data_bearing=True)`: `column_rules.audit_columns` groups
`core` then `data_bearing_only` (if data-bearing), columns whose `layers`
contains the layer → `SRC_FILE_NAME, REC_CREATION_TIME, REC_UPDATED_TIME[, LOB]`.
`stage_default_type()` = `type_promotion.stage_default`. `promote_type(t)`:
lower, collapsed key in any `promotions.observed[*].source_type` → its
`standard`; else `None`. `type_from_example(ex)`: when
`infer_from_example.enabled` and `ex` matches
`^[-+]?\d{1,3}(,\d{3})*\.\d+%?$|^[-+]?\d*\.\d+%?$` → `decimal_point_example`
(`Decimal(10,2)`); else `None`. Column names are never consulted.

### B6. `completeness.py`

Output (the assessment): `{status, blockers: [{kind,text}], questions,
sources, grounding: {strict_checked, strict_failed, advisory_checked,
advisory_flagged, off_workbook}, notes}`. No model calls.

**B6.1 norm.** NFKC; curly quotes→straight; `‐ ‑ ‒ – — ―`→`-`; NBSP→space;
`[*_#|\`]`→space; collapse whitespace; strip; lower. `_tokens` = `[a-z0-9_]+`
runs of length ≥ 4.

**B6.2 questions.** `{id, kind, source, text, options, free_text, context,
answer: None|{value,by,at}}`; `id = f"{kind}-{sha1(json.dumps({'kind':kind,'context':context}, sort_keys=True))[:12]}"`.
Constants: `KEEP="Keep it as extracted"`, `REMOVE="Remove it"`,
`ALL_SOURCES="All of them"`, `USE_DEFAULT_TYPE="Use the ACFC default type"`,
`LEAVE_TYPE_BLANK="Leave the standard type blank"`. `source` = feed name or
`source <i+1>`, `None` for project fields. De-duplicate by id, first wins.

| kind | text | options / free | context |
|---|---|---|---|
| `unverified` | `The extracted <field, underscores→spaces> '<value>' could not be found verbatim in the FRD. Keep it, remove it, or type the correct value.` | KEEP, REMOVE / yes | `{path, field, feed_index?, target?, value}` |
| `weak_match` | `This <field> only loosely matches the FRD's wording: '<value>'. Keep it, remove it, or restate it.` | KEEP, REMOVE / yes | same |
| `attribution` | `This rule names a column that <n> sources share (<names>) and the FRD does not say which file it applies to. Which does it? — '<rule[:200]>'` | names + ALL_SOURCES / no | `{rule, feed_indices, names}` |
| `file_pairing` | `Which file in the vendor dictionary describes source '<name>' (<patterns or "no file pattern stated">)?` | unused patterns (or all) / no | `{feed_index, candidates}` |
| `target_gap` schema | `The <layer> schema for source '<name>' is stated neither in the FRD nor derivable from the naming standards (domain: <domain>). Pick the domain, or type the schema name.` | `known_terms("domains")` / yes | `{feed_index, layer, attribute:"schema"}` |
| `target_gap` catalog | `The <layer> catalog for source '<name>' is stated neither in the FRD nor in the naming standards. Type it, or leave it blank.` | none / yes | `{…, attribute:"catalog"}` |
| `target_gap` tables | `The FRD names no <layer> table for source '<name>'. Type the table name.` | none / yes | `{…, attribute:"tables"}` |
| `dictionary_types` | `The vendor left the data type blank for <n> column(s) on <source> (<first 5, …>). Nothing in the dictionary or the standards says what they are. The standard-layer type falls back to the ACFC default (<stage default>), which reads exactly like a real type. What should the workbook say?` | USE_DEFAULT_TYPE, LEAVE_TYPE_BLANK / no | `{sheet, file, columns}` |

**B6.3 grounding.** Strict scalars `landing_location`, `sttm_reference`;
strict lists `file_name_patterns`, `record_segments`, `lobs`,
`requirement_ids`; also strict `project.project_id`, `project.project_name`,
and per `stage_target`/`standard_target`: `catalog`, `schema`, each table.
Advisory scalars `recycle_rule`, `history_backfill`, `archive_retention`,
`phi_pii_notes`; advisory lists `validation_rules`, `load_windows_sla`.
Strict passes when `norm(v)` ⊂ normed FRD, or splitting on `[()\[\];|]` gives ≥ 2
parts all ⊂. Advisory passes at token overlap ≥ 0.75 (no tokens = pass).
On-workbook fields: `file_name_patterns lobs landing_location recycle_rule
validation_rules frequency source_system domain sub_domain delimiter
feed_name catalog schema tables`. A miss on any other field → `off_workbook`
note `<path>: '<v>' is not verbatim in the FRD — kept; it is not written to
the workbook` (or `… only loosely matches the FRD — kept; …`). A miss on an
on-workbook field → `unverified` / `weak_match`. Every miss also listed as
`"<path>: '<v>'"` in `strict_failed`/`advisory_flagged`.

**B6.4 enrich.** `PROJECT_ID_LINE_RE` on normed FRD: model gave none → set,
note `project_id <pid> taken from the FRD's 'Project ID' line (the model
returned none)`; differs → FRD wins, note. `(REG#\d+)\s+(\d{4})` on raw FRD →
unique `REG#n code` pairs; feeds with empty `lobs` get them all, note.

**B6.5 attribution** (≥ 2 feeds). Group validation and recycle rules by
`norm(rule)`. For a rule on ≥ 2 feeds: (1) its normed text contains a file
pattern or the feed name of every feed it sits on → note `rule kept on all
<n> sources — the FRD names each file: '<rule[:80]>'`; (2) feeds whose paired
columns it mentions (B7.2) = none → note `rule names no column; kept on all
<n> sources as source-level text: …`; (3) exactly one → drop from the others
(`validation_rules`, and null `recycle_rule` if equal), note `rule attributed
to '<feed>' — the only source whose dictionary has the column it names: …`;
(4) several → `attribution` question over those feeds.

**B6.6 pair_files.** Files with a field sheet only. 1 file + 1 feed → pair.
Else tokens: file side `loose_tokens(pattern) ∪ loose_tokens(field_sheet)`;
feed side tokens of every pattern ∪ feed name; a match is equality or either
a prefix of the other; file-side token weight `1 / (#files carrying it)`.
Candidates sorted by score desc, feed idx, file idx; greedy assign skipping
used; **refuse a tie**: skip if another unused candidate has the same score
and shares exactly one of feed/file. Unpaired feeds → `file_pairing`.

**B6.7 derive_targets.** Per feed, layers `stage`/`standard` from
`stage_target`/`standard_target`: `tables` as stated; `catalog`/`schema` =
FRD value (origin `frd`) else `catalog_for`/`schema_for(layer, domain)`
(origin `standards v<NAMING_VERSION>`) else `None` (origin `unknown`) +
`target_gap`; no tables → `target_gap`. Record `load_strategy` and
`load_strategy_code`. Source = `{feed_index, feed_name, layers: {stage:
{tables, catalog, schema, origin, load_strategy, load_strategy_code},
standard: {...}}}` + (set by assess) `file, field_sheet, n_columns`.

**B6.8 assess(spec, content, vdd, vdd_name).** Validate through
`FrdIngestionSpec` (minus `source_file`). enrich; grounding. Blockers:
`no_sources` `The FRD names no source files to ingest — nothing to map.`;
`no_dictionary` `No vendor data dictionary is paired with this FRD. Add
VDD_<same name>.xlsx to the vdds volume — the source columns come only from
it.`; `empty_dictionary` `<vdd> names no columns on any field sheet — nothing
to map.`. pair_files; `columns_by_feed` from paired sheets; attribution;
derive_targets; fill `file/field_sheet/n_columns`. Feeds + VDD but no pairing
and no pairing question → `no_file_pairing` `None of the dictionary's files
could be matched to the FRD's sources.`. Dictionary severity: owners = sources
whose sheet or file the problem names; `field_sheet_missing|field_sheet_empty|
field_sheet_unreadable` with owners → blocker per owner `<feed> is mapped to
dictionary file <file>, which <names a field sheet that is not in the
workbook | names no columns | could not be read>. That source has no columns
to map, so the workbook would carry only the standards' audit rows for it.`;
`missing_datatypes` with owners → `dictionary_types` per owner; else note
`dictionary: <detail>`. Status: blockers → `cannot_generate`; any unanswered →
`needs_input`; else `ready`.

**B6.9 answers.** `record_answer(assessment, qid, value, by, at)` sets
`answer`, recomputes status, `KeyError` if unknown. `apply_answers(spec,
assessment)` → `{pairing_override: {feed_index: pattern}, blank_type_sheets}`:
`unverified`/`weak_match`: KEEP nothing; REMOVE removes from list or nulls
scalar; text replaces (holder = feed or `project`, into `context.target` when
set). `project_id`: set. `attribution`: ALL_SOURCES nothing; else drop the
rule from every feed in `feed_indices` not named. `dictionary_types`:
LEAVE_TYPE_BLANK adds the sheet. `file_pairing`: override. `target_gap`:
tables split on commas (origin `reviewer`); schema that is a known domain →
`schema_for` (origin `reviewer (domain <x>)`); else set (empty→`None`).

### B7. `render.py`

**B7.1 build_rows(source, feed, fields, leave_type_blank)** → `(rows, notes)`.
Per field in sheet order: segment = `segment` or `""` (track first-seen
order). Table for a segment: the layer's table ending `_HDR|_DTL|_TRL` for
`header|detail|trailer`, else first, else `""`; standard falls back to the
stage table. **Type ladder** for standard: `promote_type(vendor)`; if `None`
or equal to the stage default → `type_from_example(example)` (origin
`example value`, count `inferred_from_example`); still `None` → origin
`default`, or `blank (vendor gave no type)` under `leave_type_blank` (count
`blank_types`). Unmatched vendor types → `unpromoted_types`. Standard cell =
`""` if blank-and-leave, else promoted or stage default. Stage cell = stage
default always. Row: `source_column, datatype (vendor word or ""), length,
description, sample (example), phi, mandatory (=required), nullable (=not
required or None), segment, comment (allowed values), business_rule (notes),
fixed_start, fixed_end, fixed_length (=length), position, audit False,
type_origin, type_from_vendor, stage {catalog,schema,table,column,datatype},
standard {...}`, column = source name as-is. Then per segment: data-bearing =
norm segment ∈ `{"", detail, dtl, data, body, record}`; for each
`audit_columns("stage", db)` one row `source_column "NA"`, `audit True`, stage
`(name, datatype)`, standard the same-named standard audit column. Notes:
`{n_rows, n_audit_rows, inferred_from_example, unpromoted_types (sorted),
blank_types, segments}`.

**B7.2 place_rules(feed, rows).** `_column_mentions(text, cols)` = columns
matching `(?<![A-Za-z0-9_])<col>(?![A-Za-z0-9_])` case-insensitive, longest
first. Each validation rule → every mentioned column, else `unattributed`.
`recycle = {text, column: first mention or None}`. Source-level text =
unattributed + `Recycle rule: <text>` when the recycle rule names no column,
newline-joined.

**B7.3 built-in layouts.** Fills: header `D9E1F2`, band `BDD7EE`, mark
`FFF2CC` + italic. `TYPE_LEGEND = "Amber italic DataType = the vendor gave
neither a data type nor an example value for this column, so the ACFC default
applies. Confirm before use."` Mark the standard type cell when origin ∈
{`default`, `blank (vendor gave no type)`}; mark the source type cell when
the vendor gave no type; never audit rows; never example-inferred types.

sheet_per_table (≥ 2 sources): `FILE_DETAILS` header `Vendor | FileName |
File Description | Location | Frequency`, one row per source: source_system,
patterns joined `; `, source-level text, landing_location, frequency.
`VERSION_HISTORY` header `Version | Date | Author | Change Description`, row
`0.1 | <today ISO> | frd-sttm-agent | Auto-generated from <source_file>`. Per
source with rows: sheet `MAPPING-<STAGE TABLE UPPER>`[:31]; row 1 `Source
File Layout` col 1, `Stage Layer` col 9, `Standard Layer` col 14; row 2:
`Database column Name, NULL CHECK, Description, Sample Value, DataType, PHI
Field, Mandatory Field, Comment, Schema, TableName, ColumnName, DataType,
<blank>, Schema, TableName, ColumnName, DataType` + `Recycle Flag` iff a
recycle rule. Data from row 3: name; `NULL`/`Not NULL`/blank from nullable;
description; sample; vendor type; `Yes`/`No`/blank PHI and mandatory; Comment
= placed rules + allowed values newline-joined; stage 4; blank; standard 4;
Recycle Flag `Y ( <text> )` on the named column's row. Freeze `A3`, width 22.
Legend under FILE_DETAILS if any mark.

single_sheet (1 source): sheet titled feed name[:31]; bold-key rows `File(s)`
(patterns newline-joined), `File Generator`, `File Location`, `LOB` (strip
leading `REG#n `, comma-joined), `File frequency`, `Domain` (upper),
`Sub-Domain`, `File type` (`<format> (<delim> delimited)`), then `Recycle
rule` and `Validation rules (source-level)` if present; band row `Source
Layout | … | Stage Layer | … | Standard Layer`; headers source `#, Field Name,
Data Type, Length, Field Length (fixed width), Start position (fixed width),
End Position (fixed width), Segment, PII, Comments, Business Rule`, per target
`Catalog, Schema, TableName, ColumnName, DataType, Mandatory Column, Primary
Key, Field Description`; rows: index, name, type, length, fixed length/start/
end, segment, `Yes`/blank PHI, description or allowed values, placed rules +
notes, then per target the 5 fields, `Yes`/blank mandatory, blank, description.
Freeze under header, width 18, legend 2 rows below if any mark.

**B7.4 render into an approved workbook's layout — P2.** Given `layout_of`
(B8): open the workbook, borrow structure only. sheet_per_table: clear
FILE_DETAILS and VERSION_HISTORY below their headers and write into the mapped
columns; per source take the first unused MAPPING sheet (copy when short),
rename `MAPPING-<TABLE>` (suffix `-<i>` on collision); capture the first data
row's font/fill/border/alignment/number_format per column, delete data rows,
write by role (`("index",None)` → row number; source `datatype` → vendor word
or nothing; `business_rule` gets placed rules only when no `comment` column),
restore styles, apply marks; remove unused MAPPING sheets; report
`unfilled_columns` (unmapped headers). single_sheet: fill `meta_rows` col B
(`file_name_patterns` newline-joined, `lobs` stripped comma-joined,
`file_type`, `domain` upper, else the feed attribute), fill rows, delete other
sheets.

**B7.5 render_workbook(spec, sources, vdd, out_path, layout=None,
pairing_override=None, blank_type_sheets=None).** Per source: pattern =
override or `file`; sheet from the VDD FILES record; rows + placement. No rows
anywhere → `ValueError("no rows to render — no source is paired with a
dictionary file that names columns")`. Choose: P2 layouts per B7.4; else 1
unit → single_sheet; else sheet_per_table. Info: `dialect, layout_from,
marked_types, n_rows, rows_per_source, files_per_source, unpromoted_types,
inferred_from_example, blank_types, rule_placement [{source, by_column,
recycle, unattributed}]` (+ `sheets, unfilled_columns, removed_sheets` in P2).
`preview_rows(...)` → per source `{source, file, n_rows, rows[:limit]}` with
`{source_column, datatype, stage, standard, audit, type_origin}`.

### B8. `reference_layout.py`

`_n` = NFKC + quote/dash map + collapse; `_nl` lower; `loose_tokens(s)` = set
of `[a-z0-9]{4,}` of lower s. Audit marker: source column `na`. Dialects:
sheet_per_table = any `MAPPING-*` sheet (row 1 bands, row 2 headers, data
row 3); single_sheet = first sheet with a header row (≤ 30) holding a cell
starting `field name`, band row above, `key|value` meta rows above that.
Bands: first cell starting `source`, containing `stage layer`, containing
`standard layer`. Header aliases (equality or prefix after `_nl`) — source:
`source_column`: database column name, database name, client data table
column name, field name · `description` · `sample`: sample value, example
values · `datatype`: datatype, data type · `nullable_raw`: null check ·
`phi_raw`: phi field, phi/pii field, pii · `mandatory_raw`: mandatory field,
mandatory, mandatory column · `comment`: comment, comments · `length` ·
`fixed_length`: field length (fixed width) · `fixed_start`: start position
(fixed width) · `fixed_end`: end position (fixed width) · `segment`: segment
(ex:header,trailer,detail), segment · `business_rule`: business rule. Target:
`catalog`, `schema`, `table`: tablename, table name · `column`: columnname,
column name · `datatype`. Unmapped header containing `recycle` → role
`("recycle",None)`; `#` → `("index",None)`. FILE_DETAILS: `vendor`: vendor,
source system, source · `file_name`: filename, file name, file, files ·
`description`: file description, description · `location`: location, landing
location, file location, path · `frequency`: frequency, file frequency.
VERSION_HISTORY: `version, date, author, change`: change description,
description, change, changes. Single-sheet meta: `file_name_patterns`:
file(s), file, files, file name, filename · `source_system`: file generator,
vendor, source system, source · `landing_location`: file location, location,
landing location · `lobs`: lob, lobs, line of business · `frequency` ·
`domain` · `sub_domain`: sub-domain, sub domain, subdomain · `file_type`: file
type, format, file format (longest alias wins).

`layout_of(path)` (P2) → sheet_per_table `{dialect, path, sheets: {name:
{label_row 1, header_row 2, first_data_row 3, width, columns [{index, header,
role}], unmapped}}, file_details {headers, columns}|None, version_history
|None}`; single_sheet `{dialect, path, sheet, label_row, header_row,
first_data_row, width, columns, unmapped, meta_rows [{row, key, field|None}]}`.
`parse_reference_workbook(path)` (index/tools only, never a run) →
`{dialect, feeds: {stage table lower: {sheet, fields, ref_targets}}, meta}`;
fields normalised (`datatype` default `String`, `nullable` = not "not null",
flags yes/y/true).

### B9. `corpus.py`

`name_key(name)`: strip extensions repeatedly (alnum, ≤ 5 chars); split on
non-alnum, lower; pop leading and trailing tokens in `{frd, sttm, vdd, dict}`;
join. `build_index(frds, vdds, reference)` → version **6**:
`{version, generated_at, documents: {stem: {frd, sha256, vdd|null, sttm|null,
ambiguous_name, status, generatable, reason}}, vdds: {name: {n_files,
n_fields, files, problems: [kinds], sha256}}, references: {name: {dialect,
n_sources, n_columns, sha256, error?}}, unpaired_vdds, unpaired_references,
vdd_errors}`. Ambiguous = > 1 FRD, VDD, or STTM shares the key. Eligibility:
ambiguous → `ambiguous`, false, `More than one file shares this name — rename
so FRD_/VDD_/STTM_ pair one-to-one.`; no VDD → `no_dictionary`, false, `No
vendor data dictionary is paired with this FRD. Add VDD_<same name>.xlsx.`;
VDD with 0 fields → `no_dictionary`, false, `<vdd> names no columns — the
vendor returned an empty dictionary.`; STTM → `mapped`, true, `Already has an
approved STTM. A new draft reads only the FRD and the VDD, never the approved
workbook.`; else `ready`, true, `FRD and vendor data dictionary present.`.
Unreadable reference → recorded with `error`. `pairing_rows(index)` → per FRD
`{doc_id, frd_file, frd_sha256, vdd_file, vdd_files, vdd_columns, sttm_file,
sttm_columns, status, generatable, reason, indexed_at}`. `save_index` writes
`reference_sttms/corpus_index.json` (indent 2, sorted). `load_index`: `None`
if absent; `CorpusIndexError` if unreadable or version ≠ 6.

### B10. `pipeline.py`

`Paths(frds, vdds, reference, output)`, `Paths.under(root, …)`, `run_dir`,
`inputs_dir` = `<run>/inputs`. `RUN_FILE="run.json"`, `REPORT_FILE="report.md"`,
`INPUTS_DIR="inputs"`, statuses `failed`, `rendered`. `new_run_id(existing)` =
`run_%Y%m%d_%H%M%S` UTC, `-2`, `-3`… on collision. `sttm_file_name(doc_id)`:
strip leading `frd[_\-\s]*` (ci) → `STTM_<stem>.xlsx`. `save_run` writes
`run.json` via `.part` + replace and regenerates `report.md`.

`choose_layout(paths, doc_id, n_sources)` (P2; Phase 1 returns `None`): want
`sheet_per_table` if > 1 source else `single_sheet`; candidates = reference
workbooks whose `name_key` ≠ the FRD's and name ≠ the own STTM's; first whose
`layout_of` has the wanted dialect and sheets; else `None`.

`run_extract(paths, run_id, doc_id, provider, model, max_tokens, client=None,
triggered_by, render_if_ready=True, frd_file=None, vdd_file=None)`:
1. Save `{run_id, doc_id, created_at, triggered_by, provider, model, status:
   "extracting", error: None, standards_sha256, naming_version,
   engineering_version, inputs: {frd, vdd} | None}`.
2. FRD = `inputs/<frd_file>` if given else the `frds` file with stem
   `doc_id`. Parse → `frd: {source_file, content_sha256, project_id,
   heading_count, table_count, chars}`.
3. Client if none; model name prefixed for databricks.
4. VDD = `inputs/<vdd_file>` or the single `vdds/*.xlsx` with the same
   `name_key` (else `None`). Parse (B2); P2: on `DictionaryError` or 0 fields
   try B3, record `vdd_repair_meta` (with `code_parser_said`), a failed repair
   is recorded not raised. Record `vdd: {file, n_files, n_fields, files,
   problems, normalised_by_model}` | `{file, error, 0, 0, [], [], False}` |
   `None`.
5. Extract. P2: normalised VDD kept under `vdd_normalised`.
6. `extraction` = spec by alias + `source_file`; `extraction_meta`;
   `assessment = assess(...)`; status = assessment status; save.
7. Ready and `render_if_ready` → `run_render`. Any exception → `failed`,
   `error "<Type>: <msg>"`, `traceback`, save, re-raise.

`run_render(paths, run_id)`: blockers → `ValueError`. VDD = `vdd_normalised`
or parse `vdd_source(run)` (uploaded input or volume file). Deep-copy spec and
assessment; `apply_answers`; `choose_layout`; render to a temp file then copy
into the run dir (**sequential write: UC volumes reject seeks**). Record
`applied {extraction, sources, pairing_override, blank_type_sheets}`, `render
{…info, workbook, rendered_at, sha256, unanswered: [ids]}`, status `rendered`,
error `None`. Failures as above.

`answer(paths, run_id, qid, value, by)`: record; if status was `needs_input`
or `ready` set the assessment's. `reindex(paths)`: build + save. `list_runs`,
`summary(run)` = `{run_id, doc_id, status, created_at, triggered_by, error,
n_sources, n_questions, n_answered, n_blockers, workbook, rendered_at}`.
`report_md(run)`: `# STTM run <id> — <doc_id>`; `**Status: <s>**  ·  created
<t>  ·  model <m> via <p>`; `## Error` block; `FRD: \`<file>\` (<chars> chars,
<h> headings, <t> tables)`; `VDD: \`<file>\` (<n> files, <m> columns)` or
`VDD: **none paired**`; `Grounding: <ok>/<checked> identifiers verbatim in the
FRD; <flagged> of <checked> prose fields loosely matched`; `## Cannot generate`
bullets; `## Sources` `- **<name>** ← \`<file>\` (<n> columns) → stage
<c>.<s>.<tables> / standard …`; `## Questions` `- [x| ] (<kind>) <text> →
**<answer>**`; `## Notes`; `## Workbook` (file, rows, dialect, layout, rows per
source, inferred/unpromoted types, unfilled columns, rule placement lines,
unanswered count).

### B11. `notebooks/pipeline.py`

Cell 1: `%pip install "anthropic>=0.60" "pydantic>=2" openpyxl python-docx
pypdf databricks-sdk` then `dbutils.library.restartPython()`. Cell 2: locate
`src/`: notebook path from
`dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()`
(prefix `/Workspace` if missing) → `<parent of notebooks>/src`; fallbacks
`cwd/../src`, `cwd/src`; insert into `sys.path`. Widgets via
`dbutils.widgets.text(name, default)`: `task catalog schema run_id doc_id
frd_file vdd_file provider model triggered_by pairing_table` + four volume
names; `paths = Paths.under(f"/Volumes/{catalog}/{schema}", …)`. `extract`
needs `run_id`+`doc_id` → `run_extract` (print report); `render` needs
`run_id`; `reindex` → index, then `spark.createDataFrame(pairing_rows(index),
schema)` written `mode("overwrite").option("overwriteSchema","true").saveAsTable(f"{catalog}.{schema}.{pairing_table}")`
with schema: `doc_id, frd_file, frd_sha256` string not null; `vdd_file` string
null; `vdd_files, vdd_columns` int; `sttm_file` string null; `sttm_columns`
int; `status` string; `generatable` boolean; `reason, indexed_at` string.
Unknown task raises.

### B12. Backend (`app/backend/`)

`settings.py`: A's config; `IS_DATABRICKS`; `VOLUME_ROOT=/Volumes/<c>/<s>`;
`volume_path(kind)`; `PATHS` = four folders under `LOCAL_ROOT` (local) or a
mirror dir in the container (databricks).

`storage.py` (no-ops locally): `_mirror_dir(remote, local, recursive)` via
`w.files.list_directory_contents`/`download`, always rewrite (`.part` then
replace; `corpus_index.json` can keep its size), delete local files no longer
remote. `pull_documents(force)` mirrors `frds vdds reference_sttms` at most
every 60 s unless forced. `list_remote_runs()` = `run_*` dirs newest first.
`pull_run(run_id)` recursive; missing remote dir is not an error.
`pull_all_runs()`. `push_run_file(run_id, name)`. `push_document(kind, name,
bytes)`. P2: `push_run_input(run_id, name, bytes)` (local always + volume in
databricks mode, because the job opens it).

`jobs.py`: `resolve_job_id()` = `STTM_JOB_ID` or the single job named
`STTM_JOB_NAME` (else `JobError` "set STTM_JOB_ID"). `start(task, run_id,
doc_id, triggered_by, frd_file, vdd_file)` → `w.jobs.run_now(job_id,
job_parameters={task, run_id, doc_id, triggered_by, provider, model, catalog,
schema, frd_file, vdd_file})` → `(job_run_id, run_page_url)`. `wait(id)`: poll
5 s, timeout 1800 s; terminal + `SUCCESS` returns; else `JobError` with the
state message or the task's run-output error.

`runs.py`: `_active: {run_id: {phase, task, doc_id, job_run_id, url, error,
started_at, finished_at}}` under a lock. **One run at a time**: `busy()`;
second start → `RuntimeError("a run is already in progress — one at a time")`.
`_job_or_local(run_id, task, doc_id, by, local_fn, **job_kwargs)` in a daemon
thread: databricks → `jobs.start`, record url, `jobs.wait`, `finally
storage.pull_run`; local → `local_fn()`; exceptions recorded on the entry;
phase ends `done`. `allocate_run_id()` avoids local + remote ids. `start(doc_id,
by, run_id=None, frd_file=None, vdd_file=None)`. `render(run_id, by)`: blockers
→ `ValueError`; busy → `RuntimeError`; push `run.json` first. `answer(...)`:
busy → `RuntimeError`; record; push. `load(run_id)` pulls when missing.
`view(run_id)` = `run.json` minus `traceback`/`vdd_normalised` + `live` +
`preview` (limit 60; failure → `[{"error": …}]`) + `summary` + `sources`
(applied once rendered, else assessed). `list_all()`. P2: `rerun_uploaded`.

`app.py`: FastAPI "FRD to STTM Agent"; on startup `start_reindex(by="startup")`
in a thread (job in databricks mode, pipeline locally); `_who(request)` = first
of `x-forwarded-email`, `x-forwarded-preferred-username`, `x-forwarded-user`,
else `local`; mount `app/frontend/dist` at `/` (`html=True`, `Cache-Control:
no-store` on HTML). Routes:

| Route | Behaviour |
|---|---|
| `GET /api/config` | `{mode, provider, model, catalog, schema, volumes, data_root}` |
| `GET /api/documents` | pull; no index → `{built:false, documents:[], unpaired_vdds:[], unpaired_references:[], vdd_errors:{}, generated_at:null}`; else `built:true`, documents sorted, each = `doc_id` + index entry + `vdd_summary` + `sttm_summary` |
| `POST /api/reindex` 202 · `GET /api/reindex` | `{state: idle|running|done|failed, error, url, finished_at, trigger}` |
| `GET /api/documents/{doc_id}/{frd|vdd|sttm}` | file download; 404 unknown/unpaired/missing |
| `POST /api/documents/upload?kind=` 201 | multipart `file`; suffix check; push; reindex |
| `GET /api/runs` | `{runs: [summary + live]}` |
| `POST /api/runs` 201 | `{doc_id, from_run?}`; 404 not indexed; 400 not generatable (reason); 409 busy → `{run_id, doc_id}` |
| `GET /api/runs/{id}` | view; live but unwritten → `{run_id, doc_id, status:"extracting", live, preview:[], summary:null}`; else 404 |
| `POST /api/runs/{id}/answers` | `{question_id, value}` → `{status, questions}`; 400 unknown; 409 busy |
| `POST /api/runs/{id}/render` 202 | `{run_id, live}`; 400 blockers; 409 busy |
| `GET /api/runs/{id}/workbook` | the `.xlsx`; 404 none |
| `GET /api/runs/{id}/report` | `report.md`, `text/markdown` |
| P2 `POST /api/runs/upload` 201 | multipart `frd`,`vdd`; 400 bad suffix; 409 busy; allocate id, push both under `inputs/`, start → `{run_id, doc_id (FRD stem), frd, vdd}` |
| P2 `GET /api/runs/{id}/inputs/{frd|vdd}` | an uploaded input |

`uvicorn.run(app, host=STTM_BIND_HOST, port=DATABRICKS_APP_PORT or 8000)`.

### B13. UI (`app/frontend/dist/index.html`)

**Phase 1: one static file**, vanilla JS + `fetch`, no build step. The React/
Vite/Tailwind/App Kit stack of the original is **P2 and optional**; it costs
many files and a Node toolchain for no functional gain.

Masthead: "FRD to STTM Agent", subtitle "Source-to-target mapping, drafted
from an approved FRD and the vendor's data dictionary", a runtime chip
("Databricks Apps" | "Local" from `/api/config`). Footer: "Two inputs, both
read · the FRD (by Claude) and the vendor data dictionary (by code) · nothing
is guessed — what is missing is asked" / "Column names are carried as-is from
the dictionary; the client standards decide the target side". A run is
addressable as `?run=<id>`.

**Picker**: four-step strip (Two documents are read / Extract and check / Say
what is missing / Build the STTM). Stat tiles: FRDs in the volume · With a
vendor dictionary (x / n) · With an approved STTM · Can be generated. Groups:
**Ready to map** (`ready`), **Already mapped** (`mapped`; hint: the approved
STTM is never read for content), **Cannot be generated yet** (reason in red).
Row: FRD name (download link) · chip `dictionary · N columns · M files · K
gaps` (gap labels below) or `no vendor dictionary` · chip `approved STTM · N
rows` · button "Generate STTM" → `POST /api/runs`. Panel listing
`unpaired_vdds` and `vdd_errors`. Footer line: reindex state (poll 2 s while
running; job link; failure; last indexed), "The volumes are indexed
automatically when the app starts and after every upload." P2: an "Upload a
pair" panel (FRD accept `.docx,.pdf,.md,.markdown,.txt`; VDD `.xlsx`) →
`POST /api/runs/upload`.

**Run view**: back link; doc id, run id, "view the job run" link when `live.url`.
Status notice (label + explanation):

| status | label | explanation |
|---|---|---|
| `extracting` | Reading the documents | The agent is reading both documents: Claude extracts the source files, target tables and rules from the FRD, and the vendor data dictionary is read column by column (normalised by Claude if it strays from the template). This runs as a Databricks job and usually takes one to three minutes. |
| `ready` | Ready to generate | Everything needed to build the STTM was found in the two documents — nothing is missing. Generate whenever you are ready. |
| `needs_input` | Needs your answers | Below is what the agent extracted from the FRD and the dictionary, and what is still missing to build the STTM. It will not guess those items — answer them, then generate. No second model call is made. |
| `cannot_generate` | Cannot generate | The STTM cannot be built from these documents. The reasons are listed below; fix the input and start a new run. |
| `rendered` | STTM generated | The STTM workbook is ready to download. Every row comes from the vendor dictionary; the target side comes from the FRD and the client standards. |
| `failed` | Failed | The run did not complete. The error is shown below; start a new run once it is addressed. |

Poll `GET /api/runs/{id}` every 3 s while `live.phase == "running"` or status
`extracting`. While running show "Running as a Databricks job · m:ss elapsed"
and the steps (extract: Read the FRD / Read the vendor data dictionary /
Extract … with Claude / Check every identifier verbatim … / Decide the target
side …; render: Apply your answers / Build the rows … / Write the workbook
into the client's own layout). Failed → error text + "Run again" (`POST
/api/runs` same doc; P2 `from_run` for uploaded pairs). Then: four tiles (FRD
`<h> headings · <t> tables`; dictionary `<n> columns · <m> files` or "none
paired"; grounding `<ok> / <checked> verbatim`; questions `<answered> / <n>
answered`) + dictionary gap labels; blockers; **Sources** cards (file, column
count; rows stage/standard `catalog.schema.tables` with origins: `frd` →
"stated in the FRD", `standards v…` → "from the ACFC naming standards (v…)",
`reviewer…` → "your answer", `unknown` → "not stated anywhere — asked below");
**Questions** cards (kind label; source; open/answered badge; radios + "Other…"
text when `free_text`; "Save answer" → `POST …/answers`; answered time/by);
generate panel ("Generate the STTM" / "Regenerate the STTM with these
answers"; "<n> question(s) still open — you can generate now and the workbook
will note them, or answer first."); after render: "Download the STTM workbook"
+ "Run report" and a summary (file, rows, sheets, layout, time,
`inferred_from_example`, `unpromoted_types`, `marked_types` amber note,
`unfilled_columns`, unanswered); **Rows** preview per source (8 rows,
expandable; audit rows muted); collapsible **Notes**.

Kind labels: `unverified` Not found in the FRD · `weak_match` Loose match to
the FRD · `attribution` Which source does this rule apply to? · `project_id`
Conflicting project ids · `file_pairing` Which dictionary file is this source?
· `target_gap` Target not stated · `dictionary_types` The dictionary left data
types blank. Gap labels: `missing_descriptions` some columns have no
description · `missing_datatypes` some columns have no data type ·
`missing_required_flags` some columns have no required flag ·
`missing_phi_flags` some columns have no PHI flag · `missing_segments` some
columns name no record segment · `field_sheet_missing` a field sheet named on
FILES is missing · `field_sheet_empty` a field sheet is empty ·
`template_example_rows` the template's example rows were still present ·
`field_count_mismatch` declared and actual field counts differ ·
`sheet_not_listed` a sheet is not listed on FILES · `no_field_sheet_named` a
FILES row names no field sheet · `files_row_ignored` a FILES row was read as a
note · `normalised_by_model` the workbook did not fit the template and was
normalised by Claude (column names verified) · `model_columns_not_in_workbook`
column names the model returned that are not in the workbook were dropped ·
`file_without_pattern` a FILES row has no file name pattern.

### B14. Tests

`conftest.py`: `make_frd(path, project_id="1234567",
file_pattern="claims_YYYYMMDD.csv", stage_table="clm_claims_stg",
standard_table="clm_claims", schema="stg_clm", extra_paragraphs=())` — docx
with title "Claims Intake FRD", `Project ID: <id> Claims Intake Redesign`,
heading "In Scope" (a long paragraph + a List Bullet), heading "Data
Ingestion Requirements" with `SRQ226433 The process shall load the claims file
daily.`, a 6×2 table (Object/data Format csv · Target Schema · Target Table
Name · Domain and Subdomain `CLAIMS / Intake` · File Name · Standard Table),
heading "Data Quality" with `If the CLAIM_ID column is NULL, reject the record
to the reject table.` and `Process shall fail when file layout is not as per
source dictionary.`. `make_vdd(path, files=None, marker_rows=False)` — FILES
header + one row per file (sheet `file<i>`); field sheets with the template
header; default columns CLAIM_ID int Y N, MEMBER_ID varchar Y Y, AMOUNT
decimal N N; `marker_rows` adds a `delete once replaced` row.
`make_reference_sheet_per_table(path, table="other_table")` and
`make_reference_single_sheet(path)` — another feed's approved STTM in each
dialect with B7.3's exact bands/headers, one data row + one audit row.
`spec_for(...)` canned one-feed spec. `FakeClient(spec, stop_reason)`:
`messages.stream(**kw)` context whose `get_final_message()` returns the spec
as JSON text, usage 10/5. Fixture `data_root`: four folders, one synthetic
pair, one other-feed STTM.

Required tests (Phase 1): `test_frd_parsing`, `test_dictionary`,
`test_standards`, `test_completeness`, `test_render`, `test_corpus`,
`test_pipeline`, `test_backend`. P2: `test_dictionary_repair`,
`test_eval_against_approved`, borrowed-layout cases in `test_render`. Section
E lists the assertions.

### B15. Tools

`tools/push_documents.py <folder> [--dry-run] [--no-reindex]`: route by prefix
(`FRD_*`+FRD suffix → `frds`; `VDD_*|DICT_*.xlsx` → `vdds`; `STTM_*.xlsx` →
`reference_sttms`; others skipped), create missing managed volumes, upload via
Files API, run the job `task=reindex` (id from `STTM_JOB_ID` or the single job
named `STTM_JOB_NAME`), poll to completion; reads `CATALOG`, `SCHEMA`.
P2 `tools/eval_against_approved.py <agent.xlsx> <approved.xlsx> [--run
run.json] [--out eval.xlsx]`: both through `parse_reference_workbook`, tables
by stage table, rows by normed source column (audit by stage column), twelve
fields each classified `match | question | agent_blank | approved_blank |
diff`, summary workbook.

---

## C. Deploy in ACFC

**Preflight.** `databricks current-user me`; `databricks serving-endpoints get
databricks-claude-sonnet-5` (READY); `databricks apps list` (error = Apps
off); `databricks catalogs list` (pick `<catalog>`). Ask a human: serverless
jobs on? rights to create schema/volumes/job/App? Python 3.11+ in Apps?

**Names.** `<catalog>` into `databricks.yml`, `app.yaml`, notebook default.

**Schema, volumes, job.**
```bash
databricks schemas create sttm_agent <catalog>
for v in frds vdds reference_sttms output_sttms; do databricks volumes create <catalog> sttm_agent $v MANAGED; done
databricks bundle validate -t dev && databricks bundle deploy -t dev && databricks bundle summary -t dev
```
Dev mode prefixes the job name `[dev <user>]`: put the numeric id in `app.yaml`
`STTM_JOB_ID`. No serverless → add to the job `job_clusters: [{job_cluster_key:
sttm, new_cluster: {spark_version: "15.4.x-scala2.12", node_type_id: "<small
node>", num_workers: 1}}]` and `job_cluster_key: sttm` on the task.

**Documents.** `export CATALOG=<catalog> SCHEMA=sttm_agent; python
tools/push_documents.py <folder> --dry-run; python tools/push_documents.py
<folder>`. Verify `frd_pairing` and `corpus_index.json`. Manual:
`databricks bundle run frd_sttm_pipeline -t dev --params task=reindex`.

**Smoke test (billed).** `databricks bundle run frd_sttm_pipeline -t dev
--params task=extract,run_id=smoke_$(date +%Y%m%d_%H%M%S),doc_id=<doc_id>`;
`report.md` in the output volume shows the status. A JSON/format error = a
model slip; run once more.

**App.**
```bash
databricks apps create frd-sttm-review-app --description "FRD to STTM review app"
databricks apps get frd-sttm-review-app -o json          # service_principal_client_id
databricks sync . /Workspace/Users/<you>/frd-to-sttm-agent-app --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude 'local_data' --exclude '.databricks' --exclude '.DS_Store'
```
Never sync `.git` (a pack over 10 MB fails the deploy). Keep
`python-multipart` in `requirements.txt`.

**Grants (exactly these).**
```bash
SP=<service principal client id>
databricks grants update CATALOG <catalog> --json "{\"changes\":[{\"principal\":\"$SP\",\"add\":[\"USE_CATALOG\"]}]}"
databricks grants update SCHEMA <catalog>.sttm_agent --json "{\"changes\":[{\"principal\":\"$SP\",\"add\":[\"USE_SCHEMA\"]}]}"
for v in frds vdds reference_sttms; do databricks grants update VOLUME <catalog>.sttm_agent.$v --json "{\"changes\":[{\"principal\":\"$SP\",\"add\":[\"READ_VOLUME\"]}]}"; done
databricks grants update VOLUME <catalog>.sttm_agent.output_sttms --json "{\"changes\":[{\"principal\":\"$SP\",\"add\":[\"READ_VOLUME\",\"WRITE_VOLUME\"]}]}"
databricks jobs update-permissions <job id> --json "{\"access_control_list\":[{\"service_principal_name\":\"$SP\",\"permission_level\":\"CAN_MANAGE_RUN\"}]}"
```

**Run.** `databricks apps start frd-sttm-review-app && databricks apps deploy
frd-sttm-review-app --source-code-path /Workspace/Users/<you>/frd-to-sttm-agent-app`;
open the url; walk section E's workspace checks; `databricks apps stop
frd-sttm-review-app`. Redeploy = sync + deploy.

**Reached through the unified agent console (optional, 2026-09-04).** The
program's one-screen console (repo `unified-agent-console`, Databricks App
`unified-agent-console`) reverse-proxies this backend under `/api/sttm/*`
using the console's own service principal. To light that side up in a
workspace: deploy this App there, grant the console's
`service_principal_client_id` **CAN_USE** on `frd-sttm-review-app`
(`databricks apps update-permissions frd-sttm-review-app --json
'{"access_control_list":[{"service_principal_name":"<console sp>",
"permission_level":"CAN_USE"}]}'`), set `STTM_BACKEND_URL` to this App's
URL in the console's `app.yaml` (same workspace only — the SP token does
not cross workspaces) and redeploy the console. Until then the console
reports the FRD→STTM side as "not configured for this deployment". The
console surfaces every `detail` string and status code of section B12
verbatim, so keep them meaningful (409 one-at-a-time, 400 cannot-generate,
404 unknown run, 502 corpus index).

---

## D. Verbatim artifacts

Copy each block into its file exactly. Keys beginning with `_` in D1 and D2
are provenance the code never reads; keep them.

### D1. `standards/naming_standards.json`

```json
{
  "version": "1.1.0",
  "kind": "naming_standards",
  "_about": [
    "Transcription of the client's naming standards document into versioned config.",
    "Precedent and doctrine: contracts/frd_label_contract.json — loaded at start-up by a",
    "loader that fails loudly if the file is missing or unversioned, with NO hardcoded",
    "fallback. A rule baked into Python is invisible to the reviewer, silently stale when",
    "the client revises the document, and not re-derivable in the ACFC rebuild.",
    "",
    "Every vocabulary below is VERBATIM from the source document. Anything not in that",
    "document is marked _status UNSOURCED and must never be silently applied — see",
    "column_rules."
  ],
  "source_documents": [
    {
      "name": "EDO Data Engineering Naming Standards.docx",
      "sha256": "508575aaeca4e078952e43177426eafbdffd4c94566dd9775e779d8b8b70c83f",
      "read_on": "2026-08-26",
      "tables_transcribed": [5, 6, 7, 8, 9, 10, 11, 12]
    }
  ],
  "vocabularies": {
    "data_layers": {
      "_source": "table 12 'Data Layers'",
      "values": {
        "RAW": "RAW", "STAGE": "STG", "STANDARD": "STD", "GOLD": "GLD", "CONSUMPTION": "CMP",
        "EVENTHUB": "EVN", "ON-PREM": "ONP", "MDM PUBLISH": "MDM", "RDM PUBLISH": "RDM"
      }
    },
    "domains": {
      "_source": "table 8 'Domain/Subdomain'",
      "_incomplete": "The SD FRD's domain 'sdoh' is NOT in this table. Real documents use domains the standard does not list, so an unknown domain must fall through to the FRD's own value and gate — never be invented.",
      "values": {
        "CLAIMS": "CLM", "CLINICAL": "CLIN", "PROVIDER": "PRV", "NETWORK": "NWK", "VISION": "VISN",
        "PHARMACY": "RX", "DENTAL": "DNTL", "MEMBER": "MBR", "ENROLLMENT": "ENRL", "ELIGIBLITY": "ELIG",
        "COVERAGE": "CVRG", "LABORATORY": "LAB", "MEDICATION": "MED", "IMMUNIZATION": "IMNZ",
        "VITAL SIGN": "VTL", "GENERIC": "GNR"
      }
    },
    "load_strategies": {
      "_source": "table 11 'Load Strategy'",
      "_note": "These four are the sanctioned set. CodeGen's FrdContract enum currently accepts only {'Truncate and Load','Append'} and therefore rejects the SFMC FRD, which states 'Upsert'. The enum is wrong, not the FRD.",
      "values": {
        "TRUNCATE & LOAD": "TRUNC", "TRUNCATE AND LOAD": "TRUNC", "APPEND": "INSRT",
        "UPDATE ELSE INSERT": "UPSRT", "UPSERT": "UPSRT", "EXTRACTS": "EXTR"
      },
      "_alias_note": "'Truncate and Load' and 'Upsert' are not in the document; they are the spellings real FRDs use for the sanctioned strategies above. Aliases, not new strategies."
    },
    "product_codes": {
      "_source": "table 6 'Project / Use Case'",
      "values": {
        "CMS – CONSUMPTION LOAD, JSONS CREATIONS": "CMS", "HEDIS2.0": "HDS", "WIDER CIRCLE": "WDC",
        "MILLIMAN": "MMN", "FEE SCHEDULE": "FSC", "SOMATUS": "SOM", "DIGITAL": "DGT", "ACTUARIAL": "ACT",
        "MDM/RDM": "MDM", "DATA LAKE": "DLK", "EDH": "EDH"
      }
    },
    "sub_product_codes": {
      "_source": "table 7 'Project / Use Case'",
      "values": {
        "BITS2.0": "BCL", "LAKE TO LAKE": "LTL", "HVR": "HVR", "FHIR": "FHR",
        "DATA SCIENCE -SERVING LAYER (SANDBOX)": "BDS", "FORCAST": "FCT", "NO SUB-PRODUCT": "NSP"
      }
    },
    "frequencies": {
      "_source": "table 10 'Frequency'",
      "values": {
        "HOURLY": "HRL", "DAILY": "DLY", "WEEKLY": "WKL", "FORTNIGHTLY": "FRT", "MONTHLY": "MTH",
        "QUARTERLY": "QTR", "YEARLY": "YRL", "AD-HOC": "ADH", "ONE TIME": "ONT", "HISTORY DATA LOAD": "HST"
      }
    },
    "region_lob": {
      "_source": "table 9 'Region'",
      "values": {
        "REGION 1": "REG_1", "REGION 2": "REG_2", "REGION 5": "REG_5", "REGION 6": "REG_6",
        "EXCHANGE": "REG_EXCH", "MMP": "REG_MMP", "ALL REGION": "REG_ALL", "ALL": "ALL"
      }
    },
    "source_target": {
      "_source": "table 5 'Source / Target'",
      "values": {
        "FACETS": "FACETS", "JIVA": "JIVA", "CDR": "CDR", "UTIL": "UTIL",
        "ENTERPRISE DATA WAREHOUSE": "EDWH", "ELTSS": "LTSS", "FILE": "FILE"
      }
    }
  },
  "derivations": {
    "_about": "How an STTM cell is COMPUTED from the vocabularies above plus the FRD's Structural Metadata. Each carries its evidence and its confidence; anything OBSERVED rather than STATED must be gated, not silently applied.",
    "schema": {
      "stage": {
        "pattern": "STG_{domain}",
        "_status": "OBSERVED",
        "_evidence": "CAQH STTM stage schema 'STG_MBR' (domain MEMBER→MBR); SFMC FRD 'Stage: stg_mbr'; SD STTM 'stg_sdoh'. Three documents agree on the shape.",
        "_case": "UNSOURCED — CAQH renders it upper (STG_MBR), SD and SFMC lower (stg_sdoh, stg_mbr). The standards document states no case rule. Follow the matched template's case; gate if there is no template."
      },
      "standard": {
        "pattern": "{domain}",
        "_status": "OBSERVED",
        "_evidence": "CAQH standard schema 'MBR'; SFMC FRD 'Standard: mbr'; SD STTM 'sdoh'. The standard layer drops the layer prefix."
      }
    },
    "catalog": {
      "stage": "PR_DLK",
      "standard": "PR_STD",
      "_status": "OBSERVED_SINGLE_PAIR",
      "_evidence": "CAQH STTM only: stage Catalog 'PR_DLK', standard Catalog 'PR_STD'. The SD STTM has NO catalog columns at all.",
      "_caution": "The two are not built the same way: PR_DLK is PR_ + the PRODUCT code for Data Lake, while PR_STD is PR_ + the STANDARD LAYER abbreviation. That is one workbook's convention, not a stated rule. Do not generalise to other layers without a second mapped pair; gate instead."
    }
  },
  "column_rules": {
    "_status": "PARTIALLY_SOURCED",
    "_about": [
      "NEITHER standards document contains a target COLUMN naming rule or an audit-column",
      "set. Both were grepped on 2026-08-26 for SRC_FILE_NAME / REC_CREATION_TIME /",
      "REC_UPDATED_TIME / case rules / prefix rules: no hits. The documents stop at object",
      "level (layers, domains, catalogs, load strategy).",
      "",
      "v1.1.0 (2026-08-26) splits what was one UNSOURCED blob, after measuring each rule",
      "against the two mapped STTMs. One naming convention reproduces perfectly and is now",
      "DERIVABLE; the other reproduces 19% and is not a rule at all. The audit columns turned",
      "out to be consistent across BOTH pairs once segment role is accounted for, so they",
      "move out of the per-convention blocks into a shared set."
    ],
    "confirmed_by": null,
    "audit_columns": {
      "_about": [
        "Target columns with NO source field. Measured over both mapped STTMs: the core three",
        "appear on EVERY table of BOTH workbooks, in BOTH layers, with identical datatypes",
        "(case differs only: 'String' vs 'string'). They are not convention-specific.",
        "",
        "LOB appears on data-bearing tables only -- CAQH's EXT_TPL_CAQH_DTL and all three SD",
        "tables -- and NOT on CAQH's header/trailer control tables, which is coherent: a line",
        "of business is a per-record business attribute, meaningless on a file control record.",
        "",
        "An EARLIER NOTE IN THIS REPO CLAIMED THE TWO WORKBOOKS DISAGREED (three columns vs",
        "four). They do not. The apparent conflict was reading only CAQH's header segment."
      ],
      "core": {
        "_status": "OBSERVED_BOTH_PAIRS",
        "_evidence": "Present on all 3 CAQH tables and all 3 SD tables, stage and standard.",
        "applies_to": "every table",
        "columns": [
          {"name": "SRC_FILE_NAME", "datatype": "String", "layers": ["stage", "standard"]},
          {"name": "REC_CREATION_TIME", "datatype": "timestamp", "layers": ["stage", "standard"]},
          {"name": "REC_UPDATED_TIME", "datatype": "timestamp", "layers": ["stage", "standard"]}
        ]
      },
      "data_bearing_only": {
        "_status": "OBSERVED_BOTH_PAIRS",
        "_evidence": "CAQH EXT_TPL_CAQH_DTL and all three SD tables; absent from CAQH's HDR and TRL.",
        "applies_to": "tables carrying business records, not file header/trailer control records",
        "columns": [
          {"name": "LOB", "datatype": "String", "layers": ["stage", "standard"]}
        ]
      },
      "feed_specific": {
        "_status": "OBSERVED_SINGLE_PAIR",
        "_evidence": "FILE_TYPE appears on CAQH's detail table only. One workbook, one table.",
        "_note": "Do NOT emit for a new feed. Recorded so a reviewer recognises it, and so the template-fill path is not mistaken for an agent error when it appears.",
        "columns": [
          {"name": "FILE_TYPE", "datatype": "String", "layers": ["stage", "standard"]}
        ]
      },
      "_case_note": "CAQH writes datatypes 'String'/'timestamp', SD writes 'string'/'timestamp'. No case rule is stated anywhere; follow the matched template's case."
    },
    "conventions": {
      "as_is": {
        "_status": "DERIVABLE",
        "_evidence": "Measured 2026-08-26 over the SD STTM: target column == source column on 399 of 399 non-audit rows across all three sheets. 100%. Every apparent miss was an audit row (source 'NA'), not a naming failure.",
        "target_column": "as_is",
        "prefix": "",
        "strip_source_prefix": false
      },
      "prefixed_upper_snake": {
        "_status": "NOT_DERIVABLE",
        "_evidence": "Measured 2026-08-26 over the CAQH STTM: the rule 'TPL_' + UPPER_SNAKE(source) reproduces 22 of 115 rows = 19.1%. 46 distinct word substitutions, and 58 of the 93 misses change the WORD COUNT -- words are dropped entirely ('Member ID Family Sequence Number' -> TPL_FAMILY_SEQ_NO; 'National Payer ID' -> TPL_PAYER_ID).",
        "target_column": "upper_snake",
        "prefix": "TPL_",
        "_prefix_lead": "TPL_ equals the CAQH source's own Sub-Domain value ('TPL'). One data point -- check a third mapped STTM before encoding it as a rule.",
        "_why_not_derivable": [
          "These are not abbreviations of the source names; they are an EXISTING WAREHOUSE",
          "VOCABULARY. 'Member ID' -> TPL_MEME_ID and 'Subscriber or Dependent indicator' ->",
          "TPL_SBSB_IND: MEME, SBSB and GRP are Facets column names, and the CAQH FRD names",
          "Facets as the incumbent on-prem system. So the missing input is not a rule document",
          "-- it is a TERM CATALOG of the columns that already exist in the client's warehouse.",
          "",
          "The right source for it is information_schema.columns over the existing PR_STD /",
          "PR_DLK tables, which needs read access rather than a client meeting. Matching a",
          "source field to an existing column is then a scored alignment against a REAL column,",
          "never a generated string. Until that exists, this convention must be gated."
        ]
      }
    },
    "default_convention": null,
    "_default_note": "Deliberately null. Which convention applies to a given FRD is stated in neither FRD and in neither standards document; today it is inferred from the matched template STTM. Selecting one by default would reintroduce the implicit borrow this file exists to remove. This is an open question for the client."
  }
}
```

### D2. `standards/engineering_standards.json`

```json
{
  "version": "1.1.0",
  "kind": "engineering_standards",
  "_about": [
    "Transcription of the STTM-relevant rules from the client's engineering/coding",
    "standards document. Most of that document is Azure Data Factory and Databricks BUILD",
    "practice (IIG Framework, Key Vault, ForEach parallelism, dynamic linked services) —",
    "that is the CodeGen agent's input, not this agent's, and is deliberately not",
    "transcribed here. Only rules that decide an STTM cell or a gate appear below.",
    "",
    "Same doctrine as naming_standards.json: versioned, loaded loudly, no hardcoded",
    "fallback, and anything not stated in the document is marked UNSOURCED."
  ],
  "source_documents": [
    {
      "name": "EDO Data Engineering Coding Standards.docx",
      "sha256": "8648d329c28d567c49cc41a6bab8183ceca12d687d92d8313c0ef6d310f8c2e8",
      "read_on": "2026-08-26"
    }
  ],
  "type_promotion": {
    "basis": "source_datatype",
    "_status": "STATED",
    "_quote": "Standard table should be created with proper datatype as per the source column datatype.",
    "_never": "sample_value",
    "_corroboration": [
      "docs/THREE_INPUT_ARCHITECTURE.md §8a: in the SD golden STTM all 90 standard-layer",
      "Decimal(10,2) cells are exactly the rows whose SAMPLE had a fractional part, and a",
      "_pct column whose sample was 0 stayed String. Sample-driven typing reproduces the",
      "golden workbook but is NOT the client's rule — the golden-pair eval must report",
      "those rows as disagreements, not as agent errors."
    ],
    "stage_default": "String",
    "_stage_default_status": "OBSERVED",
    "_stage_default_evidence": "Both mapped STTMs type every stage-layer column String regardless of source type. Not stated in the document.",
    "promotions": {
      "_status": "UNSOURCED",
      "_note": "The mapping from a vendor datatype to a standard-layer datatype is not tabulated anywhere. Observed pairs only, from the two mapped STTMs.",
      "observed": [
        {"source_type": ["int", "integer"], "standard": "Int"},
        {"source_type": ["numeric", "decimal"], "standard": "Decimal(10,2)"},
        {"source_type": ["varchar", "char", "string", "alpha numeric"], "standard": "String"},
        {
          "source_type": ["datetime2", "date", "timestamp"],
          "standard": "String",
          "_caution": "CAQH types 20 datetime2 source fields but renders them String in BOTH layers ('Load as is'). Do not promote dates without checking the convention in force."
        }
      ]
    },
    "infer_from_example": {
      "_status": "OBSERVED",
      "_about": [
        "When the vendor declares no specific type (String / blank), the standard-layer type may be",
        "inferred from the vendor's EXAMPLE VALUE in the data dictionary. Measured 2026-08-28 on the",
        "approved SD STTM: an example with a decimal point -> Decimal(10,2) on 139 of 139 rows; no",
        "decimal point -> String on 241 of 258. The column NAME does not predict the type (`_pct` is",
        "Decimal 93 / String 42; every `_count` column is String), so the name is deliberately not used.",
        "Stage stays String regardless. Switch off with enabled=false; every inferred cell is recorded",
        "in the run's provenance as 'example value'."
      ],
      "enabled": true,
      "decimal_point_example": "Decimal(10,2)"
    }
  },
  "data_quality": {
    "recycle_flag": {
      "_status": "STATED",
      "_quote": "NULL check on the referential column(s), If the recycle flag is enabled.",
      "column_name": "Recycle Flag",
      "_column_name_status": "OBSERVED",
      "_column_name_evidence": "The rendered column name comes from the render path (04) and the mapped STTMs, not from the document."
    },
    "rejects": {
      "_status": "STATED",
      "_quote": "All the rejects need to be captured in the reject table and email alert generated for the Production Support team.",
      "reject_table": null,
      "_reject_table_note": "The document mandates a reject table but does not name it. UNSOURCED — ask before emitting a name.",
      "notify": "Production Support team"
    },
    "apply_at": {
      "_status": "STATED",
      "_quote": "Ensure to apply the proper Data Quality rules when loading the data into the Stage and Standard tables.",
      "layers": ["stage", "standard"]
    }
  },
  "run_control_table": {
    "_status": "STATED",
    "_about": "The run-log shape the client's pipelines write. Not an STTM cell — recorded so the agent never invents a different one, and so CodeGen has it.",
    "columns": ["OBJECT_NAME", "SRC_REC_COUNT", "TGT_REC_COUNT", "REJECTED_REC_COUNT", "EXEC_STATUS", "ERROR_MESSAGE", "BATCH_DATE"]
  },
  "security": {
    "_status": "STATED",
    "no_hardcoded_credentials": true,
    "secret_store": "Azure Key Vault",
    "suppress_pii_in_activity_logs": true,
    "_note": "Recorded because it constrains the landing-zone probe of docs/THREE_INPUT_ARCHITECTURE.md §7: raw rows must never reach a model or a log line."
  }
}
```

### D3. `src/frdsttm/models.py`

```python
"""
frdsttm.models — the extraction schema (what Claude reads out of an FRD).

`FrdIngestionSpec` is rendered into the extraction prompt as a JSON schema —
the per-field descriptions ARE the extraction guidance — and the model's
answer is validated back into it (`extra="forbid"` is the drift guard).
No Databricks or Spark dependency.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Project(_Model):
    """Document-level project identification."""

    project_id: Optional[str] = Field(default=None, description="Project identifier exactly as stated, e.g. '1007412'. Null if not stated.")
    project_name: Optional[str] = Field(default=None, description="Project name as stated on the title page or header.")
    business_context_summary: Optional[str] = Field(default=None, description="1-3 sentence summary of the business goal, using the document's own terms.")


class AcdItem(_Model):
    """Row of the Assumptions, Constraints & Dependencies table."""

    name: Optional[str] = Field(default=None, description="The 'Name' cell.")
    description: Optional[str] = Field(default=None, description="The 'Description' cell.")
    acd_type: Optional[str] = Field(default=None, description="The stated type: Assumption, Constraint, or Dependency (normalize singular/plural to singular).")


class TableTarget(_Model):
    """Stage (silver) or Standard (gold) layer target as stated in the document."""

    catalog: Optional[str] = Field(default=None, description="Catalog/workspace if stated (e.g. 'PR_DLK' for the stage layer, 'PR_STD' for the standard layer). Null otherwise.")
    schema_: Optional[str] = Field(default=None, alias="schema", description="Schema name (e.g. 'stg_mbr' or 'stg_sdh' for the stage layer; 'MBR', 'sdh', or 'care' for the standard layer).")
    tables: List[str] = Field(default_factory=list, description="All table names for this feed at this layer, verbatim.")
    load_strategy: Optional[str] = Field(default=None, description="As stated, e.g. 'Truncate and Load' for the stage layer or 'Append' for the standard layer.")


class Feed(_Model):
    """One distinct source file/feed the document requires to be ingested.

    A feed is one source file pattern (or one family of patterns sharing a
    layout) with its own target tables. If the document describes three files
    with three target tables, emit three feeds.
    """

    feed_name: Optional[str] = Field(default=None, description="Short name for the feed, preferring the document's own naming (e.g. a target table name like 'cv_community_risk', or 'CVX Coverage Inbound Files').")
    source_system: Optional[str] = Field(default=None, description="The vendor/source that produces the file, e.g. 'CVX', 'Civic Vantage (CV)', a state system. Null if not stated.")
    file_name_patterns: List[str] = Field(default_factory=list, description="Every file name pattern stated for this feed, verbatim including date placeholders and wildcards (e.g. 'demographic_extract_CCYY_MM.csv', 'YYYYMMDD_2044_1540_CoverageReport_*_P_I_1_T.txt').")
    file_format: Optional[str] = Field(default=None, description="File type as stated: csv, psv, txt, fixed-width, etc. Null if not stated.")
    delimiter: Optional[str] = Field(default=None, description="Field delimiter if stated (e.g. ',', '|'). Null if not stated.")
    record_segments: List[str] = Field(default_factory=list, description="Record segments if the file is multi-record (e.g. ['Header','Detail','Trailer']). Empty array if the document does not describe segments.")
    frequency: Optional[str] = Field(default=None, description="Delivery frequency as stated, e.g. 'Weekly Monday 8 PM', 'Monthly', 'Yearly twice (Jan-Feb and Aug-Sep)'.")
    load_windows_sla: List[str] = Field(default_factory=list, description="Stated load-timing/SLA rules, close to verbatim (e.g. 'File should be loaded before business hours 8 AM', 'received between 11-15th of every month before 5 PM EST').")
    lobs: List[str] = Field(default_factory=list, description="Lines of business in scope for this feed, as stated: names like 'OHDS', bare codes like '0100', or Region/LOB code pairs listed in metadata tables (e.g. 'REG#1 0100') - capture every code listed.")
    domain: Optional[str] = Field(default=None, description="Data domain if stated, e.g. 'Member'.")
    sub_domain: Optional[str] = Field(default=None, description="Sub-domain if stated, e.g. 'Third Party Liability', 'Social Determinants of Health'.")
    landing_location: Optional[str] = Field(default=None, description="ADLS/landing path verbatim if stated, e.g. 'mftlanding/inbound/member/coverage/cvx'. Null if not stated.")
    stage_target: Optional[TableTarget] = Field(default=None, description="Stage (silver) layer target as stated.")
    standard_target: Optional[TableTarget] = Field(default=None, description="Standard (gold) layer target as stated.")
    validation_rules: List[str] = Field(default_factory=list, description="Stated processing/validation rules for this feed, one per rule, close to verbatim. Check ALL sections, especially 'Data Ingestion Requirements' subsections ('Data Quality', 'Technical Metadata', 'Administrative Metadata'): rules are often written as prose inside a single Description cell that covers several files at once — split them and attribute each rule to the feed whose file or table it names, even when nearby template rows read 'NA' or are blank. Examples: 'Process shall fail when file layout is not as per source dictionary', 'If the ZIP_CODE column is NULL, reject the record to the reject table', 'Member ID validation should be performed against CoreMember for existence', 'LOB_ID field should be mapped to Payer Area field'.")
    recycle_rule: Optional[str] = Field(default=None, description="Any stated recycle/retry handling for unmatched or rejected records, including a dedicated recycle table, a recycle flag, or a day window (e.g. 'Invalid records moved to Recycle table, retained 15 days', 'Recycle Flag enabled for 7 days: check SUBS_ID against PR_STD.COREMEMBER.CM_SUBS_MASTER for GRP_CK = 47'). Capture the window and any reference table/condition verbatim. Often stated in prose inside Data Quality sections rather than in a labeled row — a template row saying 'NA' does not override a prose statement that names this feed's file. Null only if the document truly states none for this feed.")
    history_backfill: Optional[str] = Field(default=None, description="Stated historical/backfill requirements, e.g. 'History files up to 3 years should be loaded; one-time movement from O drive to ADLS'. Null if none.")
    archive_retention: Optional[str] = Field(default=None, description="Stated archive/retention schedule, e.g. 'Files: 7 Years; Data: Corporate retention schedule'. Null if none.")
    phi_pii_notes: Optional[str] = Field(default=None, description="Any stated sensitivity/masking notes for this feed's data. Null if none.")
    sttm_reference: Optional[str] = Field(default=None, description="How the document refers to the accompanying mapping/STTM document for this feed (e.g. 'Refer CVX STTM'). Copy the cited cell VALUE(S) verbatim and nothing else: never include the row label they sit under (e.g. 'Link to STTM'), never add words. If more than one cell cites it, join the values with '; '. Null if none.")
    requirement_ids: List[str] = Field(default_factory=list, description="Requirement identifiers in the document that this feed's facts came from, verbatim (e.g. 'SRQ226433', 'MDST231070', 'SIR226434', 'NFR226417'). Provenance for traceability.")


class FrdIngestionSpec(_Model):
    """Ingestion specification extracted from a Functional Requirements Document (FRD) for onboarding vendor/state source files into the data lake (source-to-stage-to-standard). Extract ONLY facts stated in the document. Use null (or an empty array) for anything the document does not state. Never invent file names, table names, schemas, schedules, or rules. Preserve identifiers, file name patterns, paths, and table names verbatim, including placeholders like YYYYMMDD."""

    project: Optional[Project] = Field(default=None, description="Document-level project identification.")
    in_scope: List[str] = Field(default_factory=list, description="Scope items listed under 'In Scope', one string per substantive item, close to verbatim. Skip scaffolding lead-ins that carry no content (e.g. 'Below LOB's is in Scope.', 'Below are the file name & description...').")
    out_of_scope: List[str] = Field(default_factory=list, description="Items listed under 'Out of Scope', one string per item.")
    assumptions_constraints_dependencies: List[AcdItem] = Field(default_factory=list, description="Rows of the Assumptions, Constraints & Dependencies table.")
    feeds: List[Feed] = Field(default_factory=list, description="One entry per distinct source file/feed the document requires to be ingested. A feed is one source file pattern (or one family of patterns sharing a layout) with its own target tables. If the document describes three files with three target tables, emit three feeds.")
    system_interfaces: List[str] = Field(default_factory=list, description="Stated system interface requirements not tied to a single feed (e.g. connectivity between the data lake and an on-prem application), one summary string per interface with its requirement id if stated.")
    open_items: List[str] = Field(default_factory=list, description="Anything the document marks as TBD, pending, or to-be-provided (e.g. 'SR# for SFG Template: TBD', 'History files start date to be provided by Source team').")
```

### D4. Extraction prompts

System (`SYSTEM_PROMPT`; paragraphs joined by blank lines):

```
You are extracting a structured feed-level ingestion specification from a Functional Requirements Document (FRD) for onboarding vendor/state source files into a healthcare data lake (source-to-stage-to-standard).

Extract ONLY facts stated in the document. Use null (or an empty array) for anything the document does not state. Never invent file names, table names, schemas, schedules, or rules. Preserve identifiers, file name patterns, paths, and table names verbatim, including placeholders like YYYYMMDD.

Per-feed rules are often written as prose inside 'Data Ingestion Requirements' subsections and cover several files at once — split them and attribute each rule to the feed whose file or table it names, even when nearby template rows read 'NA' or are blank. Recycle rules and column-conditioned rules are especially prone to this attribution problem; downstream code will gate ambiguities for human review, so it is safer to attach a rule to every plausibly-named feed than to guess a single owner.
```

User (`build_prompt`):

```
Extract the ingestion specification from the FRD below.

Respond with ONLY a single JSON object (no markdown fences, no prose) that validates against this JSON schema. Emit every property explicitly; use null or [] for anything the document does not state; never add properties not in the schema.

JSON SCHEMA:
<json.dumps(FrdIngestionSpec.model_json_schema(), indent=2)>

FRD DOCUMENT (parsed markdown):
<the FRD markdown>
```

### D5. Dictionary-repair prompt and models (P2)

System:

```
You normalise a vendor's data dictionary spreadsheet into a fixed structure. The workbook was written by a vendor and may not follow the template: headers may be renamed, merged, split across rows, or missing; sheets may be organised differently. Recover the structure faithfully. Copy every column name exactly as written. Use null for anything the workbook does not state. Never invent a file, a column, a data type or a flag.
```

User:

```
Normalise the vendor data dictionary below into the JSON structure described by this schema. Respond with ONLY one JSON object (no markdown fences, no prose).

JSON SCHEMA:
<json.dumps(RepairedDictionary.model_json_schema(), indent=2)>

WORKBOOK (one line per row, cells separated by ' | '):
<the workbook text>
```

Pydantic models, `extra="forbid"`:
`RepairedFile`: `file_name_pattern: str` "The delivered file's name pattern,
verbatim from the workbook." · `file_title: Optional[str]` · `format:
Optional[str]` "csv, psv, txt, fixed-width… verbatim if stated." ·
`delimiter: Optional[str]` · `header_row: Optional[bool]` "True if the file
carries a header row, False if stated not to, null if unknown." ·
`field_sheet: str` "The name of the sheet that lists this file's columns,
exactly as the workbook names it." · `multi_record: Optional[bool]`.
`RepairedField`: `position: Optional[int]` · `name: str` "The column name
EXACTLY as written in the workbook. Never invent one." · `datatype` ·
`length` · `required: Optional[bool]` "True for Y/yes/required/not null,
False for N/no/optional, null if not stated." · `description` ·
`allowed_values` · `example` · `phi: Optional[bool]` · `segment: Optional[str]`.
`RepairedDictionary` (docstring "A vendor data dictionary normalised into the
template shape. Use ONLY what the workbook text contains: never invent a
file, a column, a type or a flag. Leave unknown cells null."): `files:
List[RepairedFile]` "One entry per delivered file. If the workbook lists no
files explicitly, one file per column sheet, using the sheet name as the file
name pattern." · `fields: dict[str, List[RepairedField]]` "Per field_sheet
name: the columns in that sheet, in order."

### D6. Manifests

`pyproject.toml`:
```toml
[project]
name = "frdsttm"
version = "1.0.0"
description = "FRD + vendor data dictionary -> STTM workbook, on Databricks"
requires-python = ">=3.11"
dependencies = ["anthropic>=0.60", "pydantic>=2", "openpyxl", "python-docx", "pypdf", "databricks-sdk"]

[project.optional-dependencies]
app = ["fastapi", "uvicorn[standard]", "python-multipart"]
dev = ["pytest", "httpx"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", "app/backend"]
```

`requirements.txt`:
```
fastapi
uvicorn[standard]
python-multipart
pydantic>=2
databricks-sdk
anthropic>=0.60
python-docx
pypdf
openpyxl
```

`app.yaml`:
```yaml
command: ["python", "app/backend/app.py"]
env:
  - {name: "STTM_APP_MODE", value: "databricks"}
  - {name: "CATALOG", value: "<catalog>"}
  - {name: "SCHEMA", value: "sttm_agent"}
  - {name: "STTM_PROVIDER", value: "databricks"}
  - {name: "STTM_MODEL", value: "claude-sonnet-5"}
  - {name: "STTM_JOB_NAME", value: "frd_sttm_pipeline"}
  - {name: "STTM_JOB_ID", value: "<numeric job id after bundle deploy>"}
```

`databricks.yml`:
```yaml
bundle:
  name: frd_sttm_agent
include:
  - resources/*.yml
variables:
  catalog:
    description: Unity Catalog catalog holding the sttm_agent schema.
    default: <catalog>
  schema:
    description: Schema with the four volumes (frds, vdds, reference_sttms, output_sttms).
    default: sttm_agent
targets:
  dev:
    mode: development
    default: true
```

`resources/pipeline_job.yml` (serverless, no retries):
```yaml
resources:
  jobs:
    frd_sttm_pipeline:
      name: frd_sttm_pipeline
      parameters:
        - {name: task, default: reindex}
        - {name: catalog, default: ${var.catalog}}
        - {name: schema, default: ${var.schema}}
        - {name: run_id, default: ""}
        - {name: doc_id, default: ""}
        - {name: provider, default: databricks}
        - {name: model, default: claude-sonnet-5}
        - {name: triggered_by, default: manual}
        - {name: frd_file, default: ""}
        - {name: vdd_file, default: ""}
      tasks:
        - task_key: pipeline
          max_retries: 0
          notebook_task:
            notebook_path: ../notebooks/pipeline.py
            base_parameters:
              task: "{{job.parameters.task}}"
              catalog: "{{job.parameters.catalog}}"
              schema: "{{job.parameters.schema}}"
              run_id: "{{job.parameters.run_id}}"
              doc_id: "{{job.parameters.doc_id}}"
              provider: "{{job.parameters.provider}}"
              model: "{{job.parameters.model}}"
              triggered_by: "{{job.parameters.triggered_by}}"
              frd_file: "{{job.parameters.frd_file}}"
              vdd_file: "{{job.parameters.vdd_file}}"
              frds_volume: frds
              vdds_volume: vdds
              reference_volume: reference_sttms
              output_volume: output_sttms
              pairing_table: frd_pairing
```

### D7. `run.json` keys

`run_id doc_id created_at triggered_by provider model status error traceback?
standards_sha256 naming_version engineering_version inputs frd vdd
vdd_repair_meta? vdd_normalised? extraction extraction_meta assessment
applied? render?` — shapes in B10 and B7.5.

---

## E. Acceptance

Report each as done / failed / skipped with evidence.

**Unit (offline, `pytest`)**
- [ ] Phase 1 test modules exist and pass with no network, no credentials.
- [ ] FRD docx → `#` headings, a `| --- |` row, `- ` bullets, `**SRQ226433 — …**`; < 500 chars or no headings → `FrdParseError`.
- [ ] VDD with a title row above FILES parses; `PHI / PII` → `phi`; `delete once replaced` rows dropped and counted; each B2 problem kind producible; `required` None ≠ False.
- [ ] `promote_type("INT")`→`Int`; `("date")`→`String`; `("money")`→`None`; `type_from_example("0.73")`→`Decimal(10,2)`; `("4")`→`None`; `schema_for("stage","Member")`→`STG_MBR`; `("stage","sdoh")`→`None`; `catalog_for("standard")`→`PR_STD`; `audit_columns("stage",False)` 3 cols, `(…,True)` 4 with `LOB` last.
- [ ] Grounding: a file pattern absent from the FRD → `unverified` with KEEP/REMOVE + free text; a missing `requirement_ids` value → note only; a 60%-overlap rule → `weak_match`.
- [ ] Attribution: names both files → kept + note; names no column → kept + note; names a column one feed has → moved + note; names a shared column → one `attribution` question with both names + `All of them`.
- [ ] Pairing: distinct tokens → paired; equal scores → `file_pairing` for both.
- [ ] Status: no VDD → `cannot_generate` (`no_dictionary`); open question → `needs_input`; all answered → `ready`.
- [ ] Rows: 3 columns, one segment → 7 rows (`SRC_FILE_NAME`, `REC_CREATION_TIME`, `REC_UPDATED_TIME`, `LOB` appended); header segment appends 3; stage type always `String`; `int`→`Int`, `decimal`→`Decimal(10,2)`, `varchar`→`String`; `String` + example `0.5` → `Decimal(10,2)` origin `example value`; no type, no example → `String` origin `default` + amber mark; `leave_type_blank` → `""`.
- [ ] Placement: the CLAIM_ID rule lands on `CLAIM_ID`; the layout rule is unattributed and appears in FILE_DETAILS' description.
- [ ] Built-in headers equal B7.3; `Recycle Flag` only with a recycle rule; single-sheet meta rows in order.
- [ ] `choose_layout` never returns the feed's own STTM (P2: by key and by name).
- [ ] `name_key("FRD_Medicare_Expansion.docx") == name_key("VDD_Medicare Expansion.xlsx")`; two FRDs one key → both `ambiguous`.
- [ ] `run_extract` with `FakeClient` writes `run.json` + `report.md`, reaches `rendered` when nothing is missing, records `failed` + traceback on exception.
- [ ] Backend: second `POST /api/runs` while running → 409; unknown question → 400; blockers → 400 on render; (P2) `.txt` VDD upload → 400.

**Workspace**
- [ ] `frd_pairing`: one row per FRD; generatable = has a VDD.
- [ ] Terminal extraction wrote `run.json` + `report.md` with a status.
- [ ] App lists the corpus; tiles match the table.
- [ ] An App run reaches `needs_input`/`ready`; answers save with the caller's email; workbook downloads.
- [ ] Workbook: target column names = source names row for row; every sheet ends with the audit columns; version row names `frd-sttm-agent`.
- [ ] With an own approved STTM present, `render.layout_from` is another feed's or `None`.
- [ ] App stopped.

**Findings, not bugs to patch by hand** (observed against an approved
workbook 2026-09-04): vendor `Int` with a decimal example stays `Int` (the
example rule fires only from `String`); vendor `Date` renders `String`;
`All of them` leaves a rule on every feed it sat on, including one lacking the
column (scope should be the named feeds); a rule naming no column stays
file-level where an analyst placed it on a column by reading the FRD's
ordering (should become a question kind).

**Failure modes met in dev**: App deploy "File size … exceeded max size" on
`.git/objects/pack` → resync excluding `.git`. Backend won't start, multipart
mentioned → `python-multipart` missing. "2 jobs named frd_sttm_pipeline" →
dev prefix, set `STTM_JOB_ID`. JSON/format error → run again. Empty picker
with full volumes → reindex. Corpus tiles flap between loads → open issue in
the summary fetch; reload.
