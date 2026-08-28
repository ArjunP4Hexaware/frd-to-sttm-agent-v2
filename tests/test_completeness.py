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


def test_shared_rule_is_an_attribution_question():
    spec = spec_for()
    spec["feeds"].append({**spec["feeds"][0], "feed_name": "Second", "file_name_patterns": ["b.csv"]})
    qs = c.attribution_questions(spec)
    assert len(qs) == 1 and qs[0]["options"] == ["Claims Intake", "Second", c.ALL_SOURCES]


def test_enrich_fills_project_id_from_frd(tmp_path):
    spec = spec_for(project_id=None)
    notes, qs = c.enrich(spec, _content(tmp_path))
    assert spec["project"]["project_id"] == "1234567" and notes and not qs


def test_enrich_disagreement_is_a_question(tmp_path):
    spec = spec_for(project_id="9999999")
    _, qs = c.enrich(spec, _content(tmp_path))
    assert qs[0]["kind"] == "project_id" and qs[0]["options"] == ["9999999", "1234567"]


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
    spec = spec_for()
    spec["feeds"].append({**spec["feeds"][0], "feed_name": "Second"})
    a = {"blockers": [], "questions": c.attribution_questions(spec), "sources": []}
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
