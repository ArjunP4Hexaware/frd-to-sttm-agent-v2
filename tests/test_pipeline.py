import pytest

from conftest import FakeClient, make_frd, spec_for
from frdsttm import completeness as c
from frdsttm import pipeline


def _paths(root):
    return pipeline.Paths.under(root)


def test_extract_ready_renders_immediately(data_root):
    run = pipeline.run_extract(_paths(data_root), "run_1", "FRD_Claims_Intake", client=FakeClient(spec_for()),
                               provider="anthropic", model="m")
    assert run["status"] == pipeline.STATUS_RENDERED
    assert (data_root / "output_sttms" / "run_1" / "FRD_Claims_Intake.xlsx").is_file()
    assert (data_root / "output_sttms" / "run_1" / "report.md").is_file()
    assert run["render"]["layout_from"] is None          # the other feed's workbook is sheet-per-table; one source → built-in
    assert run["standards_sha256"] and run["extraction_meta"]["input_tokens"] == 10


def test_extract_needs_input_then_answer_then_render(data_root):
    spec = spec_for(stage_table="invented_table")
    paths = _paths(data_root)
    run = pipeline.run_extract(paths, "run_2", "FRD_Claims_Intake", client=FakeClient(spec), provider="anthropic", model="m")
    assert run["status"] == c.STATUS_NEEDS_INPUT and "render" not in run
    q = next(q for q in run["assessment"]["questions"] if q["kind"] == "unverified")
    run = pipeline.answer(paths, "run_2", q["id"], "clm_claims_stg", by="tester")
    assert run["status"] == c.STATUS_READY
    run = pipeline.run_render(paths, "run_2")
    assert run["status"] == pipeline.STATUS_RENDERED
    assert run["applied"]["extraction"]["feeds"][0]["stage_target"]["tables"] == ["clm_claims_stg"]
    assert run["render"]["unanswered"] == []


def test_extract_without_dictionary_cannot_generate(data_root):
    (data_root / "vdds" / "VDD_Claims_Intake.xlsx").unlink()
    run = pipeline.run_extract(_paths(data_root), "run_3", "FRD_Claims_Intake", client=FakeClient(spec_for()),
                               provider="anthropic", model="m")
    assert run["status"] == c.STATUS_CANNOT and run["vdd"] is None
    with pytest.raises(ValueError):
        pipeline.run_render(_paths(data_root), "run_3")


def test_model_failure_is_recorded(data_root):
    with pytest.raises(RuntimeError):
        pipeline.run_extract(_paths(data_root), "run_4", "FRD_Claims_Intake",
                             client=FakeClient(spec_for(), stop_reason="max_tokens"), provider="anthropic", model="m")
    run = pipeline.load_run(_paths(data_root), "run_4")
    assert run["status"] == pipeline.STATUS_FAILED and "max_tokens" in run["error"]


def test_reindex_and_list_runs(data_root):
    paths = _paths(data_root)
    idx = pipeline.reindex(paths)
    assert (data_root / "reference_sttms" / "corpus_index.json").is_file()
    assert idx["documents"]["FRD_Claims_Intake"]["generatable"]
    pipeline.run_extract(paths, "run_5", "FRD_Claims_Intake", client=FakeClient(spec_for()), provider="anthropic", model="m")
    runs = pipeline.list_runs(paths)
    assert runs[0]["run_id"] == "run_5" and runs[0]["workbook"] == "FRD_Claims_Intake.xlsx"


def test_choose_layout_prefers_matching_dialect(data_root):
    from conftest import make_reference_single_sheet
    make_reference_single_sheet(data_root / "reference_sttms" / "STTM_Single.xlsx")
    lay = pipeline.choose_layout(_paths(data_root), "FRD_Claims_Intake", 1)
    assert lay["dialect"] == "single_sheet" and lay["path"].endswith("STTM_Single.xlsx")
    lay = pipeline.choose_layout(_paths(data_root), "FRD_Claims_Intake", 3)
    assert lay["dialect"] == "sheet_per_table"


def test_new_run_id_uniquifies():
    a = pipeline.new_run_id(set())
    assert pipeline.new_run_id({a}) == f"{a}-2"


def test_trailing_comma_is_tolerated_and_malformed_json_retried_once():
    from frdsttm import extract as ex
    from conftest import spec_for
    import json as _json

    assert ex.clean_json_text('```json\n{"a": [1, 2,],}\n```') == '{"a": [1, 2]}'

    class Flaky(FakeClient):
        def stream(self, **kw):
            ctx = super().stream(**kw)
            client = self
            calls = self.calls

            class C:
                def __enter__(s_):
                    return s_

                def __exit__(s_, *a):
                    return False

                def get_final_message(s_):
                    m = ctx.get_final_message()
                    if calls == 1:                      # first answer: not JSON at all
                        m.content[0].text = "{not json"
                    return m
            return C()

    c = Flaky(spec_for())
    spec, meta = ex.extract(c, "d", "x", model="m")
    assert c.calls == 2 and meta["attempts"] == 2 and spec.feeds[0].feed_name == "Claims Intake"

    c = Flaky(spec_for())
    c.spec = {"not": "a spec"}
    with pytest.raises(RuntimeError, match="does not match"):
        ex.extract(c, "d", "x", model="m")              # wrong SHAPE is not retried
    assert c.calls == 2
