"""Append-only audit trail for every governed action the review app takes.

What gets recorded (each is one `record(...)` call at the route that does
the thing, AFTER it succeeded unless stated otherwise):

    run.started / run.finished          a billed pipeline run (who, which FRD,
                                        its sha256, mode, job run id/url)
    resolution.recorded                 a human-in-the-loop decision
    rerender.started / rerender.finished stage-04 re-render
    workbook.downloaded                 the HAND-OFF: a reviewer took a rendered
                                        STTM out of the app (sha256 + size of
                                        exactly the bytes served)
    corpus.sync.started                 "Sync now" / start-up sync
    upload.received                     a demo upload

Every event carries: event_id, ts (UTC ISO), kind, actor, actor_source,
app_mode, and the action's own fields. Never a secret, never document
content — ids, names, hashes, counts, statuses only.

Storage — one JSON file per event, never a file that is rewritten:

    <audit root>/events/<UTC ts>_<kind>_<8 hex>.json

- local mode: <repo>/local_dev_fixtures/<STTM_AUDIT_VOLUME>/events/ (gitignored).
- databricks mode: the same relative layout under the Unity Catalog volume
  /Volumes/<CATALOG>/<SCHEMA>/<STTM_AUDIT_VOLUME>/ via the Files API (the
  app's service principal needs WRITE VOLUME on it), AND a local mirror in
  the container so GET /api/demo/audit can list without a round trip.
  A failed volume write FAILS THE GOVERNED ACTION (AuditWriteError → the
  caller's 502 naming the volume) — an unaudited action in the deployed
  app is the outcome this module exists to prevent.

One-file-per-event is deliberate: appends from concurrent request threads
(or two App instances) can never corrupt each other, and the whole trail is
a table away — `SELECT * FROM read_files('/Volumes/.../events/', format =>
'json')` — with no ETL. Retention of the trail is a client governance
decision (docs/AI_GOVERNANCE.md); nothing here deletes.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

APP_MODE = os.environ.get("STTM_APP_MODE", "local")
IS_DATABRICKS_APP = APP_MODE == "databricks"

ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = ROOT / "local_dev_fixtures"
AUDIT_VOLUME = os.environ.get("STTM_AUDIT_VOLUME", "sttm_audit")
EVENTS_SUBDIR = "events"
CATALOG = os.environ.get("CATALOG", "arjun_workspace")
SCHEMA = os.environ.get("SCHEMA", "sttm_agent")

# Listing cap for GET /api/demo/audit — newest first; the volume is the
# complete record, the endpoint is a window onto it.
LIST_LIMIT_DEFAULT = 200
LIST_LIMIT_MAX = 2000

KINDS = (
    "run.started", "run.finished",
    "resolution.recorded",
    "rerender.started", "rerender.finished",
    "workbook.downloaded",
    "corpus.sync.started",
    "upload.received",
)


class AuditWriteError(RuntimeError):
    """The durable audit write failed — the governed action must not proceed
    as if it had been recorded. Caller's 502."""


def _now() -> str:
    # Microseconds: the file name sorts chronologically even for events
    # written within the same second (a download right after a resolution).
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _ts_compact(ts_iso: str) -> str:
    # 2026-08-23T14:05:09.123456+00:00 -> 20260823T140509.123456Z
    return ts_iso.replace("-", "").replace(":", "").split("+", 1)[0] + "Z"


def local_events_dir() -> Path:
    return LOCAL_ROOT / AUDIT_VOLUME / EVENTS_SUBDIR


def volume_events_dir() -> str:
    return f"/Volumes/{CATALOG}/{SCHEMA}/{AUDIT_VOLUME}/{EVENTS_SUBDIR}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_event(kind: str, identity: dict, **fields) -> dict:
    """The event dict — pure, no I/O; tests assert on this shape."""
    if kind not in KINDS:
        raise ValueError(f"unknown audit event kind {kind!r} (known: {KINDS})")
    event = {
        "event_id": uuid.uuid4().hex,
        "ts": _now(),
        "kind": kind,
        "actor": identity.get("actor") or "unknown",
        "actor_source": identity.get("source") or "unknown",
        "app_mode": APP_MODE,
    }
    for key, value in fields.items():
        if key in event:
            raise ValueError(f"audit field {key!r} collides with a reserved field")
        event[key] = value
    return event


def _file_name(event: dict) -> str:
    return f"{_ts_compact(event['ts'])}_{event['kind']}_{event['event_id'][:8]}.json"


def _write_local(event: dict) -> Path:
    directory = local_events_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _file_name(event)
    payload = json.dumps(event, indent=2, ensure_ascii=False, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return path


def _write_volume(event: dict) -> str:
    """Durable copy in Unity Catalog. Imported lazily so local mode and the
    offline suite never touch the Databricks SDK."""
    import jobs_runner  # noqa: PLC0415 — same-directory module, SDK inside

    dest = f"{volume_events_dir()}/{_file_name(event)}"
    payload = json.dumps(event, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        w = jobs_runner._workspace_client()
        w.files.upload(dest, payload, overwrite=False)
    except Exception as exc:  # noqa: BLE001 — every SDK failure is the same fact here
        raise AuditWriteError(
            f"could not write audit event {event['kind']} to {dest} "
            f"({type(exc).__name__}: {exc}). Create the volume "
            f"{CATALOG}.{SCHEMA}.{AUDIT_VOLUME} and grant the app's service "
            "principal READ VOLUME + WRITE VOLUME on it; the action was NOT "
            "performed because it could not be recorded."
        ) from exc
    return dest


def record(kind: str, identity: dict, **fields) -> dict:
    """Build + persist one event; returns it. Raises AuditWriteError in
    databricks mode when the volume write fails (callers map it to 502 and
    do not perform the action). Local writes are best-effort durable (they
    are the only copy in local mode and the mirror in databricks mode)."""
    event = build_event(kind, identity, **fields)
    if IS_DATABRICKS_APP:
        _write_volume(event)
    _write_local(event)
    return event


def list_events(limit: int = LIST_LIMIT_DEFAULT, kind: str | None = None) -> list[dict]:
    """Newest first from the local events dir (in databricks mode the
    caller mirrors the volume down first — see demo.py's audit route)."""
    limit = max(1, min(int(limit), LIST_LIMIT_MAX))
    directory = local_events_dir()
    if not directory.is_dir():
        return []
    events = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if kind and event.get("kind") != kind:
            continue
        events.append(event)
        if len(events) >= limit:
            break
    return events
