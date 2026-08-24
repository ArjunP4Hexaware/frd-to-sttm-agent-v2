"""Demo-app backend tests (review_app_react/backend/demo.py) — mirrors the
sibling brd-to-frd repo's demo-app test approach: FastAPI TestClient over
the demo router, subprocess layer faked (no notebook is ever executed, no
network), replay assertions against the tracked _live_e2e_20260807b set."""

from __future__ import annotations

import json
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


def test_frd_must_be_a_type_01_can_parse(client, live_dirs):
    """Runs accept every type 01_frd_ingest parses (the corpus carries
    whatever SharePoint holds), and nothing else."""
    bad = demo.PRELOADED_DIR / "notes.xlsx"
    bad.write_text("hi")
    res = client.post("/api/demo/runs", json={"frd": str(bad)})
    assert res.status_code == 400
    assert ".docx" in res.json()["detail"] and ".txt" in res.json()["detail"]
    ok = demo.PRELOADED_DIR / "notes.txt"
    ok.write_text("hi")
    assert demo._validate_frd(str(ok)) == ok.resolve()


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


# --------------------------------------------------------------------------- #
# Gate-strip quote repair (_clean_note / _truncate_words)
# --------------------------------------------------------------------------- #
ZIP_RULE_FULL = ("If the ZIP_CODE column is NULL, then we are rejecting the "
                 "record and moving it to the reject table from the below files.")


def test_truncate_words_full_and_boundary():
    assert demo._truncate_words("short rule") == "short rule"
    long = "word " * 60
    cut = demo._truncate_words(long, 200)
    assert len(cut) <= 201 and cut.endswith("…") and not cut.rstrip("…").endswith("wor")


def test_clean_note_restores_full_rule_from_old_hard_slice():
    # The exact defect seen in rehearsal: the pre-fix pipeline stored a
    # rule[:90] hard slice ending mid-word ("...moving it to the reje").
    note = ("confirmed rule attribution on ['a', 'b'] — candidates match "
            f"dictionary column membership exactly: '{ZIP_RULE_FULL[:90]}'")
    cleaned = demo._clean_note(note, [ZIP_RULE_FULL])
    assert "reje'" not in cleaned
    assert ZIP_RULE_FULL in cleaned  # full rule is 118 chars — under the limit


def test_clean_note_word_boundary_fallback_without_match():
    note = f"removed rule from 'x' — not in its dictionary: '{ZIP_RULE_FULL[:90]}'"
    cleaned = demo._clean_note(note, [])
    assert "reje'" not in cleaned
    assert cleaned.endswith("…'")


def test_clean_note_leaves_unquoted_notes_alone():
    note = "cleared recycle_rule on 'x' — dictionary recycle marker present only on ['y']"
    assert demo._clean_note(note, [ZIP_RULE_FULL]) == note


@needs_replay_set
def test_replay_payload_serves_repaired_quote(client):
    res = client.get(f"/api/demo/artifacts/{REPLAY_SET}/results", params={"doc": "demo_frd"})
    notes = res.json()["gate"]["auto_confirmed_notes"]
    assert notes and all("reje'" not in n for n in notes)
    assert any("reject table from the below files." in n for n in notes)


# --------------------------------------------------------------------------- #
# Human-in-the-loop on a finished run (2026-08-22 evening)
# --------------------------------------------------------------------------- #

def _gated_set(root: Path, set_id="sttm_out_demo_20260822_180000", doc_id="syn_doc") -> Path:
    """A finished run whose v1 contract carries one candidate-having
    ambiguity and one advisory (candidate-less) one — synthetic."""
    d = root / set_id
    (d / "extractions").mkdir(parents=True)
    (d / "contracts").mkdir()
    (d / "extractions" / f"{doc_id}.json").write_text(json.dumps({"feeds": []}))
    contract = {
        "status": "PASS_WITH_FLAGS", "feeds": [],
        "_provenance": {
            "ambiguities": [{
                "id": "amb-1", "kind": "attribution", "has_candidates": True,
                "candidates": ["FEED_A", "FEED_B"],
                "text": "Rule 'reject when NULL' appears on FEED_A and FEED_B",
                "context": {"rule": "reject when NULL"},
            }],
            "grounding": {"advisory_flagged": [{
                "id": "adv-1", "kind": "advisory_grounding", "has_candidates": False,
                "candidates": [], "text": "retention prose drifted", "context": {},
            }]},
        },
    }
    (d / "contracts" / f"{doc_id}.contract.json").write_text(json.dumps(contract))
    return d


def test_review_lists_gated_items_with_no_resolutions(client, live_dirs):
    _gated_set(live_dirs)
    r = client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/review?doc=syn_doc")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_items"] == 2 and body["n_resolved"] == 0
    assert {i["id"] for i in body["items"]} == {"amb-1", "adv-1"}
    assert body["items"][0]["resolution"] is None
    assert body["rerender"]["state"] == "idle"


def test_review_404_without_a_contract_and_400_for_a_curated_set(client, live_dirs):
    assert client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/review?doc=x").status_code == 404
    assert client.get("/api/demo/artifacts/sttm_out/review?doc=x").status_code == 400


