"""The bundle job, through the Jobs API (databricks mode)."""

from __future__ import annotations

import time

import settings
import storage

POLL_SECONDS = 5
TIMEOUT_SECONDS = 1800


class JobError(RuntimeError):
    pass


def resolve_job_id() -> int:
    if settings.JOB_ID:
        return int(settings.JOB_ID)
    w = storage.client()
    matches = [j for j in w.jobs.list(name=settings.JOB_NAME)
               if getattr(getattr(j, "settings", None), "name", None) == settings.JOB_NAME]
    if len(matches) == 1:
        return matches[0].job_id
    raise JobError(
        f"{len(matches)} jobs named {settings.JOB_NAME!r}. Deploy the bundle and set STTM_JOB_ID "
        f"to the job id (a dev-mode deploy prefixes the name).")


def start(task: str, run_id: str = "", doc_id: str = "", triggered_by: str = "app",
          frd_file: str = "", vdd_file: str = "") -> tuple[int, str | None]:
    w = storage.client()
    params = {"task": task, "run_id": run_id, "doc_id": doc_id, "triggered_by": triggered_by,
              "provider": settings.PROVIDER, "model": settings.MODEL,
              "catalog": settings.CATALOG, "schema": settings.SCHEMA,
              # empty unless the reviewer uploaded the pair for this run
              "frd_file": frd_file, "vdd_file": vdd_file}
    waiter = w.jobs.run_now(job_id=resolve_job_id(), job_parameters=params)
    job_run_id = getattr(waiter, "run_id", None) or waiter.response.run_id
    try:
        url = w.jobs.get_run(job_run_id).run_page_url
    except Exception:  # noqa: BLE001
        url = None
    return job_run_id, url


def _s(v):
    return "" if v is None else getattr(v, "value", str(v))


def wait(job_run_id: int) -> None:
    w = storage.client()
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        jr = w.jobs.get_run(job_run_id)
        life = _s(getattr(jr.state, "life_cycle_state", None))
        result = _s(getattr(jr.state, "result_state", None))
        if life in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            if result == "SUCCESS":
                return
            detail = _s(getattr(jr.state, "state_message", None))
            for t in jr.tasks or []:
                try:
                    out = w.jobs.get_run_output(t.run_id)
                    detail = getattr(out, "error", None) or detail
                except Exception:  # noqa: BLE001
                    pass
            raise JobError(detail or "job run failed")
        if time.monotonic() > deadline:
            raise JobError(f"job run {job_run_id} still {life} after {TIMEOUT_SECONDS}s")
        time.sleep(POLL_SECONDS)
