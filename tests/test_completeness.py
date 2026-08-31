import json

from conftest import make_frd, make_vdd, spec_for
from frdsttm import completeness as c
from frdsttm.dictionary import parse_dictionary_workbook
from frdsttm.frd_parsing import parse_frd


def _content(tmp_path, **kw):
    return parse_frd(make_frd(tmp_path / "FRD_x.docx", **kw))["content"]


def test_grounded_spec_has_no_unverified_questions(tmp_path):
    summary, qs = c.grounding_audit(spec_for(), _content(tmp_path))
    assert summary["strict_failed"] == []
    assert [q for q in qs if q["kind"] == "unverified"] == []


def test_invented_table_becomes_a_question(tmp_path):
    spec = spec_for(stage_table="made_up_table")
    summary, qs = c.grounding_audit(spec, _content(tmp_path))
    assert summary["strict_failed"] == ["feeds[0].stage_target.tables: 'made_up_table'"]
    q = next(q for q in qs if q["kind"] == "unverified")
    assert q["options"] == [c.KEEP, c.REMOVE] and q["free_text"] is True
    assert q["source"] == "Claims Intake"


def _two_sources(rule):
    spec = spec_for(validation_rules=[rule])
    spec["feeds"].append({**spec["feeds"][0], "feed_name": "Second", "file_name_patterns": ["b.csv"]})
    return spec


def test_shared_rule_on_a_shared_column_is_an_attribution_question():
    spec = _two_sources("If CLAIM_ID is NULL, reject the record.")
    qs, notes = c.attribution_questions(spec, {0: ["CLAIM_ID"], 1: ["CLAIM_ID", "OTHER"]})
    assert len(qs) == 1 and qs[0]["options"] == ["Claims Intake", "Second", c.ALL_SOURCES] and not notes


def test_shared_rule_that_names_every_file_is_settled_by_the_frd():
    spec = _two_sources("If CLAIM_ID is NULL, reject — from the below files: claims_YYYYMMDD.csv; b.csv")
    qs, notes = c.attribution_questions(spec, {0: ["CLAIM_ID"], 1: ["CLAIM_ID"]})
    assert qs == [] and len(notes) == 1 and "names each file" in notes[0]
    assert len(spec["feeds"][0]["validation_rules"]) == 1 and len(spec["feeds"][1]["validation_rules"]) == 1


def test_generic_shared_rule_stays_on_all_sources_without_a_question():
    spec = _two_sources("All the required fields should be populated from the inbound file.")
    qs, notes = c.attribution_questions(spec, {0: ["CLAIM_ID"], 1: ["MEMBER_ID"]})
    assert qs == [] and "names no column" in notes[0]
    assert len(spec["feeds"][0]["validation_rules"]) == 1 and len(spec["feeds"][1]["validation_rules"]) == 1


def test_rule_naming_a_column_only_one_source_has_is_attributed_by_code():
    spec = _two_sources("If MEMBER_ID is NULL, reject the record.")
    qs, notes = c.attribution_questions(spec, {0: ["CLAIM_ID"], 1: ["MEMBER_ID"]})
    assert qs == [] and "attributed to 'Second'" in notes[0]
    assert spec["feeds"][0]["validation_rules"] == [] and len(spec["feeds"][1]["validation_rules"]) == 1


def test_grounding_miss_off_the_workbook_is_a_note_not_a_question(tmp_path):
    spec = spec_for(requirement_ids=["SRQ999999"])           # invented, but never rendered
    summary, qs = c.grounding_audit(spec, _content(tmp_path))
    assert summary["strict_failed"] == ["feeds[0].requirement_ids: 'SRQ999999'"]
    assert qs == [] and len(summary["off_workbook"]) == 1


def test_project_id_disagreement_is_a_note_and_the_frd_wins(tmp_path):
    spec = spec_for(project_id="7654321")
    notes, qs = c.enrich(spec, _content(tmp_path))
    assert qs == [] and spec["project"]["project_id"] == "1234567" and "FRD's line is used" in notes[0]


def test_enrich_fills_project_id_from_frd(tmp_path):
    spec = spec_for(project_id=None)
    notes, qs = c.enrich(spec, _content(tmp_path))
    assert spec["project"]["project_id"] == "1234567" and notes and not qs


def test_pair_files_single_is_automatic(tmp_path):
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "v.xlsx"))
    pairing, qs = c.pair_files(spec_for(), vdd)
    assert pairing[0]["file_name_pattern"] == "claims_YYYYMMDD.csv" and not qs


