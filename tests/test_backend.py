"""The FastAPI backend in LOCAL mode over a synthetic data root, with the
model call faked. The deployed (databricks) mode differs only in where the
pipeline runs; the routes are the same."""

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from conftest import FakeClient, spec_for


@pytest.fixture
def api(data_root, monkeypatch):
    monkeypatch.setenv("STTM_APP_MODE", "local")
    monkeypatch.setenv("STTM_LOCAL_DATA", str(data_root))
    monkeypatch.setenv("STTM_PROVIDER", "anthropic")
    import settings
    importlib.reload(settings)
    import storage, runs, app as app_module  # noqa: E401
    importlib.reload(storage)
    importlib.reload(runs)
    importlib.reload(app_module)
    from frdsttm import extract, pipeline
    pipeline.reindex(settings.PATHS)
    fake = FakeClient(spec_for())
    monkeypatch.setattr(extract, "build_client", lambda provider, **kw: fake)
    return TestClient(app_module.app), fake


def _wait(client, run_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}").json()
        live = r.get("live")
        if not live or live.get("phase") == "done":
            return r
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_config_and_documents(api):
    client, _ = api
    assert client.get("/api/config").json()["mode"] == "local"
    docs = client.get("/api/documents").json()
    assert docs["built"] and docs["documents"][0]["doc_id"] == "FRD_Claims_Intake"
    assert docs["documents"][0]["generatable"] and docs["documents"][0]["vdd_summary"]["n_fields"] == 3


def test_full_run_flow(api):
    client, fake = api
    r = client.post("/api/runs", json={"doc_id": "FRD_Claims_Intake"})
    assert r.status_code == 201
    run_id = r.json()["run_id"]
    run = _wait(client, run_id)
    assert run["status"] == "rendered" and fake.calls == 1
    assert run["preview"][0]["n_rows"] == 7 and run["summary"]["workbook"] == "STTM_Claims_Intake.xlsx"
    wb = client.get(f"/api/runs/{run_id}/workbook")
    assert wb.status_code == 200 and wb.content[:2] == b"PK"
    assert client.get("/api/runs").json()["runs"][0]["run_id"] == run_id
    assert "# STTM run" in client.get(f"/api/runs/{run_id}/report").text


def test_questions_then_render(api):
    client, fake = api
    fake.spec = spec_for(stage_table="invented_table")
    run_id = client.post("/api/runs", json={"doc_id": "FRD_Claims_Intake"}).json()["run_id"]
    run = _wait(client, run_id)
    assert run["status"] == "needs_input"
    q = next(q for q in run["assessment"]["questions"] if q["kind"] == "unverified")
    r = client.post(f"/api/runs/{run_id}/answers", json={"question_id": q["id"], "value": "clm_claims_stg"})
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert client.post(f"/api/runs/{run_id}/render").status_code == 202
    run = _wait(client, run_id)
    assert run["status"] == "rendered" and fake.calls == 1      # no second model call


def test_refusals(api):
    client, _ = api
    assert client.post("/api/runs", json={"doc_id": "nope"}).status_code == 404
    assert client.get("/api/runs/run_missing").status_code == 404
    assert client.get("/api/documents/FRD_Claims_Intake/sttm").status_code == 404
    assert client.get("/api/documents/FRD_Claims_Intake/vdd").status_code == 200


def test_not_generatable_is_refused(api, data_root):
    client, _ = api
    (data_root / "vdds" / "VDD_Claims_Intake.xlsx").unlink()
    client.post("/api/reindex")
    time.sleep(0.5)
    assert client.get("/api/reindex").json()["state"] == "done"
    assert client.post("/api/runs", json={"doc_id": "FRD_Claims_Intake"}).status_code == 400


