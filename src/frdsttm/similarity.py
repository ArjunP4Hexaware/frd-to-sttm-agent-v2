"""
frdsttm.similarity — deterministic FRD ↔ STTM-workbook similarity.

The template architecture's retrieval core (2026-08-22, see
docs/TEMPLATE_ARCHITECTURE.md). Every score here is computed by plain code
over parsed artifacts — NO model calls, no network, no embeddings vendor.
That is deliberate, on three grounds:

- the program is Anthropic-only as model vendor and the Claude API has no
  embeddings surface; a second vendor for similarity would be a governance
  decision, not an implementation detail;
- the corpus is tens-to-hundreds of documents, where identifier overlap +
  token cosine outperform their complexity budget;
- an SME can be shown WHY a template matched ("84% of the workbook's
  columns appear in the FRD"), which an embedding distance cannot offer.
  The `components` dict on every score exists for exactly that display.

Division of labor unchanged from the repo's doctrine: the LLM proposes
(stage 02 extraction, with retrieved exemplars in the prompt), code decides
(this module's scores, `decide_templates`' mode verdict, 03's gates).

Every threshold is config: callers resolve them through
``thresholds_from(param)`` where ``param(name, default)`` is the notebook
widget / env-var accessor each caller already has. A numeric literal in
caller logic is a bug; the defaults below are the single source of truth.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter

from frdsttm.reference_workbooks import _nl, loose_tokens

# --------------------------------------------------------------------------- #
# thresholds & weights — single source of truth for defaults
# --------------------------------------------------------------------------- #
# Names double as widget/env names (upper-cased by _param the usual way).
THRESHOLD_DEFAULTS = {
    # template choice (04 render + backend display).
    # CALIBRATED 2026-08-25 on the two real Hexaware pairs with the merged
    # features 04 now scores (index markdown features + contract_features):
    # each FRD vs its own STTM 0.236 / 0.253, vs the other pair's STTM
    # 0.046 / 0.033. The seeded values (0.55 / 0.30, from the synthetic smoke
    # fixture whose FRDs enumerate columns) were unreachable on real
    # documents -- every render went freeform. Re-calibrate when the corpus
    # grows; record outcomes in docs/TEMPLATE_ARCHITECTURE.md.
    "template_single_min": 0.15,   # top-1 at/above this → single-template mode
    "template_amalgam_min": 0.08,  # refs at/above this may join an amalgam
    "template_top_k": 3,           # amalgam considers at most this many refs
    # corpus pairing (which FRD belongs to which existing STTM)
    "pair_min": 0.35,              # below this an FRD stays unmapped
    "pair_high": 0.65,             # at/above this a pair is high-confidence
}

# Component weights for the combined score. Identifier overlap dominates by
# design: two feeds that share column vocabulary are the same *shape* even
# when their prose differs, and shape is what a template transfers.
WEIGHTS = {
    "columns": 0.45,
    "tables": 0.20,
    "tokens": 0.25,
    "name": 0.10,
}

# Generic vocabulary that appears in every FRD/STTM in this domain and
# carries no pairing signal. Kept short and auditable on purpose.
_STOPWORDS = {
    "with", "from", "this", "that", "shall", "should", "must", "will",
    "file", "files", "data", "table", "tables", "column", "columns",
    "field", "fields", "record", "records", "source", "target", "stage",
    "standard", "layer", "load", "loads", "null", "value", "values",
    "date", "name", "type", "requirements", "requirement", "document",
    "ingestion", "mapping", "details", "description",
}

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
_DOTTED_RE = re.compile(r"\b[A-Za-z_][\w]*\.[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?\b")


def thresholds_from(param) -> dict:
    """Resolve every threshold through the caller's widget/env accessor.

    ``param(name, default)`` must return a string (the `_param` contract in
    the notebooks and the env accessor in the backend). Floats parse
    loudly — a mistyped widget value must not silently fall back.

    A BLANK value means "not set", not "invalid" (fixed 2026-08-24): every
    optional knob ships blank in `.env.example`, and the documented load
    (`set -a; . ./.env; set +a`) exports a blank as an empty string, so
    `os.environ.get(name, default)` returns "" rather than the default. An
    empty widget behaves the same way. Treating that as a parse error made
    following the repo's own setup instructions raise here.
    """
    out = {}
    for name, default in THRESHOLD_DEFAULTS.items():
        raw = str(param(name, str(default))).strip()
        if not raw:
            raw = str(default)
        try:
            out[name] = int(raw) if name == "template_top_k" else float(raw)
        except ValueError as exc:
            raise ValueError(
                f"threshold {name!r} must be numeric, got {raw!r} — fix the "
                f"widget/env var (see frdsttm.similarity.THRESHOLD_DEFAULTS)"
            ) from exc
    return out


# --------------------------------------------------------------------------- #
# feature extraction
# --------------------------------------------------------------------------- #
def _identifiers(text: str) -> set[str]:
    """snake_case / SCREAMING_SNAKE / dotted identifiers, normalized."""
    found = {m.group(0) for m in _IDENTIFIER_RE.finditer(text)}
    found |= {m.group(0) for m in _DOTTED_RE.finditer(text)}
    return {_nl(t) for t in found}


def _prose_counts(text: str) -> Counter:
    return Counter(t for t in loose_tokens(text) if t not in _STOPWORDS)


def frd_features(doc_id: str, markdown: str) -> dict:
    """Features of one parsed FRD (stage-01 markdown — the exact text the
    pipeline sees; parse with frdsttm.frd_parsing, never a second parser)."""
    return {
        "columns": _identifiers(markdown),
        "tables": {t for t in _identifiers(markdown) if "." in t},
        "tokens": _prose_counts(markdown),
        "name": loose_tokens(doc_id),
    }


def contract_features(doc_id: str, contract: dict) -> dict:
    """Features of one stage-03 mapping contract — the EXTRACTION's vocabulary.

    Calibrated on the two real Hexaware pairs (2026-08-25): a real FRD names
    its feeds and target tables and states rules in prose; it does not
    enumerate columns (33 identifiers in the FRD vs 391 columns in its own
    STTM — column overlap 0.005, score 0.11 against its own workbook). What an
    FRD and its STTM actually share is the feed key and the stage/standard
    TABLE names, which the extraction carries as structured fields. Those go
    into ``tables`` in exactly the shape ``workbook_features`` uses (bare,
    normalised table names plus the feed key) so the two sides are comparable.
    Prose tokens come from the contract text; ``columns`` stays empty here and
    is supplied by the FRD-markdown features when merged (see
    ``merge_features``) — never by the contract, whose target side would
    otherwise be scored against itself."""
    tables: set[str] = set()
    for feed in contract.get("feeds", []) or []:
        key = _nl(feed.get("feed_name", ""))
        if key:
            tables.add(key)
        for layer in ("stage_target", "standard_target"):
            target = feed.get(layer) or {}
            for name in target.get("tables") or []:
                n = _nl(name)
                if n:
                    tables.add(n)
    tables.discard("")
    return {
        "columns": set(),
        "tables": tables,
        "tokens": _prose_counts(json.dumps(contract, ensure_ascii=False)),
        "name": loose_tokens(doc_id),
    }


def merge_features(*feats: dict) -> dict:
    """Union of feature sets (sets unioned, token counts summed). Used by 04 to
    score the FRD-markdown features from the index TOGETHER with the
    extraction's table vocabulary."""
    out = {"columns": set(), "tables": set(), "tokens": Counter(), "name": set()}
    for f in feats:
        if not f:
            continue
        out["columns"] |= set(f.get("columns", ()))
        out["tables"] |= set(f.get("tables", ()))
        out["tokens"].update(f.get("tokens", {}))
        out["name"] |= set(f.get("name", ()))
    return out


