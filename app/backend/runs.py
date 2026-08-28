"""Run lifecycle: start (extract), answer, render, read.

Databricks mode → the bundle job runs the pipeline; local mode → the same
functions in a thread. Either way the truth is `output_sttms/<run_id>/run.json`.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import settings
import storage
from frdsttm import completeness, pipeline

_lock = threading.Lock()
_active: dict[str, dict] = {}       # run_id -> {"phase", "job_run_id", "url", "error", "started_at"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def active(run_id: str) -> dict | None:
    with _lock:
        return dict(_active[run_id]) if run_id in _active else None


def _set(run_id: str, **f):
    with _lock:
        _active.setdefault(run_id, {}).update(f)


def _clear(run_id: str):
    with _lock:
        _active.pop(run_id, None)


def _job_or_local(run_id: str, task: str, doc_id: str, by: str, local_fn):
    def work():
        try:
            if settings.IS_DATABRICKS:
                job_run_id, url = None, None
                try:
                    job_run_id, url = None, None
                    import jobs
                    job_run_id, url = jobs.start(task, run_id=run_id, doc_id=doc_id, triggered_by=by)
                    _set(run_id, job_run_id=job_run_id, url=url)
                    jobs.wait(job_run_id)
                finally:
                    storage.pull_run(run_id)
            else:
                local_fn()
        except Exception as exc:  # noqa: BLE001 — recorded, never a stuck spinner
            _set(run_id, error=f"{type(exc).__name__}: {exc}")
        finally:
            _set(run_id, phase="done", finished_at=_now())
    threading.Thread(target=work, daemon=True).start()


def start(doc_id: str, by: str = "app") -> str:
    with _lock:
        if any(a.get("phase") == "running" for a in _active.values()):
            raise RuntimeError("a run is already in progress — one at a time")
    existing = {p.name for p in settings.PATHS.output.iterdir()} if settings.PATHS.output.is_dir() else set()
    existing |= set(storage.list_remote_runs())
    run_id = pipeline.new_run_id(existing)
    _set(run_id, phase="running", task="extract", doc_id=doc_id, started_at=_now(), error=None)
    _job_or_local(run_id, "extract", doc_id, by,
                  lambda: pipeline.run_extract(settings.PATHS, run_id, doc_id, provider=settings.PROVIDER,
                                               model=settings.MODEL, triggered_by=by))
    return run_id


def render(run_id: str, by: str = "app") -> None:
    run = load(run_id)
    if run.get("assessment", {}).get("blockers"):
        raise ValueError("this run cannot be rendered — see its blockers")
    a = active(run_id)
    if a and a.get("phase") == "running":
        raise RuntimeError("this run is busy")
    storage.push_run_file(run_id, pipeline.RUN_FILE)     # the answers travel with the run
    _set(run_id, phase="running", task="render", doc_id=run["doc_id"], started_at=_now(), error=None,
         job_run_id=None, url=None)
    _job_or_local(run_id, "render", run["doc_id"], by, lambda: pipeline.run_render(settings.PATHS, run_id))


def answer(run_id: str, question_id: str, value: str, by: str = "app") -> dict:
    a = active(run_id)
    if a and a.get("phase") == "running":
        raise RuntimeError("this run is busy")
    run = pipeline.answer(settings.PATHS, run_id, question_id, value, by=by)
    storage.push_run_file(run_id, pipeline.RUN_FILE)
    return run


def load(run_id: str) -> dict:
    if not (settings.PATHS.run_dir(run_id) / pipeline.RUN_FILE).is_file():
        storage.pull_run(run_id)
    return pipeline.load_run(settings.PATHS, run_id)


def view(run_id: str) -> dict:
    """run.json + the live phase + the rows the workbook will carry."""
    run = load(run_id)
    out = {k: v for k, v in run.items() if k not in ("traceback",)}
    out["live"] = active(run_id)
    preview = []
    if run.get("assessment") and run.get("vdd") and run["vdd"].get("file") and not run["assessment"]["blockers"]:
        try:
            from frdsttm.dictionary import parse_dictionary_workbook
            from frdsttm.render import preview_rows
            vdd_path = settings.PATHS.vdds / run["vdd"]["file"]
            if not vdd_path.is_file():
                storage.pull_documents()          # a fresh container has no mirror yet
            vdd = parse_dictionary_workbook(vdd_path)
            applied = run.get("applied") or {}
            spec = applied.get("extraction") or run["extraction"]
            sources = applied.get("sources") or run["assessment"]["sources"]
            preview = preview_rows(spec, sources, vdd, applied.get("pairing_override"), limit=60)
        except Exception as exc:  # noqa: BLE001 — a preview problem is not a run problem
            preview = [{"error": f"{type(exc).__name__}: {exc}"}]
    out["preview"] = preview
    out["summary"] = pipeline.summary(run)
    return out


def list_all() -> list[dict]:
    storage.pull_all_runs()
    rows = pipeline.list_runs(settings.PATHS)
    for r in rows:
        r["live"] = active(r["run_id"])
    return rows


STATUS_LABEL = {
    "extracting": "Reading the FRD and the dictionary",
    completeness.STATUS_READY: "Everything needed is present",
    completeness.STATUS_NEEDS_INPUT: "Needs your answers",
    completeness.STATUS_CANNOT: "Cannot generate",
    pipeline.STATUS_RENDERED: "STTM generated",
    pipeline.STATUS_FAILED: "Failed",
}
