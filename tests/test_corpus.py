import json

import pytest

from conftest import make_frd, make_reference_sheet_per_table, make_vdd
from frdsttm import corpus


def test_name_key():
    assert corpus.name_key("FRD_Medicare Expansion-MIDS.docx") == corpus.name_key("VDD_Medicare_Expansion-MIDS.xlsx")
    assert corpus.name_key("STTM_X.sttm.xlsx") == corpus.name_key("FRD_X.docx") == "x"
    assert corpus.name_key("DICT_X.xlsx") == "x"


def test_index_pairs_by_name_and_decides_eligibility(data_root):
    make_frd(data_root / "frds" / "FRD_No_Dictionary.docx")
    make_frd(data_root / "frds" / "FRD_Other_Feed.docx")
    make_vdd(data_root / "vdds" / "VDD_Other_Feed.xlsx")
    make_vdd(data_root / "vdds" / "VDD_Orphan.xlsx")
    idx = corpus.build_index(data_root / "frds", data_root / "vdds", data_root / "reference_sttms")
    d = idx["documents"]
    assert d["FRD_Claims_Intake"]["status"] == "ready" and d["FRD_Claims_Intake"]["generatable"]
    assert d["FRD_No_Dictionary"]["status"] == "no_dictionary" and not d["FRD_No_Dictionary"]["generatable"]
    assert d["FRD_Other_Feed"]["status"] == "mapped" and d["FRD_Other_Feed"]["generatable"]
    assert d["FRD_Other_Feed"]["sttm"] == "STTM_Other_Feed.xlsx"
    assert idx["unpaired_vdds"] == ["VDD_Orphan.xlsx"] and idx["unpaired_references"] == []
    assert idx["references"]["STTM_Other_Feed.xlsx"]["dialect"] == "sheet_per_table"


def test_ambiguous_names_are_not_paired(data_root):
    make_vdd(data_root / "vdds" / "DICT_Claims_Intake.xlsx")
    idx = corpus.build_index(data_root / "frds", data_root / "vdds", data_root / "reference_sttms")
    e = idx["documents"]["FRD_Claims_Intake"]
    assert e["status"] == "ambiguous" and e["vdd"] is None and not e["generatable"]


def test_pairing_rows_and_index_round_trip(data_root):
    idx = corpus.build_index(data_root / "frds", data_root / "vdds", data_root / "reference_sttms")
    rows = corpus.pairing_rows(idx)
    assert rows[0]["doc_id"] == "FRD_Claims_Intake" and rows[0]["vdd_columns"] == 3
    corpus.save_index(idx, data_root / "reference_sttms")
    assert corpus.load_index(data_root / "reference_sttms")["documents"] == idx["documents"]


def test_wrong_version_raises(tmp_path):
    (tmp_path / corpus.INDEX_NAME).write_text(json.dumps({"version": 1}))
    with pytest.raises(corpus.CorpusIndexError):
        corpus.load_index(tmp_path)
    assert corpus.load_index(tmp_path / "nowhere") is None
