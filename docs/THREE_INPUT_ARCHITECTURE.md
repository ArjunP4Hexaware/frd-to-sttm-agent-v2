# Three-input architecture: FRD + vendor data dictionary + standards doc

Decided 2026-08-25 (Arjun). Status: DESIGN — nothing below is built.
Companion to docs/TEMPLATE_ARCHITECTURE.md, which this supersedes in one
respect: the template STTM stops being the source of column rows.

## 1. The problem, measured

Read against its own approved STTM, one real FRD names **2** source
columns (both inside reject rules); the STTM has **~410** column rows
across three sheets. The FRD says "refer to the mapping document" for
the field list six times — it points at the STTM for the very content
the agent is meant to produce. The other real FRD is the same shape, and
its file is a headerless, multi-record-type pipe file whose field names
exist only in the vendor's spec.

Today `04_sttm_render.derive_field_mappings` takes every column row,
every datatype and every audit row from the matched **template STTM**
(`dictionary["feeds"][key]["fields"]` — see its own docstring). With the
own STTM pinned as the template (the demo configuration), the output is
the approved workbook's rows with the FRD's table names substituted.
Every "accuracy vs golden" figure produced this way is self-referential.

## 2. Where each STTM cell actually comes from

| STTM column(s) | True source | Today's source |
|---|---|---|
| File name, schema/table per layer, load strategy, frequency, vendor, landing folder, DQ + recycle rules | **FRD** | FRD (02 extraction) — correct |
| Source column name, position, type, length, null rule, sample, description, valid values, PHI | **Vendor data dictionary** | the template STTM's rows |
| Target column name (as-is vs renamed/prefixed), casing, stage type policy, standard type promotion, audit columns, catalog per layer | **Client standards doc** | the template STTM's target side |
| Sheet/band layout, header labels, widths | the client's STTM template | the template STTM — correct, stays |

Two facts drive the design. The dictionary **cannot be reconstructed**
from a file (descriptions and rules are never in the data; headerless
files do not even carry names). The standards **cannot be inferred from
one template** (the two real STTMs apply different naming rules under
the same client).

## 3. Target data flow

```
FRD_<name>.docx ───────────── 01 ingest ──► frd_documents (unchanged)
DICT_<name>[.<file>].xlsx|csv ─ 01b dict parse ─► source_layouts/<doc_id>.layout.json   NEW
contracts/*_standards.json ── versioned CONFIG (naming + engineering), loaded at start   NEW
                                       │
02 extract ── FRD content + exemplars ──┤ (unchanged call; the layout is NOT
                                       │  fed to the LLM — it is deterministic input
                                       ▼  to 03/04, and PHI never reaches a prompt)
03 contract build ── attach layout per feed; bind FRD rules to layout columns;
                     apply standards to derive targets; gate what is missing
04 render ── rows FROM the layout; target names/types FROM standards;
             LAYOUT FROM the template (sheets, bands, headers, widths only)
```

Stage by stage, what changes:

- **01b — dictionary parser** (new module beside `frd_parsing.py`, say
  `frdsttm/dictionary_parsing.py`). Input: the vendor's dictionary in
  whatever shape it arrives (xlsx sheet, csv, a table inside a docx).
  Output: one `source_layout` per file pattern (schema in §4). The parser
  recovers roles by header synonyms the same way `reference_workbooks`
  already does for STTM headers (`Field Name` / `Database column Name` /
  `Client Data Table Column` are all "source column"). Unrecognised
  columns are kept verbatim under `extra`, never dropped.
- **01c — standards loader** (`frdsttm.standards`, modelled on
  `label_contract`). The two client documents are transcribed ONCE into
  versioned config under `contracts/` (§5); the pipeline consumes only the
  structured form and fails loudly if it is missing or unversioned.
- **02** is unchanged at the call site. The layout is not an LLM input:
  it is deterministic, may contain PHI samples, and nothing in it needs
  interpretation. The one thing 02 gains is `feed.layout_ref` — the
  file-pattern key the layout attaches on — which it already has as
  `file_name_patterns`.
