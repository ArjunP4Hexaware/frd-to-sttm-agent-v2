"""One-time migration: string-shaped ambiguities/resolutions -> structured schema.

Rewrites the real fixture contract JSONs under
`local_dev_fixtures/sttm_out/contracts/*.contract.json` from the pre-
structured-ambiguity shape (bare strings in `_provenance.ambiguities` /
`_provenance.grounding.advisory_flagged`, human_resolutions keyed by exact
text match) to the GatedAmbiguity / HumanResolution schema now emitted
natively by `03_contract_build.py` and `04_sttm_render.py` (see
`notebooks/_models.py`).

This is a ONE-TIME transform for the two local demo/CAQH fixture contracts that
already carry hand-reviewed `human_resolutions` from prior sessions -- not a
general-purpose converter wired into the pipeline. A fresh `03_contract_build.py`
run never needs this: it emits the structured shape natively. Run again
(idempotent) only if you need to re-derive from the original string shape;
it will error loudly if the contract has already been migrated (no bare-string
ambiguities left to migrate).

Attribution ambiguities are re-derived from `contract["feeds"]` using the
exact same grouping algorithm as `attribution_check()` (03_contract_build.py)
/ `_attribution_groups()` (04_sttm_render.py) -- ported here rather than
imported, for the same reason both of those files can't be imported directly
(they run pipeline driver code at module import time). This recovers the
*untruncated* rule text (needed for `context.rule` and for byte-identical
`id` generation) directly from the feeds, not from the old ambiguity string,
which embeds only a 120-char truncation.

Advisory-grounding and disagreement ambiguities have no equivalent
full-fidelity source to re-derive from (the flagged value IS the full
string, already captured in the old text's repr) -- those are parsed
directly out of the old text with the same shapes review_app/app.py's
(now-deleted) `parse_ambiguity()` used, since this is exactly the
narrow, one-time use that kind of parsing was always meant for.

Usage:
    python scripts/migrate_ambiguity_ids.py [--dry-run]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "notebooks"))

from _models import GatedAmbiguity, HumanResolution  # noqa: E402

CONTRACTS_DIR = REPO_ROOT / "local_dev_fixtures" / "sttm_out" / "contracts"
FIXTURES = [
    # Anonymized form of the original fixture filename (2026-08-06 scrub);
    # the one-time migration already ran, so this allowlist entry is inert
    # unless the local contract file is renamed to match.
    "FRD_Medicare Expansion-OHDS - Social Factors.contract.json",
    "FRD_STG_STD_PaymentIntegrity_TPL_CAQH_To_DL_Ingestion_1005034.contract.json",
]


# --------------------------------------------------------------------------- #
# _ambiguity_id / _attr_norm -- verbatim ports of 03_contract_build.py /
# 04_sttm_render.py's versions. Must stay byte-identical, or migrated ids
# won't match what a fresh pipeline run of unchanged inputs would produce.
# --------------------------------------------------------------------------- #
_ATTR_UNICODE_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", " ": " ",
})


def _attr_norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_ATTR_UNICODE_MAP)
    t = re.sub(r"[*_#|`]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _ambiguity_id(kind: str, text: str, context: dict) -> str:
    key = json.dumps({"kind": kind, "text": text, "context": context}, sort_keys=True)
    return f"{kind}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _make_ambiguity(kind: str, text: str, candidates: list, context: dict) -> dict:
    obj = {
        "id": _ambiguity_id(kind, text, context),
        "kind": kind,
        "text": text,
        "has_candidates": bool(candidates),
        "candidates": list(candidates),
        "context": context,
    }
    GatedAmbiguity.model_validate(obj)  # fail loud if this migration produced a bad shape
    return obj


# --------------------------------------------------------------------------- #
# attribution: re-derive full-fidelity groups from contract["feeds"] directly
# -- same algorithm as attribution_check()/_attribution_groups(), so the
# regenerated `text` is byte-identical to what's already stored in the old
# ambiguities list (used below to match old string -> new object).
# --------------------------------------------------------------------------- #
def _attribution_groups_from_feeds(feeds: list) -> list[dict]:
    seen: dict = {}
    for i, feed in enumerate(feeds):
        rules = list(feed.get("validation_rules") or [])
        if feed.get("recycle_rule"):
            rules = rules + [feed["recycle_rule"]]
        for rule in rules:
            entry = seen.setdefault(_attr_norm(rule), {"rule": rule, "feed_indices": [], "feed_names": []})
            entry["feed_indices"].append(i)
            entry["feed_names"].append(feed["feed_name"])
    groups = []
    for entry in seen.values():
        if len(entry["feed_indices"]) > 1:
            text = (f"rule applied to {len(entry['feed_names'])} feeds ({', '.join(entry['feed_names'])}) — "
                    f"attribution unconfirmed pending source dictionary: {entry['rule'][:120]!r}")
            context = {"feed_names": entry["feed_names"], "feed_indices": entry["feed_indices"], "rule": entry["rule"]}
            groups.append(_make_ambiguity("attribution", text, entry["feed_names"], context))
    return groups


# --------------------------------------------------------------------------- #
# advisory_grounding / disagreement: no richer source than the old text
# itself exists (the flagged/disagreeing value IS the full string already),
# so these are parsed directly out of it -- narrow, one-time use of the same
# shapes review_app/app.py's now-deleted parse_ambiguity() used.
# --------------------------------------------------------------------------- #
_ADVISORY_SPLIT_RE = re.compile(r"^(?P<path>.+?): (?P<value_repr>['\"].*)$", re.S)
_FEED_PATH_RE = re.compile(r"^feeds\[(?P<idx>\d+)\]\([^)]*\)\.(?P<field>\w+)$")
_DISAGREEMENT_RE = re.compile(
    r"^(?P<field>[\w.]+): agent said (?P<agent>.+), content says (?P<content>.+) "
    r"— kept agent value, flagged$"
)


def _migrate_advisory_text(text: str) -> dict:
    m = _ADVISORY_SPLIT_RE.match(text)
    if not m:
        raise ValueError(f"advisory ambiguity text doesn't match the expected 'path: repr(value)' shape: {text!r}")
    path, value = m.group("path"), ast.literal_eval(m.group("value_repr"))
    context = {"path": path, "original_value": value}
    fm = _FEED_PATH_RE.match(path)
    if fm:
        context["feed_index"] = int(fm.group("idx"))
        context["field"] = fm.group("field")
    return _make_ambiguity("advisory_grounding", text, [], context)


def _migrate_disagreement_text(text: str) -> dict:
    m = _DISAGREEMENT_RE.match(text)
    if not m:
        raise ValueError(f"disagreement ambiguity text doesn't match the expected shape: {text!r}")
    field = "project." + m.group("field") if not m.group("field").startswith("project.") else m.group("field")
    agent_v, content_v = m.group("agent").strip("'\""), m.group("content").strip("'\"")
    context = {"field": field, "agent_value": agent_v, "content_value": content_v}
    return _make_ambiguity("disagreement", text, [agent_v, content_v], context)


def _classify_and_migrate(old_text: str, attribution_groups: list[dict]) -> dict:
    """Attribution first (exact text match against feed-derived groups,
    highest fidelity); then advisory-shape and disagreement-shape regexes.
    Raises loudly if nothing matches -- never silently drop an ambiguity."""
    for g in attribution_groups:
        if g["text"] == old_text:
            return g
    for parser in (_migrate_disagreement_text, _migrate_advisory_text):
        try:
            return parser(old_text)
        except ValueError:
            continue
    raise ValueError(
        f"could not classify ambiguity text into attribution/disagreement/advisory_grounding "
        f"shape -- refusing to migrate rather than guess: {old_text!r}")


# --------------------------------------------------------------------------- #
# human_resolutions: old shape -> HumanResolution
# --------------------------------------------------------------------------- #
def _migrate_resolution(old: dict, new_ambiguity: dict) -> dict:
    res = old["resolution"]
    chosen, override, note = res.get("chosen_candidate"), res.get("override_value"), res.get("note")
    has_candidates = new_ambiguity["has_candidates"]

    if chosen is not None:
        resolution_type = "candidate_pick"
        chosen_candidate = chosen
        rationale = note or None
    elif has_candidates:
        # A free-text override recorded against a candidate-having ambiguity
        # -- e.g. the demo fixture's third gated item -- is exactly the "structural pick
        # required, not provided" shape this migration must preserve as
        # unresolved/still-gated, not silently upgrade to applied.
        resolution_type = "free_text"
        chosen_candidate = None
        rationale = " | ".join(filter(None, [override, note])) or None
    else:
        # Candidate-free (advisory_grounding): override_value IS the real
        # resolution, same as it is applied today.
        resolution_type = "free_text"
        chosen_candidate = None
        rationale = " | ".join(filter(None, [override, note])) or None

    new = {
        "ambiguity_id": new_ambiguity["id"],
        "kind": new_ambiguity["kind"],
        "resolution_type": resolution_type,
        "chosen_candidate": chosen_candidate,
        "rationale": rationale,
        "candidates_snapshot": old.get("candidates") or [],
        "resolved_at": res["resolved_at"],
        "resolved_by": None,  # not captured by either review app today
    }
    HumanResolution.model_validate(new)
    return new


def migrate_contract(contract: dict) -> tuple[dict, list[str]]:
    """Returns (migrated_contract, log_lines). Mutates a deep-copied
    contract, leaves the input untouched."""
    import copy
    contract = copy.deepcopy(contract)
    prov = contract.setdefault("_provenance", {})
    log = []

    old_ambiguities = prov.get("ambiguities", [])
    old_advisory = prov.get("grounding", {}).get("advisory_flagged", [])
    old_resolutions = prov.get("human_resolutions", [])

    if old_ambiguities and isinstance(old_ambiguities[0], dict):
        raise ValueError("ambiguities are already structured (dicts, not strings) -- already migrated, refusing to re-run")

    attribution_groups = _attribution_groups_from_feeds(contract.get("feeds", []))

    old_to_new: dict[str, dict] = {}
    new_ambiguities = []
    for text in old_ambiguities:
        obj = _classify_and_migrate(text, attribution_groups)
        old_to_new[text] = obj
        new_ambiguities.append(obj)
        log.append(f"  ambiguity -> {obj['kind']:12s} id={obj['id']}  has_candidates={obj['has_candidates']}  {text[:80]!r}")

    new_advisory = []
    for text in old_advisory:
        obj = _classify_and_migrate(text, attribution_groups)
        old_to_new[text] = obj
        new_advisory.append(obj)
        log.append(f"  advisory   -> {obj['kind']:12s} id={obj['id']}  has_candidates={obj['has_candidates']}  {text[:80]!r}")

    new_resolutions = []
    for old_res in old_resolutions:
        old_text = old_res["ambiguity"]
        new_ambiguity = old_to_new.get(old_text)
        if new_ambiguity is None:
            raise ValueError(
                f"human_resolutions references an ambiguity text not found in "
                f"_provenance.ambiguities/advisory_flagged -- refusing to migrate: {old_text!r}")
        new_res = _migrate_resolution(old_res, new_ambiguity)
        new_resolutions.append(new_res)
        log.append(
            f"  resolution -> {new_res['kind']:12s} {new_res['resolution_type']:14s} "
            f"chosen={new_res['chosen_candidate']!r}  applied_expected="
            f"{'YES' if new_res['resolution_type'] == 'candidate_pick' or not new_ambiguity['has_candidates'] else 'NO (structural pick required)'}")

    prov["ambiguities"] = new_ambiguities
    prov.setdefault("grounding", {})["advisory_flagged"] = new_advisory
    prov["human_resolutions"] = new_resolutions
    return contract, log


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print the migration plan without writing any files.")
    args = ap.parse_args()

    for name in FIXTURES:
        path = CONTRACTS_DIR / name
        print(f"\n=== {name} ===")
        contract = json.loads(path.read_text(encoding="utf-8"))
        migrated, log = migrate_contract(contract)
        for line in log:
            print(line)
        if args.dry_run:
            print("  (--dry-run: not written)")
            continue
        path.write_text(json.dumps(migrated, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  written -> {path}")


if __name__ == "__main__":
    main()
