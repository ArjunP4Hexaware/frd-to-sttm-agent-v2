"""04's standards fill: the client's target-side rules replacing the template borrow.

Before 2026-08-26 a catalog or schema the FRD did not state came out null, and
the renderer leaned on whichever reference workbook matched to look complete —
which only works when the FRD is already mapped, making the accuracy figure
self-referential (docs/THREE_INPUT_ARCHITECTURE.md §5).

`apply_standards_targets` fills the gap from contracts/naming_standards.json
instead. The three rules these tests pin: the FRD always wins; every fill is
recorded with the contract's own confidence; an underivable value stays NULL
and is gated, never invented.

Functions are lifted from 04_sttm_render.py by name (AST), as in
tests/test_render_template_fill.py.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import pytest

from frdsttm import standards as S
from frdsttm import term_catalog
from frdsttm.reference_workbooks import _nl

RENDER_SRC = Path(__file__).resolve().parent.parent / "notebooks" / "04_sttm_render.py"
_NEEDED = {
    "_ATTR_UNICODE_MAP", "_attr_norm", "_ambiguity_id",
    "_standards_fill_target", "apply_standards_targets",
    "_SEGMENT_SUFFIX", "_table_for_segment", "derive_field_mappings",
    "target_column",   # 2026-08-27: target names come from the term catalog
}


def _load():
    tree = ast.parse(RENDER_SRC.read_text(encoding="utf-8"))
    picked, found = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _NEEDED:
            picked.append(node); found.add(node.name)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)} & _NEEDED
            if names:
                picked.append(node); found |= names
    assert found == _NEEDED, f"loader drifted: missing {_NEEDED - found}"
    ns = {"re": re, "json": json, "hashlib": hashlib, "unicodedata": unicodedata,
          "_std": S, "_nl": _nl, "_tc": term_catalog}
    exec(compile(ast.fix_missing_locations(ast.Module(body=picked, type_ignores=[])),
                 str(RENDER_SRC), "exec"), ns)
    return ns


R = _load()


def _contract(domain="Member", stage=None, standard=None, feed_name="caqh"):
    """A one-feed contract shaped like 03's output."""
    return {
        "feeds": [{
            "feed_name": feed_name,
            "domain": domain,
            "stage_target": dict(stage or {"tables": ["EXT_TPL_CAQH_DTL"]}),
            "standard_target": dict(standard or {"tables": ["EXT_TPL_CAQH_DTL"]}),
            "fields": [],
        }],
    }


# --------------------------------------------------------------------------
# Rule 1 — the FRD always wins
# --------------------------------------------------------------------------

def test_a_stated_catalog_and_schema_are_never_overwritten():
    c = _contract(stage={"catalog": "FROM_FRD", "schema": "frd_schema", "tables": ["T"]})
    R["apply_standards_targets"](c, {0: "k"})
    stg = c["feeds"][0]["stage_target"]
    assert stg["catalog"] == "FROM_FRD"
    assert stg["schema"] == "frd_schema"
    # ...and nothing is recorded as a standards fill for those cells.
    filled = [p for p in c["_provenance"]["standards_fill"]
              if p["layer"] == "stage" and p["source"] == "standards"]
    assert filled == []


def test_the_frd_wins_per_attribute_not_per_layer():
    # Schema stated, catalog not: only the catalog is filled.
    c = _contract(stage={"schema": "frd_schema", "tables": ["T"]})
    R["apply_standards_targets"](c, {0: "k"})
    stg = c["feeds"][0]["stage_target"]
    assert stg["schema"] == "frd_schema"
    assert stg["catalog"] == "PR_DLK"


# --------------------------------------------------------------------------
# Rule 2 — fills are recorded with their confidence
# --------------------------------------------------------------------------

def test_an_unstated_catalog_and_schema_are_filled_from_the_standards():
    c = _contract(domain="Member")
    R["apply_standards_targets"](c, {0: "k"})
    stg = c["feeds"][0]["stage_target"]
    std = c["feeds"][0]["standard_target"]
    assert (stg["catalog"], stg["schema"]) == ("PR_DLK", "STG_MBR")
    assert (std["catalog"], std["schema"]) == ("PR_STD", "MBR")


def test_every_fill_records_source_confidence_and_version():
    c = _contract(domain="Member")
    R["apply_standards_targets"](c, {0: "k"})
    fills = {(p["layer"], p["attribute"]): p
             for p in c["_provenance"]["standards_fill"] if p["source"] == "standards"}
    assert set(fills) == {("stage", "catalog"), ("stage", "schema"),
                          ("standard", "catalog"), ("standard", "schema")}
    # The catalogs come from ONE mapped workbook; the contract says so and the
    # provenance must carry that through to the reviewer.
    assert fills[("stage", "catalog")]["confidence"] == "OBSERVED_SINGLE_PAIR"
    assert fills[("stage", "schema")]["confidence"] == "OBSERVED"
    for p in fills.values():
        assert p["standards_version"] == S.NAMING_VERSION
        assert p["feed"] == "caqh"


