"""Databricks-Jobs execution path for demo live runs
(review_app_react/backend/jobs_runner.py + demo.py's databricks mode).

Everything runs offline against SimpleNamespace stand-ins for the SDK's
Jobs/Files objects — no databricks.sdk import, no socket, no workspace.
The SDK itself is only imported inside jobs_runner._workspace_client, which
every test here stubs out.
"""

from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "review_app_react" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import demo  # noqa: E402
import jobs_runner as jr  # noqa: E402


# --------------------------------------------------------------------------- #
# stubs
# --------------------------------------------------------------------------- #

def _job(job_id: int, name: str):
    return SimpleNamespace(job_id=job_id, settings=SimpleNamespace(name=name))


def _task(key: str, life: str, result: str = "", run_id: int = 0, message: str = ""):
    return SimpleNamespace(
        task_key=key, run_id=run_id,
        state=SimpleNamespace(life_cycle_state=life, result_state=result,
                              state_message=message),
    )


def _job_run(life: str, result: str = "", tasks=()):
    return SimpleNamespace(
        state=SimpleNamespace(life_cycle_state=life, result_state=result),
        tasks=list(tasks), run_page_url="https://ws/#job/1/run/42",
    )


class FakeFiles:
    """Files API stand-in over an in-memory {path: bytes} tree. Paths in
    `empty_dirs` list successfully as directories with no contents — the
    "run reported SUCCESS but wrote nothing" shape."""

    def __init__(self, tree: dict[str, bytes] | None = None, empty_dirs=()):
        self.tree = dict(tree or {})
        self.empty_dirs = set(empty_dirs)
        self.created_dirs: list[str] = []

    def create_directory(self, path: str) -> None:
        self.created_dirs.append(path)

    def upload(self, path: str, data, overwrite: bool = False) -> None:
        assert overwrite, "uploads must pass overwrite=True (re-runs replace)"
        self.tree[path] = bytes(data)

    def list_directory_contents(self, path: str):
        prefix = path.rstrip("/") + "/"
        seen_dirs, entries = set(), []
        for p in sorted(self.tree):
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix):]
            if "/" in rest:  # deeper: report the immediate subdirectory once
                sub = prefix + rest.split("/", 1)[0]
                if sub not in seen_dirs:
                    seen_dirs.add(sub)
                    entries.append(SimpleNamespace(path=sub, is_directory=True))
            else:
                entries.append(SimpleNamespace(path=p, is_directory=False))
        if not entries and path.rstrip("/") not in self.empty_dirs:
            raise FileNotFoundError(path)
        return entries

    def download(self, path: str):
        return SimpleNamespace(contents=io.BytesIO(self.tree[path]))


class FakeJobs:
    def __init__(self, jobs=(), runs=(), outputs=None):
        self.jobs = list(jobs)
        self.runs = list(runs)          # scripted get_run responses; last repeats
        self.outputs = outputs or {}    # task run_id -> error text
        self.run_now_calls: list[dict] = []

    def list(self, name=None):
        return [j for j in self.jobs if name is None or j.settings.name == name]

    def run_now(self, job_id, job_parameters):
        self.run_now_calls.append({"job_id": job_id, "job_parameters": job_parameters})
        return SimpleNamespace(run_id=42)

    def get_run(self, run_id):
        return self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]

    def get_run_output(self, run_id):
        return SimpleNamespace(error=self.outputs.get(run_id))


def _w(jobs=None, files=None):
    return SimpleNamespace(jobs=jobs or FakeJobs(), files=files or FakeFiles())


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(jr, "POLL_SECONDS", 0.0)


# --------------------------------------------------------------------------- #
# job resolution — never send a run to a guessed job
# --------------------------------------------------------------------------- #

def test_resolve_job_id_env_id_wins(monkeypatch):
    monkeypatch.setenv(jr.JOB_ID_ENV, "123")
    assert jr.resolve_job_id(_w()) == 123


def test_resolve_job_id_non_numeric_env_id_raises(monkeypatch):
    monkeypatch.setenv(jr.JOB_ID_ENV, "not-a-number")
    with pytest.raises(jr.JobRunnerError, match="numeric"):
        jr.resolve_job_id(_w())


def test_resolve_job_id_exact_single_match(monkeypatch):
    monkeypatch.delenv(jr.JOB_ID_ENV, raising=False)
    monkeypatch.setattr(jr, "JOB_NAME", "frd_sttm_pipeline")
    w = _w(jobs=FakeJobs(jobs=[_job(7, "frd_sttm_pipeline")]))
    assert jr.resolve_job_id(w) == 7


