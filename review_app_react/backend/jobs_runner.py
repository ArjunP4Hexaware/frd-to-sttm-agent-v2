"""Databricks-Jobs execution path for the demo tab's live runs.

Why this module exists (decided 2026-08-21, supersedes the storage half of
the Apps-container problem): in the deployed App, a live run triggers the
REAL bundle-deployed `frd_sttm_pipeline` job via the Jobs API instead of
spawning `notebooks/0N_*.py` as subprocesses in the app container. The
notebooks then run as genuine workspace notebook tasks (`IS_DATABRICKS` is
True), so artifacts land natively in Unity Catalog — volumes for files,
`catalog.schema.table` for Delta — and survive an App restart by
construction. There is nothing to sync because nothing durable is ever
written to the container.

The subprocess path in demo.py remains the LOCAL-mode implementation (dev
laptops, offline tests). The mode switch is `STTM_APP_MODE`, the same knob
data_access.py already switches on. This module is the whole
databricks-mode implementation; demo.py owns the Run lifecycle and calls
the functions here from its worker thread.

Since 2026-08-22 the same module also drives the SharePoint SYNC job
(`frd_sttm_sharepoint_sync`, resources/frd_sttm_sync_job.yml): the Corpus
panel's "Sync now" triggers it, waits for it, then mirrors the frd_raw and
sttm_reference volumes down to the container (`mirror_corpus`) so the
picker lists exactly what Unity Catalog holds and runs can stage from a
local copy. The scheduled ticks of that job need no app involvement; the
app re-mirrors lazily whenever its local index is missing or older than
the volume's (corpus_routes._index_or_none).

Run insulation, mirrored from the local mode's env-var scheme: every
job parameter that names an output gets the run suffix —
`raw_volume=demo_raw/<suffix>`, `out_volume=<app-out-volume>/<suffix>`
(sub-DIRECTORIES of two pre-created volumes, not per-run volumes), and
suffixed `docs_table`/`contracts_table`/`runs_table` names — so a demo run
can never overwrite the curated volumes or tables. `sharepoint_fetch_mode`
is passed as an explicit "skip" because the app stages the reviewer's
chosen FRD into the run's raw directory itself.

Auth: `WorkspaceClient()` with no arguments — in an Apps container the
platform injects the app's service-principal credentials; the SDK picks
them up. The SDK is imported lazily inside `_workspace_client` so this
module imports cleanly in offline test runs.

Workspace prerequisites (verified on deploy day, not assumed):
  - the bundle job is deployed; its (possibly dev-prefixed) name matches
    STTM_DEMO_JOB_NAME, or STTM_DEMO_JOB_ID pins the id directly
  - volumes exist: the raw staging volume (default `demo_raw`) and the app
    out volume (default `sttm_out_app`), plus `sttm_reference` holding at
    least one reference workbook (stage 04 fails loudly without one)
  - secret scope `sttm_agent/anthropic_api_key` is populated
  - the app's service principal can run the job and read/write the volumes

Failure posture: fail loud, never guess. An unresolvable job name raises
naming both remedies; a failed run surfaces the failed task's error text;
a poll that outlives the timeout raises rather than spinning forever.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

# Same env names/defaults demo.py uses. Read here independently (not
# imported from demo) so this module has no import cycle with it.
CATALOG = os.environ.get("CATALOG", "arjun_workspace")
SCHEMA = os.environ.get("SCHEMA", "sttm_agent")
RAW_STAGING_VOLUME = os.environ.get("STTM_DEMO_RAW_STAGING_VOLUME", "demo_raw")

# The volume that holds one sub-directory per app-triggered run. Deliberately
# NOT `sttm_out` (the curated volume the review tab reads): app runs stay
# insulated, and promotion into the curated store remains a separate,
# deliberate act — same philosophy as the reviewer's own SharePoint upload.
APP_OUT_VOLUME = os.environ.get("STTM_DEMO_APP_OUT_VOLUME", "sttm_out_app")

# The two volumes the SYNC job maintains (same env names demo.py /
# corpus_routes.py use for their local mirrors).
RAW_VOLUME = os.environ.get("STTM_DEMO_PRELOADED_VOLUME", "frd_raw")
REFERENCE_VOLUME = os.environ.get("STTM_REFERENCE_VOLUME", "sttm_reference")
# The vendor data dictionaries (2026-08-27) — `vdd_raw`, named to sit beside
# `frd_raw`: two raw source documents, two raw volumes. Its own volume and
# not the reference one, which is globbed for TEMPLATE workbooks.
DICT_VOLUME = os.environ.get("STTM_VDD_VOLUME", "vdd_raw")

# Job identity: an explicit numeric id wins; otherwise the name is resolved
# via the Jobs API and must match EXACTLY ONE job. Note that a bundle target
# with `mode: development` deploys the job under a prefixed name like
# "[dev <user>] frd_sttm_pipeline" — set STTM_DEMO_JOB_NAME to the name as
# it actually appears in the workspace.
JOB_ID_ENV = "STTM_DEMO_JOB_ID"
JOB_NAME = os.environ.get("STTM_DEMO_JOB_NAME", "frd_sttm_pipeline")
SYNC_JOB_ID_ENV = "STTM_SYNC_JOB_ID"
SYNC_JOB_NAME = os.environ.get("STTM_SYNC_JOB_NAME", "frd_sttm_sharepoint_sync")
# The render-only job (resources/frd_sttm_render_job.yml): stage 04 over an
# EXISTING run's suffix, used by the human-in-the-loop re-render — no
# re-extraction, nothing billed.
RENDER_JOB_ID_ENV = "STTM_RENDER_JOB_ID"
RENDER_JOB_NAME = os.environ.get("STTM_RENDER_JOB_NAME", "frd_sttm_render")

POLL_SECONDS = float(os.environ.get("STTM_DEMO_JOB_POLL_SECONDS", "5"))
RUN_TIMEOUT_SECONDS = int(os.environ.get("STTM_DEMO_JOB_TIMEOUT_SECONDS", "1800"))

# The five task_keys of frd_sttm_pipeline, in graph order — these are the
# databricks-mode stage ids the progress view renders. Kept next to the
# job-parameter builder because both mirror resources/frd_sttm_job.yml.
JOB_STAGES = (
    ("sharepoint_fetch", "SharePoint fetch (not required — document already staged)"),
    ("ingest", "Ingest the FRD"),
    ("extract", "Extract mappings (AI model call)"),
    ("contract_build", "Validate, ground & gate"),
    ("render", "Render the STTM workbook"),
)

# Remote run directories that qualify for rehydration: the suffix families
# demo.py's _SET_DIR_RE accepts, minus the `sttm_out_` prefix (remote dirs
# are named by bare suffix; the local mirror adds the prefix back).
_REMOTE_SET_RE = re.compile(r"^(demo|live_e2e)_[A-Za-z0-9_-]+$")


class JobRunnerError(RuntimeError):
    """A jobs-path failure with an operator-actionable message."""


def _workspace_client():
    """The one construction point, and the only place the SDK is imported."""
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _enum_str(value) -> str:
    """SDK state fields are enums (or None); compare as bare strings."""
    if value is None:
        return ""
    return getattr(value, "value", str(value))


def volume_root() -> str:
    return f"/Volumes/{CATALOG}/{SCHEMA}"


def raw_dir_for(suffix: str) -> str:
    return f"{volume_root()}/{RAW_STAGING_VOLUME}/{suffix}"


def out_dir_for(suffix: str) -> str:
    return f"{volume_root()}/{APP_OUT_VOLUME}/{suffix}"


def resolve_job_id(w, job_name: str | None = None, id_env: str | None = None,
                   name_env: str = "STTM_DEMO_JOB_NAME") -> int:
    """The pinned id env var if set; else exactly one Jobs-API name match.

    Defaults address the pipeline job; the sync job passes its own name /
    env names (see resolve_sync_job_id). Zero or multiple matches raise
    naming both remedies — a run must never be sent to a guessed job.
    """
    job_name = JOB_NAME if job_name is None else job_name
    id_env = JOB_ID_ENV if id_env is None else id_env
    explicit = os.environ.get(id_env, "").strip()
    if explicit:
        try:
            return int(explicit)
        except ValueError as exc:
            raise JobRunnerError(f"{id_env} must be a numeric job id, got {explicit!r}") from exc

    matches = [j for j in w.jobs.list(name=job_name)
               if getattr(getattr(j, "settings", None), "name", None) == job_name]
    if len(matches) == 1:
        return matches[0].job_id
    if not matches:
        raise JobRunnerError(
            f"No job named {job_name!r} in this workspace. Deploy the bundle "
            f"(databricks bundle deploy), then set {name_env} to the "
            f"job's name AS DEPLOYED (a dev-mode target prefixes it, e.g. "
            f"'[dev <user>] {job_name}') — or set {id_env} to the "
            f"numeric job id."
        )
    ids = ", ".join(str(j.job_id) for j in matches)
    raise JobRunnerError(
        f"{len(matches)} jobs named {job_name!r} (ids: {ids}). Set {id_env} "
        f"to the one this app should run — refusing to guess."
    )


def resolve_sync_job_id(w) -> int:
    return resolve_job_id(w, SYNC_JOB_NAME, SYNC_JOB_ID_ENV, "STTM_SYNC_JOB_NAME")


def resolve_render_job_id(w) -> int:
    return resolve_job_id(w, RENDER_JOB_NAME, RENDER_JOB_ID_ENV, "STTM_RENDER_JOB_NAME")


def job_parameters(suffix: str) -> dict[str, str]:
    """The insulated parameter set for one app-triggered run.

    Table names get the suffix with non-identifier characters replaced —
    the collision uniquifier can put a '-' in the suffix, which
    `catalog.schema.table` names reject.
    """
    table_suffix = re.sub(r"[^A-Za-z0-9_]", "_", suffix)
    return {
        "catalog": CATALOG,
        "schema": SCHEMA,
        "raw_volume": f"{RAW_STAGING_VOLUME}/{suffix}",
        "out_volume": f"{APP_OUT_VOLUME}/{suffix}",
        "docs_table": f"frd_documents_{table_suffix}",
        "contracts_table": f"frd_contracts_{table_suffix}",
        "runs_table": f"frd_sttm_runs_{table_suffix}",
        "sharepoint_fetch_mode": "skip",
        # Provenance (docs/AI_GOVERNANCE.md): who asked for this run and the
        # app's own run id, stamped by 04 on the runs table. Declared as
        # job parameters in resources/frd_sttm_job.yml; the caller overrides
        # triggered_by per request via start_job_run(..., triggered_by=).
        "triggered_by": "unknown",
        "run_label": suffix,
    }


def stage_frd(w, suffix: str, frd_path: Path) -> str:
    """Upload the reviewer's chosen FRD into the run's raw directory.

    01_frd_ingest parses its whole raw_volume, so the run-scoped directory
    must contain exactly this one document — the volume-side twin of the
    local mode's per-run demo_raw/<suffix> staging copy.
    """
    dest_dir = raw_dir_for(suffix)
    w.files.create_directory(dest_dir)
    dest = f"{dest_dir}/{frd_path.name}"
    w.files.upload(dest, frd_path.read_bytes(), overwrite=True)
    return dest


def start_job_run(w, job_id: int, suffix: str, triggered_by: str | None = None) -> int:
    """run_now with the insulated parameters; returns the run id."""
    params = job_parameters(suffix)
    if triggered_by:
        params["triggered_by"] = triggered_by
    return _run_now(w, job_id, params)


def sync_job_parameters(mode: str) -> dict[str, str]:
    """The sync job runs against the REAL volumes (its own defaults); the
    only knob the app passes is the mode — 'sync' or 'reindex', validated
    against the closed set the notebook itself enforces."""
    if mode not in ("sync", "reindex"):
        raise JobRunnerError(f"sync mode must be 'sync' or 'reindex', got {mode!r}")
    return {"sync_mode": mode}


def start_sync_job(w, job_id: int, mode: str = "sync") -> int:
    return _run_now(w, job_id, sync_job_parameters(mode))


RENDER_JOB_PARAMETERS = ("catalog", "schema", "contracts_table", "runs_table", "out_volume",
                         "triggered_by", "run_label")


def render_job_parameters(suffix: str) -> dict[str, str]:
    """The SAME insulated values as the pipeline run, restricted to the
    parameters the render-only job declares (the Jobs API rejects unknown
    job parameters)."""
    full = job_parameters(suffix)
    return {k: full[k] for k in RENDER_JOB_PARAMETERS}


def start_render_job(w, job_id: int, suffix: str, triggered_by: str | None = None) -> int:
    params = render_job_parameters(suffix)
    if triggered_by:
        params["triggered_by"] = triggered_by
    params["run_label"] = f"{suffix}:rerender"
    return _run_now(w, job_id, params)


def upload_run_manifest(w, suffix: str, payload: bytes) -> str:
    """Put the app-level run manifest (demo.run_manifest) next to the
    artifacts in the run's UC out dir, so the durable copy is
    self-describing without the app."""
    dest = f"{out_dir_for(suffix)}/run_manifest.json"
    w.files.upload(dest, payload, overwrite=True)
    return dest


def upload_contract(w, suffix: str, doc_id: str, payload: bytes) -> str:
    """Put the reviewer-edited v1 contract back into the run's UC out dir so
    the render job's 04 reads the resolutions from /Volumes."""
    dest = f"{out_dir_for(suffix)}/contracts/{doc_id}.contract.json"
    w.files.upload(dest, payload, overwrite=True)
    return dest


