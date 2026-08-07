"""Client-facing demo endpoints: live runs + replay, mirroring the sibling
brd-to-frd repo's demo_app_react backend (runner.py + artifacts.py) in this
repo's idiom.

Two jobs:

- LIVE runs: spawn the existing pipeline notebooks (01→04) as subprocesses
  with provider `anthropic`, streaming their console (SSE + polling
  fallback). No pipeline code is imported or modified — the notebooks' env
  knobs and on-disk outputs are the only interfaces used.
- REPLAY: discover saved demo/e2e artifact sets under local_dev_fixtures/
  and serve a parsed results payload (extraction summary, the stage-03 gate
  moment, the stage-04 verdict, eval-vs-golden, per-mapping rows) that the
  frontend renders identically for a finished live run and a replayed set —
  replay makes zero API calls.

Guardrails (same posture as the sibling app):

- The run suffix is generated HERE, never user-supplied and never empty:
  `demo_<YYYYmmdd_HHMMSS>`, uniquified on collision. Every output location
  the run writes (SCHEMA / OUT_VOLUME / PREVIEW_VOLUME / RAW_VOLUME) embeds
  that suffix, so a demo run can never land on `sttm_out` or any curated
  baseline path.
- One live run at a time (caller's 409). Run state is in-memory; the
  console log (data/live_run_logs/, gitignored) and the artifact set on
  disk survive a restart — the finished run is then reachable via replay.
- ANTHROPIC_API_KEY: presence is reported as a boolean only; the value is
  read from the process env or the repo .env solely to inject into the
  subprocess env, and is never logged or returned.
- The mock upload flow (orchestration.py) is untouched: this module is
  additive, mounted under /api/demo/*.

Config doctrine: this repo's knob surface is env vars with in-file
defaults (the notebooks' own `_param` pattern), not a YAML file like the
sibling's config.yaml — deliberate, to stay consistent with the rest of
this repo. No literals in logic: every tunable lives in the constants
block below.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

import eval_report as er

router = APIRouter()

# --------------------------------------------------------------------------- #
# Config (env-overridable; defaults are the local-mode layout)
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = ROOT / "local_dev_fixtures"

GOLDEN_DOC_ID = os.environ.get("STTM_DEMO_GOLDEN_DOC_ID", "demo_frd")
PRELOADED_DIR = LOCAL_ROOT / os.environ.get("STTM_DEMO_PRELOADED_VOLUME", "frd_raw")
UPLOADS_DIR = LOCAL_ROOT / os.environ.get("STTM_DEMO_UPLOADS_VOLUME", "demo_uploads")
RAW_STAGING_VOLUME = os.environ.get("STTM_DEMO_RAW_STAGING_VOLUME", "demo_raw")
LOGS_DIR = ROOT / "data" / "live_run_logs"

SUFFIX_PREFIX = "demo"  # never configurable: the insulation guarantee hangs off it
SUFFIX_TS_FORMAT = "%Y%m%d_%H%M%S"
CATALOG = os.environ.get("CATALOG", "soham_workspace")
STAGE_TIMEOUT_SECONDS = int(os.environ.get("STTM_DEMO_STAGE_TIMEOUT_SECONDS", "900"))
UPLOAD_MAX_BYTES = int(os.environ.get("STTM_DEMO_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)))
UPLOAD_ALLOWED_EXTENSIONS = (".docx",)

# Shown in the confirmation dialog before a billed run — measured on the
# two live E2E runs of 2026-08-07 (docs/LIVE_E2E_2026-08-07.md).
CALL_ESTIMATE = {
    "calls": int(os.environ.get("STTM_DEMO_EST_CALLS", "1")),
    "usd": float(os.environ.get("STTM_DEMO_EST_USD", "0.15")),
    "seconds": int(os.environ.get("STTM_DEMO_EST_SECONDS", "35")),
}

API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"

# Replay discovery: exactly these artifact-set families, nothing else. The
# same rule family as the sibling app's suffix-based scan — a set is a
# `sttm_out_<suffix>` directory whose suffix marks it as a demo run or a
# preserved live-E2E run. `sttm_out` itself (curated baselines) can never
# match: both alternatives require a non-empty marked suffix.
_SET_DIR_RE = re.compile(r"^sttm_out_(demo|live_e2e)_[A-Za-z0-9_-]+$")

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_STAGES = (
    ("01_frd_ingest.py", "Ingest FRD"),
    ("02_extract.py", "Extract (live Anthropic call)"),
    ("03_contract_build.py", "Contract build + gate"),
    ("04_sttm_render.py", "Render STTM + eval"),
)


class RunConflictError(RuntimeError):
    """A live run is already active — caller's 409."""