def test_resolve_job_id_zero_matches_names_both_remedies(monkeypatch):
    monkeypatch.delenv(jr.JOB_ID_ENV, raising=False)
    monkeypatch.setattr(jr, "JOB_NAME", "frd_sttm_pipeline")
    with pytest.raises(jr.JobRunnerError) as exc:
        jr.resolve_job_id(_w(jobs=FakeJobs(jobs=[])))
    # The dev-mode bundle prefix is the likely cause; the error must teach it.
    assert "dev" in str(exc.value) and jr.JOB_ID_ENV in str(exc.value)


def test_resolve_job_id_multiple_matches_refuses_to_guess(monkeypatch):
    monkeypatch.delenv(jr.JOB_ID_ENV, raising=False)
    monkeypatch.setattr(jr, "JOB_NAME", "frd_sttm_pipeline")
    w = _w(jobs=FakeJobs(jobs=[_job(1, "frd_sttm_pipeline"), _job(2, "frd_sttm_pipeline")]))
    with pytest.raises(jr.JobRunnerError, match="refusing to guess"):
        jr.resolve_job_id(w)


# --------------------------------------------------------------------------- #
# run insulation — the volume/table twin of the local env-var scheme
# --------------------------------------------------------------------------- #

def test_job_parameters_are_run_scoped_and_skip_fetch():
    params = jr.job_parameters("demo_20260824_101500")
    assert params["raw_volume"] == f"{jr.RAW_STAGING_VOLUME}/demo_20260824_101500"
    assert params["out_volume"] == f"{jr.APP_OUT_VOLUME}/demo_20260824_101500"
    assert params["docs_table"] == "frd_documents_demo_20260824_101500"
    assert params["contracts_table"] == "frd_contracts_demo_20260824_101500"
    assert params["runs_table"] == "frd_sttm_runs_demo_20260824_101500"
    assert params["sharepoint_fetch_mode"] == "skip"
    # Never the curated names: insulation is the point.
    assert "frd_documents" != params["docs_table"]


def test_job_parameters_sanitize_table_names_for_uniquified_suffixes():
    """The collision uniquifier appends '-N', which catalog.schema.table
    identifiers reject — table names must swap it out, volume paths keep it."""
    params = jr.job_parameters("demo_20260824_101500-2")
    assert params["docs_table"] == "frd_documents_demo_20260824_101500_2"
    assert params["raw_volume"].endswith("/demo_20260824_101500-2")


def test_stage_frd_uploads_exactly_the_chosen_document(tmp_path):
    frd = tmp_path / "client frd.docx"
    frd.write_bytes(b"DOCX")
    files = FakeFiles()
    dest = jr.stage_frd(_w(files=files), "demo_x", frd)
    assert dest == f"{jr.raw_dir_for('demo_x')}/client frd.docx"
    assert files.tree[dest] == b"DOCX"
    assert files.created_dirs == [jr.raw_dir_for("demo_x")]


# --------------------------------------------------------------------------- #
# polling — task states mirror onto stages; failures carry task detail
# --------------------------------------------------------------------------- #

def _fresh_run(tmp_path) -> demo.Run:
    return demo.Run("doc", tmp_path / "doc.docx", "demo_x", tmp_path / "run.log",
                    stages=jr.JOB_STAGES)


def test_poll_maps_task_states_and_returns_on_success(tmp_path):
    run = _fresh_run(tmp_path)
    runs = [
        _job_run("RUNNING", tasks=[_task("ingest", "RUNNING"),
                                   _task("extract", "PENDING")]),
        _job_run("TERMINATED", "SUCCESS", tasks=[
            _task("sharepoint_fetch", "TERMINATED", "SUCCESS"),
            _task("ingest", "TERMINATED", "SUCCESS"),
            _task("extract", "TERMINATED", "SUCCESS"),
            _task("contract_build", "TERMINATED", "SUCCESS"),
            _task("render", "TERMINATED", "SUCCESS"),
        ]),
    ]
    jr.poll_job_run(_w(jobs=FakeJobs(runs=runs)), run, 42)
    assert all(s["status"] == "done" for s in run.snapshot()["stages"])


def test_poll_surfaces_the_failed_tasks_error_text(tmp_path):
    run = _fresh_run(tmp_path)
    runs = [_job_run("TERMINATED", "FAILED", tasks=[
        _task("ingest", "TERMINATED", "SUCCESS"),
        _task("extract", "TERMINATED", "FAILED", run_id=901),
    ])]
    jobs = FakeJobs(runs=runs, outputs={901: "AssertionError: no key in scope"})
    with pytest.raises(jr.JobRunnerError, match="extract: AssertionError: no key in scope"):
        jr.poll_job_run(_w(jobs=jobs), run, 42)
    stages = {s["id"]: s["status"] for s in run.snapshot()["stages"]}
    assert stages["extract"] == "failed" and stages["ingest"] == "done"


