from pathlib import Path

import pytest

from conftest import make_frd
from frdsttm.frd_parsing import FrdParseError, docx_to_markdown, infer_project_id, parse_frd


def test_docx_to_markdown_keeps_structure(tmp_path):
    md = docx_to_markdown(str(make_frd(tmp_path / "FRD_x.docx")))
    assert "# Claims Intake FRD" in md
    assert "## In Scope" in md
    assert "| Target Table Name | clm_claims_stg |" in md
    assert "**SRQ226433 — The process shall load the claims file daily.**" in md
    assert "- Ingest the vendor claims file" in md


def test_parse_frd_returns_provenance(tmp_path):
    frd = parse_frd(make_frd(tmp_path / "FRD_Claims_1234567.docx"))
    assert frd["doc_id"] == "FRD_Claims_1234567"
    assert frd["project_id"] == "1234567"
    assert len(frd["content_sha256"]) == 64
    assert frd["heading_count"] >= 3 and frd["table_count"] == 1


def test_parse_frd_rejects_tiny_document(tmp_path):
    p = tmp_path / "FRD_tiny.md"
    p.write_text("# tiny\n")
    with pytest.raises(FrdParseError):
        parse_frd(p)


def test_unsupported_type(tmp_path):
    p = tmp_path / "FRD_x.xlsx"
    p.write_bytes(b"x")
    with pytest.raises(FrdParseError):
        parse_frd(p)


def test_infer_project_id():
    assert infer_project_id("FRD_X_1005034.docx") == "1005034"
    assert infer_project_id("FRD_X.docx") is None
