"""resolve_attribution tests — the D2 fix (zero-removal attribution groups).

04_sttm_render.py runs pipeline driver code at module import, so it cannot
be imported. Instead the function under test (and exactly the helpers it
closes over) are lifted out of the committed source by name via the AST and
exec'd — the tests exercise the real committed code, not a copy.
"""

import ast
import re
import unicodedata
from pathlib import Path

RENDER_SRC = Path(__file__).resolve().parent.parent / "notebooks" / "04_sttm_render.py"
# _UNI/_n/_nl were factored into frdsttm.reference_workbooks (2026-08-22);
# they are injected into the exec namespace below from the real module, so
# these tests still exercise the exact committed normalization code.
_NEEDED = {"_ATTR_UNICODE_MAP", "_attr_norm",
           "_COL_TOKEN", "_quote_rule", "resolve_attribution"}


def _load_resolve_attribution():
    tree = ast.parse(RENDER_SRC.read_text(encoding="utf-8"))
    picked = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _NEEDED:
            picked.append(node)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & _NEEDED:
                picked.append(node)
    found = {n.name for n in picked if isinstance(n, ast.FunctionDef)} | {
        t.id for n in picked if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Name)}
    assert found == _NEEDED, f"loader drifted from source: missing {_NEEDED - found}"
    module = ast.Module(body=picked, type_ignores=[])
    from frdsttm.reference_workbooks import _n, _nl

    ns = {"re": re, "unicodedata": unicodedata, "frozenset": frozenset,
          "_n": _n, "_nl": _nl}
    exec(compile(ast.fix_missing_locations(module), str(RENDER_SRC), "exec"), ns)
    return ns["resolve_attribution"]


resolve_attribution = _load_resolve_attribution()

ZIP_RULE = ("If the ZIP_CODE column is NULL, then we are rejecting the "
            "record and moving it to the reject table from the below files.")


def _contract(rule_feeds, candidates):
    """Two-feed contract carrying ZIP_RULE on `rule_feeds`, with one gated
    attribution ambiguity whose candidate set is `candidates`."""
    feeds = [
        {"feed_name": "cv_demo", "validation_rules": [ZIP_RULE] if 0 in rule_feeds else []},
        {"feed_name": "cv_risk", "validation_rules": [ZIP_RULE] if 1 in rule_feeds else []},
    ]
    return {
        "status": "PASS_WITH_FLAGS",
        "feeds": feeds,
        "_provenance": {
            "ambiguities": [{
                "id": "attribution-test1",
                "kind": "attribution",
                "text": f"rule applied to {len(candidates)} feeds: {ZIP_RULE[:60]}",
                "has_candidates": True,
                "candidates": [feeds[i]["feed_name"] for i in candidates],
                "context": {"feed_indices": sorted(candidates), "rule": ZIP_RULE},
            }],
            "grounding": {"advisory_flagged": []},
        },
    }


def _dictionary(zip_in):
    """Reference dictionary; `zip_in` lists feed keys carrying a zip_code column."""
    def fields(key):
        cols = [{"source_column": "member_id"}]
        if key in zip_in:
            cols.append({"source_column": "zip_code"})
        return cols
    return {"feeds": {k: {"fields": fields(k), "recycle_note": None}
                      for k in ("cv_demo", "cv_risk")}}


FEED_MATCH = {0: "cv_demo", 1: "cv_risk"}


def test_zero_removal_dictionary_confirmed_ambiguity_clears():
    # The D2 case from the first live E2E: the extraction already attributed
    # the rule to exactly the feeds whose dictionary carries the column ->
    # zero removals -> before the fix the ambiguity stayed gated forever.
    contract = _contract(rule_feeds={0, 1}, candidates={0, 1})
    resolutions = resolve_attribution(contract, _dictionary(zip_in={"cv_demo", "cv_risk"}), FEED_MATCH)
    assert len(resolutions) == 1 and "confirmed" in resolutions[0]
    assert contract["_provenance"]["ambiguities"] == []
    assert contract["status"] == "PASS"
    # No rule was touched -- confirmation is evidence, not mutation.
    assert contract["feeds"][0]["validation_rules"] == [ZIP_RULE]
    assert contract["feeds"][1]["validation_rules"] == [ZIP_RULE]


def test_dictionary_inconclusive_ambiguity_stays_gated():
    # Column unknown to every dictionary: no removal, no confirmation -- the
    # human gate must survive.
    contract = _contract(rule_feeds={0, 1}, candidates={0, 1})
    resolutions = resolve_attribution(contract, _dictionary(zip_in=set()), FEED_MATCH)
    assert resolutions == []
    assert len(contract["_provenance"]["ambiguities"]) == 1
    assert contract["status"] == "PASS_WITH_FLAGS"


def test_partial_overlap_removes_and_clears_as_before():
    # Pre-existing behavior (regression guard): rule spread wider than the
    # dictionary supports -> removal fires and attribution ambiguities clear.
    contract = _contract(rule_feeds={0, 1}, candidates={0, 1})
    resolutions = resolve_attribution(contract, _dictionary(zip_in={"cv_demo"}), FEED_MATCH)
    assert any("removed rule" in r for r in resolutions)
    assert contract["feeds"][1]["validation_rules"] == []
    assert contract["_provenance"]["ambiguities"] == []
    assert contract["status"] == "PASS"