def test_poll_times_out_rather_than_spinning_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(jr, "RUN_TIMEOUT_SECONDS", 0)
    run = _fresh_run(tmp_path)
    with pytest.raises(jr.JobRunnerError, match="STTM_DEMO_JOB_TIMEOUT_SECONDS"):
        jr.poll_job_run(_w(jobs=FakeJobs(runs=[_job_run("RUNNING")])), run, 42)


# --------------------------------------------------------------------------- #
# artifact mirror + rehydration — UC is durable, the container is a cache
# --------------------------------------------------------------------------- #

def _remote_tree(suffix: str) -> dict[str, bytes]:
    base = jr.out_dir_for(suffix)
    return {
        f"{base}/extractions/doc.json": b"{}",
        f"{base}/contracts/doc.contract.v2.json": b"{}",
        f"{base}/rendered/doc.sttm.xlsx": b"XLSX",
        f"{base}/reports/doc.phase5.md": b"# report",
    }


def test_download_run_artifacts_mirrors_the_tree(tmp_path):
    files = FakeFiles(_remote_tree("demo_x"))
    n = jr.download_run_artifacts(_w(files=files), "demo_x", tmp_path / "sttm_out_demo_x")
    assert n == 4
    assert (tmp_path / "sttm_out_demo_x" / "rendered" / "doc.sttm.xlsx").read_bytes() == b"XLSX"
    assert (tmp_path / "sttm_out_demo_x" / "reports" / "doc.phase5.md").is_file()
    assert not list((tmp_path / "sttm_out_demo_x").rglob("*.part"))


def test_download_zero_artifacts_is_an_error_not_an_empty_result(tmp_path):
    """A run that reports SUCCESS but wrote nothing must raise — an empty
    result presented as a finished run is the silent-failure shape this
    repo exists to prevent."""
    files = FakeFiles(empty_dirs=[jr.out_dir_for("demo_x")])
    with pytest.raises(jr.JobRunnerError, match="no\\s+artifacts|holds no"):
        jr.download_run_artifacts(_w(files=files), "demo_x", tmp_path / "sttm_out_demo_x")
    assert not (tmp_path / "sttm_out_demo_x").exists()


def test_rehydrate_mirrors_only_missing_matching_sets(tmp_path, monkeypatch):
    tree = {**_remote_tree("demo_new"), **_remote_tree("live_e2e_old")}
    root = f"{jr.volume_root()}/{jr.APP_OUT_VOLUME}"
    tree[f"{root}/not_a_run_set/file.txt"] = b"ignore me"
    files = FakeFiles(tree)
    monkeypatch.setattr(jr, "_workspace_client", lambda: _w(files=files))
    (tmp_path / "sttm_out_live_e2e_old").mkdir()  # already present locally

    fetched = jr.rehydrate_artifact_sets(tmp_path)

    assert fetched == ["demo_new"]
    assert (tmp_path / "sttm_out_demo_new" / "extractions" / "doc.json").is_file()
    assert not (tmp_path / "sttm_out_not_a_run_set").exists()
    assert not any((tmp_path / "sttm_out_live_e2e_old").iterdir())  # untouched


def test_rehydrate_unreadable_volume_raises_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(jr, "_workspace_client", lambda: _w(files=FakeFiles()))
    with pytest.raises(jr.JobRunnerError, match="service principal"):
        jr.rehydrate_artifact_sets(tmp_path)


# --------------------------------------------------------------------------- #
# demo.start_run in databricks mode — lifecycle over the jobs path
# --------------------------------------------------------------------------- #