class RunPreflightError(ValueError):
    """Bad start-run request (missing key, bad FRD path) — caller's 400."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    """`path` relative to the repo root when possible, absolute otherwise
    (a configured dir outside the repo, e.g. in tests). Display + request
    round-tripping only — never used for containment decisions."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# API-key handling — presence boolean out, value only into the subprocess env
# --------------------------------------------------------------------------- #
def _api_key_from_dotenv() -> str | None:
    """Minimal .env read for exactly one variable. The value is returned to
    the (in-process) caller for subprocess-env injection only — never logged,
    never serialized into any response."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{API_KEY_ENV_VAR}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def api_key_present() -> bool:
    return bool(os.environ.get(API_KEY_ENV_VAR, "").strip() or _api_key_from_dotenv())


# --------------------------------------------------------------------------- #
# Run state (in-memory, one active at a time — sibling's Run pattern)
# --------------------------------------------------------------------------- #
class Run:
    """State + console buffer for one live run. Thread-safe via _lock; every
    mutation bumps `seq` so SSE/pollers resume from a cursor.

    Stage tracking is by subprocess boundary, not console-marker parsing:
    unlike the sibling's single-CLI pipeline, each stage here IS its own
    subprocess, so the boundaries are exact rather than inferred."""

    def __init__(self, doc_id: str, frd_path: Path, suffix: str, log_path: Path):
        self.id = suffix
        self.suffix = suffix
        self.doc_id = doc_id
        self.frd_path = frd_path
        self.log_path = log_path
        self.artifact_set = f"sttm_out_{suffix}"
        self.is_golden = doc_id == GOLDEN_DOC_ID
        self.status = STATUS_RUNNING
        self.error: str | None = None
        self.started_at = _now()
        self.finished_at: str | None = None
        self.stages = [
            {"id": fname, "label": label, "status": "pending"}
            for fname, label in _STAGES
        ]
        self.events: list[dict] = []
        self.seq = 0
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)

    def _emit(self, kind: str, text: str) -> None:
        with self._changed:
            self.seq += 1
            self.events.append({"seq": self.seq, "kind": kind, "text": text, "ts": _now()})
            self._changed.notify_all()

    def _set_stage(self, stage_id: str, status: str) -> None:
        with self._lock:
            for stage in self.stages:
                if stage["id"] == stage_id:
                    if stage["status"] == status:
                        return
                    stage["status"] = status
                    break
            else:
                return
        self._emit("stage", f"{stage_id}:{status}")

    def _finish(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.error = error
            self.finished_at = _now()
        self._emit("status", status if not error else f"{status}: {error}")

    def snapshot(self, after_seq: int = 0) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "suffix": self.suffix,
                "doc_id": self.doc_id,
                "artifact_set": self.artifact_set,
                "is_golden": self.is_golden,
                "status": self.status,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "stages": [dict(s) for s in self.stages],
                "seq": self.seq,
                "events": [e for e in self.events if e["seq"] > after_seq],
            }

    def wait_for_change(self, seen_seq: int, timeout: float) -> None:
        with self._changed:
            if self.seq == seen_seq and self.status == STATUS_RUNNING:
                self._changed.wait(timeout)


_active_lock = threading.Lock()
_runs: dict[str, Run] = {}
_active_run_id: str | None = None


def get_run(run_id: str) -> Run | None:
    return _runs.get(run_id)


def _new_suffix() -> str:
    """`demo_<ts>`, uniquified against both in-memory runs and on-disk
    artifact sets (a restarted backend must not reuse a finished run's
    suffix within the same second)."""
    base = f"{SUFFIX_PREFIX}_{datetime.now().strftime(SUFFIX_TS_FORMAT)}"
    suffix, n = base, 1
    while suffix in _runs or (LOCAL_ROOT / f"sttm_out_{suffix}").exists():
        n += 1
        suffix = f"{base}-{n}"
    return suffix


def _validate_frd(frd_rel: str) -> Path:
    """The FRD must be an existing .docx inside the preloaded dir or the
    demo uploads dir — resolved and containment-checked so a crafted path
    can never reach outside them (and never into fixtures/ or curated
    locations)."""
    candidate = (ROOT / frd_rel).resolve()
    allowed = [PRELOADED_DIR.resolve(), UPLOADS_DIR.resolve()]
    if not any(candidate.is_relative_to(root) for root in allowed):
        raise RunPreflightError(
            "FRD path must be inside the preloaded documents or demo uploads directory"
        )
    if candidate.suffix.lower() not in UPLOAD_ALLOWED_EXTENSIONS:
        raise RunPreflightError("FRD must be a .docx file")
    if not candidate.is_file():
        raise RunPreflightError(f"FRD file not found: {frd_rel}")
    return candidate


def _subprocess_env(suffix: str, raw_volume: str) -> dict:
    """The full insulation contract in one place: every output knob embeds
    the demo suffix; mock is stripped; the provider is pinned to anthropic;
    the API key is injected (from env or .env) without ever being logged."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["SCHEMA"] = f"sttm_agent_{suffix}"
    env["OUT_VOLUME"] = f"sttm_out_{suffix}"
    env["PREVIEW_VOLUME"] = f"sttm_out_{suffix}"
    env["RAW_VOLUME"] = raw_volume
    env["STTM_LLM_PROVIDER"] = "anthropic"
    env.pop("STTM_MOCK_EXTRACTION", None)
    if not env.get(API_KEY_ENV_VAR, "").strip():
        key = _api_key_from_dotenv()
        if key:
            env[API_KEY_ENV_VAR] = key
    return env