def workbook_features(name: str, dictionary: dict) -> dict:
    """Features of one parsed reference STTM workbook (the
    frdsttm.reference_workbooks dictionary shape — same parser as render)."""
    columns: set[str] = set()
    tables: set[str] = set()
    prose_parts: list[str] = []
    for feed_key, feed in dictionary.get("feeds", {}).items():
        tables.add(_nl(feed_key))
        for f in feed.get("fields", []):
            columns.add(_nl(f.get("source_column", "")))
            prose_parts.append(f.get("description", ""))
            prose_parts.append(f.get("business_rule", ""))
        for rt in feed.get("ref_targets", []):
            for layer in ("stage", "standard"):
                t = _nl((rt.get(layer) or {}).get("table", ""))
                if t:
                    tables.add(t)
    meta = dictionary.get("meta", {})
    if isinstance(meta, dict):
        for v in meta.values():
            prose_parts.append(str(v))
    columns.discard("")
    tables.discard("")
    return {
        "columns": columns,
        "tables": tables,
        "tokens": _prose_counts("\n".join(prose_parts)),
        "name": loose_tokens(name),
    }


def serialize_features(feat: dict) -> dict:
    return {
        "columns": sorted(feat["columns"]),
        "tables": sorted(feat["tables"]),
        "tokens": dict(sorted(feat["tokens"].items())),
        "name": sorted(feat["name"]),
    }


