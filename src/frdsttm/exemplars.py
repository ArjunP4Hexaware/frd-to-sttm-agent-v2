"""
frdsttm.exemplars — retrieved few-shot exemplars for stage-02 extraction.

The corpus half of the template architecture (2026-08-22, see
docs/TEMPLATE_ARCHITECTURE.md): the k most similar PAIRED documents in the
corpus contribute a compact digest of their approved FRD→STTM mapping to
the extraction prompt, so the model sees the client's conventions —
column naming, segment shapes, datatype habits — while extracting a new
document. This is the master context document §7a position ("retrieved
exemplars over fine-tuning") made concrete.

Safety property worth stating once: exemplars CANNOT inject facts into the
output. Stage 03's grounding audit requires every strict field to appear
verbatim in the *target* FRD and advisory prose to overlap it at ≥ 0.75 —
an extraction that copies an exemplar's table name or rule text fails the
audit exactly as an invented one would. The prompt says "conventions, not
facts"; the audit enforces it.

Retrieval is frdsttm.similarity — deterministic, no model calls. The
exemplar's own document is always excluded (a doc must never be its own
exemplar), and only high/low-confidence PAIRED documents participate:
an unpaired workbook has no approved mapping to teach from.
"""

from __future__ import annotations

from pathlib import Path

from frdsttm.corpus import CORPUS_INDEX_NAME  # noqa: F401  (re-export for callers' messages)
from frdsttm.corpus import reference_features_from_index
from frdsttm.reference_workbooks import parse_reference_workbook
from frdsttm.similarity import frd_features, score_match

# Caps keep the block prompt-cache-friendly and honest about cost: an
# exemplar is a digest, never a full workbook.
MAX_FEEDS_PER_EXEMPLAR = 3
MAX_COLUMNS_PER_FEED = 25
DEFAULT_K = 2

_HEADER = (
    "REFERENCE CONVENTIONS (retrieved exemplars).\n"
    "Below are mapping digests from previously APPROVED FRD->STTM pairs that "
    "are structurally similar to this document. Use them ONLY to understand "
    "this client's conventions — how columns are named, how feeds map to "
    "stage/standard tables, how rules are phrased and attributed. NEVER copy "
    "a file name, table name, column, schedule, or rule from an exemplar "
    "into your extraction: every extracted fact must come verbatim from the "
    "FRD DOCUMENT section below, and a downstream audit rejects anything "
    "that does not appear there.\n"
)


def _digest_workbook(name: str, dictionary: dict) -> str:
    lines = [f"- exemplar workbook: {name} (dialect: {dictionary['dialect']})"]
    for i, (key, feed) in enumerate(sorted(dictionary.get("feeds", {}).items())):
        if i >= MAX_FEEDS_PER_EXEMPLAR:
            lines.append(f"  … {len(dictionary['feeds']) - i} more feed(s) omitted")
            break
        cols = feed.get("fields", [])
        shown = ", ".join(
            f"{f['source_column']}:{f.get('datatype') or 'String'}"
            for f in cols[:MAX_COLUMNS_PER_FEED]
        )
        more = f", … {len(cols) - MAX_COLUMNS_PER_FEED} more" if len(cols) > MAX_COLUMNS_PER_FEED else ""
        lines.append(f"  - feed/table {key!r} ({len(cols)} column(s)): {shown}{more}")
        if feed.get("recycle_note"):
            lines.append(f"    recycle-note convention: {feed['recycle_note']}")
    return "\n".join(lines)


def build_exemplar_block(doc_id: str, content: str, index: dict,
                         reference_dir: str | Path, k: int = DEFAULT_K) -> dict | None:
    """The exemplar prompt block for one document, or None when the corpus
    has nothing to offer (no index, no other paired documents).

    Returns {"text": <prompt block>, "exemplars": [{doc_id, reference,
    score}]} — the second half is provenance, written next to the
    extraction artifact so a reviewer can see exactly which pairs informed
    the prompt.
    """
    if not index:
        return None
    pairs = index.get("pairs", {})
    candidates = {d: p for d, p in pairs.items() if d != doc_id}
    if not candidates:
        return None

    target = frd_features(doc_id, content)
    ref_feats = reference_features_from_index(index)

    ranked = []
    for cand_doc, pair in sorted(candidates.items()):
        wb_feat = ref_feats.get(pair["reference"])
        if wb_feat is None:
            continue
        m = score_match(target, wb_feat)
        ranked.append((m["score"], cand_doc, pair["reference"]))
    ranked.sort(key=lambda t: (-t[0], t[1]))
    chosen = ranked[: max(1, int(k))]
    if not chosen:
        return None

    parts, provenance = [_HEADER], []
    for score, cand_doc, ref_name in chosen:
        dictionary = parse_reference_workbook(str(Path(reference_dir) / ref_name))
        parts.append(f"\n[exemplar: approved pair for document {cand_doc!r}, "
                     f"similarity {score:.2f}]")
        parts.append(_digest_workbook(ref_name, dictionary))
        provenance.append({"doc_id": cand_doc, "reference": ref_name,
                           "score": score})
    return {"text": "\n".join(parts) + "\n", "exemplars": provenance}