def _run_worker(run: Run, raw_volume: str) -> None:
    global _active_run_id
    try:
        run.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = _subprocess_env(run.suffix, raw_volume)
        with run.log_path.open("w", encoding="utf-8") as log:
            for fname, _label in _STAGES:
                cmd = [sys.executable, str(ROOT / "notebooks" / fname)]
                run._set_stage(fname, "running")
                run._emit("console", f"$ python notebooks/{fname}")
                log.write(f"$ python notebooks/{fname}\n")
                proc = subprocess.Popen(
                    cmd, cwd=str(ROOT), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    log.write(line + "\n")
                    log.flush()
                    run._emit("console", line)
                returncode = proc.wait(timeout=STAGE_TIMEOUT_SECONDS)
                if returncode != 0:
                    run._set_stage(fname, "failed")
                    run._finish(
                        STATUS_FAILED,
                        f"{fname} exited with code {returncode} — see console log "
                        f"({_rel(run.log_path)})",
                    )
                    return
                run._set_stage(fname, "done")
        run._finish(STATUS_DONE)
    except Exception as exc:  # noqa: BLE001 — a stuck "running" spinner is the one outcome to prevent
        run._finish(STATUS_FAILED, f"{type(exc).__name__}: {exc}")
    finally:
        with _active_lock:
            _active_run_id = None


def start_run(frd_rel: str) -> Run:
    global _active_run_id

    frd_path = _validate_frd(frd_rel)
    if not api_key_present():
        raise RunPreflightError(
            f"{API_KEY_ENV_VAR} is not set (environment or repo .env). A live run "
            "makes billed Anthropic API calls and cannot start without it."
        )

    with _active_lock:
        if _active_run_id is not None:
            raise RunConflictError(
                f"A live run is already in progress (id={_active_run_id}). "
                "One run at a time — wait for it to finish."
            )
        suffix = _new_suffix()
        # Stage the chosen document into a per-run raw dir: 01_frd_ingest
        # ingests its whole RAW_VOLUME, so the run must see exactly one file
        # — and the preloaded/frd_raw dir is never handed to a run directly.
        raw_volume = f"{RAW_STAGING_VOLUME}/{suffix}"
        raw_dir = LOCAL_ROOT / raw_volume
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(frd_path, raw_dir / frd_path.name)
        log_path = LOGS_DIR / f"{suffix}.log"
        run = Run(frd_path.stem, frd_path, suffix, log_path)
        _runs[run.id] = run
        _active_run_id = run.id

    threading.Thread(target=_run_worker, args=(run, raw_volume), daemon=True).start()
    return run


# --------------------------------------------------------------------------- #
# Replay: artifact-set discovery + unified results payload
# --------------------------------------------------------------------------- #
class ArtifactRequestError(ValueError):
    """A set/doc parameter failed validation — caller's 400."""


def _check_set_id(set_id: str) -> Path:
    if not _SET_DIR_RE.match(set_id):
        raise ArtifactRequestError(
            f"not a demo artifact set: {set_id!r} (expected sttm_out_demo_* "
            f"or sttm_out_live_e2e_*)"
        )
    return LOCAL_ROOT / set_id


_SAFE_DOC = re.compile(r"^[A-Za-z0-9._ -]+$")


def _check_doc_id(doc_id: str) -> str:
    if not _SAFE_DOC.match(doc_id) or doc_id.startswith("."):
        raise ArtifactRequestError(f"invalid doc id: {doc_id!r}")
    return doc_id


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_artifact_sets() -> list[dict]:
    """Every replayable set, newest first. A set qualifies by directory-name
    family plus having at least one extraction JSON."""
    sets = []
    if not LOCAL_ROOT.is_dir():
        return sets
    for directory in LOCAL_ROOT.iterdir():
        if not directory.is_dir() or not _SET_DIR_RE.match(directory.name):
            continue
        extractions = sorted((directory / "extractions").glob("*.json"))
        if not extractions:
            continue
        for extraction in extractions:
            doc_id = extraction.stem
            v2 = _load_json(directory / "contracts" / f"{doc_id}.contract.v2.json")
            totals = er.read_eval_totals(str(directory), doc_id)
            sets.append({
                "set_id": directory.name,
                "doc_id": doc_id,
                "source": "live_e2e" if directory.name.startswith("sttm_out_live_e2e_") else "demo_run",
                "modified_at": datetime.fromtimestamp(
                    extraction.stat().st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds"),
                "status": (v2 or {}).get("status"),
                "eval_pct": round(totals[0] / totals[1] * 100, 1) if totals else None,
                "workbook_available": (directory / "rendered" / f"{doc_id}.sttm.xlsx").is_file(),
                "is_golden": doc_id == GOLDEN_DOC_ID,
            })
    sets.sort(key=lambda s: s["modified_at"], reverse=True)
    return sets


def _mapping_rows(feed: dict) -> list[dict]:
    rows = []
    for f in feed.get("fields") or []:
        rows.append({
            "source_column": f.get("source_column"),
            "datatype": f.get("datatype"),
            "stage": _target_label(f.get("stage")),
            "standard": _target_label(f.get("standard")),
        })
    return rows


def _target_label(target: dict | None) -> str:
    if not target:
        return ""
    parts = [target.get("schema"), target.get("table"), target.get("column")]
    label = ".".join(str(p) for p in parts if p)
    dt = target.get("datatype")
    return f"{label} ({dt})" if label and dt else label


def load_results(set_id: str, doc_id: str) -> dict:
    """The unified results payload — identical shape for a replayed set and
    a finished live run (the frontend renders both through one view).

    Sections follow the demo story: extraction summary → the stage-03 gate
    moment (detected / auto-confirmed / awaiting human) → stage-04 verdict →
    eval-vs-golden → per-mapping rows + workbook availability.
    """
    directory = _check_set_id(set_id)
    doc_id = _check_doc_id(doc_id)
    if not directory.is_dir():
        raise FileNotFoundError(f"artifact set not found: {set_id}")

    extraction = _load_json(directory / "extractions" / f"{doc_id}.json")
    if extraction is None:
        raise FileNotFoundError(f"no extraction JSON for {doc_id!r} in {set_id}")
    v1 = _load_json(directory / "contracts" / f"{doc_id}.contract.json") or {}
    v2 = _load_json(directory / "contracts" / f"{doc_id}.contract.v2.json") or {}

    # -- extraction summary ------------------------------------------------
    feeds = extraction.get("feeds", [])
    feed_summaries = []
    for f in feeds:
        stage_t = f.get("stage_target") or {}
        std_t = f.get("standard_target") or {}
        feed_summaries.append({
            "feed_name": f.get("feed_name"),
            "source_system": f.get("source_system"),
            "file_name_patterns": f.get("file_name_patterns", []),
            "stage": f"{stage_t.get('schema') or '?'}.{','.join(stage_t.get('tables', []) or ['?'])}",
            "standard": f"{std_t.get('schema') or '?'}.{','.join(std_t.get('tables', []) or ['?'])}",
            "n_rules": len(f.get("validation_rules", []))
                       + (1 if f.get("recycle_rule") else 0),
            "requirement_ids": f.get("requirement_ids", []),
        })
    n_tables = sum(
        len((f.get("stage_target") or {}).get("tables", []))
        + len((f.get("standard_target") or {}).get("tables", []))
        for f in feeds
    )
    n_rules = sum(fs["n_rules"] for fs in feed_summaries)

    # -- gate moment (stage 03 detected -> stage 04 adjudicated) -----------
    p1 = v1.get("_provenance", {})
    p2 = v2.get("_provenance", {})
    g1 = p1.get("grounding", {})
    detected_items = list(p1.get("ambiguities", [])) + list(g1.get("advisory_flagged", []))
    auto_confirmed = list(p2.get("attribution_resolutions", []))
    human_resolved = list(p2.get("human_resolutions", []))
    awaiting = list(p2.get("ambiguities", [])) + list(
        p2.get("grounding", {}).get("advisory_flagged", [])
    )

    totals = er.read_eval_totals(str(directory), doc_id)
    return {
        "set_id": set_id,
        "doc_id": doc_id,
        "is_golden": doc_id == GOLDEN_DOC_ID,
        "extraction_summary": {
            "n_feeds": len(feeds),
            "n_tables": n_tables,
            "n_rules": n_rules,
            "project_id": (extraction.get("project") or {}).get("project_id"),
            "project_name": (extraction.get("project") or {}).get("project_name"),
            "feeds": feed_summaries,
        },
        "gate": {
            "detected": len(detected_items),
            "auto_confirmed": len(auto_confirmed),
            "human_resolved": len(human_resolved),
            "awaiting_human": len(awaiting),
            "detected_items": [
                {"kind": a.get("kind"), "text": a.get("text")} for a in detected_items
            ],
            "auto_confirmed_notes": auto_confirmed,
            "awaiting_items": [
                {"kind": a.get("kind"), "text": a.get("text")} for a in awaiting
            ],
            "strict_checked": g1.get("strict_checked"),
            "strict_failed": len(g1.get("strict_failed", []) or []),
            "advisory_checked": g1.get("advisory_checked"),
            "gate_status": v1.get("status"),
        },
        "verdict": {
            "status": v2.get("status") or v1.get("status"),
            "n_feeds": len(v2.get("feeds", []) or feeds),
        },
        "eval": {
            "available": totals is not None,
            "matched_cells": totals[0] if totals else None,
            "total_cells": totals[1] if totals else None,
            "pct": round(totals[0] / totals[1] * 100, 1) if totals else None,
            "is_golden": doc_id == GOLDEN_DOC_ID,
        },
        "mappings": [
            {
                "feed_name": f.get("feed_name"),
                "rows": _mapping_rows(f),
            }
            for f in v2.get("feeds", [])
        ],
        "workbook_available": (directory / "rendered" / f"{doc_id}.sttm.xlsx").is_file(),
    }


def workbook_path(set_id: str, doc_id: str) -> Path:
    directory = _check_set_id(set_id)
    doc_id = _check_doc_id(doc_id)
    return directory / "rendered" / f"{doc_id}.sttm.xlsx"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/api/demo/config")
def demo_config() -> dict:
    return {
        "provider": "anthropic",
        "call_estimate": CALL_ESTIMATE,
        "api_key_present": api_key_present(),
        "golden_doc_id": GOLDEN_DOC_ID,
        "upload_max_bytes": UPLOAD_MAX_BYTES,
    }


@router.get("/api/demo/documents")
def demo_documents() -> dict:
    """Selectable FRDs: the preloaded demo pair member plus demo uploads.
    Paths are repo-root-relative — the same form POST /api/demo/runs takes."""
    docs = []
    for source, directory in (("preloaded", PRELOADED_DIR), ("upload", UPLOADS_DIR)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.docx")):
            docs.append({
                "path": _rel(path),
                "name": path.name,
                "doc_id": path.stem,
                "source": source,
                "is_golden": path.stem == GOLDEN_DOC_ID,
                "size_bytes": path.stat().st_size,
            })
    return {"documents": docs}


_UPLOAD_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@router.post("/api/demo/uploads")
async def demo_upload(file: UploadFile) -> dict:
    """Accept a .docx FRD into the gitignored demo uploads dir. Prototype-
    scoped: synthetic or anonymized documents only (the UI states this next
    to the control; enforced shape-wise here, policy-wise by the operator)."""
    name = Path(file.filename or "").name
    if not name:
        raise HTTPException(status_code=400, detail="upload has no filename")
    if Path(name).suffix.lower() not in UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="only .docx FRDs are accepted — 01_frd_ingest parses docx only",
        )
    content = await file.read()
    if len(content) > UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"upload exceeds the {UPLOAD_MAX_BYTES} byte limit",
        )
    safe = _UPLOAD_NAME.sub("_", name)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / safe
    dest.write_bytes(content)
    return {
        "path": _rel(dest),
        "name": safe,
        "doc_id": dest.stem,
        "source": "upload",
        "is_golden": dest.stem == GOLDEN_DOC_ID,
        "size_bytes": len(content),
    }


from pydantic import BaseModel  # noqa: E402  (route-model import, keeps top tidy)


class StartDemoRunRequest(BaseModel):
    frd: str  # repo-root-relative path from GET /api/demo/documents


@router.post("/api/demo/runs", status_code=201)
def demo_start_run(body: StartDemoRunRequest) -> dict:
    try:
        run = start_run(body.frd)
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunPreflightError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return run.snapshot()


@router.get("/api/demo/runs")
def demo_list_runs() -> dict:
    return {"runs": [r.snapshot(after_seq=r.seq) for r in _runs.values()]}


def _run_or_404(run_id: str) -> Run:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"No run {run_id!r} in this backend process (run state is in-memory; "
            "a finished run's artifacts are still available under /api/demo/artifacts).",
        )
    return run


