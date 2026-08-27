"""Contract-build gating tests (frdsttm.contract_build.build_contract) —
the FAIL / PASS_WITH_FLAGS / PASS decision, pure functions only."""

from frdsttm.contract_build import GENERATOR, build_contract
from frdsttm.models import GatedAmbiguity


def _build(raw_extraction: dict, content: str) -> dict:
    return build_contract("doc1", "doc1.docx", raw_extraction, content)


CLEAN_CONTENT = "Project ID: 1005034. Feed cv_risk delivers cv_risk_file.csv."
CLEAN_EXTRACTION = {
    "project": {"project_id": "1005034"},
    "feeds": [{"feed_name": "cv_risk", "file_name_patterns": ["cv_risk_file.csv"]}],
}


def test_pass_when_clean():
    res = _build(CLEAN_EXTRACTION, CLEAN_CONTENT)
    assert res["status"] == "PASS"
    assert res["errors"] == []
    assert res["contract"]["_provenance"]["ambiguities"] == []
    assert res["contract"]["_provenance"]["grounding"]["strict_failed"] == []


def test_fail_on_schema_violation_returns_no_contract():
    res = _build({"not_a_real_field": 1}, CLEAN_CONTENT)
    assert res["status"] == "FAIL"
    assert res["contract"] is None
    assert any("schema validation" in e for e in res["errors"])
    assert "FAIL" in res["report_md"]


def test_fail_on_ungrounded_strict_field():
    raw = {"feeds": [{"feed_name": "f", "file_name_patterns": ["invented_file_xyz.csv"]}]}
    res = _build(raw, "content that never mentions that pattern")
    assert res["status"] == "FAIL"
    assert any("ungrounded strict field" in e for e in res["errors"])
    # The contract is still produced (for inspection), carrying the audit.
    assert res["contract"]["_provenance"]["grounding"]["strict_failed"]


def test_pass_with_flags_on_attribution_ambiguity():
    content = "Project ID: 1005034. Files a_file.csv and b_file.csv. ZIP CODE null check required."
    raw = {
        "feeds": [
            {"feed_name": "feed_a", "file_name_patterns": ["a_file.csv"],
             "validation_rules": ["ZIP CODE null check required"]},
            {"feed_name": "feed_b", "file_name_patterns": ["b_file.csv"],
             "validation_rules": ["ZIP CODE null check required"]},
        ]
    }
    res = _build(raw, content)
    assert res["status"] == "PASS_WITH_FLAGS"
    ambiguities = res["contract"]["_provenance"]["ambiguities"]
    assert len(ambiguities) == 1
    assert ambiguities[0]["kind"] == "attribution"


def test_pass_with_flags_on_advisory_grounding_flag():
    raw = {
        "feeds": [{"feed_name": "cv_risk", "file_name_patterns": ["cv_risk_file.csv"],
                   "validation_rules": ["entirely invented prose overlapping nothing"]}]
    }
    res = _build(raw, CLEAN_CONTENT)
    assert res["status"] == "PASS_WITH_FLAGS"
    flagged = res["contract"]["_provenance"]["grounding"]["advisory_flagged"]
    assert len(flagged) == 1
    assert flagged[0]["kind"] == "advisory_grounding"


def test_every_emitted_ambiguity_validates_against_gated_ambiguity():
    content = "Project ID: 9999999. Files a_file.csv and b_file.csv. shared rule tokens here."
    raw = {
        "project": {"project_id": "1234567"},  # disagreement with content
        "feeds": [
            {"feed_name": "feed_a", "file_name_patterns": ["a_file.csv"],
             "validation_rules": ["shared rule tokens here"]},
            {"feed_name": "feed_b", "file_name_patterns": ["b_file.csv"],
             "validation_rules": ["shared rule tokens here"]},
        ],
    }
    res = _build(raw, content)
    prov = res["contract"]["_provenance"]
    kinds = {a["kind"] for a in prov["ambiguities"]}
    assert kinds == {"attribution", "disagreement"}
    for item in prov["ambiguities"] + prov["grounding"]["advisory_flagged"]:
        GatedAmbiguity.model_validate(item)  # must not raise


def test_contract_carries_provenance_banner_fields():
    res = _build(CLEAN_EXTRACTION, CLEAN_CONTENT)
    contract = res["contract"]
    assert contract["generator"] == GENERATOR
    assert contract["generated_from_frd"] == "doc1.docx"
    assert contract["contract_name"] == "doc1 source-level mapping contract"
    assert contract["status"] == "PASS"


def test_report_lists_ambiguities_for_review():
    content = "Files a_file.csv and b_file.csv. dup rule words."
    raw = {
        "feeds": [
            {"feed_name": "feed_a", "file_name_patterns": ["a_file.csv"], "validation_rules": ["dup rule words"]},
            {"feed_name": "feed_b", "file_name_patterns": ["b_file.csv"], "validation_rules": ["dup rule words"]},
        ]
    }
    res = _build(raw, content)
    assert "Ambiguities for human review" in res["report_md"]
