"""similarity / corpus / exemplars — the template architecture's deterministic core.

All fixtures are SYNTHETIC, built in tmp_path with openpyxl (per the
no-client-documents rule, 2026-08-22). The workbook builder emits the same
sheet_per_table dialect 04_sttm_render's renderer produces, so these tests
also pin the factored parser (frdsttm.reference_workbooks) to that layout.
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from frdsttm.corpus import (
    build_corpus_index,
    load_corpus_index,
    own_reference_for,
    save_corpus_index,
)
from frdsttm.exemplars import build_exemplar_block
from frdsttm.reference_workbooks import parse_reference_workbook
from frdsttm.similarity import (
    THRESHOLD_DEFAULTS,
    decide_templates,
    frd_features,
    merge_dictionaries,
    score_match,
    thresholds_from,
    workbook_features,
)

# --------------------------------------------------------------------------- #
# synthetic builders
# --------------------------------------------------------------------------- #
SRC_HDRS = ["Database Column Name", "Description", "Datatype", "Null Check", "Comment"]
TGT_HDRS = ["Catalog", "Schema", "TableName", "ColumnName", "Datatype"]


def make_workbook(path, table: str, columns: list[str], descriptions: list[str]):
    wb = Workbook()
    ws = wb.active
    ws.title = f"MAPPING-{table.upper()}"
    labels = ["Source File Layout"] + [""] * (len(SRC_HDRS) - 1) \
        + ["Stage Layer"] + [""] * (len(TGT_HDRS) - 1) \
        + ["Standard Layer"] + [""] * (len(TGT_HDRS) - 1)
    ws.append(labels)
    ws.append(SRC_HDRS + TGT_HDRS + TGT_HDRS)
    for col, desc in zip(columns, descriptions):
        ws.append([col, desc, "String", "Not Null", ""]
                  + ["cat", "stg", table, col, "String"]
                  + ["cat", "std", table, col, "String"])
    wb.save(path)
    return path


MEMBER_COLS = ["MEMBER_ID", "ZIP_CODE", "RISK_SCORE", "EFFECTIVE_DATE", "LOB_CODE"]
CLAIM_COLS = ["CLAIM_NUMBER", "PROVIDER_NPI", "PAID_AMOUNT", "SERVICE_CODE", "ADJUDICATION_FLAG"]


def member_frd_markdown(doc_id="member_risk_frd"):
    return "\n".join([
        f"# FRD {doc_id}",
        "## Data Ingestion Requirements",
        "The member risk feed loads MEMBER_ID, ZIP_CODE, RISK_SCORE,",
        "EFFECTIVE_DATE and LOB_CODE into cat.stg.member_risk before",
        "promotion to the standard layer table cat.std.member_risk.",
        "| Column | Rule |",
        "| MEMBER_ID | reject when null |",
        "| ZIP_CODE | reject when null |",
        "Community demographic risk scoring arrives weekly from the vendor.",
    ])


def claim_frd_markdown(doc_id="claim_intake_frd"):
    return "\n".join([
        f"# FRD {doc_id}",
        "## Data Ingestion Requirements",
        "The claims intake feed loads CLAIM_NUMBER, PROVIDER_NPI,",
        "PAID_AMOUNT, SERVICE_CODE and ADJUDICATION_FLAG into",
        "cat.stg.claim_intake, then cat.std.claim_intake.",
        "Adjudication happens downstream of intake, never in this feed.",
    ])


@pytest.fixture()
def reference_dir(tmp_path):
    d = tmp_path / "sttm_reference"
    d.mkdir()
    make_workbook(d / "member_risk_sttm.xlsx", "member_risk", MEMBER_COLS,
                  ["member identifier", "member postal code", "risk score",
                   "effective date", "line of business"])
    make_workbook(d / "claim_intake_sttm.xlsx", "claim_intake", CLAIM_COLS,
                  ["claim number", "provider npi", "paid amount",
                   "service code", "adjudication flag"])
    return d


@pytest.fixture()
def frd_entries():
    return [
        {"doc_id": "member_risk_frd", "source_file": "member_risk_frd.docx",
         "content": member_frd_markdown()},
        {"doc_id": "claim_intake_frd", "source_file": "claim_intake_frd.docx",
         "content": claim_frd_markdown()},
        {"doc_id": "totally_novel_frd", "source_file": "totally_novel_frd.docx",
         "content": "# FRD\nAn eligibility roster synchronization document with "
                    "no overlap whatsoever in vocabulary or identifiers."},
    ]


def _thresholds():
    return thresholds_from(lambda name, default: default)


# --------------------------------------------------------------------------- #
# parser + features + scoring
# --------------------------------------------------------------------------- #
def test_synthetic_workbook_parses_as_sheet_per_table(reference_dir):
    d = parse_reference_workbook(str(reference_dir / "member_risk_sttm.xlsx"))
    assert d["dialect"] == "sheet_per_table"
    (key, feed), = d["feeds"].items()
    assert key == "member_risk"
    assert [f["source_column"] for f in feed["fields"]] == MEMBER_COLS
    assert feed["ref_targets"][0]["stage"]["table"] == "member_risk"


def test_matching_pair_outscores_cross_pair(reference_dir):
    member = frd_features("member_risk_frd", member_frd_markdown())
    wb_m = workbook_features("member_risk_sttm.xlsx", parse_reference_workbook(
        str(reference_dir / "member_risk_sttm.xlsx")))
    wb_c = workbook_features("claim_intake_sttm.xlsx", parse_reference_workbook(
        str(reference_dir / "claim_intake_sttm.xlsx")))
    same = score_match(member, wb_m)
    cross = score_match(member, wb_c)
    assert same["score"] > cross["score"]
    assert same["components"]["columns"] > 0.5  # every workbook column named in the FRD


def test_thresholds_from_rejects_garbage():
    with pytest.raises(ValueError, match="template_single_min"):
        thresholds_from(lambda name, default: "not-a-number"
                        if name == "template_single_min" else default)


def test_threshold_defaults_are_complete():
    t = _thresholds()
    assert set(t) == set(THRESHOLD_DEFAULTS)


# --------------------------------------------------------------------------- #
# corpus index
# --------------------------------------------------------------------------- #
def test_corpus_pairs_and_unmapped(reference_dir, frd_entries):
    index = build_corpus_index(frd_entries, reference_dir, _thresholds(),
                               generated_at="2026-08-22T00:00:00Z")
    assert own_reference_for(index, "member_risk_frd") == "member_risk_sttm.xlsx"
    assert own_reference_for(index, "claim_intake_frd") == "claim_intake_sttm.xlsx"
    assert index["unmapped"] == ["totally_novel_frd"]
    assert index["unpaired_references"] == []
    # every pair carries the display-facing evidence + how it was decided
    for pair in index["pairs"].values():
        assert {"reference", "score", "confidence", "components", "matched_by"} <= set(pair)
    # member_risk_frd / member_risk_sttm.xlsx key to the same name -> definitive
    assert index["pairs"]["member_risk_frd"]["matched_by"] == "name"
    assert index["pairs"]["member_risk_frd"]["confidence"] == "high"
    # every document carries a content fingerprint (2026-08-22, index v2)
    for entry in list(index["frds"].values()) + list(index["references"].values()):
        assert len(entry["content_sha256"]) == 64


def test_similarity_pairs_when_names_do_not_match(tmp_path, frd_entries):
    """Reference workbooks named nothing like their FRDs still pair — by
    content — and say so (`matched_by: similarity`); the novel FRD stays
    unmapped below pair_min."""
    d = tmp_path / "refs"
    d.mkdir()
    make_workbook(d / "workbook_A.xlsx", "member_risk", MEMBER_COLS,
                  ["member identifier", "member postal code", "risk score",
                   "effective date", "line of business"])
    make_workbook(d / "workbook_B.xlsx", "claim_intake", CLAIM_COLS,
                  ["claim number", "provider npi", "paid amount",
                   "service code", "adjudication flag"])
    index = build_corpus_index(frd_entries, d, _thresholds(), generated_at="t")
    assert index["pairs"]["member_risk_frd"]["reference"] == "workbook_A.xlsx"
    assert index["pairs"]["member_risk_frd"]["matched_by"] == "similarity"
    assert index["pairs"]["claim_intake_frd"]["reference"] == "workbook_B.xlsx"
    assert index["unmapped"] == ["totally_novel_frd"]


def test_ambiguous_name_keys_fall_through_to_similarity(tmp_path, frd_entries):
    """Two workbooks keying to the same name are never name-paired (no
    guessing); similarity decides, and the loser stays unpaired."""
    d = tmp_path / "refs"
    d.mkdir()
    make_workbook(d / "member_risk_sttm.xlsx", "member_risk", MEMBER_COLS,
                  ["member identifier", "member postal code", "risk score",
                   "effective date", "line of business"])
    make_workbook(d / "member-risk STTM.xlsx", "member_risk", MEMBER_COLS,
                  ["member identifier", "member postal code", "risk score",
                   "effective date", "line of business"])
    index = build_corpus_index(frd_entries[:1], d, _thresholds(), generated_at="t")
    pair = index["pairs"]["member_risk_frd"]
    assert pair["matched_by"] == "similarity"
    assert len(index["unpaired_references"]) == 1


def test_corpus_round_trip(tmp_path, reference_dir, frd_entries):
    index = build_corpus_index(frd_entries, reference_dir, _thresholds(),
                               generated_at="2026-08-22T00:00:00Z")
    save_corpus_index(index, reference_dir)
    loaded = load_corpus_index(reference_dir)
    assert loaded == index
    assert load_corpus_index(tmp_path) is None  # absent is None, not an error


# --------------------------------------------------------------------------- #
# template decision
# --------------------------------------------------------------------------- #
def _wb_feats(reference_dir):
    return {p.name: workbook_features(p.name, parse_reference_workbook(str(p)))
            for p in sorted(reference_dir.glob("*.xlsx"))}


def test_single_mode_for_close_match(reference_dir):
    target = frd_features("member_risk_frd", member_frd_markdown())
    decision = decide_templates(target, _wb_feats(reference_dir), _thresholds())
    assert decision["mode"] == "single"
    assert decision["selections"][0]["reference"] == "member_risk_sttm.xlsx"


def test_freeform_mode_when_nothing_matches(reference_dir):
    target = frd_features("novel", "eligibility roster synchronization only")
    decision = decide_templates(target, _wb_feats(reference_dir), _thresholds())
    assert decision["mode"] == "freeform"
    assert decision["selections"] == []
    assert decision["ranked"]  # the evidence is still shown


def test_exclude_own_reference_changes_the_pick(reference_dir):
    target = frd_features("member_risk_frd", member_frd_markdown())
    decision = decide_templates(target, _wb_feats(reference_dir), _thresholds(),
                                exclude={"member_risk_sttm.xlsx"})
    chosen = {s["reference"] for s in decision["selections"]}
    assert "member_risk_sttm.xlsx" not in chosen
    excluded_row = next(r for r in decision["ranked"]
                        if r["reference"] == "member_risk_sttm.xlsx")
    assert excluded_row["excluded"] is True


def test_amalgam_merge_is_first_wins(reference_dir):
    dicts = {p.name: parse_reference_workbook(str(p))
             for p in sorted(reference_dir.glob("*.xlsx"))}
    merged = merge_dictionaries(dicts, ["member_risk_sttm.xlsx",
                                        "claim_intake_sttm.xlsx"])
    assert set(merged["feeds"]) == {"member_risk", "claim_intake"}
    assert merged["feed_sources"]["member_risk"] == "member_risk_sttm.xlsx"
    assert merged["dialect"] == "sheet_per_table"


# --------------------------------------------------------------------------- #
# exemplars
# --------------------------------------------------------------------------- #
def test_exemplar_block_excludes_self_and_names_conventions(reference_dir, frd_entries):
    index = build_corpus_index(frd_entries, reference_dir, _thresholds(),
                               generated_at="2026-08-22T00:00:00Z")
    block = build_exemplar_block("member_risk_frd", member_frd_markdown(),
                                 index, reference_dir, k=2)
    assert block is not None
    assert all(e["doc_id"] != "member_risk_frd" for e in block["exemplars"])
    assert "NEVER copy" in block["text"]
    assert "claim_intake" in block["text"]  # the other pair's digest is present


def test_exemplar_block_none_without_other_pairs(reference_dir):
    index = build_corpus_index(
        [{"doc_id": "member_risk_frd", "source_file": "x.docx",
          "content": member_frd_markdown()}],
        reference_dir, _thresholds(), generated_at="2026-08-22T00:00:00Z")
    assert build_exemplar_block("member_risk_frd", member_frd_markdown(),
                                index, reference_dir) is None
    assert build_exemplar_block("member_risk_frd", member_frd_markdown(),
                                None, reference_dir) is None