def test_resolution_is_validated_and_persisted_into_the_v1_contract(client, live_dirs):
    d = _gated_set(live_dirs)
    url = "/api/demo/artifacts/sttm_out_demo_20260822_180000/resolutions?doc=syn_doc"
    # structural pick required: a free-text answer on a candidate item is refused
    r = client.post(url, json={"ambiguity_id": "amb-1", "kind": "attribution",
                               "resolution_type": "free_text", "rationale": "FEED_A"})
    assert r.status_code == 400
    # a pick outside the candidates is refused
    r = client.post(url, json={"ambiguity_id": "amb-1", "kind": "attribution",
                               "resolution_type": "candidate_pick", "chosen_candidate": "FEED_Z"})
    assert r.status_code == 400
    # unknown id / unknown type
    assert client.post(url, json={"ambiguity_id": "nope", "kind": "x",
                                  "resolution_type": "none_of_these"}).status_code == 400
    assert client.post(url, json={"ambiguity_id": "amb-1", "kind": "attribution",
                                  "resolution_type": "maybe"}).status_code == 400
    # a real pick lands in _provenance.human_resolutions and comes back on the item
    r = client.post(url, json={"ambiguity_id": "amb-1", "kind": "attribution",
                               "resolution_type": "candidate_pick", "chosen_candidate": "FEED_A",
                               "rationale": "FEED_A owns the rule"})
    assert r.status_code == 200, r.text
    assert r.json()["resolution"]["chosen_candidate"] == "FEED_A"
    saved = json.loads((d / "contracts" / "syn_doc.contract.json").read_text())
    hr = saved["_provenance"]["human_resolutions"]
    assert len(hr) == 1 and hr[0]["ambiguity_id"] == "amb-1"
    assert hr[0]["candidates_snapshot"] == ["FEED_A", "FEED_B"]
    # free text is the only answer a candidate-less item takes
    r = client.post(url, json={"ambiguity_id": "adv-1", "kind": "advisory_grounding",
                               "resolution_type": "free_text", "rationale": "keep 7 years"})
    assert r.status_code == 200
    assert client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/review?doc=syn_doc").json()["n_resolved"] == 2
    assert list((d / "contracts").glob("*.part")) == []


def test_rerender_runs_only_stage_04_with_the_runs_insulation(client, live_dirs, monkeypatch):
    _gated_set(live_dirs)
    launched = []

    class _Proc(_FakeProc):
        def __init__(self, cmd, **kwargs):
            launched.append((cmd, kwargs.get("env", {})))
            super().__init__(cmd, **kwargs)

    monkeypatch.setattr(demo.subprocess, "Popen", _Proc)
    demo._rerenders.clear()
    r = client.post("/api/demo/artifacts/sttm_out_demo_20260822_180000/rerender?doc=syn_doc")
    assert r.status_code == 202, r.text
    deadline = time.time() + 5
    while time.time() < deadline:
        st = client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/rerender").json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["state"] == "done", st
    assert len(launched) == 1
    cmd, env = launched[0]
    assert Path(cmd[-1]).name == "04_sttm_render.py"          # ONLY the render stage
    assert env["SCHEMA"] == "sttm_agent_demo_20260822_180000"  # the run's own insulation
    assert env["OUT_VOLUME"] == "sttm_out_demo_20260822_180000"
    assert "STTM_MOCK_EXTRACTION" not in env
    # the review payload reports it, and a second one while running is a 409
    assert client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/review?doc=syn_doc").json()["rerender"]["state"] == "done"
    demo._rerenders["sttm_out_demo_20260822_180000"]["state"] = "running"
    assert client.post("/api/demo/artifacts/sttm_out_demo_20260822_180000/rerender?doc=syn_doc").status_code == 409
    demo._rerenders.clear()


def test_rerender_failure_is_surfaced_not_stuck(client, live_dirs, monkeypatch):
    _gated_set(live_dirs)

    class _Failing(_FakeProc):
        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(demo.subprocess, "Popen", _Failing)
    demo._rerenders.clear()
    client.post("/api/demo/artifacts/sttm_out_demo_20260822_180000/rerender?doc=syn_doc")
    deadline = time.time() + 5
    while time.time() < deadline:
        st = client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/rerender").json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["state"] == "failed" and "04_sttm_render.py exited with code 3" in st["error"]
    demo._rerenders.clear()


# --------------------------------------------------------------------------- #
# provider-aware preflight (2026-08-24)
# --------------------------------------------------------------------------- #
def test_local_run_still_requires_the_key_on_the_anthropic_path(monkeypatch):
    """Unchanged behaviour for the default provider — a live Anthropic run
    without a key must still be refused before it starts."""
    monkeypatch.delenv("STTM_LLM_PROVIDER", raising=False)
    assert demo.needs_anthropic_key() is True


def test_databricks_provider_needs_no_anthropic_key(monkeypatch):
    """The whole point: that path reads no Anthropic key, so demanding one in
    preflight would refuse a run that would have worked."""
    monkeypatch.setenv("STTM_LLM_PROVIDER", "databricks")
    assert demo.needs_anthropic_key() is False


def test_provider_is_read_at_call_time_not_import_time(monkeypatch):
    monkeypatch.setenv("STTM_LLM_PROVIDER", "databricks")
    assert demo.llm_provider() == "databricks"
    monkeypatch.setenv("STTM_LLM_PROVIDER", "anthropic")
    assert demo.llm_provider() == "anthropic"


def test_config_reports_the_configured_provider(monkeypatch):
    monkeypatch.setenv("STTM_LLM_PROVIDER", "databricks")
    assert demo.demo_config()["provider"] == "databricks"


def test_config_defaults_to_anthropic_when_unset(monkeypatch):
    monkeypatch.delenv("STTM_LLM_PROVIDER", raising=False)
    assert demo.demo_config()["provider"] == "anthropic"