def _run_now(w, job_id: int, params: dict[str, str]) -> int:
    waiter = w.jobs.run_now(job_id=job_id, job_parameters=params)
    run_id = getattr(waiter, "run_id", None)
    if run_id is None:
        run_id = waiter.response.run_id
    return run_id


_PENDING_STATES = {"PENDING", "QUEUED", "BLOCKED", "WAITING_FOR_RETRY"}
_RUNNING_STATES = {"RUNNING", "TERMINATING"}


def _task_status(life: str, result: str) -> str:
    """Jobs-API task state → the Run.stages status vocabulary."""
    if life in _PENDING_STATES:
        return "pending"
    if life in _RUNNING_STATES:
        return "running"
    if life == "TERMINATED" and result == "SUCCESS":
        return "done"
    if life in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
        return "failed"
    return "pending"  # unknown/new lifecycle value: undecided, not failed


def _failed_task_detail(w, jr) -> str:
    """Best-effort error text from the failed task(s) — the message the
    operator actually needs, not just 'run failed'."""
    details = []
    for task in jr.tasks or []:
        state = getattr(task, "state", None)
        if _task_status(_enum_str(getattr(state, "life_cycle_state", None)),
                        _enum_str(getattr(state, "result_state", None))) != "failed":
            continue
        message = _enum_str(getattr(state, "state_message", None))
        try:
            out = w.jobs.get_run_output(task.run_id)
            message = getattr(out, "error", None) or message
        except Exception:  # noqa: BLE001 — the run error must surface even if output retrieval fails
            pass
        details.append(f"{task.task_key}: {message or 'failed (no error detail returned)'}")
    return "; ".join(details) or "job run failed (no failed-task detail available)"