def test_run_level_standards_provenance_is_recorded():
    c = _contract()
    R["apply_standards_targets"](c, {0: "k"})
    prov = c["_provenance"]["standards"]
    assert prov["standards_sha256"] == S.standards_sha256()
    assert prov["naming_version"] == S.NAMING_VERSION
    assert prov["engineering_version"] == S.ENGINEERING_VERSION
    # Surfaced so a reviewer knows the column-level rules are still unconfirmed.
    assert prov["column_rules_sourced"] is False


# --------------------------------------------------------------------------
# Rule 3 — underivable stays NULL and is gated, never invented
# --------------------------------------------------------------------------

def test_a_domain_outside_the_clients_vocabulary_is_gated_not_guessed():
    # 'sdoh' is the SD FRD's own domain and is absent from the client's
    # Domain/Subdomain table. A real gap, not a typo.
    assert S.abbreviate("domains", "sdoh") is None
    c = _contract(domain="sdoh", feed_name="sd_community_risk")
    R["apply_standards_targets"](c, {0: "k"})
    assert c["feeds"][0]["stage_target"].get("schema") is None
    assert c["feeds"][0]["standard_target"].get("schema") is None
    gated = c["_provenance"]["ambiguities"]
    kinds = {g["kind"] for g in gated}
    assert kinds == {"standards_gap"}
    attrs = {(g["context"]["layer"], g["context"]["attribute"]) for g in gated}
    assert attrs == {("stage", "schema"), ("standard", "schema")}
    for g in gated:
        assert "sdoh" in g["text"]
        assert g["context"]["domain"] == "sdoh"


def test_the_gap_reason_names_which_input_was_missing():
    c = _contract(domain="sdoh")
    R["apply_standards_targets"](c, {0: "k"})
    reasons = [p["reason"] for p in c["_provenance"]["standards_fill"]
               if p["source"] == "unresolved"]
    assert reasons
    assert all("not in the client's naming standards" in r for r in reasons)


def test_a_gated_schema_offers_the_clients_own_vocabulary_as_options():
    c = _contract(domain="sdoh")
    R["apply_standards_targets"](c, {0: "k"})
    g = next(x for x in c["_provenance"]["ambiguities"] if x["context"]["attribute"] == "schema")
    assert "MEMBER" in g["options"]
    assert g["options"] == sorted(g["options"])


def test_ambiguity_ids_are_stable_across_runs():
    ids = []
    for _ in range(2):
        c = _contract(domain="sdoh")
        R["apply_standards_targets"](c, {0: "k"})
        ids.append([g["id"] for g in c["_provenance"]["ambiguities"]])
    assert ids[0] == ids[1]
    assert all(i.startswith("standards_gap-") for i in ids[0])


def test_the_catalog_is_never_generalised_to_an_unlisted_layer():
    # PR_DLK and PR_STD are not built the same way; a third layer must gate.
    assert S.catalog_for("gold") is None


def test_a_feed_with_no_target_block_at_all_is_skipped_not_crashed():
    c = _contract()
    c["feeds"][0]["standard_target"] = None
    R["apply_standards_targets"](c, {0: "k"})
    assert c["feeds"][0]["stage_target"]["catalog"] == "PR_DLK"
    assert not [p for p in c["_provenance"]["standards_fill"] if p["layer"] == "standard"]


def test_unmatched_feeds_are_left_alone():
    c = _contract()
    c["feeds"].append({"feed_name": "other", "domain": "Member",
                       "stage_target": {"tables": ["X"]}, "standard_target": {"tables": ["X"]},
                       "fields": []})
    R["apply_standards_targets"](c, {0: "k"})   # only feed 0 matched
    assert c["feeds"][1]["stage_target"].get("catalog") is None


# --------------------------------------------------------------------------
# The rendered rows carry the filled values
# --------------------------------------------------------------------------

def _dictionary(n=2):
    return {"feeds": {"k": {
        "fields": [{"source_column": f"col_{i}", "segment": None} for i in range(n)],
        "ref_targets": [{"stage": {}, "standard": {}} for _ in range(n)],
    }}}


def test_derive_field_mappings_emits_the_filled_catalog_and_schema():
    c = _contract(domain="Member")
    R["apply_standards_targets"](c, {0: "k"})
    R["derive_field_mappings"](c, _dictionary(), {0: "k"})
    for f in c["feeds"][0]["fields"]:
        assert f["stage"]["catalog"] == "PR_DLK"
        assert f["stage"]["schema"] == "STG_MBR"
        assert f["standard"]["catalog"] == "PR_STD"
        assert f["standard"]["schema"] == "MBR"


def test_without_the_standards_pass_the_rows_are_null_as_before():
    # Pins the ordering requirement: apply_standards_targets must run FIRST.
    c = _contract(domain="Member")
    R["derive_field_mappings"](c, _dictionary(), {0: "k"})
    assert c["feeds"][0]["fields"][0]["stage"]["catalog"] is None


