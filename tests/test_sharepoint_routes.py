"""SharePoint config-probe tests (review_app_react/backend/sharepoint_routes.py).

Since 2026-08-22 the app no longer locates, imports, serves or publishes
anything through SharePoint on the request path — the picker is the corpus
(test_corpus_routes.py) and the agent never writes to the library. What is
left to test here is the tenant probe and the shared client factory's
failure mapping. FastAPI TestClient, no socket, no credential.
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
    "SHAREPOINT_FRD_FOLDER": "FRDs", "SHAREPOINT_REFERENCE_FOLDER": "STTMs",
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


def test_config_reports_unconfigured_without_raising(client, unconfigured):
    """An unwired tenant hides the sync control; it is not an error state."""
    r = client.get("/api/demo/sharepoint/config")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_config_reports_site_and_folders_without_leaking_the_secret(client, configured):
    r = client.get("/api/demo/sharepoint/config")
    body = r.json()
    assert body["configured"] is True
    assert body["site"] == "example.sharepoint.com/sites/DataOffice"
    assert body["library"] == "Project Docs"
    assert body["frd_folder"] == "FRDs"
    assert body["sttm_folder"] == "STTMs"   # where the reviewer uploads
    assert "SUPERSECRET" not in json.dumps(body)
    assert "output_folder" not in body      # there is no write target any more


def test_sttm_folder_defaults_to_the_frd_folder(client, configured, monkeypatch):
    monkeypatch.delenv("SHAREPOINT_REFERENCE_FOLDER")
    assert client.get("/api/demo/sharepoint/config").json()["sttm_folder"] == "FRDs"


def test_client_factory_maps_unconfigured_to_503(unconfigured):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as info:
        spr._client()
    assert info.value.status_code == 503
    assert "sttm_agent/sharepoint_client_secret" in info.value.detail


def test_client_factory_maps_graph_refusal_to_502_without_the_secret(configured, monkeypatch):
    from frdsttm.sharepoint import GraphError

    def refuse(cfg):
        raise GraphError(401, "u", "invalid_client", "no", "rid-1")

    monkeypatch.setattr(spr, "build_client", refuse)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as info:
        spr._client()
    assert info.value.status_code == 502
    assert "invalid_client" in info.value.detail and "rid-1" in info.value.detail
    assert "SUPERSECRET" not in info.value.detail


def test_the_module_exposes_no_write_route():
    """Guard for the 2026-08-22 decision: nothing in the app publishes."""
    paths = {route.path for route in spr.router.routes}
    assert paths == {"/api/demo/sharepoint/config"}