@pytest.fixture()
def databricks_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "IS_DATABRICKS_APP", True)
    monkeypatch.setattr(demo, "LOCAL_ROOT", tmp_path / "fixtures")
    monkeypatch.setattr(demo, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(demo, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(demo, "_runs", {})
    monkeypatch.setattr(demo, "_active_run_id", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    demo.UPLOADS_DIR.mkdir(parents=True)
    frd = demo.UPLOADS_DIR / "client_frd.docx"
    frd.write_bytes(b"DOCX")
    return frd


def _wait_terminal(run, timeout=5.0):
    deadline = time.monotonic() + timeout
    while run.snapshot()["status"] == demo.STATUS_RUNNING:
        assert time.monotonic() < deadline, "run never reached a terminal state"
        time.sleep(0.01)
    return run.snapshot()


def test_databricks_run_completes_without_a_local_api_key(databricks_mode, monkeypatch):
    frd = databricks_mode
    staged, polled = [], []
    monkeypatch.setattr(jr, "_workspace_client", lambda: SimpleNamespace(
        jobs=SimpleNamespace(get_run=lambda rid: SimpleNamespace(run_page_url="https://ws/run/42"))))
    monkeypatch.setattr(jr, "resolve_job_id", lambda w: 7)
    monkeypatch.setattr(jr, "stage_frd", lambda w, s, p: staged.append((s, p.name)))
    monkeypatch.setattr(jr, "start_job_run", lambda w, jid, s: 42)
    monkeypatch.setattr(jr, "poll_job_run", lambda w, run, rid: polled.append(rid))

    def fake_download(w, suffix, dest):
        (dest / "rendered").mkdir(parents=True)
        (dest / "rendered" / "client_frd.sttm.xlsx").write_bytes(b"XLSX")
        return 1

    monkeypatch.setattr(jr, "download_run_artifacts", fake_download)

    run = demo.start_run(str(frd))
    snap = _wait_terminal(run)

    assert snap["status"] == demo.STATUS_DONE
    assert snap["run_page_url"] == "https://ws/run/42"
    assert [s["id"] for s in snap["stages"]] == [k for k, _ in jr.JOB_STAGES]
    assert staged == [(run.suffix, "client_frd.docx")] and polled == [42]
    assert (demo.LOCAL_ROOT / run.artifact_set / "rendered" / "client_frd.sttm.xlsx").is_file()


def test_databricks_run_failure_surfaces_and_clears_the_active_slot(databricks_mode, monkeypatch):
    frd = databricks_mode
    monkeypatch.setattr(jr, "_workspace_client", lambda: SimpleNamespace(jobs=None))
    monkeypatch.setattr(jr, "resolve_job_id", lambda w: 7)
    monkeypatch.setattr(jr, "stage_frd", lambda w, s, p: None)
    monkeypatch.setattr(jr, "start_job_run", lambda w, jid, s: 42)

    def fail_poll(w, run, rid):
        raise jr.JobRunnerError("extract: AssertionError: no key in scope")

    monkeypatch.setattr(jr, "poll_job_run", fail_poll)

    run = demo.start_run(str(frd))
    snap = _wait_terminal(run)

    assert snap["status"] == demo.STATUS_FAILED
    assert "no key in scope" in snap["error"]
    assert demo._active_run_id is None  # a failed run must free the slot


def test_local_mode_still_requires_the_api_key(databricks_mode, monkeypatch):
    frd = databricks_mode
    monkeypatch.setattr(demo, "IS_DATABRICKS_APP", False)
    # The fixture cleared the env var; also stub the repo-.env fallback so
    # the test cannot pass or fail on a developer machine's local key.
    monkeypatch.setattr(demo, "_api_key_from_dotenv", lambda: None)
    with pytest.raises(demo.RunPreflightError, match="ANTHROPIC_API_KEY"):
        demo.start_run(str(frd))


# --------------------------------------------------------------------------- #
# 00_sharepoint_fetch's explicit skip gate (dual-mode script, run locally)
# --------------------------------------------------------------------------- #

def _run_00(env_extra: dict) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, str(REPO / "notebooks" / "00_sharepoint_fetch.py")],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=60,
    )


def test_fetch_skip_mode_exits_cleanly_without_config():
    """skip must exit 0 BEFORE load_config — the caller staged the document
    itself, so an unwired tenant is irrelevant to this run."""
    proc = _run_00({"SHAREPOINT_FETCH_MODE": "skip"})
    assert proc.returncode == 0, proc.stderr
    assert "explicitly skipped" in proc.stdout


def test_fetch_mode_rejects_unrecognized_values():
    proc = _run_00({"SHAREPOINT_FETCH_MODE": "maybe"})
    assert proc.returncode != 0
    assert "sharepoint_fetch_mode" in proc.stderr


# --------------------------------------------------------------------------- #
# the SYNC job (2026-08-22): trigger, parameters, volume mirror
# --------------------------------------------------------------------------- #

class _SyncJobs:
    def __init__(self, names):
        self._names = names
        self.run_now_calls = []

    def list(self, name=None):
        import types
        return [types.SimpleNamespace(job_id=i + 100, settings=types.SimpleNamespace(name=n))
                for i, n in enumerate(self._names) if name is None or n == name]

    def run_now(self, job_id, job_parameters):
        import types
        self.run_now_calls.append({"job_id": job_id, "job_parameters": job_parameters})
        return types.SimpleNamespace(run_id=777)


class _SyncFiles:
    """A flat fake volume tree: {dir: {name: bytes}}."""

    def __init__(self, tree):
        self.tree = tree

    def list_directory_contents(self, path):
        import types
        if path not in self.tree:
            raise RuntimeError(f"no such volume dir {path}")
        for name, payload in self.tree[path].items():
            yield types.SimpleNamespace(path=f"{path}/{name}", is_directory=payload is None)

    def download(self, path):
        import io, types
        d, _, name = path.rpartition("/")
        return types.SimpleNamespace(contents=io.BytesIO(self.tree[d][name]))


def _sync_w(names=("frd_sttm_sharepoint_sync",), tree=None):
    import types
    return types.SimpleNamespace(jobs=_SyncJobs(list(names)), files=_SyncFiles(tree or {}))


def test_resolve_sync_job_id_uses_its_own_name_and_env(monkeypatch):
    monkeypatch.delenv("STTM_SYNC_JOB_ID", raising=False)
    assert jr.resolve_sync_job_id(_sync_w()) == 100
    monkeypatch.setenv("STTM_SYNC_JOB_ID", "4242")
    assert jr.resolve_sync_job_id(_sync_w(names=())) == 4242


def test_resolve_sync_job_id_missing_names_the_sync_env_vars(monkeypatch):
    monkeypatch.delenv("STTM_SYNC_JOB_ID", raising=False)
    with pytest.raises(jr.JobRunnerError) as info:
        jr.resolve_sync_job_id(_sync_w(names=("frd_sttm_pipeline",)))
    assert "STTM_SYNC_JOB_NAME" in str(info.value) and "STTM_SYNC_JOB_ID" in str(info.value)


def test_start_sync_job_passes_only_the_mode():
    """The sync job runs against the REAL volumes by its own defaults; the
    app never re-points it."""
    w = _sync_w()
    assert jr.start_sync_job(w, 100, "sync") == 777
    assert w.jobs.run_now_calls == [{"job_id": 100, "job_parameters": {"sync_mode": "sync"}}]
    jr.start_sync_job(w, 100, "reindex")
    assert w.jobs.run_now_calls[-1]["job_parameters"] == {"sync_mode": "reindex"}
    with pytest.raises(jr.JobRunnerError):
        jr.sync_job_parameters("maybe")


def test_mirror_corpus_mirrors_both_volumes_and_drops_departed_files(tmp_path):
    root = jr.volume_root()
    tree = {
        f"{root}/{jr.RAW_VOLUME}": {"a.docx": b"A", "sub": None},
        f"{root}/{jr.REFERENCE_VOLUME}": {"a.sttm.xlsx": b"X", "corpus_index.json": b"{}"},
    }
    frd_local, ref_local = tmp_path / "frd_raw", tmp_path / "sttm_reference"
    frd_local.mkdir()
    (frd_local / "gone.docx").write_bytes(b"stale")   # no longer in the volume
    counts = jr.mirror_corpus(_sync_w(tree=tree), frd_local, ref_local)
    assert counts == {"frd": 1, "reference": 2}
    assert (frd_local / "a.docx").read_bytes() == b"A"
    assert not (frd_local / "gone.docx").exists()
    assert (ref_local / "corpus_index.json").is_file()
    assert list(frd_local.glob("*.part")) == []


def test_mirror_corpus_unreadable_volume_raises_an_actionable_error(tmp_path):
    with pytest.raises(jr.JobRunnerError) as info:
        jr.mirror_corpus(_sync_w(tree={}), tmp_path / "a", tmp_path / "b")
    assert "READ" in str(info.value)


def test_wait_for_run_returns_on_success_and_raises_on_failure(monkeypatch):
    import types
    monkeypatch.setattr(jr, "POLL_SECONDS", 0)

    class Jobs:
        def __init__(self, states):
            self.states = list(states)

        def get_run(self, run_id):
            life, result = self.states.pop(0)
            return types.SimpleNamespace(
                tasks=[types.SimpleNamespace(task_key="sharepoint_sync", run_id=1,
                                             state=types.SimpleNamespace(
                                                 life_cycle_state=life, result_state=result,
                                                 state_message="boom"))],
                state=types.SimpleNamespace(life_cycle_state=life, result_state=result))

        def get_run_output(self, run_id):
            return types.SimpleNamespace(error="notebook raised")

    jr.wait_for_run(types.SimpleNamespace(jobs=Jobs([("RUNNING", ""), ("TERMINATED", "SUCCESS")])), 1)
    with pytest.raises(jr.JobRunnerError, match="notebook raised"):
        jr.wait_for_run(types.SimpleNamespace(jobs=Jobs([("TERMINATED", "FAILED")])), 1)
