"""Unity Catalog volumes ↔ the container's mirror (databricks mode only).

The App cannot mount a volume, so it reads and writes through the Files
API. Documents are pulled down before they are listed; a run directory is
pulled after the job finishes and pushed after the reviewer answers.
In local mode every function is a no-op — the folders ARE the data.
"""

from __future__ import annotations

from pathlib import Path

import settings

_w = None


def client():
    global _w
    if _w is None:
        from databricks.sdk import WorkspaceClient
        _w = WorkspaceClient()
    return _w


def _mirror_dir(remote: str, local: Path, recursive: bool = False) -> int:
    w = client()
    local.mkdir(parents=True, exist_ok=True)
    keep, n = set(), 0
    for entry in w.files.list_directory_contents(remote):
        name = Path(entry.path).name
        if entry.is_directory:
            if recursive:
                n += _mirror_dir(entry.path, local / name, True)
            continue
        keep.add(name)
        target = local / name
        # Always write: a rewritten corpus_index.json can keep its size to the byte
        # (only the timestamp changed), so size is not a freshness check.
        payload = w.files.download(entry.path).contents.read()
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        n += 1
    for stale in local.iterdir():
        if stale.is_file() and stale.name not in keep and not stale.name.endswith(".part"):
            stale.unlink()
    return n


_last_pull = 0.0
PULL_EVERY_SECONDS = 60.0


def pull_documents(force: bool = False) -> dict:
    """frds, vdds, reference_sttms (incl. corpus_index.json) → local mirror.
    At most once a minute unless forced — the picker calls this per request."""
    global _last_pull
    if not settings.IS_DATABRICKS:
        return {}
    import time
    if not force and time.monotonic() - _last_pull < PULL_EVERY_SECONDS:
        return {}
    out = {kind: _mirror_dir(settings.volume_path(kind), getattr(settings.PATHS, attr))
           for kind, attr in (("frds", "frds"), ("vdds", "vdds"), ("reference", "reference"))}
    _last_pull = time.monotonic()
    return out


def list_remote_runs() -> list[str]:
    if not settings.IS_DATABRICKS:
        return []
    try:
        entries = list(client().files.list_directory_contents(settings.volume_path("output")))
    except Exception as exc:  # noqa: BLE001
        if "not found" in str(exc).lower():
            return []
        raise
    return sorted((Path(e.path).name for e in entries
                   if e.is_directory and Path(e.path).name.startswith("run_")), reverse=True)


def pull_run(run_id: str) -> None:
    """Mirror one run directory; a run the job has not written yet is not an error."""
    if not settings.IS_DATABRICKS:
        return
    try:
        # recursive: an uploaded pair lives in <run_id>/inputs/ and the preview reads it
        _mirror_dir(f"{settings.volume_path('output')}/{run_id}", settings.PATHS.run_dir(run_id), True)
    except Exception as exc:  # noqa: BLE001 — absent remote dir → the caller's FileNotFoundError
        if "not found" in str(exc).lower() or type(exc).__name__ in ("NotFound", "ResourceDoesNotExist"):
            return
        raise


def pull_all_runs() -> None:
    for rid in list_remote_runs():
        if not (settings.PATHS.run_dir(rid) / "run.json").is_file():
            pull_run(rid)


def push_run_file(run_id: str, name: str) -> None:
    if not settings.IS_DATABRICKS:
        return
    payload = (settings.PATHS.run_dir(run_id) / name).read_bytes()
    client().files.upload(f"{settings.volume_path('output')}/{run_id}/{name}", payload, overwrite=True)


def push_run_input(run_id: str, name: str, payload: bytes) -> bytes:
    """Write one directly-uploaded document under <run_id>/inputs/ — locally
    always (the picker and the preview read it there) and into the output
    volume in databricks mode, because the JOB is what opens it."""
    from frdsttm.pipeline import INPUTS_DIR
    target = settings.PATHS.inputs_dir(run_id) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    if settings.IS_DATABRICKS:
        client().files.upload(
            f"{settings.volume_path('output')}/{run_id}/{INPUTS_DIR}/{name}", payload, overwrite=True)
    return payload


def push_document(kind: str, name: str, payload: bytes) -> None:
    """Upload one document into a volume (the app's own upload control)."""
    if settings.IS_DATABRICKS:
        client().files.upload(f"{settings.volume_path(kind)}/{name}", payload, overwrite=True)
    target = getattr(settings.PATHS, {"frds": "frds", "vdds": "vdds", "reference": "reference"}[kind]) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