def poll_job_run(w, run, run_id: int) -> None:
    """Poll the run to a terminal state, mirroring task states onto
    `run.stages` and emitting console events on every transition. Raises
    JobRunnerError on failure or timeout; returns on SUCCESS."""
    def on_tasks(jr):
        for task in jr.tasks or []:
            state = getattr(task, "state", None)
            run._set_stage(task.task_key, _task_status(
                _enum_str(getattr(state, "life_cycle_state", None)),
                _enum_str(getattr(state, "result_state", None)),
            ))

    wait_for_run(w, run_id, on_tasks=on_tasks)
    run._emit("console", f"job run {run_id} finished: SUCCESS")


def wait_for_run(w, run_id: int, on_tasks=None) -> None:
    """Poll any job run to a terminal state. `on_tasks(jr)` (optional) is
    called with each fresh run object so a caller can mirror task states.
    Raises JobRunnerError on failure or timeout; returns on SUCCESS."""
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while True:
        jr = w.jobs.get_run(run_id)
        if on_tasks is not None:
            on_tasks(jr)

        life = _enum_str(getattr(getattr(jr, "state", None), "life_cycle_state", None))
        result = _enum_str(getattr(getattr(jr, "state", None), "result_state", None))
        if life in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            if result == "SUCCESS":
                return
            raise JobRunnerError(_failed_task_detail(w, jr))

        if time.monotonic() > deadline:
            raise JobRunnerError(
                f"job run {run_id} still {life or 'PENDING'} after "
                f"{RUN_TIMEOUT_SECONDS}s — see the run page for its state. "
                f"Raise STTM_DEMO_JOB_TIMEOUT_SECONDS if runs legitimately "
                f"take longer."
            )
        time.sleep(POLL_SECONDS)


