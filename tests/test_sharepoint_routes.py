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


def _stub_graph(monkeypatch, *, children=None, content=b"", fail=None):
    """Patch build_client so no Graph call leaves the process."""
    class FakeClient:
        def list_documents(self, suffixes=None):
            if fail:
                raise fail
            from frdsttm.sharepoint import SharePointItem
            return [SharePointItem(**c) for c in (children or [])]

        def download_item(self, item_id):
            if fail:
                raise fail
            return content

    monkeypatch.setattr(spr, "build_client", lambda cfg: FakeClient())


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
