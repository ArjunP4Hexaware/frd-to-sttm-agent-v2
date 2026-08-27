"""A bookmarked ?set=&doc= must work after an App restart (2026-08-27).

In databricks mode the container's artifact-set mirror is empty after a
restart until GET /api/demo/artifacts rehydrates EVERY set from the UC
volume (~1 min for 36). Until then a direct results / workbook / review
request for one set 404'd and the results screen was blank — the "results
are addressable" feature was broken for exactly the case it exists for.

`demo._check_set_id`, the common entry of every artifact route, now fetches
just the requested set on a miss. Pinned here: fetched once when absent,
never when present, never in local mode, and an unknown set still ends as
the caller's 404 rather than a 502.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

BACKEND = Path(__file__).resolve().parent.parent / "review_app_react" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import demo  # noqa: E402
import jobs_runner as jr  # noqa: E402


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(demo, "IS_DATABRICKS_APP", True)
    monkeypatch.setattr(jr, "_workspace_client", lambda: object())
    calls = []

    def fake_download(w, suffix, dest):
        calls.append((suffix, dest))
        if suffix == "demo_missing":
            raise jr.JobRunnerError("no artifacts")
        dest.mkdir(parents=True)
        return 1

    monkeypatch.setattr(jr, "download_run_artifacts", fake_download)
    return tmp_path, calls


def test_absent_set_is_fetched_once_by_its_suffix(dbx):
    root, calls = dbx
    path = demo._check_set_id("sttm_out_demo_20260827_180908")
    assert path == root / "sttm_out_demo_20260827_180908" and path.is_dir()
    assert calls == [("demo_20260827_180908", path)]


def test_present_set_is_not_fetched(dbx):
    root, calls = dbx
    (root / "sttm_out_demo_20260827_180908").mkdir()
    demo._check_set_id("sttm_out_demo_20260827_180908")
    assert calls == []


def test_unknown_set_falls_through_to_the_callers_404(dbx):
    root, calls = dbx
    path = demo._check_set_id("sttm_out_demo_missing")
    assert calls and not path.is_dir()
    with pytest.raises(FileNotFoundError):
        demo.load_results("sttm_out_demo_missing", "doc")


def test_local_mode_never_touches_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(demo, "IS_DATABRICKS_APP", False)
    monkeypatch.setattr(jr, "download_run_artifacts",
                        lambda *a: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert demo._check_set_id("sttm_out_demo_x") == tmp_path / "sttm_out_demo_x"