def _walk_files(w, root: str):
    """Yield (volume_path, relative_path) for every file under root."""
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in w.files.list_directory_contents(current):
            if entry.is_directory:
                stack.append(entry.path)
            else:
                yield entry.path, os.path.relpath(entry.path, root)


def download_run_artifacts(w, suffix: str, dest_dir: Path) -> int:
    """Mirror the run's UC out-directory into the local artifact-set dir.

    The UC volume is the durable store; this local copy only exists so the
    ENTIRE existing results machinery (load_results, workbook download,
    replay discovery) works unchanged on a directory shaped exactly like a
    local run's. Cheap: a full set is ~1 MB.
    """
    count = 0
    for volume_path, rel in _walk_files(w, out_dir_for(suffix)):
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = w.files.download(volume_path).contents.read()
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        count += 1
    if count == 0:
        raise JobRunnerError(
            f"job run reported SUCCESS but {out_dir_for(suffix)} holds no "
            f"artifacts — refusing to present an empty result as a finished run"
        )
    return count


def rehydrate_artifact_sets(local_root: Path) -> list[str]:
    """Mirror app-run artifact sets from the UC volume that are not yet on
    the container disk, so finished runs survive an App restart. Returns the
    set ids fetched. Raises JobRunnerError if the volume is unreadable —
    an unreadable store must never masquerade as an empty one."""
    w = _workspace_client()
    root = f"{volume_root()}/{APP_OUT_VOLUME}"
    try:
        entries = list(w.files.list_directory_contents(root))
    except Exception as exc:  # noqa: BLE001 — mapped to one operator-facing error
        raise JobRunnerError(
            f"Cannot list {root} ({type(exc).__name__}: {exc}). Create the "
            f"volume and grant the app's service principal READ/WRITE on it."
        ) from exc

    fetched = []
    for entry in entries:
        name = Path(entry.path).name
        if not entry.is_directory or not _REMOTE_SET_RE.match(name):
            continue
        dest = local_root / f"sttm_out_{name}"
        if dest.is_dir():
            continue
        download_run_artifacts(w, name, dest)
        fetched.append(name)
    return fetched


