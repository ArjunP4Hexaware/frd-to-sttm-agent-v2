"""Corpus endpoints — the SharePoint sync, the picker's source of truth, and
reference-workbook downloads.

The review-app half of the template architecture (docs/TEMPLATE_ARCHITECTURE.md),
reshaped 2026-08-22 around the sync (src/frdsttm/sync.py):

- GET  /api/demo/corpus                  — index summary + sync state
                                           (absent index is a normal state)
- GET  /api/demo/corpus/frds             — every FRD with its pairing verdict,
                                           the ONLY list the picker offers
- POST /api/demo/corpus/sync             — "Sync now": run the SharePoint sync
                                           (or a network-free reindex), 202
- start_sync_on_startup()                 — the SAME sync, kicked off by app.py's
                                           lifespan when the app starts (2026-08-22
                                           late decision: no schedule)
- GET  /api/demo/corpus/sync             — progress of that background run
- GET  /api/demo/corpus/references/{name}— an approved STTM from the reference
                                           volume, for the "already mapped" view
- GET  /api/demo/corpus/config           — can a sync be offered, which folders

What "mapped" means here: the corpus index pairs each FRD with its STTM
(name first — the library's `FRD_<name>` ↔ `STTM_<name>` convention —
deterministic similarity second), built over what the frd_raw and
sttm_reference volumes hold. The index is written by the sync this module
starts at app START-UP and on "Sync now" (in databricks mode that is the
bundle job `frd_sttm_sharepoint_sync`, which has no schedule of its own);
the app only ever READS it. There is no SharePoint lookup on the request
path any more, and nothing here writes to SharePoint — the reviewer
uploads a finished STTM to the library's STTM folder themselves; the next
sync pulls it into sttm_reference and pairs it.

Mode behavior follows the rest of the backend (STTM_APP_MODE):
- local: the sync runs in-process against local_dev_fixtures/frd_raw and
  /sttm_reference (the same code the notebook runs).
- databricks: "Sync now" triggers the bundle-deployed sync JOB via the Jobs
  API (jobs_runner), waits for it, then mirrors both volumes down to the
  container so the picker lists exactly what Unity Catalog holds and runs
  can stage from a local copy. The app also re-mirrors lazily when its
  local index is missing or older than STTM_CORPUS_REFRESH_SECONDS (another
  app instance, or a manual job run, may have synced).
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import jobs_runner
from demo import IS_DATABRICKS_APP, LOCAL_ROOT, PRELOADED_DIR, _rel
from sharepoint_routes import _client, _client_secret, _param

# ORDER IS LOAD-BEARING: sharepoint_routes' import above bootstraps src/ onto
# sys.path (the deployed App does not pip-install the frdsttm package), so
# the frdsttm imports below MUST come after it. Do not let a formatter sort
# them ahead.
from frdsttm.corpus import (  # noqa: E402
    CORPUS_INDEX_NAME,
    CorpusIndexError,
    load_corpus_index,
)
from frdsttm.sharepoint import GraphError, SharePointConfigError, load_config  # noqa: E402
from frdsttm.similarity import thresholds_from  # noqa: E402
from frdsttm.sync import (  # noqa: E402
    load_manifest,
    manifest_by_local_name,
    reindex,
    sync_from_sharepoint,
)

router = APIRouter()

REFERENCE_VOLUME = os.environ.get("STTM_REFERENCE_VOLUME", "sttm_reference")
REFERENCE_DIR = LOCAL_ROOT / REFERENCE_VOLUME

# databricks mode: how stale the container's mirror of the volumes may get
# before a read re-mirrors from Unity Catalog (another instance or a manual
# job run may have written there). Small files; cheap.
CORPUS_REFRESH_SECONDS = int(os.environ.get("STTM_CORPUS_REFRESH_SECONDS", "120"))

# The library's naming convention (learned 2026-08-22): every FRD is
# FRD_<name>.<ext>, every STTM STTM_<name>.xlsx. Blank disables the filter.
FRD_NAME_PREFIX = os.environ.get("STTM_FRD_NAME_PREFIX", "FRD_").strip()
STTM_NAME_PREFIX = os.environ.get("STTM_STTM_NAME_PREFIX", "STTM_").strip()

# Sync when the app starts (decided 2026-08-22 late evening, replacing the
# cron schedule). "0"/"false"/"no" turns it off — local dev and tests.
SYNC_ON_STARTUP = os.environ.get("STTM_SYNC_ON_STARTUP", "1").strip().lower() not in ("0", "false", "no")

# Same accessor shape the notebooks' _param has, so thresholds resolve from
# the same-named env vars here and from widgets there.
_thresholds = lambda: thresholds_from(  # noqa: E731
    lambda name, default: os.environ.get(name.upper(), default))

SYNC_MODES = ("sync", "reindex")


def _uc_reference_dir() -> str:
    return f"{jobs_runner.volume_root()}/{REFERENCE_VOLUME}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# index access (with the databricks-mode mirror)
# --------------------------------------------------------------------------- #
def _mirror_from_uc() -> dict | None:
    """Bring the container's frd_raw + sttm_reference copies in step with
    Unity Catalog. Returns the counts, or None when the volumes are not
    readable yet — which, for a READ, is the ordinary "nothing synced in
    this workspace yet" state, not an error (the sync endpoint itself is
    where an unreadable volume fails loudly)."""
    try:
        w = jobs_runner._workspace_client()
        return jobs_runner.mirror_corpus(w, PRELOADED_DIR, REFERENCE_DIR)
    except Exception:  # noqa: BLE001 — see docstring
        return None


def _local_index_age() -> float | None:
    path = REFERENCE_DIR / CORPUS_INDEX_NAME
    return (time.time() - path.stat().st_mtime) if path.is_file() else None


def _index_or_none():
    """The corpus index, or None when unbuilt. Corrupt raises — never degrade."""
    if IS_DATABRICKS_APP:
        age = _local_index_age()
        if age is None or age > CORPUS_REFRESH_SECONDS:
            _mirror_from_uc()
    try:
        return load_corpus_index(REFERENCE_DIR)
    except CorpusIndexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _summary(index, manifest) -> dict:
    base = {
        "built": False, "generated_at": None, "synced_at": manifest.get("synced_at"),
        "n_frds": 0, "n_references": 0, "n_pairs": 0, "n_unmapped": 0,
        "unpaired_references": [],
    }
    if index is None:
        return base
    return {
        **base,
        "built": True,
        "generated_at": index["generated_at"],
        "n_frds": len(index["frds"]),
        "n_references": len(index["references"]),
        "n_pairs": len(index["pairs"]),
        "n_unmapped": len(index["unmapped"]),
        "unpaired_references": index["unpaired_references"],
    }


# --------------------------------------------------------------------------- #
# sync state (one background sync at a time)
# --------------------------------------------------------------------------- #
_sync_lock = threading.Lock()
_sync: dict = {
    "state": "idle",           # idle | running | done | failed
    "mode": None,
    "trigger": None,           # startup | request
    "started_at": None,
    "finished_at": None,
    "error": None,
    "run_page_url": None,      # databricks mode: the job-run page
    "summary": None,
}


def _set_sync(**fields) -> None:
    with _sync_lock:
        _sync.update(fields)


def _sync_snapshot() -> dict:
    with _sync_lock:
        return dict(_sync)


def _strip_index(summary: dict) -> dict:
    return {k: v for k, v in summary.items() if k != "index"}


def _sync_worker(mode: str, client, reference_folder: str | None) -> None:
    """Local mode: the sync code in-process. Databricks mode: trigger the
    sync JOB, wait, mirror. Either way the Run-lifecycle shape is the same
    try/finally demo.py's workers use: a stuck 'running' is the one outcome
    to prevent."""
    try:
        if IS_DATABRICKS_APP:
            w = jobs_runner._workspace_client()
            job_id = jobs_runner.resolve_sync_job_id(w)
            run_id = jobs_runner.start_sync_job(w, job_id, mode)
            try:
                url = w.jobs.get_run(run_id).run_page_url
            except Exception:  # noqa: BLE001 — the link is a convenience
                url = None
            _set_sync(run_page_url=url)
            jobs_runner.wait_for_run(w, run_id)
            mirrored = jobs_runner.mirror_corpus(w, PRELOADED_DIR, REFERENCE_DIR)
            summary = {"job_run_id": run_id, "mirrored": mirrored}
        elif mode == "reindex":
            index, skipped = reindex(PRELOADED_DIR, REFERENCE_DIR, _thresholds(), _now())
            summary = {"mode": "reindex", "skipped": skipped,
                       "n_frds": len(index["frds"]), "n_references": len(index["references"]),
                       "n_pairs": len(index["pairs"]), "n_unmapped": len(index["unmapped"])}
        else:
            result = sync_from_sharepoint(
                client, frd_dir=PRELOADED_DIR, reference_dir=REFERENCE_DIR,
                reference_folder=reference_folder, thresholds=_thresholds(), now_iso=_now(),
                frd_prefix=FRD_NAME_PREFIX, reference_prefix=STTM_NAME_PREFIX,
            )
            index = result["index"]
            summary = {**_strip_index(result), "mode": "sync",
                       "n_frds": len(index["frds"]), "n_references": len(index["references"]),
                       "n_pairs": len(index["pairs"]), "n_unmapped": len(index["unmapped"])}
        _set_sync(state="done", finished_at=_now(), summary=summary)
    except GraphError as exc:
        _set_sync(state="failed", finished_at=_now(),
                  error=f"SharePoint refused the sync ({exc.code}). "
                        + (f"Graph request-id {exc.request_id}." if exc.request_id else ""))
    except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
        _set_sync(state="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}")


class SyncRequest(BaseModel):
    confirm: bool = False
    mode: str = "sync"


@router.post("/api/demo/corpus/sync", status_code=202)
def corpus_sync(body: SyncRequest) -> dict:
    """"Sync now" / "Rebuild index": start one background sync and return
    its state (poll GET /api/demo/corpus/sync).

    Zero model calls, but it rewrites the two volumes and the corpus index,
    so it is confirm-gated like every other state-changing control here.
    `mode`: "sync" (SharePoint → volumes → index) or "reindex" (index from
    the volumes as they are; no network, no tenant needed). Anything else
    is a 400 — no value is guessed.
    """
    if body.confirm is not True:
        raise HTTPException(
            status_code=400,
            detail="the sync downloads the SharePoint library into the raw/"
                   "reference volumes and rebuilds the corpus index — it "
                   "requires an explicit {\"confirm\": true}",
        )
    if body.mode not in SYNC_MODES:
        raise HTTPException(status_code=400,
                            detail=f"mode must be one of {SYNC_MODES}, got {body.mode!r}")

    client, reference_folder = None, None
    if body.mode == "sync" and not IS_DATABRICKS_APP:
        # Resolve config + sign-in HERE so an unwired tenant is the caller's
        # 503 and a refused sign-in its 502 — not a failed background state.
        cfg, client = _client()
        reference_folder = cfg.sttm_folder

    return _start_sync(body.mode, client, reference_folder, trigger="request")


def _start_sync(mode: str, client, reference_folder: str | None, *, trigger: str) -> dict:
    with _sync_lock:
        if _sync["state"] == "running":
            raise HTTPException(status_code=409, detail="a sync is already running")
        _sync.update(state="running", mode=mode, trigger=trigger, started_at=_now(),
                     finished_at=None, error=None, run_page_url=None, summary=None)
    threading.Thread(target=_sync_worker, args=(mode, client, reference_folder),
                     daemon=True).start()
    return _sync_snapshot()


def start_sync_on_startup() -> dict | None:
    """Run the sync when the app starts (app.py's lifespan calls this).

    Decided 2026-08-22 late evening, replacing the cron schedule: the person
    opening the app is the reason a fresh corpus matters, so the app brings
    Unity Catalog in step with SharePoint the moment it starts — in the
    background, never blocking first paint; the Corpus panel shows the same
    running/done/failed state as "Sync now".

    - databricks mode: trigger the sync JOB and mirror (same worker).
    - local mode with a wired tenant: the sync in-process.
    - local mode with NO tenant configured: a network-free REINDEX, so the
      picker reflects whatever the volumes hold (this is not an error — an
      unwired tenant is the ordinary dev state).
    - STTM_SYNC_ON_STARTUP=0: do nothing, return None.
    Never raises: a start-up failure is recorded in the sync state, not
    thrown into the server's lifespan.
    """
    if not SYNC_ON_STARTUP:
        return None
    mode, client, reference_folder = "sync", None, None
    if not IS_DATABRICKS_APP:
        try:
            cfg, client = _client()
            reference_folder = cfg.sttm_folder
        except HTTPException:
            mode = "reindex"   # no tenant wired (503) / sign-in refused (502): index the volumes
    try:
        return _start_sync(mode, client, reference_folder, trigger="startup")
    except HTTPException:
        return _sync_snapshot()  # a sync is already running — fine


@router.get("/api/demo/corpus/sync")
def corpus_sync_state() -> dict:
    return _sync_snapshot()


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
@router.get("/api/demo/corpus")
def corpus_summary() -> dict:
    index = _index_or_none()
    return {**_summary(index, load_manifest(REFERENCE_DIR)), "sync": _sync_snapshot()}


@router.get("/api/demo/corpus/frds")
def corpus_frds() -> dict:
    """Every corpus FRD with its pairing verdict — the picker's whole world.

    `path` is repo-root-relative — the exact shape POST /api/demo/runs takes,
    so the Generate button reuses the existing billed-run gate unchanged.
    Paired FRDs carry their reference workbook (downloadable via
    /references/{name}) plus, when the sync recorded them, the SharePoint
    links and modified stamps of both files. Unbuilt corpus → 200 with an
    empty list plus built=false (empty-vs-unreachable stays distinguishable:
    a corrupt index is a 502).
    """
    index = _index_or_none()
    if index is None:
        return {"built": False, "frds": []}
    manifest = load_manifest(REFERENCE_DIR)
    frd_meta = manifest_by_local_name(manifest, "frd")
    ref_meta = manifest_by_local_name(manifest, "reference")
    frds = []
    for doc_id, entry in sorted(index["frds"].items()):
        pair = index["pairs"].get(doc_id)
        source_name = Path(entry["source_file"]).name
        local = PRELOADED_DIR / source_name
        fm = frd_meta.get(source_name, {})
        rm = ref_meta.get(pair["reference"], {}) if pair else {}
        ref_path = (REFERENCE_DIR / pair["reference"]) if pair else None
        frds.append({
            "doc_id": doc_id,
            "name": source_name,
            "path": _rel(local) if local.is_file() else None,
            "runnable": local.is_file(),
            "content_sha256": entry.get("content_sha256"),
            "web_url": fm.get("web_url"),
            "modified": fm.get("modified"),
            "paired": pair is not None,
            "reference": pair["reference"] if pair else None,
            "matched_by": pair.get("matched_by") if pair else None,
            "score": pair["score"] if pair else None,
            "confidence": pair["confidence"] if pair else None,
            "components": pair["components"] if pair else None,
            "reference_web_url": rm.get("web_url"),
            "reference_modified": rm.get("modified"),
            "reference_size_bytes": (ref_path.stat().st_size
                                     if ref_path is not None and ref_path.is_file() else None),
        })
    return {"built": True, "frds": frds}


@router.get("/api/demo/corpus/references/{name}")
def corpus_reference(name: str):
    """Serve one approved STTM from the reference volume — the workbook a
    paired FRD is mapped by. Only names the corpus index lists are served:
    the index is the allow-list, so this can never read an arbitrary file."""
    index = _index_or_none()
    if index is None or name not in index.get("references", {}):
        raise HTTPException(status_code=404, detail="no such reference STTM in the corpus")
    path = REFERENCE_DIR / Path(name).name
    if not path.is_file() and IS_DATABRICKS_APP:
        _mirror_from_uc()
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"{name} is in the corpus index but not in {REFERENCE_DIR} — "
                   f"run a sync to re-mirror the reference volume",
        )
    return Response(
        content=path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


# Config probe so the frontend can offer the right control: "Sync now" only
# with a wired tenant (same "unconfigured is normal" contract as the
# SharePoint config probe); "Rebuild index" always.
@router.get("/api/demo/corpus/config")
def corpus_config() -> dict:
    """Presence probe only — no sign-in, no network (a probe that signed in
    would make every page load a Graph call)."""
    base = {"mode": "databricks" if IS_DATABRICKS_APP else "local",
            "reference_volume": _uc_reference_dir() if IS_DATABRICKS_APP else str(REFERENCE_DIR)}
    try:
        cfg = load_config(_param, _client_secret)
    except SharePointConfigError:
        return {**base, "available": False, "frd_folder": None, "reference_folder": None}
    return {**base, "available": True, "frd_folder": cfg.frd_folder,
            "reference_folder": cfg.sttm_folder}