@router.get("/api/demo/runs/{run_id}")
def demo_get_run(run_id: str, after: int = 0) -> dict:
    """Polling fallback: full run state, events restricted to seq > after."""
    return _run_or_404(run_id).snapshot(after_seq=after)


@router.get("/api/demo/runs/{run_id}/events")
async def demo_stream_run_events(run_id: str, after: int = 0):
    """SSE stream of run events (console lines, stage transitions, terminal
    status). Ends once the run is terminal and every event has been sent."""
    import asyncio

    from fastapi.responses import StreamingResponse

    run = _run_or_404(run_id)

    async def gen():
        cursor = after
        while True:
            snap = run.snapshot(after_seq=cursor)
            for event in snap["events"]:
                cursor = event["seq"]
                yield f"event: {event['kind']}\ndata: {json.dumps(event)}\n\n"
            if snap["status"] != STATUS_RUNNING and cursor >= snap["seq"]:
                yield (
                    "event: end\ndata: "
                    + json.dumps({"status": snap["status"], "error": snap["error"]})
                    + "\n\n"
                )
                return
            # Block in a worker thread on the run's condition variable so the
            # event loop stays free; 1s cap doubles as an SSE keep-alive tick.
            await asyncio.to_thread(run.wait_for_change, cursor, 1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/demo/artifacts")
def demo_list_artifacts() -> dict:
    return {"artifact_sets": list_artifact_sets()}


@router.get("/api/demo/artifacts/{set_id}/results")
def demo_artifact_results(set_id: str, doc: str) -> dict:
    try:
        return load_results(set_id, doc)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/demo/artifacts/{set_id}/workbook")
def demo_artifact_workbook(set_id: str, doc: str) -> FileResponse:
    try:
        path = workbook_path(set_id, doc)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no rendered STTM workbook for {doc!r} in this artifact set",
        )
    return FileResponse(
        str(path),
        filename=f"{doc}.sttm.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
