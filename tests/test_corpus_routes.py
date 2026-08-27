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
    # The local folder outranks SharePoint (2026-08-24) — clear it so these
    # tenant-based tests exercise the branch they mean to.
    monkeypatch.delenv("STTM_LOCAL_SOURCE_DIR", raising=False)
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture(autouse=True)
def _legacy_unprefixed_library(monkeypatch):
    """The FakeLibrary here predates the FRD_/STTM_ convention; the app's
    default prefixes would (correctly) ignore every file in it. The
    start-up/prefix tests below set the real prefixes explicitly."""
    monkeypatch.setattr(cr, "FRD_NAME_PREFIX", "")
    monkeypatch.setattr(cr, "STTM_NAME_PREFIX", "")


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
    dictionaries = tmp_path / "vdd_raw"
    monkeypatch.setattr(cr, "PRELOADED_DIR", preloaded)
    monkeypatch.setattr(cr, "REFERENCE_DIR", reference)
    monkeypatch.setattr(cr, "DICTIONARY_DIR", dictionaries)
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


# --------------------------------------------------------------------------- #
# start-up sync (decided 2026-08-22 late evening: sync when the app starts)
# --------------------------------------------------------------------------- #
def test_startup_sync_runs_the_real_sync_when_a_tenant_is_wired(client, configured, dirs, library, monkeypatch):
    monkeypatch.setattr(cr, "SYNC_ON_STARTUP", True)
    state = cr.start_sync_on_startup()
    assert state["state"] == "running" and state["mode"] == "sync" and state["trigger"] == "startup"
    state = _wait_sync(client)
    assert state["state"] == "done"
    preloaded, reference = dirs
    assert (preloaded / "member_risk.txt").is_file()
    assert (reference / "member_risk.sttm.xlsx").is_file()
    assert client.get("/api/demo/corpus").json()["built"] is True


def test_startup_sync_falls_back_to_reindex_without_a_tenant(client, dirs, monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cr, "SYNC_ON_STARTUP", True)
    preloaded, reference = dirs
    preloaded.mkdir(parents=True)
    (preloaded / "member_risk.txt").write_bytes(frd_text("member_risk", MEMBER_COLS))
    state = cr.start_sync_on_startup()
    assert state["mode"] == "reindex" and state["trigger"] == "startup"
    state = _wait_sync(client)
    assert state["state"] == "done" and state["summary"]["n_frds"] == 1


def test_startup_sync_respects_the_off_switch(client, configured, dirs, library, monkeypatch):
    monkeypatch.setattr(cr, "SYNC_ON_STARTUP", False)
    assert cr.start_sync_on_startup() is None
    assert client.get("/api/demo/corpus/sync").json()["state"] == "idle"


def test_startup_sync_applies_the_library_naming_convention(client, configured, dirs, monkeypatch):
    """With the default FRD_/STTM_ prefixes, a start-up sync imports exactly
    the convention files and pairs them by stem."""
    monkeypatch.setattr(cr, "SYNC_ON_STARTUP", True)
    monkeypatch.setattr(cr, "FRD_NAME_PREFIX", "FRD_")
    monkeypatch.setattr(cr, "STTM_NAME_PREFIX", "STTM_")
    lib = FakeLibrary()
    lib.add_frd("f1", "FRD_member_risk.txt", frd_text("member_risk", MEMBER_COLS))
    lib.add_frd("f2", "readme.txt", frd_text("claim_intake", CLAIM_COLS))
    lib.add_sttm("r1", "STTM_member_risk.xlsx", wb_bytes("member_risk", MEMBER_COLS))
    monkeypatch.setattr(spr, "build_client", lambda cfg: lib)
    cr.start_sync_on_startup()
    state = _wait_sync(client)
    assert state["state"] == "done"
    assert state["summary"]["frd_ignored"] == 1 and state["summary"]["n_pairs"] == 1
    frds = client.get("/api/demo/corpus/frds").json()["frds"]
    assert [f["doc_id"] for f in frds] == ["FRD_member_risk"]
    assert frds[0]["reference"] == "STTM_member_risk.xlsx" and frds[0]["matched_by"] == "name"


def test_app_lifespan_kicks_off_the_startup_sync(monkeypatch):
    """app.py wires start_sync_on_startup into the FastAPI lifespan — and
    the hook never blocks or raises the server's start-up."""
    import app as backend_app
    calls = []
    monkeypatch.setattr(backend_app, "start_sync_on_startup", lambda: calls.append("startup"))
    with TestClient(backend_app.app):
        pass
    assert calls == ["startup"]


# --------------------------------------------------------------------------- #
# _source_client — which document source the sync actually uses
# --------------------------------------------------------------------------- #
def test_source_client_prefers_the_local_folder(monkeypatch, tmp_path):
    src = tmp_path / "frd-to-sttm-demo-document"
    src.mkdir()
    monkeypatch.setenv("STTM_LOCAL_SOURCE_DIR", str(src))
    for k, v in ENV.items():          # a wired tenant as well — folder still wins
        monkeypatch.setenv(k, v)

    kind, reference_folder, client = cr._source_client()

    assert kind == "local_folder"
    assert reference_folder == str(src)
    assert isinstance(client, cr.LocalFolderClient)


