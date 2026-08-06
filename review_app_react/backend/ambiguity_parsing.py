"""Ported from the retired review_app/app.py's inline helpers (Streamlit app,
deleted once this backend became the only maintained review app).

review_app/app.py could never be imported directly -- it ran Streamlit page
config and UI calls at module import time -- so this was always a straight
port (same logic, same shape) rather than a shared import. `data_access.py`
(the actual data-access layer and `_provenance.human_resolutions`
persistence format) is a real shared module, now living in this directory.

There used to be a `parse_ambiguity()` here that regex-reconstructed
`candidates` out of a bare ambiguity string, because 03_contract_build.py
only emitted display strings. It's gone: 03_contract_build.py now emits
structured GatedAmbiguity objects directly (`id`/`kind`/`has_candidates`/
`candidates`/`context` -- see notebooks/_models.py), so both `gated_items()`
below and review_app/app.py's copy just read those fields off the contract
JSON.
"""

from __future__ import annotations

import re
from typing import Any


def gated_items(contract: dict) -> list[dict]:
    prov = contract.get("_provenance", {})
    return list(prov.get("ambiguities", [])) + list(prov.get("grounding", {}).get("advisory_flagged", []))


def existing_resolution(contract: dict, ambiguity_id: str) -> dict | None:
    for r in contract.get("_provenance", {}).get("human_resolutions", []):
        if r["ambiguity_id"] == ambiguity_id:
            return r
    return None


def merge_resolution(contract: dict, resolution_record: dict[str, Any]) -> None:
    """Mutates `contract` in place, merging one resolution record into
    `_provenance.human_resolutions` keyed by ambiguity_id -- same upsert
    logic app.py's form-submit handler uses, applied to a single record
    instead of a whole form's worth at once."""
    prov = contract.setdefault("_provenance", {})
    existing = {r["ambiguity_id"]: r for r in prov.get("human_resolutions", [])}
    existing[resolution_record["ambiguity_id"]] = resolution_record
    prov["human_resolutions"] = list(existing.values())


def rule_text(raw: dict) -> str | None:
    """The FRD rule a gated ambiguity is about, as its own field.

    Response shaping only -- the contract JSON is read, never rewritten.
    03_contract_build.py's attribution ambiguities already carry the rule
    verbatim as `context.rule` (structured, so it is preferred). For any
    ambiguity without that field, fall back to the first single- or
    double-quoted span inside the display text, which is where the rule is
    otherwise buried. Returns None when neither exists; callers keep the
    full `text` to render, so nothing is ever blank.
    """
    ctx = raw.get("context") or {}
    rule = ctx.get("rule")
    if isinstance(rule, str) and rule.strip():
        return rule.strip()
    m = re.search(r"'([^']{2,})'|\"([^\"]{2,})\"", raw.get("text") or "")
    if m:
        span = (m.group(1) or m.group(2) or "").strip()
        if span:
            return span
    return None


def to_gated_item_dict(contract: dict, raw: dict) -> dict:
    """One GatedAmbiguity plus its existing resolution (if any), in the
    shape both app.py's `DocumentDetail` route and orchestration.py's
    run-scoped review endpoint return -- factored here so the two callers
    (shared and per-run contract paths) can't drift out of sync."""
    prior = existing_resolution(contract, raw["id"])
    return {
        "id": raw["id"],
        "text": raw["text"],
        "rule_text": rule_text(raw),
        "kind": raw["kind"],
        "has_candidates": raw["has_candidates"],
        "candidates": raw["candidates"],
        "context": raw.get("context") or {},
        "resolution": prior,
    }


def validate_resolution_submission(
    ambiguity: dict, resolution_type: str, chosen_candidate: str | None, rationale: str | None
) -> str | None:
    """Same structural-pick-required policy app.py's submit_resolution()
    enforces server-side (never trust the client alone): a candidate-having
    ambiguity needs a real candidate_pick or an explicit none_of_these; a
    candidate-free one (advisory_grounding) only has free_text to give.
    Returns an error message string, or None if the submission is valid --
    shared so the run-scoped resolutions endpoint enforces the identical
    rule rather than re-deriving it."""
    if resolution_type == "candidate_pick":
        if not chosen_candidate or chosen_candidate not in ambiguity["candidates"]:
            return "candidate_pick requires chosen_candidate to be one of this ambiguity's candidates."
    elif resolution_type == "none_of_these":
        if not ambiguity["has_candidates"]:
            return "none_of_these only applies to an ambiguity that has candidates."
    elif resolution_type == "free_text":
        if not rationale:
            return "free_text requires a rationale."
    return None