- **03** does the joining. Per feed: find the layout whose file pattern
  matches `file_name_patterns` (exact stem, then glob); bind each FRD
  validation/recycle rule to the layout column it names (today 04 does
  this against template columns — same code, different column source);
  apply the standards to derive stage/standard names and types; emit a
  gated ambiguity for every feed without a layout and for every rule
  naming a column the layout does not have.
- **04** renders rows from `feed["fields"]` exactly as now — the change
  is that `derive_field_mappings` reads the layout + standards instead of
  `dictionary["feeds"][key]`. The template contributes layout only.
  Audit rows come from the standards doc, not the template.

## 4. The source-layout contract

```json
{
  "layout_id": "DICT_<name>__demographics_package",
  "file_pattern": "demographics_package_CCYY_MM.csv",
  "format": {"kind": "delimited", "delimiter": ",", "header_row": true,
             "segments": []},
  "provenance": {"source": "dictionary|probe|upload",
                 "document": "DICT_<name>.xlsx", "sheet": "...",
                 "content_sha256": "..."},
  "fields": [
    {"position": 1, "name": "zip_code", "datatype": "String",
     "length": null, "nullable": false, "sample": "90025",
     "description": "Zip code of data", "allowed_values": null,
     "range": null, "phi": false, "segment": null,
     "extra": {}}
  ]
}
```

Rules: every field value is verbatim from the dictionary or null. A
`probe`-sourced layout has `description`, `allowed_values`, `range` and
`phi` null by construction. A layout for a multi-segment file sets
`format.segments` and each field's `segment` (HDR/DTL/TRL), which 04's
`_table_for_segment` already consumes.

## 5. The standards contract

**Two client documents, not one (Arjun, 2026-08-25): a NAMING standards
document and an ENGINEERING standards document.** Naming feeds the target
column/table/schema rules below. Engineering is expected to feed the
audit-column set, load-strategy vocabulary, reject/recycle table
conventions and notification rules — i.e. the parts of the STTM that are
client convention rather than FRD fact — and is also the input CodeGen
will want. Which sections of each become config is decided by reading
them (Arjun has both; they are client documents and stay out of the repo).

**They are CONFIGURATION, not per-run input, and not code.** The precedent
is `contracts/frd_label_contract.json`: a versioned file the pipeline
loads at start-up through a loader that fails loudly if it is missing or
unversioned, with no hard-coded fallback. So: `contracts/naming_standards
.json` + `contracts/engineering_standards.json` (or one
`client_standards.json` with both sections), `version`ed, loaded by a
`frdsttm.standards` module the way `label_contract` is. A reviewer never
uploads them; an operator updates the file and bumps the version when the
client's document changes, and every run records `standards_sha256` in
its provenance the way `system_prompt_sha256` is recorded today. Why not
bake the rules into Python: a rule in code is invisible to the reviewer,
silently stale when the client revises the document, and not re-derivable
in the ACFC rebuild — a decision that is not written down does not
survive the hand-off. Why not per-run upload: the standards do not vary
by FRD, so asking for them each time is friction that invites skipping
them.

What the two real STTMs prove must be expressible:

```yaml
naming:
  target_column: as_is | upper_snake | lower_snake
  prefix: ""            # e.g. "TPL_"
  strip_source_prefix: false
types:
  stage_default: String
  standard_promotion:            # applied to the SOURCE datatype/name
    - {when: {name_suffix: ["_pct", "_score", "_index"]}, to: "Decimal(10,2)"}
    - {when: {source_type: ["Int", "integer"]}, to: "Int"}
audit_columns:
  - {name: SRC_FILE_NAME,     datatype: String,    layers: [stage, standard]}
  - {name: REC_CREATION_TIME, datatype: timestamp, layers: [stage, standard]}
  - {name: REC_UPDATED_TIME,  datatype: timestamp, layers: [stage, standard]}
catalogs: {stage: PR_DLK, standard: PR_STD}
recycle_flag_column: "Recycle Flag"
```