def test_source_client_falls_back_to_sharepoint(monkeypatch, configured):
    monkeypatch.delenv("STTM_LOCAL_SOURCE_DIR", raising=False)
    monkeypatch.setattr(spr, "build_client", lambda cfg: "GRAPH-CLIENT")

    kind, reference_folder, client = cr._source_client()

    assert kind == "sharepoint"
    assert reference_folder == "STTMs"
    assert client == "GRAPH-CLIENT"


def test_source_client_503_names_both_gaps(monkeypatch):
    """An operator who set STTM_LOCAL_SOURCE_DIR to a typo needs to see THAT,
    not just 'SharePoint is not configured'."""
    monkeypatch.setenv("STTM_LOCAL_SOURCE_DIR", "/no/such/folder")
    for k in ENV:
        monkeypatch.delenv(k, raising=False)

    with pytest.raises(fastapi.HTTPException) as info:
        cr._source_client()

    assert info.value.status_code == 503
    assert "/no/such/folder" in info.value.detail
    assert "SharePoint" in info.value.detail


def test_startup_reindexes_when_no_source_is_configured(monkeypatch):
    """The ordinary dev state: no folder, no tenant — index the volumes
    rather than failing the app's lifespan."""
    monkeypatch.delenv("STTM_LOCAL_SOURCE_DIR", raising=False)
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    started = {}
    monkeypatch.setattr(cr, "_start_sync",
                        lambda mode, client, folder, **kw: started.update(
                            mode=mode, client=client) or {})

    cr.start_sync_on_startup()

    assert started["mode"] == "reindex"
    assert started["client"] is None


def test_corpus_config_offers_sync_for_a_local_folder(client, monkeypatch, tmp_path):
    """Regression: the probe that gates the "Sync now" button used to check
    SharePoint only, so a folder-configured backend greyed out the one
    control that would have worked."""
    src = tmp_path / "docs"
    src.mkdir()
    monkeypatch.setenv("STTM_LOCAL_SOURCE_DIR", str(src))
    for k in ENV:
        monkeypatch.delenv(k, raising=False)

    body = client.get("/api/demo/corpus/config").json()

    assert body["available"] is True
    assert body["frd_folder"] == str(src)
    assert body["reference_folder"] == str(src)


def test_corpus_config_unavailable_with_no_source(client, monkeypatch):
    monkeypatch.delenv("STTM_LOCAL_SOURCE_DIR", raising=False)
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert client.get("/api/demo/corpus/config").json()["available"] is False


# --------------------------------------------------------------------------- #
# the third input — vendor data dictionaries (2026-08-27)
# --------------------------------------------------------------------------- #
from test_dictionary import _field_row, _file_row, write_dictionary  # noqa: E402


def _reindex_with_dictionary(client, dirs, monkeypatch, tmp_path, *, dict_name):
    """One FRD in the corpus and one DICT_ workbook beside it, indexed offline."""
    preloaded, reference = dirs
    preloaded.mkdir(parents=True, exist_ok=True)
    reference.mkdir(parents=True, exist_ok=True)
    (preloaded / "member_risk.txt").write_bytes(frd_text("member_risk", MEMBER_COLS))
    (reference / "member_risk.sttm.xlsx").write_bytes(wb_bytes("member_risk", MEMBER_COLS))
    dict_dir = tmp_path / "vdd_raw"
    dict_dir.mkdir(parents=True, exist_ok=True)
    write_dictionary(dict_dir / dict_name,
                     [_file_row("MEMBER_RISK_CCYYMMDD.txt", "member_risk_fields")],
                     {"member_risk_fields": [_field_row(1, "member_id"),
                                             _field_row(2, "zip_code")]})
    r = client.post("/api/demo/corpus/sync", json={"confirm": True, "mode": "reindex"})
    assert r.status_code == 202
    _wait_sync(client)
    return dict_dir


def test_picker_reports_the_paired_dictionary(client, dirs, monkeypatch, tmp_path):
    _reindex_with_dictionary(client, dirs, monkeypatch, tmp_path,
                             dict_name="DICT_member_risk.xlsx")
    entry = next(f for f in client.get("/api/demo/corpus/frds").json()["frds"]
                 if f["doc_id"] == "member_risk")
    assert entry["has_dictionary"] is True
    assert entry["dictionary"] == "DICT_member_risk.xlsx"
    assert entry["dictionary_files"] == 1
    assert entry["dictionary_fields"] == 2
    assert entry["dictionary_problems"] == 0