def mirror_volume_dir(w, remote_dir: str, local_dir: Path) -> int:
    """Mirror the FILES directly under one volume directory into `local_dir`
    (sub-directories are not descended — frd_raw and sttm_reference are
    flat by construction). Local files that no longer exist remotely are
    removed, so the local picker never lists a document the sync has
    already dropped. Returns the number of files mirrored."""
    entries = list(w.files.list_directory_contents(remote_dir))
    local_dir.mkdir(parents=True, exist_ok=True)
    remote_names = set()
    count = 0
    for entry in entries:
        if entry.is_directory:
            continue
        name = Path(entry.path).name
        remote_names.add(name)
        payload = w.files.download(entry.path).contents.read()
        target = local_dir / name
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        count += 1
    for stale in local_dir.iterdir():
        if stale.is_file() and stale.name not in remote_names:
            stale.unlink()
    return count


def mirror_corpus(w, frd_local: Path, reference_local: Path,
                  dictionary_local: Path | None = None) -> dict:
    """Bring the container's copies of frd_raw + sttm_reference (+ vdd_raw)
    in step with Unity Catalog (the sync job's output). Raises JobRunnerError
    if a REQUIRED volume is unreadable — an unreadable store must never
    masquerade as empty.

    The dictionary volume is the one exception, and deliberately so: it is
    NEW (2026-08-27) and will not exist in a workspace deployed before it.
    An absent vdd_raw must degrade to "no vendor dictionaries yet", which
    is the honest state and the one every FRD is in today — not break the
    picker for the two volumes that do exist. It is reported as
    ``dictionary: None`` so the difference stays visible rather than reading
    as an empty volume.
    """
    out = {}
    for key, remote, local in (("frd", f"{volume_root()}/{RAW_VOLUME}", frd_local),
                               ("reference", f"{volume_root()}/{REFERENCE_VOLUME}", reference_local)):
        try:
            out[key] = mirror_volume_dir(w, remote, local)
        except Exception as exc:  # noqa: BLE001 — mapped to one operator-facing error
            raise JobRunnerError(
                f"Cannot mirror {remote} ({type(exc).__name__}: {exc}). Create the "
                f"volume and grant the app's service principal READ on it."
            ) from exc
    if dictionary_local is not None:
        try:
            out["dictionary"] = mirror_volume_dir(
                w, f"{volume_root()}/{DICT_VOLUME}", Path(dictionary_local))
        except Exception:  # noqa: BLE001 — see the docstring: absent is legitimate
            out["dictionary"] = None
    return out