def test_a_gated_feed_still_renders_its_rows_with_null_schema():
    # Never a silent sparse render: the rows exist, the cell is empty, the
    # ambiguity says why.
    c = _contract(domain="sdoh")
    R["apply_standards_targets"](c, {0: "k"})
    R["derive_field_mappings"](c, _dictionary(3), {0: "k"})
    fields = c["feeds"][0]["fields"]
    assert len(fields) == 3
    assert all(f["stage"]["schema"] is None for f in fields)
    assert c["_provenance"]["ambiguities"]


# --------------------------------------------------------------------------
# AS-IS UNLESS CONFIRMED (Arjun, 2026-08-27 evening) — no guessed names
# --------------------------------------------------------------------------

def _renaming_contract():
    """A source whose FRD SIGNALS a rename: sub-domain TPL carried into the
    table names (the CAQH shape)."""
    return {"feeds": [{
        "feed_name": "caqh", "domain": "Member", "sub_domain": "Third Party Liability",
        "stage_target": {"tables": ["EXT_TPL_CAQH_DTL"]},
        "standard_target": {"tables": ["EXT_TPL_CAQH_DTL"]},
        "fields": [],
    }]}


def _catalog_with_a_leak():
    """Another workbook that maps zip_code to a TPL_ name."""
    parsed = {"feeds": {"t": {
        "fields": [{"source_column": "zip_code", "audit": False},
                   {"source_column": "member_id", "audit": False}],
        "ref_targets": [{"stage": {"column": "TPL_RECIP_ZIP_CODE"}, "standard": {"column": "TPL_RECIP_ZIP_CODE"}},
                        {"stage": {"column": "TPL_MEME_ID"}, "standard": {"column": "TPL_MEME_ID"}}],
    }}}
    return term_catalog.build_catalog({"STTM_OTHER.xlsx": parsed})


def test_unconfirmed_rule_carries_names_as_is_and_gates_the_signal(monkeypatch):
    monkeypatch.setitem(S.NAMING_STANDARDS["column_rules"], "confirmed_by", None)
    c = _renaming_contract()
    R["derive_field_mappings"](c, _dictionary(2), {0: "k"})
    names = [(f["stage"]["column"], f["standard"]["column"]) for f in c["feeds"][0]["fields"]]
    assert names == [("col_0", "col_0"), ("col_1", "col_1")]        # vendor's names, unchanged
    g = [a for a in c["_provenance"]["ambiguities"] if a["kind"] == "column_convention_unconfirmed"]
    assert len(g) == 1 and "carried as-is" in g[0]["text"]          # once per source, not per row
    conv = c["_provenance"]["term_catalog"]["column_conventions"]["caqh"]
    assert conv["inferred"]["convention"] == "prefixed_upper_snake"
    assert conv["applied"]["convention"] == "as_is" and conv["confirmed_by_client"] is False
    assert c["_provenance"]["term_catalog"]["carried_as_is"] == 4    # 2 rows x 2 layers


def test_a_confirmed_rule_applies_the_inferred_rename_again(monkeypatch):
    """Flipping it back is config, not code."""
    monkeypatch.setitem(S.NAMING_STANDARDS["column_rules"], "confirmed_by", "client sign-off")
    c = _renaming_contract()
    R["derive_field_mappings"](c, _dictionary(1), {0: "k"})
    assert c["feeds"][0]["fields"][0]["stage"]["column"] == "TPL_COL_0"
    assert not [a for a in c["_provenance"].get("ambiguities", [])
                if a["kind"] == "column_convention_unconfirmed"]


def test_the_catalog_is_not_consulted_under_as_is(monkeypatch):
    """The SD leak: zip_code -> TPL_RECIP_ZIP_CODE came from CAQH's workbook
    landing on an as-is source. Under as-is the vocabulary is never asked."""
    monkeypatch.setitem(S.NAMING_STANDARDS["column_rules"], "confirmed_by", None)
    c = {"feeds": [{"feed_name": "sd", "domain": "sdoh", "sub_domain": "Public",
                    "stage_target": {"tables": ["sd_community_risk"]},
                    "standard_target": {"tables": ["sd_community_risk"]}, "fields": []}]}
    d = {"feeds": {"k": {"fields": [{"source_column": "zip_code", "segment": None},
                                    {"source_column": "member_id", "segment": None}],
                         "ref_targets": [{"stage": {}, "standard": {}}] * 2}}}
    R["derive_field_mappings"](c, d, {0: "k"}, _catalog_with_a_leak(), set())
    assert [f["stage"]["column"] for f in c["feeds"][0]["fields"]] == ["zip_code", "member_id"]
    assert c["_provenance"]["term_catalog"]["filled_from_catalog"] == 0
    assert not c["_provenance"].get("ambiguities")                    # as-is source: nothing to ask