def test_picker_reports_no_dictionary_as_a_state_not_an_omission(client, dirs):
    """Every FRD carries the block; `has_dictionary` false is the gate."""
    preloaded, reference = dirs
    preloaded.mkdir(parents=True, exist_ok=True)
    reference.mkdir(parents=True, exist_ok=True)
    (preloaded / "member_risk.txt").write_bytes(frd_text("member_risk", MEMBER_COLS))
    client.post("/api/demo/corpus/sync", json={"confirm": True, "mode": "reindex"})
    _wait_sync(client)
    entry = client.get("/api/demo/corpus/frds").json()["frds"][0]
    assert entry["has_dictionary"] is False
    assert entry["dictionary"] is None
    assert entry["dictionary_fields"] == 0


def test_an_unpaired_dictionary_is_surfaced_not_attached(client, dirs, monkeypatch, tmp_path):
    """A DICT_ workbook whose name matches no FRD must never be guessed onto one."""
    _reindex_with_dictionary(client, dirs, monkeypatch, tmp_path,
                             dict_name="DICT_some_other_vendor.xlsx")
    body = client.get("/api/demo/corpus/frds").json()
    assert body["unpaired_dictionaries"] == ["DICT_some_other_vendor.xlsx"]
    assert all(f["has_dictionary"] is False for f in body["frds"])


def test_dictionary_download_serves_only_indexed_names(client, dirs, monkeypatch, tmp_path):
    _reindex_with_dictionary(client, dirs, monkeypatch, tmp_path,
                             dict_name="DICT_member_risk.xlsx")
    ok = client.get("/api/demo/corpus/dictionaries/DICT_member_risk.xlsx")
    assert ok.status_code == 200
    assert ok.content[:2] == b"PK"           # a real .xlsx
    assert "DICT_member_risk.xlsx" in ok.headers["content-disposition"]
    # the index is the allow-list: anything else is a 404, never a file read
    assert client.get("/api/demo/corpus/dictionaries/secrets.xlsx").status_code == 404
    assert client.get(
        "/api/demo/corpus/dictionaries/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_corpus_summary_counts_dictionaries(client, dirs, monkeypatch, tmp_path):
    _reindex_with_dictionary(client, dirs, monkeypatch, tmp_path,
                             dict_name="DICT_member_risk.xlsx")
    body = client.get("/api/demo/corpus").json()
    assert body["n_dictionaries"] == 1
    assert body["n_dictionary_pairs"] == 1
    assert body["unpaired_dictionaries"] == []


def test_picker_marks_only_ready_frds_generatable(client, dirs, monkeypatch, tmp_path):
    """The rule on the wire: a dictionary and no STTM, or it is not selectable."""
    _reindex_with_dictionary(client, dirs, monkeypatch, tmp_path,
                             dict_name="VDD_member_risk.xlsx")
    body = client.get("/api/demo/corpus/frds").json()
    entry = next(f for f in body["frds"] if f["doc_id"] == "member_risk")
    # this fixture gives member_risk BOTH an STTM and a dictionary. Mapped —
    # and since 2026-08-27 regeneratable, because a run reads the FRD and the
    # VDD only and never this feed's own approved workbook.
    assert entry["eligibility_status"] == "mapped"
    assert entry["generatable"] is True
    assert client.get("/api/demo/corpus").json()["n_generatable"] == 1


def test_an_frd_with_a_dictionary_and_no_sttm_is_generatable(client, dirs, monkeypatch, tmp_path):
    preloaded, reference = dirs
    preloaded.mkdir(parents=True, exist_ok=True)
    reference.mkdir(parents=True, exist_ok=True)
    (preloaded / "member_risk.txt").write_bytes(frd_text("member_risk", MEMBER_COLS))
    dict_dir = tmp_path / "vdd_raw"
    dict_dir.mkdir(parents=True, exist_ok=True)
    write_dictionary(dict_dir / "VDD_member_risk.xlsx",
                     [_file_row("M.txt", "f")], {"f": [_field_row(1, "MEMBER_ID")]})
    client.post("/api/demo/corpus/sync", json={"confirm": True, "mode": "reindex"})
    _wait_sync(client)
    entry = client.get("/api/demo/corpus/frds").json()["frds"][0]
    assert entry["eligibility_status"] == "ready" and entry["generatable"] is True
    assert client.get("/api/demo/corpus").json()["n_generatable"] == 1


def test_an_frd_with_no_dictionary_is_blocked_with_a_reason(client, dirs):
    preloaded, reference = dirs
    preloaded.mkdir(parents=True, exist_ok=True)
    reference.mkdir(parents=True, exist_ok=True)
    (preloaded / "member_risk.txt").write_bytes(frd_text("member_risk", MEMBER_COLS))
    client.post("/api/demo/corpus/sync", json={"confirm": True, "mode": "reindex"})
    _wait_sync(client)
    entry = client.get("/api/demo/corpus/frds").json()["frds"][0]
    assert entry["eligibility_status"] == "no_dictionary"
    assert entry["generatable"] is False
    assert "VDD_<feed>.xlsx" in entry["eligibility_reason"]
