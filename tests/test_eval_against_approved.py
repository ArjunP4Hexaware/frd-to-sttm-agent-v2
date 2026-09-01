"""tools/eval_against_approved.py — offline, on a rendered synthetic workbook
whose copy is mutated into the "approved" one."""
import sys
from pathlib import Path

from openpyxl import load_workbook

from frdsttm import completeness as c
from frdsttm import render
from frdsttm.dictionary import parse_dictionary_workbook
from conftest import make_vdd, spec_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import eval_against_approved as ev  # noqa: E402


def _agent_workbook(tmp_path):
    spec = spec_for()
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "v.xlsx"))
    sources, _ = c.derive_targets(spec)
    pairing, _ = c.pair_files(spec, vdd)
    for s in sources:
        f = pairing.get(s["feed_index"])
        s["file"], s["field_sheet"] = (f["file_name_pattern"], f["field_sheet"]) if f else (None, None)
    out = tmp_path / "agent.xlsx"
    render.render_workbook(spec, sources, vdd, out)
    return out, spec


def _cols(ws):
    """header row + column indexes: source name, source type, stage schema/column, standard type."""
    hr = next(r for r in range(1, 30) if ws.cell(r, 2).value == "Field Name")
    hdr = [ws.cell(hr, i).value or "" for i in range(1, ws.max_column + 1)]
    cols = [i for i, h in enumerate(hdr, 1) if h == "ColumnName"]
    types = [i for i, h in enumerate(hdr, 1) if h in ("DataType", "Data Type")]
    schemas = [i for i, h in enumerate(hdr, 1) if h == "Schema"]
    return hr, {"name": 2, "stage_schema": schemas[0], "stage_col": cols[0], "std_col": cols[1], "std_type": types[-1]}


def _row_of(ws, hr, name_col, name):
    return next(r for r in range(hr + 1, ws.max_row + 1) if ws.cell(r, name_col).value == name)


def test_unit_outcome():
    assert ev.outcome("String", " string ") == "match"
    assert ev.outcome(True, True) == "match"
    assert ev.outcome("", "stg") == "agent_blank"
    assert ev.outcome("", "stg", questioned=True) == "question"
    assert ev.outcome("stg", "") == "approved_blank"
    assert ev.outcome("a", "b") == "disagree"


def test_row_key_audit_rows_do_not_collide():
    audit = {"source_column": "NA", "audit": True}
    assert ev.row_key(audit, {"stage": {"column": "LOB"}}) == "audit:lob"
    assert ev.row_key(audit, {"stage": {"column": "SRC_FILE_NAME"}}) == "audit:src_file_name"
    assert ev.row_key({"source_column": " Claim_ID ", "audit": False}, {}) == "claim_id"


def test_identical_workbooks_all_match(tmp_path):
    agent, _ = _agent_workbook(tmp_path)
    res = ev.evaluate(agent, agent)
    assert not res["findings"] and len(res["tables"]) == 1
    t = res["tables"][0]
    assert t["n_matched"] == 7 and not t["agent_only"] and not t["approved_only"]
    assert all(set(c) == {"match"} for c in t["per_field"].values())
    assert sum(1 for r in t["rows"] if r["audit"]) == 4
    assert t["per_field"]["source.datatype"] == {"match": 3}      # audit rows: source band not scored
    assert t["per_field"]["stage.column"] == {"match": 7}


def test_mutated_copy_lands_in_the_right_buckets(tmp_path):
    agent, _ = _agent_workbook(tmp_path)
    wb = load_workbook(agent)
    ws = wb.active
    hr, k = _cols(ws)
    ws.cell(_row_of(ws, hr, k["name"], "MEMBER_ID"), k["stage_col"], "member_identifier")   # BSA renamed
    ws.cell(_row_of(ws, hr, k["name"], "AMOUNT"), k["std_type"], "Decimal(12,4)")             # BSA typed differently
    ws.cell(_row_of(ws, hr, k["name"], "AMOUNT"), k["stage_schema"]).value = None                   # BSA left blank
    ws.delete_rows(_row_of(ws, hr, k["name"], "CLAIM_ID"))                                     # BSA dropped a row
    r = ws.max_row + 1
    ws.cell(r, k["name"], "EXTRA_COL")                                                          # BSA added a row
    ws.cell(r, k["stage_col"], "EXTRA_COL")
    approved = tmp_path / "approved.xlsx"
    wb.save(approved)

    res = ev.evaluate(agent, approved)
    t = res["tables"][0]
    assert t["agent_only"] == ["claim_id"] and t["approved_only"] == ["extra_col"]
    assert t["n_matched"] == 6
    assert t["per_field"]["stage.column"] == {"match": 5, "disagree": 1}
    assert t["per_field"]["standard.datatype"] == {"match": 5, "disagree": 1}
    assert t["per_field"]["stage.schema"] == {"match": 5, "approved_blank": 1}
    assert ("stage.column", "MEMBER_ID", "member_identifier", 1) in t["distinct_disagreements"]
    assert res["totals"]["stage.column"]["disagree"] == 1

    out = ev.write_report(res, tmp_path / "eval.xlsx")
    names = load_workbook(out).sheetnames
    assert names[:2] == ["Summary", "Disagreements"] and len(names) == 3


def test_unanswered_question_turns_agent_blank_into_question(tmp_path):
    agent, spec = _agent_workbook(tmp_path)
    wb = load_workbook(agent)
    ws = wb.active
    hr, k = _cols(ws)
    for r in range(hr + 1, ws.max_row + 1):
        if ws.cell(r, k["name"]).value:
            ws.cell(r, k["stage_schema"]).value = None       # the agent left the stage schema open
    blank = tmp_path / "agent_blank.xlsx"
    wb.save(blank)
    run = {"extraction": spec,
           "assessment": {"questions": [
               {"kind": "target_gap", "answer": None,
                "context": {"feed_index": 0, "layer": "stage", "attribute": "schema"}}]}}

    assert ev.questioned_fields(run) == {("clm_claims_stg", "stage.schema"), ("claims intake", "stage.schema")}
    with_run = ev.evaluate(blank, agent, run)["tables"][0]["per_field"]["stage.schema"]
    without = ev.evaluate(blank, agent)["tables"][0]["per_field"]["stage.schema"]
    assert with_run == {"question": 7} and without == {"agent_blank": 7}

    run["assessment"]["questions"][0]["answer"] = "stg_clm"
    assert ev.questioned_fields(run) == set()


def test_table_key_mismatch_is_reported_and_paired_by_order():
    agent = {"dialect": "x", "feeds": {"a_tbl": {"fields": [], "ref_targets": []}}}
    approved = {"dialect": "x", "feeds": {"b_tbl": {"fields": [], "ref_targets": []}}}
    pairs, findings = ev.pair_tables(agent, approved)
    assert pairs == [("a_tbl ~ b_tbl", "a_tbl", "b_tbl")]
    assert findings and "paired by sheet order" in findings[0]
