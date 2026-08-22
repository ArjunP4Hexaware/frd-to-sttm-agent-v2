"""SharePoint picker route tests (review_app_react/backend/sharepoint_routes.py).

FastAPI TestClient over the router with the Graph transport stubbed: no
socket, no credential, no notebook executed. Mirrors test_demo_backend.py's
approach and keeps the suite offline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent / "review_app_react" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import demo  # noqa: E402
import sharepoint_routes as spr  # noqa: E402

ENV = {
    "SHAREPOINT_TENANT_ID": "tid", "SHAREPOINT_CLIENT_ID": "cid",
    "SHAREPOINT_CLIENT_SECRET": "SUPERSECRET",
    "SHAREPOINT_HOST": "example.sharepoint.com",
    "SHAREPOINT_SITE_PATH": "/sites/DataOffice",
    "SHAREPOINT_LIBRARY": "Project Docs",
    "SHAREPOINT_FRD_FOLDER": "FRDs", "SHAREPOINT_OUTPUT_FOLDER": "STTMs",
}


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(spr.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def configured(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def unconfigured(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def _stub_graph(monkeypatch, *, children=None, outputs=None, content=b"", fail=None):
    """Patch build_client so no Graph call leaves the process.

    `children` lists the FRD folder (folder=None); `outputs` lists the
    output folder (any explicit folder override) — mirroring the two
    folders the locate flow reads.
    """
    uploads: list = []

    class FakeClient:
        def list_documents(self, suffixes=None, folder=None):
            if fail:
                raise fail
            from frdsttm.sharepoint import SharePointItem
            rows = children if folder is None else outputs
            return [SharePointItem(**c) for c in (rows or [])]

        def download_item(self, item_id):
            if fail:
                raise fail
            return content

        def upload_file(self, local_path):
            if fail:
                raise fail
            uploads.append(local_path)
            return {"webUrl": f"https://x/STTMs/{Path(local_path).name}"}

    monkeypatch.setattr(spr, "build_client", lambda cfg: FakeClient())
    return uploads


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

def test_config_reports_unconfigured_without_raising(client, unconfigured):
    """An unwired tenant hides the picker; it is not an error state."""
    r = client.get("/api/demo/sharepoint/config")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_config_reports_site_without_leaking_the_secret(client, configured):
    r = client.get("/api/demo/sharepoint/config")
    body = r.json()
    assert body["configured"] is True
    assert body["site"] == "example.sharepoint.com/sites/DataOffice"
    assert body["library"] == "Project Docs"
    assert "SUPERSECRET" not in json.dumps(body)


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #

def test_documents_returns_pickable_items(client, configured, monkeypatch):
    _stub_graph(monkeypatch, children=[
        {"item_id": "1", "name": "frd.docx", "size": 12,
         "modified": "2026-08-20T10:00:00Z", "web_url": "https://x/frd.docx"},
    ])
    body = client.get("/api/demo/sharepoint/documents").json()
    assert body["library"] == "Project Docs" and body["folder"] == "FRDs"
    assert body["documents"][0]["item_id"] == "1"
    assert body["documents"][0]["size_bytes"] == 12


def test_documents_503_when_not_configured(client, unconfigured):
    """503 = nobody wired the tenant yet, distinct from a Graph refusal."""
    r = client.get("/api/demo/sharepoint/documents")
    assert r.status_code == 503
    assert "sttm_agent/sharepoint_client_secret" in r.json()["detail"]


def test_documents_502_on_graph_refusal_without_the_secret(client, configured, monkeypatch):
    from frdsttm.sharepoint import GraphError
    _stub_graph(monkeypatch, fail=GraphError(403, "u", "accessDenied", "no", "rid-1"))
    r = client.get("/api/demo/sharepoint/documents")
    assert r.status_code == 502
    assert "accessDenied" in r.json()["detail"]
    assert "SUPERSECRET" not in r.text


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #

def test_import_lands_in_uploads_and_returns_a_runnable_document(
        client, configured, monkeypatch, tmp_path):
    monkeypatch.setattr(spr, "UPLOADS_DIR", tmp_path)
    _stub_graph(monkeypatch, content=b"DOCXBYTES")
    r = client.post("/api/demo/sharepoint/import",
                    json={"item_id": "1", "name": "client frd.docx"})
    assert r.status_code == 201
    body = r.json()
    assert body["source"] == "sharepoint"
    assert body["name"] == "client_frd.docx"        # sanitised like an upload
    assert (tmp_path / "client_frd.docx").read_bytes() == b"DOCXBYTES"
    assert list(tmp_path.glob("*.part")) == []
    # The returned shape is what POST /api/demo/runs consumes.
    assert set(body) >= {"path", "name", "doc_id", "source", "is_golden", "size_bytes"}


def test_import_rejects_non_docx(client, configured, monkeypatch, tmp_path):
    monkeypatch.setattr(spr, "UPLOADS_DIR", tmp_path)
    _stub_graph(monkeypatch, content=b"x")
    r = client.post("/api/demo/sharepoint/import",
                    json={"item_id": "1", "name": "notes.pdf"})
    assert r.status_code == 400 and "docx" in r.json()["detail"]


def test_import_sanitises_a_traversal_style_name(client, configured, monkeypatch, tmp_path):
    """A library file name is untrusted input: it must never escape UPLOADS_DIR."""
    monkeypatch.setattr(spr, "UPLOADS_DIR", tmp_path)
    _stub_graph(monkeypatch, content=b"x")
    r = client.post("/api/demo/sharepoint/import",
                    json={"item_id": "1", "name": "../../etc/evil.docx"})
    assert r.status_code == 201
    written = list(tmp_path.iterdir())
    assert [p.name for p in written] == ["evil.docx"]
    assert not (tmp_path.parent / "etc").exists()


def test_import_413_when_over_the_demo_cap(client, configured, monkeypatch, tmp_path):
    monkeypatch.setattr(spr, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(spr, "UPLOAD_MAX_BYTES", 4)
    _stub_graph(monkeypatch, content=b"toolong")
    r = client.post("/api/demo/sharepoint/import",
                    json={"item_id": "1", "name": "big.docx"})
    assert r.status_code == 413
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# locate — name in, FRD found in the library; existing STTM short-circuits
# --------------------------------------------------------------------------- #

_FRD_ITEM = {"item_id": "frd-1", "name": "Community Risk FRD.docx", "size": 100,
             "modified": "2026-08-20T10:00:00Z", "web_url": "https://x/frd"}
_OTHER_ITEM = {"item_id": "frd-2", "name": "CAQH Roster FRD.docx", "size": 90,
               "modified": "2026-08-19T10:00:00Z", "web_url": "https://x/frd2"}
_STTM_ITEM = {"item_id": "sttm-1", "name": "Community Risk FRD.sttm.xlsx",
              "size": 5000, "modified": "2026-08-20T12:00:00Z",
              "web_url": "https://x/sttm"}


def test_locate_exact_name_imports_and_is_ready_to_run(client, configured, monkeypatch, tmp_path):
    """Case-insensitive, extension optional — but exact, never fuzzy."""
    monkeypatch.setattr(spr, "UPLOADS_DIR", tmp_path)
    _stub_graph(monkeypatch, children=[_FRD_ITEM, _OTHER_ITEM], content=b"DOCX")
    r = client.post("/api/demo/sharepoint/locate", json={"name": "community risk frd"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["document"]["doc_id"] == "Community_Risk_FRD"
    assert (tmp_path / "Community_Risk_FRD.docx").read_bytes() == b"DOCX"


def test_locate_presents_the_existing_sttm_and_a_runnable_document(client, configured, monkeypatch, tmp_path):
    """2026-08-22: the existing_sttm response ALSO imports the FRD so the
    reviewer can deliberately regenerate despite the existing workbook
    (previously nothing was downloaded). Import is read-only; the existing
    STTM is only replaced by an explicit confirm-gated publish later."""
    monkeypatch.setattr(spr, "UPLOADS_DIR", tmp_path)
    _stub_graph(monkeypatch, children=[_FRD_ITEM], outputs=[_STTM_ITEM], content=b"DOCX")
    r = client.post("/api/demo/sharepoint/locate",
                    json={"name": "Community Risk FRD.docx"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "existing_sttm"
    assert body["sttm"]["item_id"] == "sttm-1"
    assert body["frd"]["item_id"] == "frd-1"
    doc = body["document"]
    assert doc["source"] == "sharepoint"
    assert (tmp_path / doc["name"]).read_bytes() == b"DOCX"  # runnable import


def test_locate_partial_match_returns_candidates_never_autopicks(client, configured, monkeypatch, tmp_path):
    monkeypatch.setattr(spr, "UPLOADS_DIR", tmp_path)
    _stub_graph(monkeypatch, children=[_FRD_ITEM, _OTHER_ITEM], content=b"DOCX")
    r = client.post("/api/demo/sharepoint/locate", json={"name": "FRD"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "candidates"
    assert {c["name"] for c in body["candidates"]} == {
        "Community Risk FRD.docx", "CAQH Roster FRD.docx"}
    assert list(tmp_path.iterdir()) == []


def test_locate_no_match_is_404_naming_the_folder(client, configured, monkeypatch):
    _stub_graph(monkeypatch, children=[_FRD_ITEM])
    r = client.post("/api/demo/sharepoint/locate", json={"name": "nonexistent"})
    assert r.status_code == 404
    assert "Project Docs" in r.json()["detail"]


def test_locate_blank_name_is_400(client, configured, monkeypatch):
    _stub_graph(monkeypatch, children=[_FRD_ITEM])
    r = client.post("/api/demo/sharepoint/locate", json={"name": "   "})
    assert r.status_code == 400


def test_existing_sttm_download_serves_only_output_folder_items(client, configured, monkeypatch):
    _stub_graph(monkeypatch, outputs=[_STTM_ITEM], content=b"XLSXBYTES")
    ok = client.get("/api/demo/sharepoint/sttm/sttm-1")
    assert ok.status_code == 200
    assert ok.content == b"XLSXBYTES"
    assert "Community_Risk_FRD.sttm.xlsx" in ok.headers["content-disposition"]
    # An id outside the output folder must 404, never proxy the item.
    assert client.get("/api/demo/sharepoint/sttm/frd-1").status_code == 404


# --------------------------------------------------------------------------- #
# publish — the manual, confirm-gated write path (decided 2026-08-21)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def rendered_workbook(monkeypatch, tmp_path):
    """A demo artifact set holding one rendered workbook, addressed the same
    way the download endpoint addresses it (demo.workbook_path)."""
    set_id, doc_id = "sttm_out_demo_20260821_120000", "client_frd"
    wb = tmp_path / set_id / "rendered" / f"{doc_id}.sttm.xlsx"
    wb.parent.mkdir(parents=True)
    wb.write_bytes(b"XLSXBYTES")
    monkeypatch.setattr(demo, "LOCAL_ROOT", tmp_path)
    return set_id, doc_id, wb


def test_publish_requires_explicit_confirm(client, configured, monkeypatch, rendered_workbook):
    """A bare POST must never write to the client's library — confirm:true is
    the whole point of the manual gate."""
    uploads = _stub_graph(monkeypatch)
    set_id, doc_id, _wb = rendered_workbook
    for body in ({"set_id": set_id, "doc_id": doc_id},
                 {"set_id": set_id, "doc_id": doc_id, "confirm": False}):
        r = client.post("/api/demo/sharepoint/publish", json=body)
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"]
    assert uploads == []


def test_publish_uploads_exactly_one_reviewed_workbook(client, configured, monkeypatch, rendered_workbook):
    uploads = _stub_graph(monkeypatch)
    set_id, doc_id, wb = rendered_workbook
    r = client.post("/api/demo/sharepoint/publish",
                    json={"set_id": set_id, "doc_id": doc_id, "confirm": True})
    assert r.status_code == 200
    body = r.json()
    assert body["published"] is True
    assert body["name"] == f"{doc_id}.sttm.xlsx"
    assert body["web_url"].endswith(f"{doc_id}.sttm.xlsx")
    assert body["target"] == "example.sharepoint.com/sites/DataOffice/Project Docs/STTMs"
    assert uploads == [wb]
    assert "SUPERSECRET" not in r.text


def test_publish_404_when_no_workbook_rendered(client, configured, monkeypatch, rendered_workbook):
    uploads = _stub_graph(monkeypatch)
    set_id, _doc_id, _wb = rendered_workbook
    r = client.post("/api/demo/sharepoint/publish",
                    json={"set_id": set_id, "doc_id": "never_rendered", "confirm": True})
    assert r.status_code == 404
    assert uploads == []


def test_publish_rejects_a_curated_baseline_set_id(client, configured, monkeypatch, rendered_workbook):
    """Only demo/live_e2e artifact sets are addressable — `sttm_out` itself
    (the curated baselines) must be a 400, same rule as the download path."""
    uploads = _stub_graph(monkeypatch)
    r = client.post("/api/demo/sharepoint/publish",
                    json={"set_id": "sttm_out", "doc_id": "demo_frd", "confirm": True})
    assert r.status_code == 400
    assert uploads == []


def test_publish_503_when_not_configured(client, unconfigured, monkeypatch, rendered_workbook):
    set_id, doc_id, _wb = rendered_workbook
    r = client.post("/api/demo/sharepoint/publish",
                    json={"set_id": set_id, "doc_id": doc_id, "confirm": True})
    assert r.status_code == 503


def test_publish_502_on_graph_refusal(client, configured, monkeypatch, rendered_workbook):
    from frdsttm.sharepoint import GraphError
    _stub_graph(monkeypatch, fail=GraphError(403, "u", "accessDenied", "no", "rid-9"))
    set_id, doc_id, _wb = rendered_workbook
    r = client.post("/api/demo/sharepoint/publish",
                    json={"set_id": set_id, "doc_id": doc_id, "confirm": True})
    assert r.status_code == 502
    assert "accessDenied" in r.json()["detail"]
    assert "SUPERSECRET" not in r.text