One standards file per client convention, selected per FRD by the
dictionary/template pairing (an FRD under the `TPL_` convention pairs
with that standards file). If the client has ONE standards doc that
covers both conventions with conditions, the parser has to recover the
conditions — find out before choosing the schema (§8).

## 6. Ingress: how the dictionary reaches the agent

**Decided 2026-08-25: dictionaries live in SharePoint (Arjun) → corpus
kind `DICT_<name>[__<file>].xlsx`, harvested by the sync.** Per-run upload
stays as the fallback path for a dictionary that arrives by email. The
standards are NOT corpus documents — see §5 (versioned config).

The two options, for the record:

- **Corpus kinds (preferred if the documents already sit in the
  library).** The sync's prefix split gains `DICT_` beside
  `FRD_` / `STTM_` (`sync.filter_by_prefix` already generalises; the
  index gains `dictionaries` beside `frds` /
  `references`; pairing is the same stem rule, `DICT_X` ↔ `FRD_X`, with a
  per-file suffix when one FRD has several feeds). Zero new UI: the
  picker shows each FRD with its dictionary status the way it shows
  mapped/unmapped today.
- **Per-run upload (if BSAs get dictionaries by email).** The picker
  gains an attach step per feed before the billed-run gate; the upload
  lands in `frd_raw/dictionaries/` and the sync leaves it alone the way
  it leaves hand-staged files alone. The app already has upload
  endpoints (UI-less since 2026-08-21) to build on.

Build the corpus path first either way — the upload path writes into the
same place and the pipeline never knows the difference.

## 7. The landing-zone probe (fallback + validator, not an input)

- Resolution: `mftlanding` → storage root is a one-line environment
  config (`STTM_LANDING_ROOTS`, JSON map). The FRD already states the
  relative folder and the file pattern; it does not change.
- Code only: pyarrow/pandas schema inference over the header row and N
  sample rows. The LLM never sees raw rows (PHI). What it produces is a
  `probe`-sourced layout (§4) — names, positions, inferred types, one
  masked sample per column, observed nullability.
- Headered delimited files only. A headerless file yields a field COUNT
  and a gated item; the agent never guesses names.
- When a dictionary exists too, the probe is a check: field count and
  order must agree, or 03 gates "layout drift" — the CAQH FRD asks for
  exactly this ("process shall fail when file layout is not as per source
  dictionary").
- Empty folder at authoring time: gated, not an error (§8(3)).
- Access: the App's service principal needs READ on the landing
  container — a new grant for `90_uc_governance` to record.

## 8. Open questions — ask the client before scoping code

1. ~~Standards doc: structured or prose?~~ → two documents (naming +
   engineering), both in hand; the question is now which sections become
   config keys. Answered by reading them.
2. ~~Where do vendor dictionaries live today?~~ → SharePoint (2026-08-25).
3. **Is a sample file normally in `mftlanding` when the STTM is
   written?** Decides how often the probe fills anything.
4. Does one standards doc cover both naming conventions seen in the two
   real STTMs, and if so how does it say which applies?

## 8a. The worked example: the SD vendor dictionary

`sample_documents/DICT_Medicare Expansion-MIDS - Socially Determined.xlsx`
(2026-08-25; a client-derived document — leaves with the Friday purge,
never committed). It is what the vendor input MUST contain, in the shape
§4's parser consumes: a `FILES` sheet (pattern, format, delimiter, header
row, encoding, cadence, description) and one sheet per file with
`Position | Field Name | Data Type | Length | Required | Description |
Allowed Values / Range | Example Value | PHI/PII`. 399 fields (86 / 267 /
46). It contains NOTHING the client decides — no target names, no
stage/standard types, no audit rows — so a run that uses it plus the FRD
plus the standards, with the reference STTM excluded, is a fair test.

Provenance caveat, stated in its README sheet: it was reverse-engineered
from the approved STTM's source band as a stand-in for the vendor's real
spec, so it inherits that band's gaps (no lengths; "String" on every
community-file field).

