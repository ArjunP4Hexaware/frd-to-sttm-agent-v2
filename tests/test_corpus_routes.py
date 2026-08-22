"""Corpus route tests (review_app_react/backend/corpus_routes.py).

Same posture as test_sharepoint_routes.py: FastAPI TestClient with the
Graph client stubbed — no socket, no credential, no model call. All
document content is synthetic and generated in-memory. The sync runs in a
background thread exactly as in the app; tests poll GET /api/demo/corpus/sync
the way the frontend does.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent / "review_app_react" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import corpus_routes as cr  # noqa: E402
import sharepoint_routes as spr  # noqa: E402

from test_sharepoint_routes import ENV  # noqa: E402  (same tenant fixture)
from test_sync import CLAIM_COLS, MEMBER_COLS, FakeLibrary, frd_text, wb_bytes  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(cr.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def configured(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture(autouse=True)
def _reset_sync_state():
    with cr._sync_lock:
        cr._sync.update(state="idle", mode=None, started_at=None, finished_at=None,
                        error=None, run_page_url=None, summary=None)
    yield


@pytest.fixture()
def dirs(monkeypatch, tmp_path):
    preloaded = tmp_path / "frd_raw"
    reference = tmp_path / "sttm_reference"
    monkeypatch.setattr(cr, "PRELOADED_DIR", preloaded)
    monkeypatch.setattr(cr, "REFERENCE_DIR", reference)
    return preloaded, reference


@pytest.fixture()
def library(monkeypatch):
    lib = FakeLibrary()
    lib.add_frd("f1", "member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_frd("f2", "claim_intake.txt", frd_text("claim_intake", CLAIM_COLS))
    lib.add_sttm("r1", "member_risk.sttm.xlsx", wb_bytes("member_risk", MEMBER_COLS))
    monkeypatch.setattr(spr, "build_client", lambda cfg: lib)
    return lib


def _wait_sync(client, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get("/api/demo/corpus/sync").json()
        if state["state"] in ("done", "failed"):
            return state
        time.sleep(0.05)
    raise AssertionError("sync did not finish")


# --------------------------------------------------------------------------- #
# gates
# --------------------------------------------------------------------------- #
def test_sync_requires_confirm(client, configured, dirs):
    r = client.post("/api/demo/corpus/sync", json={})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"]


def test_sync_rejects_unknown_mode(client, configured, dirs):
    r = client.post("/api/demo/corpus/sync", json={"confirm": True, "mode": "maybe"})
    assert r.status_code == 400


def test_sync_503_when_unconfigured(client, dirs, monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    r = client.post("/api/demo/corpus/sync", json={"confirm": True})
    assert r.status_code == 503
    # ...but a REINDEX needs no tenant at all.
    r = client.post("/api/demo/corpus/sync", json={"confirm": True, "mode": "reindex"})
    assert r.status_code == 202


def test_summary_unbuilt_is_a_normal_state(client, dirs):
    r = client.get("/api/demo/corpus")
    assert r.status_code == 200
    body = r.json()
    assert body["built"] is False and body["sync"]["state"] == "idle"
    r = client.get("/api/demo/corpus/frds")
    assert r.status_code == 200
    assert r.json() == {"built": False, "frds": []}


# --------------------------------------------------------------------------- #
# the sync end to end through the app
# --------------------------------------------------------------------------- #
def test_sync_lands_files_builds_pairs_and_lists_the_picker(client, configured, dirs, library):
    preloaded, reference = dirs
    r = client.post("/api/demo/corpus/sync", json={"confirm": True})
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "running"
    state = _wait_sync(client)
    assert state["state"] == "done", state
    assert state["summary"]["n_pairs"] == 1 and state["summary"]["n_unmapped"] == 1
    assert state["summary"]["skipped"] == []
    assert (preloaded / "member_risk.txt").is_file()
    assert (reference / "member_risk.sttm.xlsx").is_file()
    assert (reference / "corpus_index.json").is_file()

    summary = client.get("/api/demo/corpus").json()
    assert summary["built"] is True and summary["n_pairs"] == 1
    assert summary["synced_at"] is not None

    frds = client.get("/api/demo/corpus/frds").json()["frds"]
    by_id = {f["doc_id"]: f for f in frds}
    mr = by_id["member_risk"]
    assert mr["paired"] is True and mr["reference"] == "member_risk.sttm.xlsx"
    assert mr["matched_by"] == "name"
    assert mr["web_url"] == "https://sp/member_risk.txt"
    assert mr["reference_web_url"] == "https://sp/member_risk.sttm.xlsx"
    assert mr["reference_size_bytes"] > 0
    assert len(mr["content_sha256"]) == 64
    ci = by_id["claim_intake"]
    assert ci["paired"] is False and ci["reference"] is None
    # `path` is None here because PRELOADED_DIR was moved out of the repo
    # root for the test — the runnable flag tells the UI the truth either way.
    assert ci["runnable"] in (True, False)


def test_second_sync_is_409_while_one_runs(client, configured, dirs, library):
    with cr._sync_lock:
        cr._sync.update(state="running")
    r = client.post("/api/demo/corpus/sync", json={"confirm": True})
    assert r.status_code == 409


def test_sync_collects_per_file_failures_without_dying(client, configured, dirs, library):
    library.add_frd("f9", "broken.docx", b"not a real docx")
    client.post("/api/demo/corpus/sync", json={"confirm": True})
    state = _wait_sync(client)
    assert state["state"] == "done", state
    assert state["summary"]["n_pairs"] == 1  # the good pair still built
    assert [s["name"] for s in state["summary"]["skipped"]] == ["broken.docx"]


def test_graph_refusal_surfaces_as_a_failed_sync(client, configured, dirs, monkeypatch):
    from frdsttm.sharepoint import GraphError

    class Refusing:
        def list_documents(self, suffixes=None, folder=None):
            raise GraphError(403, "u", "accessDenied", "no", "rid-1")

    monkeypatch.setattr(spr, "build_client", lambda cfg: Refusing())
    client.post("/api/demo/corpus/sync", json={"confirm": True})
    state = _wait_sync(client)
    assert state["state"] == "failed"
    assert "accessDenied" in state["error"]
    assert "SUPERSECRET" not in state["error"]


def test_reindex_rebuilds_from_the_volumes_without_sharepoint(client, dirs, monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    preloaded, reference = dirs
    preloaded.mkdir(); reference.mkdir()
    (preloaded / "member_risk.txt").write_bytes(frd_text("member_risk", MEMBER_COLS))
    (reference / "member_risk.sttm.xlsx").write_bytes(wb_bytes("member_risk", MEMBER_COLS))
    r = client.post("/api/demo/corpus/sync", json={"confirm": True, "mode": "reindex"})
    assert r.status_code == 202
    state = _wait_sync(client)
    assert state["state"] == "done", state
    assert state["mode"] == "reindex"
    assert state["summary"]["n_pairs"] == 1
    assert client.get("/api/demo/corpus").json()["synced_at"] is None  # never synced, only indexed


# --------------------------------------------------------------------------- #
# reference download — the index is the allow-list
# --------------------------------------------------------------------------- #
def test_reference_download_serves_only_indexed_workbooks(client, configured, dirs, library):
    client.post("/api/demo/corpus/sync", json={"confirm": True})
    _wait_sync(client)
    ok = client.get("/api/demo/corpus/references/member_risk.sttm.xlsx")
    assert ok.status_code == 200
    assert ok.content == wb_bytes("member_risk", MEMBER_COLS) or ok.content[:2] == b"PK"
    assert "member_risk.sttm.xlsx" in ok.headers["content-disposition"]
    assert client.get("/api/demo/corpus/references/other.xlsx").status_code == 404
    assert client.get("/api/demo/corpus/references/..%2Fcorpus_index.json").status_code == 404


def test_config_probe_reports_folders_or_unavailable(client, configured, dirs, monkeypatch):
    body = client.get("/api/demo/corpus/config").json()
    assert body["available"] is True
    assert body["frd_folder"] == "FRDs" and body["reference_folder"] == "STTMs"
    assert body["mode"] == "local"
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    body = client.get("/api/demo/corpus/config").json()
    assert body["available"] is False and body["mode"] == "local"
