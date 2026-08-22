# Template architecture — corpus-driven, retrieval-based STTM generation

**Status: implemented 2026-08-22** (Arjun's decision, extending the manager's
8–10-template proposal to a mined corpus). This document is the rationale an
ACFC rebuilder needs; the code is the authority on details.

## The idea in one paragraph

Every **approved FRD→STTM pair is a template**. On first run in an
environment, the app ingests every FRD and STTM it can reach in SharePoint,
pairs them deterministically, and stores the result as a corpus index in
Unity Catalog (the reference volume). When an **unmapped** FRD is selected
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
- **Corpus bootstrap makes zero LLM calls.** Ingest-all ≠ extract-all:
  bootstrap parses and indexes; the billed extraction runs only when a
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
| `frdsttm/frd_parsing.py` | src (factored from 01) | ONE FRD parser for pipeline + bootstrap |
| `frdsttm/reference_workbooks.py` | src (factored from 04) | ONE workbook-dictionary parser, both dialects |
| `frdsttm/similarity.py` | src | features, scores, pairing, template decision, thresholds |
| `frdsttm/corpus.py` | src | `corpus_index.json` build/load, pairs, unmapped |
| `frdsttm/exemplars.py` | src | retrieved-exemplar prompt block + provenance |
| 02 `sttm_exemplars` | notebook | exemplar block into the live prompt; sidecar `<doc>.exemplars.json` |
| 04 template decision | notebook | mode → dictionary → render; cross eval; `_provenance.template_decision` |
| `backend/corpus_routes.py` | review app | bootstrap, corpus listing, reference import |
| Corpus panel + regenerate | frontend | unmapped list, template evidence, regenerate-despite-existing |

**Storage.** `corpus_index.json` lives **in the reference volume** next to
the workbooks it indexes (`sttm_reference` in UC; `local_dev_fixtures/
sttm_reference/` locally) — notebooks read it from the same `/Volumes` path
they already read references from, and it travels with them. FRDs land in
`frd_raw`, reference STTMs in `sttm_reference`, exactly the volumes the
pipeline already scans. No new tables: the index is one JSON artifact,
rebuilt idempotently by bootstrap.

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
  both templates and eval. Bootstrap errs toward `unmapped` (below
  `pair_min`), and low-confidence pairs are labeled; SME confirmation UI is
  the designed follow-up.
- The corpus index is rebuilt whole on each bootstrap (idempotent, cheap at
  this scale); incremental refresh is deliberately out of scope.