**Finding while building it — the golden STTM is not rule-typed.** In the
community-risk sheet all 90 standard-layer `Decimal(10,2)` cells are
exactly the rows whose SAMPLE value had a fractional part, and all 177
`String` cells had a whole-number sample; `economic_risk_score_1_hex_pct`
is a percentage left as `String` because its sample was `0`. So: (a) type
promotion must come from the dictionary's vendor type or a name-based
standards rule, never from the sample; (b) the golden-pair eval must
report these rows as "agent disagrees with golden" without scoring them
as errors — the agent is right there.

## 9. Gating and provenance rules (settled)

- No layout for a feed → gated ambiguity naming the file pattern; the
  workbook still renders with the feed's FILE_DETAILS row and an empty
  mapping sheet flagged in `_provenance`. Never a silent sparse render.
- A rule naming a column absent from the layout → gated.
- `_provenance.cell_sources` records, per rendered row, which input each
  column came from (`frd` / `dictionary` / `probe` / `standards` /
  `template`). The results view shows it.
- The grounding audit extends to the source side: every source-side cell
  must be verbatim from the layout or null.

## 10. Build order (each step: tests first, then the code — Arjun writes it)

1. `dictionary_parsing.py` + `source_layout` schema + tests against the
   two real dictionaries' shapes (headered csv; multi-segment pipe).
2. Standards schema + loader + tests (the two conventions round-trip).
3. 03: attach layouts, bind rules, apply standards, gate — tests for
   every gating case in §9.
4. 04: `derive_field_mappings` reads layout + standards; template is
   layout-only. The self-referential eval is replaced by a real one:
   generated rows vs the approved STTM, with the own STTM never an input.
5. Sync + corpus: the `DICT_` kind, pairing, picker status.
6. Probe, last — it is the fallback.

## 11. The standards documents, read (2026-08-26)

Pulled in commit `9159447` as `standards/`. FOUR documents arrived, but
only TWO are standards — the other two are FRD artifacts and belong to a
different part of the argument.

### 11.1 `EDO Data Engineering Naming Standards.docx` — the naming input

14 tables of controlled vocabulary. The ones that become config:

