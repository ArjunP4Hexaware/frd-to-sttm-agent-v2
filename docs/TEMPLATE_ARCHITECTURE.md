# Template architecture — corpus-driven, retrieval-based STTM generation

**Status: implemented 2026-08-22** (Arjun's decision, extending the manager's
8–10-template proposal to a mined corpus). This document is the rationale an
ACFC rebuilder needs; the code is the authority on details.

## The idea in one paragraph

Every **approved FRD→STTM pair is a template**. The SharePoint **sync**
(`frdsttm/sync.py`; run when the app starts and on its "Sync now" — no
schedule since 2026-08-22 late evening; the `frd_sttm_sharepoint_sync` job
is the databricks-mode execution target) lands every FRD and STTM it can reach in SharePoint in
the `frd_raw` / `sttm_reference` volumes — bulk on its first tick,
incrementally after — pairs them deterministically, and stores the result
as a corpus index in Unity Catalog (the reference volume). When an
**unmapped** FRD is selected
for generation, the agent retrieves the most similar approved pairs: their
conventions feed the extraction prompt (stage 02), the best-matching
workbook(s) drive the rendered layout and dictionary (stage 04), and the
run's report shows exactly which templates were used and why. Where a
document's own STTM already exists, it is **excluded from candidacy and used
as eval ground truth** (cross-validation) — so every regeneration doubles as
a measured golden-pair eval, which is the §7a "eval set first" position of
the master context document made automatic.

## Why retrieval is deterministic (the LLM-efficiency argument)

The LLM is used **only where language understanding is irreducible** — the
one extraction call per document (stage 02). Everything else is plain code:

- **No embeddings vendor.** The program is Anthropic-only as model vendor
  and the Claude API has no embeddings surface; a second vendor for
  similarity would be a data-governance decision, not an implementation
  detail. For a corpus of tens-to-hundreds of documents, identifier overlap
  + token cosine (`frdsttm/similarity.py`) is free, offline-testable, and —
  decisive for SMEs and executives — **explainable**: the UI/report can say
  "84% of this workbook's columns appear in the FRD". Databricks-hosted
  embeddings (in-workspace, data never leaves UC) are the designed upgrade
  path if the corpus outgrows lexical matching.
- **The sync makes zero LLM calls.** Ingest-all ≠ extract-all: the sync
  downloads, parses and indexes; the billed extraction runs only when a
  generation is requested.
- **Verdicts in code.** Template mode (single / amalgam / freeform), pair
  confidence, and every threshold are computed by code from config —
  the LLM never decides "did this match".
- **Prompt caching.** The extraction prompt keeps a byte-stable
  instruction+schema prefix; exemplars sit after it, the FRD last — repeated
  prefixes are nearly free under Anthropic prompt caching.

## Components

| Piece | Where | Role |
|---|---|---|
| `frdsttm/frd_parsing.py` | src (factored from 01) | ONE FRD parser for pipeline + sync |
| `frdsttm/reference_workbooks.py` | src (factored from 04) | ONE workbook-dictionary parser, both dialects |
| `frdsttm/similarity.py` | src | features, scores, pairing, template decision, thresholds |
| `frdsttm/corpus.py` | src | `corpus_index.json` (v2) build/load, pairs, unmapped, `content_sha256` per document |
| `frdsttm/sync.py` | src | SharePoint → volumes → index: incremental download (eTag/modified/size via `sync_manifest.json`), departed-file removal, `reindex()` |
| `notebooks/00_sharepoint_sync.py` + `resources/frd_sttm_sync_job.yml` | job | the sync (triggered at app start-up / "Sync now"; no schedule); `sync_mode=sync|reindex`; `frd_name_prefix` / `sttm_name_prefix` |
| `frdsttm/exemplars.py` | src | retrieved-exemplar prompt block + provenance |
| 02 `sttm_exemplars` | notebook | exemplar block into the live prompt; sidecar `<doc>.exemplars.json` |
| 04 template decision | notebook | mode → dictionary → render; cross eval; `_provenance.template_decision` |
| `backend/corpus_routes.py` | review app | the picker's source of truth (`/corpus/frds`), "Sync now"/"Rebuild index" (background, 202 + polled state), reference-workbook download, config probe |
| Corpus panel + regenerate | frontend | the picker: unmapped FRDs → generate; mapped FRDs → present STTM + regenerate-despite-existing; sync controls |

