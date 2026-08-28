"""An off-template VDD: the code parser cannot read it, Claude normalises it,
and every column name the model returns is verified against the workbook."""

import json
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from conftest import FakeClient, make_frd, spec_for
from frdsttm import dictionary_repair as dr
from frdsttm import pipeline
from frdsttm.dictionary import DictionaryError, parse_dictionary_workbook


def make_off_template_vdd(path):
    """One sheet, no FILES sheet, headers the parser does not know."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Claims layout"
    ws.append(["Vendor claims extract — claims_YYYYMMDD.csv"])
    ws.append(["Col", "Kind", "Mandatory?", "Sensitive", "Meaning"])
    ws.append(["CLAIM_ID", "int", "yes", "no", "Claim identifier"])
    ws.append(["MEMBER_ID", "varchar", "yes", "yes", "Member identifier"])
    ws.append(["AMOUNT", "decimal", "no", "no", "Billed amount"])
    wb.save(str(path))
    return path


REPAIRED = {
    "files": [{"file_name_pattern": "claims_YYYYMMDD.csv", "file_title": "Vendor claims extract", "format": "csv",
               "delimiter": None, "header_row": None, "field_sheet": "Claims layout", "multi_record": None}],
    "fields": {"Claims layout": [
        {"position": 1, "name": "CLAIM_ID", "datatype": "int", "length": None, "required": True, "description": "Claim identifier",
         "allowed_values": None, "example": None, "phi": False, "segment": None},
        {"position": 2, "name": "MEMBER_ID", "datatype": "varchar", "length": None, "required": True, "description": "Member identifier",
         "allowed_values": None, "example": None, "phi": True, "segment": None},
        {"position": 3, "name": "AMOUNT", "datatype": "decimal", "length": None, "required": False, "description": "Billed amount",
         "allowed_values": None, "example": None, "phi": False, "segment": None},
        {"position": 4, "name": "INVENTED_COLUMN", "datatype": "int", "length": None, "required": None, "description": None,
         "allowed_values": None, "example": None, "phi": None, "segment": None},
    ]},
}


def test_code_parser_refuses_off_template(tmp_path):
    with pytest.raises(DictionaryError):
        parse_dictionary_workbook(make_off_template_vdd(tmp_path / "VDD_x.xlsx"))


def test_repair_normalises_and_drops_invented_columns(tmp_path):
    path = make_off_template_vdd(tmp_path / "VDD_x.xlsx")
    parsed, meta = dr.repair_dictionary(FakeClient(REPAIRED), path, model="m")
    assert parsed["n_files"] == 1 and parsed["n_fields"] == 3 and parsed["normalised_by_model"]
    names = [f["name"] for f in parsed["fields"]["Claims layout"]]
    assert names == ["CLAIM_ID", "MEMBER_ID", "AMOUNT"]
    assert meta["dropped"] == ["INVENTED_COLUMN"]
    kinds = {p["kind"] for p in parsed["problems"]}
    assert kinds == {"normalised_by_model", "model_columns_not_in_workbook"}
    assert parsed["fields"]["Claims layout"][1]["phi"] is True


def test_workbook_as_text_contains_every_cell(tmp_path):
    text = dr.workbook_as_text(make_off_template_vdd(tmp_path / "v.xlsx"))
    assert "=== SHEET: Claims layout ===" in text and "CLAIM_ID | int | yes | no | Claim identifier" in text


class TwoAnswerClient(FakeClient):
    """First call: the dictionary normalisation. Second call: the FRD extraction."""

    def __init__(self, repaired, spec):
        super().__init__(spec)
        self.answers = [repaired, spec]

    def stream(self, **kwargs):
        self.spec = self.answers[min(self.calls, len(self.answers) - 1)]
        return super().stream(**kwargs)


def test_pipeline_uses_the_normalised_dictionary_end_to_end(data_root):
    (data_root / "vdds" / "VDD_Claims_Intake.xlsx").unlink()
    make_off_template_vdd(data_root / "vdds" / "VDD_Claims_Intake.xlsx")
    client = TwoAnswerClient(REPAIRED, spec_for())
    run = pipeline.run_extract(pipeline.Paths.under(data_root), "run_r", "FRD_Claims_Intake", client=client,
                               provider="anthropic", model="m")
    assert client.calls == 2
    assert run["vdd"]["normalised_by_model"] is True and run["vdd"]["n_fields"] == 3
    assert run["status"] == pipeline.STATUS_RENDERED and run["render"]["n_rows"] == 7
    assert "normalised by Claude" in pipeline.report_md(run)


def test_pipeline_does_not_call_the_model_for_a_template_dictionary(data_root):
    client = FakeClient(spec_for())
    run = pipeline.run_extract(pipeline.Paths.under(data_root), "run_t", "FRD_Claims_Intake", client=client,
                               provider="anthropic", model="m")
    assert client.calls == 1 and run["vdd"]["normalised_by_model"] is False
