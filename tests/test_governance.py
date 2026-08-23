"""Governance controls of the review app (docs/AI_GOVERNANCE.md):
identity resolution, the append-only audit trail, and the provenance that
rides on runs, resolutions, re-renders and the workbook hand-off.

Offline like the rest of the suite: FastAPI TestClient, faked subprocess /
Databricks SDK, audit store pointed at tmp (tests/conftest.py)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent / "review_app_react" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import audit  # noqa: E402
import demo  # noqa: E402
import identity as ident  # noqa: E402
import jobs_runner as jr  # noqa: E402

from test_demo_jobs_runner import FakeFiles, FakeJobs, _w  # noqa: E402

FORWARDED = {"X-Forwarded-Email": "reviewer@example.com",
             "X-Forwarded-Preferred-Username": "reviewer",
             "X-Forwarded-User": "1234567890"}


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(demo.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_run_state():
    demo._runs.clear()
    demo._active_run_id = None
    demo._rerenders.clear()
    yield
    demo._runs.clear()
    demo._active_run_id = None
    demo._rerenders.clear()


@pytest.fixture()
def live_dirs(tmp_path, monkeypatch):
    preloaded = tmp_path / "frd_raw"
    uploads = tmp_path / "demo_uploads"
    preloaded.mkdir()
    (preloaded / "demo_frd.docx").write_bytes(b"fake docx bytes")
    monkeypatch.setattr(demo, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(demo, "PRELOADED_DIR", preloaded)
    monkeypatch.setattr(demo, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(demo, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(demo, "api_key_present", lambda: True)
    return tmp_path


class _FakeProc:
    def __init__(self, cmd, **kwargs):
        self.captured_env = kwargs.get("env", {})
        self.stdout = iter([f"fake stage output for {Path(cmd[-1]).name}\n"])

    def wait(self, timeout=None):
        return 0


def _wait_terminal(run, timeout=5.0):
    deadline = time.monotonic() + timeout
    while run.snapshot()["status"] == demo.STATUS_RUNNING:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    # the finished event is written after the slot is released; give it a beat
    deadline = time.monotonic() + timeout
    while not any(e["kind"] == "run.finished" for e in audit.list_events()):
        assert time.monotonic() < deadline, "run.finished never recorded"
        time.sleep(0.01)
    return run.snapshot()


def _events(kind=None):
    return audit.list_events(kind=kind)


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #

def test_identity_from_headers_prefers_email_then_username_then_id():
    assert ident.identity_from_headers({}) is None
    full = ident.identity_from_headers({k.lower(): v for k, v in FORWARDED.items()})
    assert full["actor"] == "reviewer@example.com" and full["source"] == ident.SOURCE_DATABRICKS
    assert full["username"] == "reviewer" and full["user_id"] == "1234567890"
    assert ident.identity_from_headers({"x-forwarded-preferred-username": "r"})["actor"] == "r"
    assert ident.identity_from_headers({"x-forwarded-user": "99"})["actor"] == "99"


def test_local_mode_identity_is_the_os_user_and_says_so(monkeypatch):
    monkeypatch.setattr(ident, "IS_DATABRICKS_APP", False)
    monkeypatch.setenv("STTM_LOCAL_USER", "arjun-dev")
    who = ident.resolve_identity(None)
    assert who == {"actor": "arjun-dev", "source": ident.SOURCE_LOCAL,
                   "email": None, "username": "arjun-dev", "user_id": None}


def test_databricks_mode_refuses_an_identity_less_request(monkeypatch):
    monkeypatch.setattr(ident, "IS_DATABRICKS_APP", True)
    monkeypatch.setattr(ident, "REQUIRE_IDENTITY", True)
    fake_request = SimpleNamespace(headers={})
    with pytest.raises(fastapi.HTTPException) as exc:
        ident.resolve_identity(fake_request)
    assert exc.value.status_code == 401
    assert "x-forwarded-email" in exc.value.detail
    # the escape hatch records `unknown` instead of refusing — never silently an OS user
    monkeypatch.setattr(ident, "REQUIRE_IDENTITY", False)
    who = ident.resolve_identity(fake_request)
    assert who["actor"] == ident.ACTOR_UNKNOWN and who["source"] == ident.SOURCE_UNKNOWN


def test_databricks_mode_reads_the_forwarded_identity(monkeypatch):
    monkeypatch.setattr(ident, "IS_DATABRICKS_APP", True)
    who = ident.resolve_identity(SimpleNamespace(headers={k.lower(): v for k, v in FORWARDED.items()}))
    assert who["actor"] == "reviewer@example.com"


# --------------------------------------------------------------------------- #
# audit store
# --------------------------------------------------------------------------- #

def test_event_shape_and_reserved_fields():
    who = {"actor": "a@b", "source": "databricks_apps"}
    e = audit.build_event("run.started", who, run_id="demo_1", doc_id="d")
    assert {"event_id", "ts", "kind", "actor", "actor_source", "app_mode", "run_id", "doc_id"} <= set(e)
    assert e["actor"] == "a@b" and e["kind"] == "run.started"
    with pytest.raises(ValueError):
        audit.build_event("not.a.kind", who)
    with pytest.raises(ValueError):
        audit.build_event("run.started", who, actor="someone else")


def test_local_store_is_one_file_per_event_newest_first_and_filterable():
    who = {"actor": "x", "source": "local"}
    audit.record("run.started", who, run_id="r1")
    audit.record("workbook.downloaded", who, set_id="s", doc_id="d", sha256="0" * 64, size_bytes=1)
    audit.record("run.finished", who, run_id="r1", status="done")
    files = sorted(audit.local_events_dir().glob("*.json"))
    assert len(files) == 3 and all(f.suffix == ".json" for f in files)
    assert not list(audit.local_events_dir().glob("*.part"))
    kinds = [e["kind"] for e in audit.list_events()]
    assert kinds[0] == "run.finished"          # newest first
    assert [e["kind"] for e in audit.list_events(kind="run.started")] == ["run.started"]
    assert audit.list_events(limit=1)[0]["kind"] == "run.finished"


class _AppendOnlyFiles:
    """Files API stand-in that enforces the audit contract: an event file is
    written once and never overwritten (overwrite=False, unique names)."""

    def __init__(self):
        self.tree: dict[str, bytes] = {}

    def upload(self, path, data, overwrite=False):
        assert overwrite is False, "audit events must never be written with overwrite=True"
        assert path not in self.tree, f"audit event {path} written twice"
        self.tree[path] = bytes(data)


def test_databricks_mode_writes_the_volume_first_and_fails_closed(monkeypatch):
    monkeypatch.setattr(audit, "IS_DATABRICKS_APP", True)
    files = _AppendOnlyFiles()
    monkeypatch.setattr(jr, "_workspace_client", lambda: SimpleNamespace(files=files))
    e = audit.record("workbook.downloaded", {"actor": "a@b", "source": "databricks_apps"},
                     set_id="s", doc_id="d", sha256="f" * 64, size_bytes=3)
    dest = next(iter(files.tree))
    assert dest.startswith(audit.volume_events_dir() + "/") and dest.endswith(".json")
    assert json.loads(files.tree[dest])["event_id"] == e["event_id"]
    # and the local mirror exists too
    assert len(list(audit.local_events_dir().glob("*.json"))) == 1

    class _Broken:
        def upload(self, *a, **k):
            raise PermissionError("no WRITE VOLUME")

    monkeypatch.setattr(jr, "_workspace_client", lambda: SimpleNamespace(files=_Broken()))
    with pytest.raises(audit.AuditWriteError) as exc:
        audit.record("run.started", {"actor": "a@b", "source": "databricks_apps"}, run_id="r")
    assert "WRITE VOLUME" in str(exc.value) and audit.AUDIT_VOLUME in str(exc.value)
    # nothing was mirrored for the refused event
    assert len(list(audit.local_events_dir().glob("*.json"))) == 1


def test_sha256_of_matches_hashlib(tmp_path):
    import hashlib
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc" * 1000)
    assert audit.sha256_of(p) == hashlib.sha256(b"abc" * 1000).hexdigest()


# --------------------------------------------------------------------------- #
# runs: who started it, over which bytes, and the outcome
# --------------------------------------------------------------------------- #

def test_run_records_actor_hash_manifest_and_finish(client, live_dirs, monkeypatch):
    procs = []

    class _Capture(_FakeProc):
        def __init__(self, cmd, **kwargs):
            super().__init__(cmd, **kwargs)
            procs.append(self)

    monkeypatch.setattr(demo.subprocess, "Popen", _Capture)
    res = client.post("/api/demo/runs", json={"frd": str(live_dirs / "frd_raw" / "demo_frd.docx")},
                      headers=FORWARDED)
    assert res.status_code == 201, res.text
    snap = res.json()
    assert snap["triggered_by"] == "reviewer@example.com"
    assert snap["frd_sha256"] == audit.sha256_of(live_dirs / "frd_raw" / "demo_frd.docx")

    run = demo.get_run(snap["id"])
    final = _wait_terminal(run)
    assert final["status"] == demo.STATUS_DONE

    started = _events("run.started")
    finished = _events("run.finished")
    assert len(started) == 1 and len(finished) == 1
    assert started[0]["actor"] == "reviewer@example.com"
    assert started[0]["actor_source"] == ident.SOURCE_DATABRICKS
    assert started[0]["frd_sha256"] == snap["frd_sha256"]
    assert started[0]["run_id"] == finished[0]["run_id"] == snap["id"]
    assert finished[0]["status"] == "done" and finished[0]["error"] is None

    # the manifest travels with the artifact set
    manifest = json.loads((live_dirs / snap["artifact_set"] / "run_manifest.json").read_text())
    assert manifest["triggered_by"] == "reviewer@example.com"
    assert manifest["frd_sha256"] == snap["frd_sha256"]
    assert manifest["status"] == "done" and manifest["app_mode"] == demo.APP_MODE

    # the notebooks received the same provenance through the env
    assert procs and procs[0].captured_env["TRIGGERED_BY"] == "reviewer@example.com"
    assert procs[0].captured_env["RUN_LABEL"] == snap["id"]


def test_run_without_forwarded_identity_is_the_local_user_in_local_mode(client, live_dirs, monkeypatch):
    monkeypatch.setattr(demo.subprocess, "Popen", _FakeProc)
    monkeypatch.setenv("STTM_LOCAL_USER", "dev-box")
    res = client.post("/api/demo/runs", json={"frd": str(live_dirs / "frd_raw" / "demo_frd.docx")})
    assert res.status_code == 201
    assert res.json()["triggered_by"] == "dev-box"
    assert _events("run.started")[0]["actor_source"] == ident.SOURCE_LOCAL


def test_databricks_mode_run_is_refused_without_identity(client, live_dirs, monkeypatch):
    monkeypatch.setattr(ident, "IS_DATABRICKS_APP", True)
    res = client.post("/api/demo/runs", json={"frd": str(live_dirs / "frd_raw" / "demo_frd.docx")})
    assert res.status_code == 401
    assert demo._active_run_id is None and _events() == []


def test_a_failed_audit_write_blocks_the_run(client, live_dirs, monkeypatch):
    def boom(*a, **k):
        raise audit.AuditWriteError("volume refused")
    monkeypatch.setattr(audit, "record", boom)
    res = client.post("/api/demo/runs", json={"frd": str(live_dirs / "frd_raw" / "demo_frd.docx")})
    assert res.status_code == 502 and "volume refused" in res.json()["detail"]
    assert demo._active_run_id is None and demo._runs == {}


def test_databricks_job_run_carries_triggered_by_and_job_run_id(live_dirs, monkeypatch):
    monkeypatch.setattr(demo, "IS_DATABRICKS_APP", True)
    jobs = FakeJobs(runs=[SimpleNamespace(run_page_url="https://ws/run/42")])
    files = FakeFiles()
    w = _w(jobs=jobs, files=files)
    monkeypatch.setattr(jr, "_workspace_client", lambda: w)
    monkeypatch.setattr(jr, "resolve_job_id", lambda w: 7)
    monkeypatch.setattr(jr, "stage_frd", lambda w, s, p: None)
    monkeypatch.setattr(jr, "poll_job_run", lambda w, run, rid: None)

    def fake_download(w, suffix, dest):
        (dest / "rendered").mkdir(parents=True)
        return 1
    monkeypatch.setattr(jr, "download_run_artifacts", fake_download)

    run = demo.start_run(str(live_dirs / "frd_raw" / "demo_frd.docx"),
                         identity={"actor": "reviewer@example.com", "source": "databricks_apps"})
    snap = _wait_terminal(run)
    assert snap["status"] == demo.STATUS_DONE and snap["job_run_id"] == 42
    params = jobs.run_now_calls[0]["job_parameters"]
    assert params["triggered_by"] == "reviewer@example.com"
    assert params["run_label"] == run.suffix
    # the manifest was uploaded next to the artifacts in the UC out dir
    dest = f"{jr.out_dir_for(run.suffix)}/run_manifest.json"
    manifest = json.loads(files.tree[dest])
    assert manifest["job_run_id"] == 42 and manifest["triggered_by"] == "reviewer@example.com"
    assert _events("run.finished")[0]["job_run_id"] == 42


def test_job_parameter_sets_declare_the_provenance_knobs():
    params = jr.job_parameters("demo_x")
    assert params["triggered_by"] == "unknown" and params["run_label"] == "demo_x"
    render = jr.render_job_parameters("demo_x")
    assert {"triggered_by", "run_label"} <= set(render)
    jobs = FakeJobs()
    jr.start_job_run(_w(jobs=jobs), 1, "demo_x", triggered_by="a@b")
    assert jobs.run_now_calls[-1]["job_parameters"]["triggered_by"] == "a@b"
    jr.start_render_job(_w(jobs=jobs), 2, "demo_x", triggered_by="a@b")
    p = jobs.run_now_calls[-1]["job_parameters"]
    assert p["triggered_by"] == "a@b" and p["run_label"] == "demo_x:rerender"
    # and the Jobs API receives ONLY declared parameters for the render job
    assert set(p) == set(jr.RENDER_JOB_PARAMETERS)


# --------------------------------------------------------------------------- #
# human-in-the-loop: resolutions are attributed; re-renders and the hand-off
# are recorded
# --------------------------------------------------------------------------- #

def _gated_set(root: Path, set_id="sttm_out_demo_20260822_180000", doc_id="syn_doc") -> Path:
    d = root / set_id
    (d / "extractions").mkdir(parents=True)
    (d / "contracts").mkdir()
    (d / "rendered").mkdir()
    (d / "extractions" / f"{doc_id}.json").write_text("{}")
    contract = {
        "doc_id": doc_id, "status": "PASS_WITH_FLAGS", "feeds": [],
        "_provenance": {
            "ambiguities": [{
                "id": "amb-1", "kind": "attribution", "has_candidates": True,
                "candidates": ["FEED_A", "FEED_B"], "text": "rule X names no feed",
                "context": {},
            }],
            "grounding": {"advisory_flagged": []},
        },
    }
    (d / "contracts" / f"{doc_id}.contract.json").write_text(json.dumps(contract))
    (d / "rendered" / f"{doc_id}.sttm.xlsx").write_bytes(b"PK-fake-xlsx")
    return d


def test_resolution_is_attributed_to_the_forwarded_identity(client, live_dirs):
    d = _gated_set(live_dirs)
    url = "/api/demo/artifacts/sttm_out_demo_20260822_180000/resolutions?doc=syn_doc"
    r = client.post(url, json={"ambiguity_id": "amb-1", "kind": "attribution",
                               "resolution_type": "candidate_pick", "chosen_candidate": "FEED_A"},
                    headers=FORWARDED)
    assert r.status_code == 200, r.text
    saved = json.loads((d / "contracts" / "syn_doc.contract.json").read_text())
    assert saved["_provenance"]["human_resolutions"][0]["resolved_by"] == "reviewer@example.com"
    ev = _events("resolution.recorded")
    assert len(ev) == 1 and ev[0]["actor"] == "reviewer@example.com"
    assert ev[0]["ambiguity_id"] == "amb-1" and ev[0]["chosen_candidate"] == "FEED_A"
    # a refused submission records nothing
    client.post(url, json={"ambiguity_id": "amb-1", "kind": "attribution",
                           "resolution_type": "candidate_pick", "chosen_candidate": "FEED_Z"},
                headers=FORWARDED)
    assert len(_events("resolution.recorded")) == 1


def test_workbook_download_is_the_recorded_hand_off(client, live_dirs):
    d = _gated_set(live_dirs)
    r = client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/workbook?doc=syn_doc",
                   headers=FORWARDED)
    assert r.status_code == 200 and r.content == b"PK-fake-xlsx"
    ev = _events("workbook.downloaded")
    assert len(ev) == 1
    assert ev[0]["actor"] == "reviewer@example.com"
    assert ev[0]["sha256"] == audit.sha256_of(d / "rendered" / "syn_doc.sttm.xlsx")
    assert ev[0]["size_bytes"] == len(b"PK-fake-xlsx")
    assert ev[0]["set_id"] == "sttm_out_demo_20260822_180000" and ev[0]["doc_id"] == "syn_doc"
    # a 404 download records nothing
    client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/workbook?doc=other",
               headers=FORWARDED)
    assert len(_events("workbook.downloaded")) == 1


def test_rerender_is_recorded_start_and_finish_with_actor(client, live_dirs, monkeypatch):
    _gated_set(live_dirs)
    envs = []

    class _Capture(_FakeProc):
        def __init__(self, cmd, **kwargs):
            super().__init__(cmd, **kwargs)
            envs.append(self.captured_env)

    monkeypatch.setattr(demo.subprocess, "Popen", _Capture)
    r = client.post("/api/demo/artifacts/sttm_out_demo_20260822_180000/rerender?doc=syn_doc",
                    headers=FORWARDED)
    assert r.status_code == 202, r.text
    deadline = time.monotonic() + 5
    while demo._rerender_state("sttm_out_demo_20260822_180000")["state"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    deadline = time.monotonic() + 5
    while not _events("rerender.finished"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert _events("rerender.started")[0]["actor"] == "reviewer@example.com"
    fin = _events("rerender.finished")[0]
    assert fin["status"] == "done" and fin["actor"] == "reviewer@example.com"
    assert envs[0]["TRIGGERED_BY"] == "reviewer@example.com"
    assert envs[0]["RUN_LABEL"].endswith(":rerender")


def test_upload_is_recorded_with_its_hash(client, live_dirs):
    r = client.post("/api/demo/uploads",
                    files={"file": ("My FRD.docx", b"docx-bytes", "application/octet-stream")},
                    headers=FORWARDED)
    assert r.status_code == 200, r.text
    ev = _events("upload.received")
    assert ev[0]["actor"] == "reviewer@example.com" and ev[0]["file"] == "My_FRD.docx"
    import hashlib
    assert ev[0]["sha256"] == hashlib.sha256(b"docx-bytes").hexdigest()


def test_audit_endpoint_lists_newest_first_and_is_identity_gated(client, live_dirs, monkeypatch):
    _gated_set(live_dirs)
    client.get("/api/demo/artifacts/sttm_out_demo_20260822_180000/workbook?doc=syn_doc", headers=FORWARDED)
    client.post("/api/demo/artifacts/sttm_out_demo_20260822_180000/resolutions?doc=syn_doc",
                json={"ambiguity_id": "amb-1", "kind": "attribution",
                      "resolution_type": "candidate_pick", "chosen_candidate": "FEED_B"},
                headers=FORWARDED)
    r = client.get("/api/demo/audit", headers=FORWARDED)
    assert r.status_code == 200
    body = r.json()
    assert body["n_events"] == 2
    assert [e["kind"] for e in body["events"]] == ["resolution.recorded", "workbook.downloaded"]
    assert set(body["kinds"]) == set(audit.KINDS)
    r = client.get("/api/demo/audit?kind=workbook.downloaded", headers=FORWARDED)
    assert [e["kind"] for e in r.json()["events"]] == ["workbook.downloaded"]
    monkeypatch.setattr(ident, "IS_DATABRICKS_APP", True)
    assert client.get("/api/demo/audit").status_code == 401


# --------------------------------------------------------------------------- #
# notebooks: local append-only runs table
# --------------------------------------------------------------------------- #

def test_local_runs_table_appends_and_merges_schema(tmp_path):
    pytest.importorskip("deltalake")
    from frdsttm.local_tables import append_table, read_table
    append_table(tmp_path, "c", "s", "runs", [{"doc_id": "a", "status": "PASS", "n": 1}])
    append_table(tmp_path, "c", "s", "runs", [{"doc_id": "a", "status": "PASS", "n": 2,
                                               "triggered_by": "x@y"}])
    rows = read_table(tmp_path, "c", "s", "runs")
    assert [r["n"] for r in sorted(rows, key=lambda r: r["n"])] == [1, 2]
    assert {r.get("triggered_by") for r in rows} == {None, "x@y"}


# --------------------------------------------------------------------------- #
# notebooks/90_uc_governance.py — the plan is pure and reviewable offline
# --------------------------------------------------------------------------- #

def test_uc_governance_plan_covers_every_asset_and_reads_as_placeholders(monkeypatch, capsys):
    import runpy
    monkeypatch.setenv("CATALOG", "cat")
    monkeypatch.setenv("SCHEMA", "sch")
    monkeypatch.delenv("DATA_OWNER", raising=False)
    ns = runpy.run_path(str(Path(__file__).resolve().parent.parent / "notebooks" / "90_uc_governance.py"))
    plan = ns["plan"]()
    assert plan[0] == "CREATE VOLUME IF NOT EXISTS cat.sch.sttm_audit"
    text = "\n".join(plan)
    for vol in ("frd_raw", "sttm_reference", "sttm_out", "sttm_out_app", "demo_raw", "sttm_audit"):
        assert f"COMMENT ON VOLUME cat.sch.{vol} IS" in text
        assert f"ALTER VOLUME cat.sch.{vol} SET TAGS" in text
    for tbl in ("frd_documents", "frd_contracts", "frd_sttm_runs"):
        assert f"COMMENT ON TABLE cat.sch.{tbl} IS" in text
        assert f"ALTER TABLE cat.sch.{tbl} SET TAGS" in text
    assert "ALTER TABLE cat.sch.frd_documents ALTER COLUMN content SET TAGS" in text
    # unset owner reads as a placeholder, never a guess
    assert "'data_owner' = 'UNASSIGNED" in text
    # the audit trail is tagged as such; client documents as possibly-PHI
    assert "'audit_trail' = 'true'" in text and "'phi_possible' = 'true'" in text
    # run-scoped copies are recognised by prefix; foreign tables are not
    assert ns["base_of"]("frd_documents_demo_20260823_101500") == "frd_documents"
    assert ns["base_of"]("some_other_table") is None
    scoped = ns["plan"](tables=["frd_documents_demo_x", "unrelated"], volumes={"frd_raw"})
    assert any("cat.sch.frd_documents_demo_x" in s for s in scoped)
    assert not any("unrelated" in s for s in scoped)
    assert not any("sttm_out_app" in s for s in scoped)      # absent volume → skipped
    assert any("cat.sch.sttm_audit" in s and s.startswith("ALTER VOLUME") for s in scoped)  # always present
    out = capsys.readouterr().out
    assert "LOCAL MODE" in out and "nothing executed" in out
