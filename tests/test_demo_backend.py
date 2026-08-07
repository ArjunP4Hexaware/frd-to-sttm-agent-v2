"""Demo-app backend tests (review_app_react/backend/demo.py) — mirrors the
sibling brd-to-frd repo's demo-app test approach: FastAPI TestClient over
the demo router, subprocess layer faked (no notebook is ever executed, no
network), replay assertions against the tracked _live_e2e_20260807b set."""

from __future__ import annotations

import re
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

import demo  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(demo.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_run_state():
    demo._runs.clear()
    demo._active_run_id = None
    yield
    demo._runs.clear()
    demo._active_run_id = None


@pytest.fixture()
def live_dirs(tmp_path, monkeypatch):
    """Point every write path at tmp so live-run tests never touch the repo's
    local_dev_fixtures. Replay tests deliberately do NOT use this fixture —
    they read the tracked artifact set."""
    preloaded = tmp_path / "frd_raw"
    uploads = tmp_path / "demo_uploads"
    preloaded.mkdir()
    (preloaded / "demo_frd.docx").write_bytes(b"fake docx")
    monkeypatch.setattr(demo, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(demo, "PRELOADED_DIR", preloaded)
    monkeypatch.setattr(demo, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(demo, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(demo, "api_key_present", lambda: True)
    return tmp_path


class _FakeProc:
    """Stands in for a pipeline-stage subprocess: emits one line, exits 0."""

    def __init__(self, cmd, **kwargs):
        self.captured_env = kwargs.get("env", {})
        self.stdout = iter([f"fake stage output for {Path(cmd[-1]).name}\n"])

    def wait(self, timeout=None):
        return 0


# --------------------------------------------------------------------------- #
# Suffix generation + insulation
# --------------------------------------------------------------------------- #

def test_suffix_format_and_prefix(live_dirs):
    suffix = demo._new_suffix()
    assert re.fullmatch(r"demo_\d{8}_\d{6}", suffix)


def test_suffix_uniquified_on_disk_collision(live_dirs):
    first = demo._new_suffix()
    (live_dirs / f"sttm_out_{first}").mkdir()
    second = demo._new_suffix()
    assert second != first
    assert second.startswith(first)  # base-2 style uniquifier, still demo_-prefixed
    assert second.startswith("demo_")


def test_subprocess_env_insulation():
    env = demo._subprocess_env("demo_20260807_120000", "demo_raw/demo_20260807_120000")
    assert env["OUT_VOLUME"] == "sttm_out_demo_20260807_120000"
    assert env["PREVIEW_VOLUME"] == "sttm_out_demo_20260807_120000"
    assert env["SCHEMA"] == "sttm_agent_demo_20260807_120000"
    assert env["RAW_VOLUME"] == "demo_raw/demo_20260807_120000"
    # The curated locations are unreachable: every knob embeds the suffix.
    assert env["OUT_VOLUME"] != "sttm_out"
    assert env["STTM_LLM_PROVIDER"] == "anthropic"
    assert "STTM_MOCK_EXTRACTION" not in env
    assert env["PYTHONUNBUFFERED"] == "1"


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #

def _wait_terminal(run, timeout=5.0):
    deadline = time.time() + timeout
    while run.status == demo.STATUS_RUNNING and time.time() < deadline:
        time.sleep(0.02)
    return run.status


def test_run_lifecycle_launch_status_completion(client, live_dirs, monkeypatch):
    monkeypatch.setattr(demo.subprocess, "Popen", _FakeProc)
    res = client.post("/api/demo/runs", json={"frd": str(live_dirs / "frd_raw" / "demo_frd.docx")})
    assert res.status_code == 201, res.text
    snap = res.json()
    assert snap["doc_id"] == "demo_frd"
    assert snap["is_golden"] is True
    assert snap["artifact_set"] == f"sttm_out_{snap['suffix']}"
    assert [s["id"] for s in snap["stages"]] == [f for f, _ in demo._STAGES]

    run = demo.get_run(snap["id"])
    assert _wait_terminal(run) == demo.STATUS_DONE

    final = client.get(f"/api/demo/runs/{snap['id']}").json()
    assert final["status"] == "done"
    assert all(s["status"] == "done" for s in final["stages"])
    assert any(e["kind"] == "console" for e in final["events"])
    # Console log persisted to the gitignored logs dir.
    assert (demo.LOGS_DIR / f"{snap['suffix']}.log").is_file()
    # Polling cursor: events strictly after `after` only.
    assert client.get(f"/api/demo/runs/{snap['id']}", params={"after": final["seq"]}).json()["events"] == []


def test_second_run_409_while_active(client, live_dirs, monkeypatch):
    started = {"n": 0}

    class _Blocking(_FakeProc):
        def __init__(self, cmd, **kwargs):
            super().__init__(cmd, **kwargs)
            started["n"] += 1

        def wait(self, timeout=None):
            time.sleep(0.3)
            return 0

    monkeypatch.setattr(demo.subprocess, "Popen", _Blocking)
    frd = str(live_dirs / "frd_raw" / "demo_frd.docx")
    first = client.post("/api/demo/runs", json={"frd": frd})
    assert first.status_code == 201
    second = client.post("/api/demo/runs", json={"frd": frd})
    assert second.status_code == 409
    assert "already in progress" in second.json()["detail"]
    _wait_terminal(demo.get_run(first.json()["id"]))


def test_failed_stage_marks_run_failed(client, live_dirs, monkeypatch):
    class _Failing(_FakeProc):
        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(demo.subprocess, "Popen", _Failing)
    res = client.post("/api/demo/runs", json={"frd": str(live_dirs / "frd_raw" / "demo_frd.docx")})
    run = demo.get_run(res.json()["id"])
    assert _wait_terminal(run) == demo.STATUS_FAILED
    snap = run.snapshot()
    assert "01_frd_ingest.py exited with code 3" in snap["error"]
    assert snap["stages"][0]["status"] == "failed"
    # The failure releases the single-run slot.
    assert demo._active_run_id is None


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #

def test_missing_key_blocks_run(client, live_dirs, monkeypatch):
    monkeypatch.setattr(demo, "api_key_present", lambda: False)
    res = client.post("/api/demo/runs", json={"frd": str(live_dirs / "frd_raw" / "demo_frd.docx")})
    assert res.status_code == 400
    assert "ANTHROPIC_API_KEY" in res.json()["detail"]
    assert demo._active_run_id is None


def test_frd_path_containment(client, live_dirs, tmp_path):
    outside = tmp_path.parent / "outside.docx"
    outside.write_bytes(b"x")
    res = client.post("/api/demo/runs", json={"frd": str(outside)})
    assert res.status_code == 400
    assert "must be inside" in res.json()["detail"]


def test_frd_must_be_docx(client, live_dirs):
    bad = demo.PRELOADED_DIR / "notes.txt"
    bad.write_text("hi")
    res = client.post("/api/demo/runs", json={"frd": str(bad)})
    assert res.status_code == 400
    assert ".docx" in res.json()["detail"]


# --------------------------------------------------------------------------- #
# Config + uploads
# --------------------------------------------------------------------------- #

def test_config_reports_key_presence_boolean_only(client, live_dirs, monkeypatch):
    monkeypatch.setattr(demo, "api_key_present", lambda: True)
    body = client.get("/api/demo/config").json()
    assert body["api_key_present"] is True
    assert body["provider"] == "anthropic"
    assert set(body["call_estimate"]) == {"calls", "usd", "seconds"}
    # No key material anywhere in the response.
    assert "sk-" not in str(body)


def test_upload_rejects_non_docx(client, live_dirs):
    res = client.post("/api/demo/uploads", files={"file": ("brd.md", b"# x", "text/markdown")})
    assert res.status_code == 400


def test_upload_rejects_oversize(client, live_dirs, monkeypatch):
    monkeypatch.setattr(demo, "UPLOAD_MAX_BYTES", 10)
    res = client.post("/api/demo/uploads", files={"file": ("a.docx", b"x" * 11, "application/octet-stream")})
    assert res.status_code == 413


def test_upload_sanitizes_and_saves(client, live_dirs):
    res = client.post(
        "/api/demo/uploads",
        files={"file": ("my FRD (v2).docx", b"content", "application/octet-stream")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "my_FRD_v2_.docx"
    assert (demo.UPLOADS_DIR / body["name"]).read_bytes() == b"content"
    assert body["is_golden"] is False


# --------------------------------------------------------------------------- #
# Replay: discovery + results payload (against the tracked replay set)
# --------------------------------------------------------------------------- #
REPLAY_SET = "sttm_out_live_e2e_20260807b"


def _replay_available() -> bool:
    return (demo.LOCAL_ROOT / REPLAY_SET / "extractions" / "demo_frd.json").is_file()


needs_replay_set = pytest.mark.skipif(
    not _replay_available(), reason="tracked replay set missing from checkout"
)


@needs_replay_set
def test_discovery_finds_tracked_replay_set(client):
    sets = client.get("/api/demo/artifacts").json()["artifact_sets"]
    entry = next(s for s in sets if s["set_id"] == REPLAY_SET)
    assert entry["doc_id"] == "demo_frd"
    assert entry["source"] == "live_e2e"
    assert entry["status"] == "PASS"
    assert entry["eval_pct"] == 94.1
    assert entry["workbook_available"] is True
    # The curated baseline dir is not a demo family and must never appear.
    assert all(s["set_id"] != "sttm_out" for s in sets)


def test_set_id_validation_rejects_traversal(client):
    for bad in ("../sttm_out", "sttm_out", "sttm_out_demo_..%2F", "sttm_out_x_y"):
        res = client.get(f"/api/demo/artifacts/{bad}/results", params={"doc": "demo_frd"})
        assert res.status_code in (400, 404), bad


@needs_replay_set
def test_results_payload_shape_and_gate_strip(client):
    res = client.get(f"/api/demo/artifacts/{REPLAY_SET}/results", params={"doc": "demo_frd"})
    assert res.status_code == 200
    body = res.json()
    es = body["extraction_summary"]
    assert es["n_feeds"] == 3
    assert es["n_rules"] > 0
    assert len(es["feeds"]) == 3
    gate = body["gate"]
    # The D2 story: 1 detected at stage 03, 1 auto-confirmed against the
    # dictionary, 0 awaiting a human.
    assert gate["detected"] == 1
    assert gate["auto_confirmed"] == 1
    assert gate["awaiting_human"] == 0
    assert gate["strict_checked"] == 45 and gate["strict_failed"] == 0
    assert body["verdict"]["status"] == "PASS"
    ev = body["eval"]
    assert ev["available"] and ev["pct"] == 94.1 and ev["is_golden"] is True
    assert body["mappings"] and all(m["rows"] for m in body["mappings"])
    first_row = body["mappings"][0]["rows"][0]
    assert set(first_row) == {"source_column", "datatype", "stage", "standard"}
    assert body["workbook_available"] is True


@needs_replay_set
def test_workbook_download(client):
    res = client.get(f"/api/demo/artifacts/{REPLAY_SET}/workbook", params={"doc": "demo_frd"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert len(res.content) > 10_000


def test_results_404_for_unknown_doc(client):
    res = client.get(f"/api/demo/artifacts/{REPLAY_SET}/results", params={"doc": "nope"})
    assert res.status_code == 404