| Vocabulary | Values |
| --- | --- |
| Data Layers | RAW · STAGE→`STG` · Standard→`STD` · GOLD→`GLD` · CONSUMPTION→`CMP` · EVENTHUB→`EVN` · ON-PREM→`ONP` |
| Domain/Subdomain | MEMBER→`MBR` · CLAIMS→`CLM` · CLINICAL→`CLIN` · PROVIDER→`PRV` · PHARMACY→`RX` (17 rows) |
| Load Strategy | Truncate & Load→`TRUNC` · Append→`INSRT` · Update Else Insert→`UPSRT` · Extracts→`EXTR` |
| Product / Sub-Product | Data Lake→`DLK` · HEDIS2.0→`HDS` · Milliman→`MMN` · No Sub-Product→`NSP` |
| Frequency | HOURLY→`HRL` · DAILY→`DLY` · WEEKLY→`WKL` · MONTHLY→`MTH` · One Time→`ONT` |
| Region/LOB, Pipeline Type, 41 connection types, ADF activity prefixes, Databricks/Python naming | (not STTM-relevant; CodeGen's) |

**This decodes the STTM cells 04 currently borrows from the template.**
CAQH's catalog `PR_DLK` is the Data Lake product code; its schema
`STG_MBR` is stage layer + member domain. So catalog and schema per layer
are DERIVABLE from these tables rather than copied from a matched
workbook — which is exactly the implicit borrow §5 set out to remove.

### 11.2 `EDO Data Engineering Coding Standards.docx` — the engineering input

Mostly ADF/Databricks build practice (IIG Framework mandate, Key Vault,
PII suppressed in activity logs, ForEach parallelism, dynamic linked
services) — i.e. CodeGen's input, not the STTM's. Three lines are ours:

- *"Standard table should be created with proper datatype as per the
  source column datatype."* Independent confirmation of §8a: type
  promotion is a function of the SOURCE type, never the sample.
- Recycle: *"NULL check on the referential column(s), if the recycle flag
  is enabled."*
- Rejects go to a reject table with an email alert to Production Support;
  a run-control table carries `OBJECT_NAME | SRC_REC_COUNT |
  TGT_REC_COUNT | REJECTED_REC_COUNT | EXEC_STATUS | ERROR_MESSAGE |
  BATCH_DATE`.

### 11.3 WHAT IS NOT IN EITHER DOCUMENT — §5 was half wrong

Grepped both for `SRC_FILE_NAME`, `REC_CREATION_TIME`, `REC_UPDATED_TIME`,
column-case rules and prefix rules. **Neither standards document contains
the target COLUMN naming rule (upper snake, the `TPL_` prefix) or the
audit-column set.** They stop at layer / domain / catalog / load-strategy
vocabulary — object-level, not column-level.

Consequence: `contracts/naming_standards.json` can be sourced from these
documents for catalogs, schemas, table names and load strategy, but the
column-level half still has NO written source. Ask where it is documented
before concluding it is undocumented; if it truly is, the config file
becomes the first written record of it and that fact must be stated in
the file itself.

One lead, one data point only: `TPL_` matches CAQH's own `Sub-Domain =
TPL`, so the column prefix may be the sub-domain abbreviation rather than
an arbitrary per-feed choice. Check against a third mapped STTM before
encoding it.

### 11.4 `FRD_Enhanced_Metadata_Template (1).docx` — the blank FRD template

Not a standard: it is the empty document BSAs author into, and therefore
the canonical structure `01_frd_ingest` parses. Its Structural Metadata
block is a FIXED 11-row list:

```
Object/data Format · Target Schema · Target Table Name ·
Domain and Subdomain · Load Strategy STG · Load Strategy STD ·
Load Strategy Consumption (EDH, BSL) · Archive Schedule ·
Source Data Dictionary · ADLS Location · Inbound File Folder Path
```

This is the FRD's half of the division of labour, and it is fixed — so
the vendor dictionary must never restate any of it. It is also where the
`DICT_` reference belongs: the `Source Data Dictionary` row.

Gotcha: the requirement id `MDST231070` is HARDCODED IN THE TEMPLATE and
appears identically in every real FRD. It is a template artifact, not a
project identifier — never key anything on it.

### 11.5 `FRD_STG_STD_SFMC_..._1005310.docx` — a THIRD real FRD

Salesforce Marketing Cloud email-campaign tracking; csv; `stg_mbr` →
`mbr`. Not a standard. Three things it settles:

1. **The landing path is normally present.** It states `ADLS Location =
   mftlanding/inbound/member/outreach/salesforce_marketing_cloud`. With
   CAQH's `mftlanding/inbound/member/tpl/caqh`, that is 2 of 3 real FRDs;
   SD's empty cell is an authoring omission, not the norm. The probe is
   more reachable than the 2026-08-26 correction in CLAUDE.md first said.
2. **The path has a derivable shape:**
   `mftlanding/inbound/<domain>/<subdomain>/<vendor>`. So a missing
   ADLS Location is INFERABLE — gate it, never fill it silently.
3. **The circular pointer is boilerplate.** Its `Source Data Dictionary`
   reads "File and field descriptions are mentioned in the mapping
   document" — word-for-word identical to SD's. Two of three. The FRD
   process has a dictionary slot that nobody ever fills; that is the
   three-input argument in one line.

### 11.6 A real CodeGen defect, found in a real document

SFMC states `Load Strategy STD = Upsert`. CodeGen's `FrdContract`
restricts `load_strategy` to `{'Truncate and Load', 'Append'}`, so this
document fails CodeGen validation before the workbook is read. The naming
standards list `Update Else Insert → UPSRT` as a sanctioned strategy, so
the ENUM is wrong, not the FRD. Widen it on the CodeGen side to the four
sanctioned strategies.