**Storage.** `corpus_index.json` (and the sync's `sync_manifest.json`) live
**in the reference volume** next to the workbooks they index
(`sttm_reference` in UC; `local_dev_fixtures/sttm_reference/` locally) —
notebooks read them from the same `/Volumes` path they already read
references from, and they travel with them. FRDs land in `frd_raw`,
reference STTMs in `sttm_reference`, exactly the volumes the pipeline
already scans. No new tables: the index is one JSON artifact, rebuilt
idempotently on every sync tick. In the deployed App the container keeps a
mirror of both volumes for listing/staging, refreshed after "Sync now" and
lazily (`STTM_CORPUS_REFRESH_SECONDS`) so another instance's sync shows up.

**Template fill (2026-08-22, late evening).** The chosen template's own
workbook layout IS the render dialect. `reference_workbooks.layout_of(path)`
returns the write-side descriptor (per mapping sheet: band/header/first-data
rows, each column's logical role via the same alias tables the parser reads
with, the unmapped columns; FILE_DETAILS / VERSION_HISTORY header maps; for
the single-sheet dialect the metadata block's keys → contract fields).
`04.render_into_template` opens the lead template, keeps sheets, bands,
headers, widths and styles, removes its data rows and writes ours under the
same headers; a template column the contract cannot fill stays blank and is
listed in `_provenance.template_fill.unfilled_columns` (reported in the
phase-5 report); sheets for feeds we do not render are removed, extra feeds
get a copy of the lead sheet; FILE_DETAILS / VERSION_HISTORY are kept from
the template or created minimal and flagged (CodeGen's extractor requires
both). The two built-in renderers (`render_sheet_per_table`,
`render_single_sheet`) remain only as the freeform fallback. Consequence: a
new client dialect is a new template in the library, not new code — and a
template missing a column the downstream extractor needs yields a workbook
that extractor rejects loudly, which is the honest outcome.

**Pairing (2026-08-22 evening).** `similarity.pair_corpus` pairs by exact
**name** first — `name_key()` strips extensions (`.sttm.xlsx` included)
and trailing role tokens (`sttm`, `frd`, `mapping`), so `Community Risk
FRD.docx` ↔ `Community Risk FRD.sttm.xlsx` ↔ `community-risk STTM.xlsx`
all key to `communityrisk`; an unambiguous one-to-one key match is
definitive (`matched_by: "name"`, confidence high), because that is the
renderer's own naming convention and the one a reviewer follows when they
upload a finished workbook. Ambiguous keys (two FRDs or two workbooks on
one key) are never name-paired — they fall through to similarity
(`matched_by: "similarity"`, gated by `pair_min`/`pair_high`). The
reviewer-upload loop therefore closes on the next sync without anyone
touching thresholds.

**Read-only.** The sync only ever READS SharePoint; nothing in the repo
writes there (see CLAUDE.md § SharePoint). A finished STTM reaches the
library because the reviewer uploads it; the sync pulls it back.

## The three template modes (exact vocabulary)

Computed by `similarity.decide_templates`, recorded in
`_provenance.template_decision`, shown in the phase5 report:

| Mode | Trigger | Render behavior |
|---|---|---|
| `single` | top eligible score ≥ `template_single_min` | that workbook is dialect + dictionary |
| `amalgam` | ≥2 workbooks ≥ `template_amalgam_min` (top-k) | merged dictionary, first-wins per table key, lead workbook's dialect; per-sheet provenance kept |
| `freeform` | nothing ≥ `template_amalgam_min` | best-effort render from the contract alone (FILE_DETAILS + rules; no column dictionary), FLAGGED — never a silent guess |

A scored template that fails `match_feeds` structurally **demotes to
freeform** (recorded as `demoted_from`) instead of the pre-2026-08-22
silent SKIP. An empty reference library is a warned state, not a fatal one
(the old `assert refs` is gone).

## Exclude-own-reference (decided 2026-08-22)

`exclude_own_reference` (default on): a document's own paired workbook is
never a template candidate for itself; it is the **eval reference**. The
eval for a cross-rendered document is name-aligned
(`evaluate_cross_reference`) with the reference as denominator — a column
the render never produced counts against the score. Numbers shown to
executives are therefore honest by construction. Setting it to `0` restores
self-referential rendering for debugging only.

## Why exemplars cannot leak facts

Stage 03's grounding audit requires every strict field to appear verbatim
in the **target** FRD and advisory prose to token-overlap it at ≥ 0.75. An
extraction that copies an exemplar's table name or rule text fails the
audit exactly as an invented one would. The exemplar block says
"conventions, not facts"; the audit enforces it. This is why exemplars
could be added without touching the quality gates.

## Thresholds (all config, no literals in logic)

Defaults in `similarity.THRESHOLD_DEFAULTS`, resolved everywhere through
`thresholds_from(param)` — notebook widgets with same-name env fallbacks,
per repo convention: `template_single_min` 0.55, `template_amalgam_min`
0.30, `template_top_k` 3, `pair_min` 0.35, `pair_high` 0.65.

**They are seeded, not calibrated.** They were set against synthetic
fixtures (`tools/make_synthetic_smoke_fixture.py`); calibrate on the two
real Hexaware pairs before the demo and record the outcome here. Known
consequence of a 2-document corpus with exclude-own on: each FRD has
exactly ONE eligible candidate (the other pair), so its score vs
`template_single_min` decides single-vs-freeform — if the two documents are
structurally unlike, lower `template_single_min`/`template_amalgam_min`
deliberately and say so in the demo, or the run is freeform.

## Honest limits (do not oversell in the demo)

- **Freeform has no column dictionary**: the rendered workbook carries feed
  metadata and rules but empty mapping sheets. Column-level freeform needs
  record-layout tables lifted from the FRD itself — designed, not built.
- **Pairing quality gates the whole idea**: a wrong FRD↔STTM pair poisons
  both templates and eval. Pairing errs toward `unmapped` (below
  `pair_min`), and low-confidence pairs are labeled; SME confirmation UI is
  the designed follow-up.
- The corpus index is rebuilt whole on each sync tick (idempotent, cheap at
  this scale); incremental refresh is deliberately out of scope.