def test_upload_routes_by_kind(api, data_root):
    client, _ = api
    r = client.post("/api/documents/upload?kind=vdd", files={"file": ("VDD_New.xlsx", b"PK\x03\x04", "application/octet-stream")})
    assert r.status_code == 201 and (data_root / "vdds" / "VDD_New.xlsx").is_file()
    r = client.post("/api/documents/upload?kind=frd", files={"file": ("FRD_New.xlsx", b"x", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_pair_generates_without_the_corpus(api, data_root, tmp_path):
    """Two files a reviewer pairs by hand: names that would never match, and
    neither file added to the volumes."""
    from conftest import make_frd, make_vdd
    client, fake = api
    loose = tmp_path / "loose"
    loose.mkdir()
    frd = make_frd(loose / "Member Eligibility v3.docx")
    vdd = make_vdd(loose / "acme_columns.xlsx")
    from frdsttm.corpus import name_key
    assert name_key(frd.name) != name_key(vdd.name)      # nothing would pair these

    r = client.post("/api/runs/upload", files={
        "frd": (frd.name, frd.read_bytes(), "application/octet-stream"),
        "vdd": (vdd.name, vdd.read_bytes(), "application/octet-stream")})
    assert r.status_code == 201
    run_id, doc_id = r.json()["run_id"], r.json()["doc_id"]
    assert doc_id == "Member Eligibility v3"

    run = _wait(client, run_id)
    assert run["status"] == "rendered" and fake.calls == 1
    assert run["inputs"] == {"frd": frd.name, "vdd": vdd.name}
    assert run["vdd"]["file"] == vdd.name and run["preview"][0]["n_rows"] == 7
    assert client.get(f"/api/runs/{run_id}/workbook").content[:2] == b"PK"

    # the pair lives with the run, and the corpus is untouched
    inputs = data_root / "output_sttms" / run_id / "inputs"
    assert (inputs / frd.name).is_file() and (inputs / vdd.name).is_file()
    assert not (data_root / "frds" / frd.name).exists()
    assert not (data_root / "vdds" / vdd.name).exists()
    assert client.get("/api/documents").json()["documents"] == [
        d for d in client.get("/api/documents").json()["documents"] if d["doc_id"] == "FRD_Claims_Intake"]

    # and the reviewer can get back what they uploaded
    assert client.get(f"/api/runs/{run_id}/inputs/vdd").content[:2] == b"PK"
    assert client.get(f"/api/runs/{run_id}/inputs/frd").status_code == 200


def test_upload_pair_refuses_the_wrong_file_types(api, data_root):
    client, _ = api
    def post(frd_name, vdd_name):
        return client.post("/api/runs/upload", files={
            "frd": (frd_name, b"PK\x03\x04", "application/octet-stream"),
            "vdd": (vdd_name, b"PK\x03\x04", "application/octet-stream")})
    assert post("FRD.xlsx", "VDD.xlsx").status_code == 400        # an FRD is not a workbook
    assert post("FRD.docx", "VDD.docx").status_code == 400        # a dictionary is not a document
    assert not (data_root / "output_sttms").exists() or \
        not any(p.name != ".keep" for p in (data_root / "output_sttms").iterdir())


def test_rerun_uses_the_runs_own_uploaded_pair(api, data_root, tmp_path):
    from conftest import make_frd, make_vdd
    client, _ = api
    loose = tmp_path / "loose2"
    loose.mkdir()
    frd = make_frd(loose / "Pharmacy FRD.docx")
    vdd = make_vdd(loose / "pharma_dict.xlsx")
    first = client.post("/api/runs/upload", files={
        "frd": (frd.name, frd.read_bytes(), "application/octet-stream"),
        "vdd": (vdd.name, vdd.read_bytes(), "application/octet-stream")}).json()["run_id"]
    _wait(client, first)

    # doc_id alone is not in the index — only from_run can find the pair
    assert client.post("/api/runs", json={"doc_id": "Pharmacy FRD"}).status_code == 404
    r = client.post("/api/runs", json={"doc_id": "Pharmacy FRD", "from_run": first})
    assert r.status_code == 201
    second = r.json()["run_id"]
    run = _wait(client, second)
    assert second != first and run["status"] == "rendered"
    assert run["inputs"] == {"frd": frd.name, "vdd": vdd.name}