def test_pair_files_by_name_tokens_and_question_on_miss(tmp_path):
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "v.xlsx", {
        "claims_YYYYMMDD.csv": [("A", "int", "Y", "N", "d", "", "", "")],
        "members_YYYYMMDD.csv": [("B", "int", "Y", "N", "d", "", "", "")],
    }))
    spec = spec_for()
    spec["feeds"].append({**spec["feeds"][0], "feed_name": "Unknown", "file_name_patterns": ["zzz_extract.csv"]})
    pairing, qs = c.pair_files(spec, vdd)
    assert pairing[0]["file_name_pattern"] == "claims_YYYYMMDD.csv"
    assert 1 not in pairing and qs[0]["kind"] == "file_pairing"
    assert qs[0]["options"] == ["members_YYYYMMDD.csv"]


def test_derive_targets_frd_wins_then_standards_then_question():
    sources, qs = c.derive_targets(spec_for())
    stage, standard = sources[0]["layers"]["stage"], sources[0]["layers"]["standard"]
    assert stage["schema"] == "stg_clm" and stage["origin"]["schema"] == "frd"
    assert stage["catalog"] == "PR_DLK" and stage["origin"]["catalog"].startswith("standards")
    assert standard["schema"] == "CLM" and standard["origin"]["schema"].startswith("standards")
    assert qs == []
    spec = spec_for()
    spec["feeds"][0]["domain"] = "sdoh"
    spec["feeds"][0]["standard_target"]["tables"] = []
    sources, qs = c.derive_targets(spec)
    kinds = [(q["kind"], q["context"]["layer"], q["context"]["attribute"]) for q in qs]
    assert ("target_gap", "standard", "schema") in kinds and ("target_gap", "standard", "tables") in kinds


def test_assess_blocks_without_dictionary(tmp_path):
    a = c.assess(spec_for(), _content(tmp_path), None, None)
    assert a["status"] == c.STATUS_CANNOT and a["blockers"][0]["kind"] == "no_dictionary"


def test_assess_ready_when_nothing_missing(tmp_path):
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "v.xlsx"))
    a = c.assess(spec_for(), _content(tmp_path), vdd, "v.xlsx")
    assert a["status"] == c.STATUS_READY, a["questions"]
    assert a["sources"][0]["file"] == "claims_YYYYMMDD.csv" and a["sources"][0]["n_columns"] == 3


def test_answers_change_status_and_apply(tmp_path):
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "v.xlsx"))
    spec = spec_for(stage_table="made_up_table")
    spec["feeds"][0]["domain"] = "sdoh"
    a = c.assess(spec, _content(tmp_path), vdd, "v.xlsx")
    assert a["status"] == c.STATUS_NEEDS_INPUT
    for q in a["questions"]:
        if q["kind"] == "unverified":
            c.record_answer(a, q["id"], "clm_claims_stg")
        elif q["kind"] == "target_gap" and q["context"]["attribute"] == "schema":
            c.record_answer(a, q["id"], "CLAIMS")
        else:
            c.record_answer(a, q["id"], c.KEEP)
    assert a["status"] == c.STATUS_READY
    spec2 = json.loads(json.dumps(spec))
    c.apply_answers(spec2, a)
    assert spec2["feeds"][0]["stage_target"]["tables"] == ["clm_claims_stg"]
    assert a["sources"][0]["layers"]["standard"]["schema"] == "CLM"


def test_apply_attribution_keeps_rule_on_chosen_source_only():
    spec = _two_sources("If CLAIM_ID is NULL, reject the record.")
    qs, _ = c.attribution_questions(spec, {0: ["CLAIM_ID"], 1: ["CLAIM_ID"]})
    a = {"blockers": [], "questions": qs, "sources": []}
    c.record_answer(a, a["questions"][0]["id"], "Second")
    c.apply_answers(spec, a)
    assert spec["feeds"][0]["validation_rules"] == [] and len(spec["feeds"][1]["validation_rules"]) == 1


def test_apply_remove_drops_the_value(tmp_path):
    spec = spec_for(stage_table="made_up_table")
    a = c.assess(spec, _content(tmp_path), None, None)
    q = next(q for q in a["questions"] if q["kind"] == "unverified")
    c.record_answer(a, q["id"], c.REMOVE)
    c.apply_answers(spec, a)
    assert spec["feeds"][0]["stage_target"]["tables"] == []


# --------------------------------------------------------------------------- #
# a dictionary that is not perfect
# --------------------------------------------------------------------------- #
def _blank_types_vdd(tmp_path, name="vblank.xlsx"):
    """The vendor filled in the columns but left two data types out."""
    return parse_dictionary_workbook(make_vdd(tmp_path / name, files={
        "claims_YYYYMMDD.csv": [
            ("CLAIM_ID", "int", "Y", "N", "Claim identifier", "", "1001", ""),
            ("MEMBER_ID", "", "Y", "Y", "Member identifier", "", "", ""),
            ("AMOUNT", "", "N", "N", "Billed amount", "", "", ""),
        ]}))