def deserialize_features(raw: dict) -> dict:
    return {
        "columns": set(raw.get("columns", [])),
        "tables": set(raw.get("tables", [])),
        "tokens": Counter(raw.get("tokens", {})),
        "name": set(raw.get("name", [])),
    }


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def _coverage_jaccard(a: set, b: set) -> float:
    """Mean of Jaccard and coverage-of-b — rewards a workbook whose whole
    vocabulary appears in the FRD without letting a giant FRD swamp it."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    jaccard = inter / len(a | b)
    coverage = inter / len(b)
    return (jaccard + coverage) / 2


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b[k] for k, v in a.items() if k in b)
    if dot == 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def score_match(frd_feat: dict, wb_feat: dict) -> dict:
    """One FRD vs one workbook. Returns {"score", "components"} — the
    components are for display ("why this template"), the score for
    decisions. Both deterministic."""
    components = {
        "columns": round(_coverage_jaccard(frd_feat["columns"], wb_feat["columns"]), 4),
        "tables": round(_coverage_jaccard(frd_feat["tables"], wb_feat["tables"]), 4),
        "tokens": round(_cosine(frd_feat["tokens"], wb_feat["tokens"]), 4),
        "name": round(_coverage_jaccard(frd_feat["name"], wb_feat["name"]), 4),
    }
    score = sum(WEIGHTS[k] * components[k] for k in WEIGHTS)
    return {"score": round(score, 4), "components": components}


# --------------------------------------------------------------------------- #
# corpus pairing (FRD ↔ its own existing STTM)
# --------------------------------------------------------------------------- #
# Name tokens that say WHAT a file is, not WHICH document it is — at EITHER
# end. The library's convention (learned 2026-08-22) is a prefix: every FRD
# is "FRD_<name>.docx" and every STTM "STTM_<name>.xlsx", so those key to the
# same "<name>"; the renderer's own suffix convention ("<doc>.sttm.xlsx") and
# the older "Community Risk STTM.xlsx" shapes still key the same way.
_NAME_ROLE_TOKENS = {"sttm", "frd", "mapping", "mappings"}


def name_key(name: str) -> str:
    """Normalised identity of a document name for exact-name pairing.

    Strips every extension (``.sttm.xlsx`` included), lower-cases, splits on
    non-alphanumerics, and drops role tokens at both ends (``FRD_`` /
    ``STTM_`` prefixes, ``… FRD`` / ``… STTM`` suffixes). Empty when nothing
    identifying is left, so it can never match another empty key.
    """
    stem = str(name)
    while True:
        head, dot, tail = stem.rpartition(".")
        if not dot or not tail or not tail.isalnum() or len(tail) > 5:
            break
        stem = head
    tokens = [t for t in re.split(r"[^a-z0-9]+", stem.lower()) if t]
    while tokens and tokens[-1] in _NAME_ROLE_TOKENS:
        tokens.pop()
    while tokens and tokens[0] in _NAME_ROLE_TOKENS:
        tokens.pop(0)
    return "".join(tokens)


def pair_corpus(frd_feats: dict, wb_feats: dict, thresholds: dict) -> dict:
    """One-to-one pairing: exact NAME match first, then greedy best-score.

    Returns {"pairs": {doc_id: {reference, score, confidence, components,
                                 matched_by}},
             "unmapped": [doc_id...], "unpaired_references": [name...]}.

    Name match (``matched_by: "name"``) is definitive: an STTM whose name
    keys to exactly one FRD — the library's ``FRD_<name>`` ↔ ``STTM_<name>``
    convention, the renderer's own ``<doc_id>.sttm.xlsx``, or a reviewer's
    ``<name> STTM.xlsx`` — is that FRD's STTM, whatever the content
    score says; confidence is "high" and the score is still recorded for
    display. Ambiguous keys (two FRDs or two workbooks sharing one key)
    are NOT name-paired — they fall through to similarity, never guessed.

    Similarity (``matched_by: "similarity"``): confidence is a computed
    verdict (code, never a model): "high" at/above pair_high, else "low".
    Below pair_min an FRD stays unmapped — a wrong pair poisons both the
    template library and the eval, so the gate errs toward unmapped and
    the reviewer can see the scores.
    """
    pairs: dict = {}
    used_refs: set[str] = set()

    # -- name match, one-to-one and unambiguous only
    frd_by_key: dict[str, list[str]] = {}
    for doc_id in frd_feats:
        k = name_key(doc_id)
        if k:
            frd_by_key.setdefault(k, []).append(doc_id)
    ref_by_key: dict[str, list[str]] = {}
    for name in wb_feats:
        k = name_key(name)
        if k:
            ref_by_key.setdefault(k, []).append(name)
    for k, doc_ids in frd_by_key.items():
        refs = ref_by_key.get(k, [])
        if len(doc_ids) != 1 or len(refs) != 1:
            continue
        doc_id, name = doc_ids[0], refs[0]
        m = score_match(frd_feats[doc_id], wb_feats[name])
        pairs[doc_id] = {
            "reference": name,
            "score": m["score"],
            "components": m["components"],
            "confidence": "high",
            "matched_by": "name",
        }
        used_refs.add(name)

    # -- similarity for the rest
    scored = []
    for doc_id, ff in frd_feats.items():
        if doc_id in pairs:
            continue
        for name, wf in wb_feats.items():
            if name in used_refs:
                continue
            m = score_match(ff, wf)
            if m["score"] >= thresholds["pair_min"]:
                scored.append((m["score"], doc_id, name, m))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    for score, doc_id, name, m in scored:
        if doc_id in pairs or name in used_refs:
            continue
        pairs[doc_id] = {
            "reference": name,
            "score": m["score"],
            "components": m["components"],
            "confidence": "high" if m["score"] >= thresholds["pair_high"] else "low",
            "matched_by": "similarity",
        }
        used_refs.add(name)

    return {
        "pairs": pairs,
        "unmapped": sorted(d for d in frd_feats if d not in pairs),
        "unpaired_references": sorted(n for n in wb_feats if n not in used_refs),
    }


# --------------------------------------------------------------------------- #
# template decision (render-time)
# --------------------------------------------------------------------------- #
def decide_templates(target_feat: dict, wb_feats: dict, thresholds: dict,
                     exclude: frozenset | set = frozenset(),
                     pinned: str | None = None) -> dict:
    """Pick the template(s) for one document. Pure code; the verdict
    vocabulary is exactly three modes:

    - "single":   top match ≥ template_single_min → that workbook is the
                  template (layout dialect + dictionary + conventions).
    - "amalgam":  no single match, but ≥ 1 workbook ≥ template_amalgam_min →
                  the top-k such workbooks are merged (first-wins per table
                  key; the best match's dialect leads).
    - "freeform": nothing clears template_amalgam_min → the agent renders
                  from the contract alone, best-effort, and the run is
                  FLAGGED — never a silent guess.

    ``exclude`` removes a document's OWN reference workbook from candidacy
    (cross-validation: the ground truth must not feed the render — decided
    2026-08-22). Excluded references are still listed in the decision for
    display, marked excluded, score omitted from selection.

    ``pinned`` names a workbook that is the template BY IDENTITY, not by
    score: the document's own STTM when the corpus paired them by exact
    name (``pair_corpus``'s ``matched_by == "name"`` — definitive there, so
    definitive here too; added 2026-08-25). Mode is "single" with that
    workbook, whatever the scores say; the ranking is still computed and
    shown. A pinned workbook that is also excluded (cross-validation on) is
    NOT pinned — exclusion wins, so exclude-own keeps meaning what it says.
    """
    ranked = []
    for name, wf in sorted(wb_feats.items()):
        m = score_match(target_feat, wf)
        ranked.append({"reference": name, "score": m["score"],
                       "components": m["components"],
                       "excluded": name in exclude})
    ranked.sort(key=lambda r: (-r["score"], r["reference"]))

    eligible = [r for r in ranked if not r["excluded"]]
    top_k = max(1, int(thresholds["template_top_k"]))

    pinned_row = next((r for r in eligible if r["reference"] == pinned), None) \
        if pinned else None
    if pinned_row is not None:
        mode, selections = "single", [pinned_row]
    elif eligible and eligible[0]["score"] >= thresholds["template_single_min"]:
        mode, selections = "single", [eligible[0]]
    else:
        amalgam = [r for r in eligible
                   if r["score"] >= thresholds["template_amalgam_min"]][:top_k]
        if amalgam:
            mode, selections = ("amalgam" if len(amalgam) > 1 else "single"), amalgam
            # A lone above-amalgam-floor match is still a single template —
            # "amalgam of one" would misreport what happened.
        else:
            mode, selections = "freeform", []

    return {
        "mode": mode,
        "selections": selections,
        "ranked": ranked,
        "pinned": pinned_row["reference"] if pinned_row is not None else None,
        "thresholds": {k: thresholds[k] for k in
                       ("template_single_min", "template_amalgam_min", "template_top_k")},
    }


def merge_dictionaries(dicts_by_name: dict, order: list[str]) -> dict:
    """Amalgamate parsed reference dictionaries, best match first.

    First-wins per table key: a later workbook never overwrites a feed the
    better-matching workbook already supplies. The lead workbook's dialect
    and meta carry — the rendered STTM must have ONE coherent layout, not a
    stitched one. Provenance per feed key is recorded so the report can say
    which template supplied which sheet.
    """
    if not order:
        raise ValueError("merge_dictionaries needs at least one reference name")
    lead = dicts_by_name[order[0]]
    merged = {"dialect": lead["dialect"], "meta": lead.get("meta", {}),
              "feeds": {}, "feed_sources": {}}
    for name in order:
        d = dicts_by_name[name]
        for key, feed in d.get("feeds", {}).items():
            if key not in merged["feeds"]:
                merged["feeds"][key] = feed
                merged["feed_sources"][key] = name
    return merged