def test_missing_datatypes_is_a_question_not_a_silent_default(tmp_path):
    vdd = _blank_types_vdd(tmp_path)
    assert any(p["kind"] == "missing_datatypes" for p in vdd["problems"])
    a = c.assess(spec_for(), _content(tmp_path), vdd, "vblank.xlsx")
    q = [x for x in a["questions"] if x["kind"] == "dictionary_types"]
    assert len(q) == 1 and a["status"] == c.STATUS_NEEDS_INPUT
    assert sorted(q[0]["context"]["columns"]) == ["AMOUNT", "MEMBER_ID"]
    assert q[0]["options"] == [c.USE_DEFAULT_TYPE, c.LEAVE_TYPE_BLANK]
    # and it is no longer buried in the notes
    assert not any("no datatype" in n for n in a["notes"])


def test_blank_type_answer_changes_the_workbook(tmp_path):
    from frdsttm.render import preview_rows
    vdd = _blank_types_vdd(tmp_path)
    content = _content(tmp_path)

    def rows_for(answer):
        a = c.assess(spec_for(), content, vdd, "vblank.xlsx")
        q = next(x for x in a["questions"] if x["kind"] == "dictionary_types")
        c.record_answer(a, q["id"], answer)
        spec = json.loads(json.dumps(spec_for()))
        applied = c.apply_answers(spec, a)
        prev = preview_rows(spec, a["sources"], vdd, applied["pairing_override"],
                            blank_type_sheets=applied["blank_type_sheets"])
        return {r["source_column"]: r["standard"]["datatype"]
                for r in prev[0]["rows"] if not r["audit"]}

    kept = rows_for(c.USE_DEFAULT_TYPE)
    blanked = rows_for(c.LEAVE_TYPE_BLANK)
    assert kept["MEMBER_ID"] == kept["AMOUNT"] != ""          # today's silent default
    assert blanked["MEMBER_ID"] == blanked["AMOUNT"] == ""    # the gap is visible instead
    assert kept["CLAIM_ID"] == blanked["CLAIM_ID"] != ""      # a real vendor type is untouched


def test_empty_field_sheet_on_a_paired_source_blocks(tmp_path):
    """A source paired to a sheet with no columns would render only audit rows."""
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "vempty.xlsx", files={
        "claims_YYYYMMDD.csv": []}))
    a = c.assess(spec_for(), _content(tmp_path), vdd, "vempty.xlsx")
    assert a["status"] == c.STATUS_CANNOT
    assert {b["kind"] for b in a["blockers"]} & {"field_sheet_empty", "empty_dictionary"}


def test_a_problem_on_an_unused_sheet_stays_a_note(tmp_path):
    """The same gap is only worth saying when no source is paired to it. Two
    dictionary files, only one named by the FRD — a single file would be paired
    automatically, so the second file is what makes the sheet genuinely unused."""
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "vunused.xlsx", files={
        "claims_YYYYMMDD.csv": [
            ("CLAIM_ID", "int", "Y", "N", "Claim identifier", "", "1001", ""),
        ],
        "roster_YYYYMMDD.csv": [                       # nothing maps this one
            ("ROSTER_ID", "", "Y", "N", "Roster identifier", "", "", ""),
        ]}))
    a = c.assess(spec_for(), _content(tmp_path), vdd, "vunused.xlsx")
    assert [s["file"] for s in a["sources"]] == ["claims_YYYYMMDD.csv"]
    assert not [x for x in a["questions"] if x["kind"] == "dictionary_types"]
    assert any("datatype" in n for n in a["notes"])


def test_descriptions_and_flags_stay_notes(tmp_path):
    """Blank passes through as blank — no answer would write a different workbook."""
    vdd = parse_dictionary_workbook(make_vdd(tmp_path / "vdesc.xlsx", files={
        "claims_YYYYMMDD.csv": [
            ("CLAIM_ID", "int", "", "", "", "", "1001", ""),
            ("MEMBER_ID", "varchar", "", "", "", "", "M1", ""),
        ]}))
    a = c.assess(spec_for(), _content(tmp_path), vdd, "vdesc.xlsx")
    assert not [x for x in a["questions"] if x["kind"] == "dictionary_types"]
    assert a["status"] == c.STATUS_READY, a["questions"]
    assert len([n for n in a["notes"] if n.startswith("dictionary:")]) >= 3
