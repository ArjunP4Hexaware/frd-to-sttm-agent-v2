"""Client-facing demo endpoints: live runs + replay, mirroring the sibling
brd-to-frd repo's demo_app_react backend (runner.py + artifacts.py) in this
repo's idiom.

Two jobs:

- LIVE runs, two mode-switched implementations behind ONE Run lifecycle
  (`STTM_APP_MODE`, the same knob data_access.py switches on):
  - local (dev laptops, offline tests): spawn the pipeline notebooks
    (01→04) as subprocesses with provider `anthropic`, streaming their
    console (SSE + polling fallback). No pipeline code is imported or
    modified — the notebooks' env knobs and on-disk outputs are the only
    interfaces used.
  - databricks (the deployed App; decided 2026-08-21): trigger the REAL
    bundle-deployed `frd_sttm_pipeline` job via the Jobs API and poll its
    task states. The notebooks run as genuine workspace tasks
    (IS_DATABRICKS True), artifacts land natively in Unity Catalog and
    survive an App restart; the finished run's out-directory is mirrored
    back to the container disk so the results machinery below works
    unchanged. See jobs_runner.py for the whole implementation and the
    workspace prerequisites.
- REPLAY: discover saved demo/e2e artifact sets under local_dev_fixtures/
  and serve a parsed results payload (extraction summary, the stage-03 gate
  moment, the stage-04 verdict, eval-vs-golden, per-mapping rows) that the
  frontend renders identically for a finished live run and a replayed set —
  replay makes zero API calls. In databricks mode the artifact listing
  first rehydrates sets from the UC volume, so finished runs reappear
  after a restart.

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

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

import ambiguity_parsing as ap
import audit
import eval_report as er
import identity as ident
import jobs_runner

# data_access (imported above) has already put src/ on sys.path for the
# deployed App, which does not pip-install the package. Same ordering rule as
# corpus_routes: do not let a formatter sort this above the local imports.
from frdsttm.corpus import load_corpus_index  # noqa: E402

router = APIRouter()

# The same mode knob data_access.py reads. "databricks" = the deployed App:
# live runs go through the Jobs API (jobs_runner.py); "local" = subprocess
# runs. Read once at import, like data_access.APP_MODE.
APP_MODE = os.environ.get("STTM_APP_MODE", "local")
IS_DATABRICKS_APP = APP_MODE == "databricks"

# --------------------------------------------------------------------------- #
# Config (env-overridable; defaults are the local-mode layout)
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = ROOT / "local_dev_fixtures"

GOLDEN_DOC_ID = os.environ.get("STTM_DEMO_GOLDEN_DOC_ID", "demo_frd")
PRELOADED_DIR = LOCAL_ROOT / os.environ.get("STTM_DEMO_PRELOADED_VOLUME", "frd_raw")
UPLOADS_DIR = LOCAL_ROOT / os.environ.get("STTM_DEMO_UPLOADS_VOLUME", "demo_uploads")
RAW_STAGING_VOLUME = os.environ.get("STTM_DEMO_RAW_STAGING_VOLUME", "demo_raw")
# Where the corpus index lives — read by the eligibility gate below. Same
# default as corpus_routes.REFERENCE_DIR; kept as its own constant here rather
# than imported, because corpus_routes imports demo and not the other way.
REFERENCE_DIR = LOCAL_ROOT / os.environ.get("STTM_REFERENCE_VOLUME", "sttm_reference")
LOGS_DIR = ROOT / "data" / "live_run_logs"

SUFFIX_PREFIX = "demo"  # never configurable: the insulation guarantee hangs off it
SUFFIX_TS_FORMAT = "%Y%m%d_%H%M%S"
CATALOG = os.environ.get("CATALOG", "arjun_workspace")
STAGE_TIMEOUT_SECONDS = int(os.environ.get("STTM_DEMO_STAGE_TIMEOUT_SECONDS", "900"))
UPLOAD_MAX_BYTES = int(os.environ.get("STTM_DEMO_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)))
UPLOAD_ALLOWED_EXTENSIONS = (".docx",)
# What a RUN accepts from the corpus (frd_raw): every type 01_frd_ingest can
# parse — mirrors frdsttm.frd_parsing.SUPPORTED_SUFFIXES (this module imports
# no frdsttm code; 00_sharepoint_fetch mirrors the same set for the same
# reason). The upload route stays .docx-only on purpose: uploads were a demo
# convenience; the corpus is the real source and carries whatever SharePoint
# holds.
RUN_ALLOWED_EXTENSIONS = (".docx", ".pdf", ".md", ".markdown", ".txt")

# Shown in the confirmation dialog before a billed run — calls/cost measured
# on the two live E2E runs of 2026-08-07 (docs/LIVE_E2E_2026-08-07.md). The
# wall-clock default differs by mode: ~35s as subprocesses, but a Jobs-API
# run adds serverless task startup per stage, so the databricks default is
# minutes, not seconds. Both remain env-overridable; re-measure on the first
# deployed run and pin STTM_DEMO_EST_SECONDS in app.yaml.
CALL_ESTIMATE = {
    "calls": int(os.environ.get("STTM_DEMO_EST_CALLS", "1")),
    "usd": float(os.environ.get("STTM_DEMO_EST_USD", "0.15")),
    "seconds": int(os.environ.get(
        "STTM_DEMO_EST_SECONDS", "300" if IS_DATABRICKS_APP else "35")),
}

API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


def llm_provider() -> str:
    """The configured extraction provider, read at CALL time.

    Read live rather than snapshotted at import so a test (or a process whose
    environment is set after import) sees the current value.
    """
    return os.environ.get("STTM_LLM_PROVIDER", "").strip().lower()


def needs_anthropic_key() -> bool:
    """Whether a LOCAL live run requires ANTHROPIC_API_KEY.

    False under STTM_LLM_PROVIDER=databricks (2026-08-24): that path reads no
    Anthropic key at all — the workspace credential authenticates against the
    Foundation Model APIs. Without this, a perfectly runnable local
    Databricks-provider run was refused in preflight for a key it never uses.
    """
    return llm_provider() != "databricks"

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
    ("01_frd_ingest.py", "Ingest the FRD"),
    ("02_extract.py", "Extract mappings (AI model call)"),
    ("03_contract_build.py", "Validate, ground & gate"),
    ("04_sttm_render.py", "Render the STTM workbook"),
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

    def __init__(self, doc_id: str, frd_path: Path, suffix: str, log_path: Path,
                 stages=_STAGES, identity: dict | None = None):
        self.id = suffix
        self.suffix = suffix
        self.doc_id = doc_id
        self.frd_path = frd_path
        self.log_path = log_path
        self.artifact_set = f"sttm_out_{suffix}"
        # Governance provenance (docs/AI_GOVERNANCE.md): who started this
        # run and exactly which bytes it ran over. Stamped on the audit
        # events, the run manifest and — via job parameters / env — the
        # notebooks' own runs table. Identity is the identity.py dict.
        self.identity = identity or {"actor": ident.ACTOR_UNKNOWN, "source": ident.SOURCE_UNKNOWN}
        self.frd_sha256 = audit.sha256_of(frd_path) if frd_path.is_file() else None
        self.job_run_id: int | None = None
        self.is_golden = doc_id == GOLDEN_DOC_ID
        self.status = STATUS_RUNNING
        self.error: str | None = None
        self.started_at = _now()
        self.finished_at: str | None = None
        # Databricks-mode runs get the workspace run-page link once the job
        # run starts — the honest "this is really running in Databricks"
        # pointer the progress view renders. Always None in local mode.
        self.run_page_url: str | None = None
        self.stages = [
            {"id": stage_id, "label": label, "status": "pending"}
            for stage_id, label in stages
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
                "run_page_url": self.run_page_url,
                "job_run_id": self.job_run_id,
                "triggered_by": self.identity.get("actor"),
                "frd_sha256": self.frd_sha256,
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
    """The FRD must be an existing parseable document inside the preloaded
    (corpus) dir or the demo uploads dir — resolved and containment-checked so a crafted path
    can never reach outside them (and never into fixtures/ or curated
    locations)."""
    candidate = (ROOT / frd_rel).resolve()
    allowed = [PRELOADED_DIR.resolve(), UPLOADS_DIR.resolve()]
    if not any(candidate.is_relative_to(root) for root in allowed):
        raise RunPreflightError(
            "FRD path must be inside the preloaded documents or demo uploads directory"
        )
    if candidate.suffix.lower() not in RUN_ALLOWED_EXTENSIONS:
        raise RunPreflightError(
            f"FRD must be one of {', '.join(RUN_ALLOWED_EXTENSIONS)} (what 01_frd_ingest parses)")
    if not candidate.is_file():
        raise RunPreflightError(f"FRD file not found: {frd_rel}")
    return candidate


def _subprocess_env(suffix: str, raw_volume: str, identity: dict | None = None,
                    run_label: str | None = None) -> dict:
    """The full insulation contract in one place: every output knob embeds
    the demo suffix; mock is stripped; the provider is pinned to anthropic;
    the API key is injected (from env or .env) without ever being logged.
    `identity` / `run_label` become TRIGGERED_BY / RUN_LABEL — the env-var
    twins of the `triggered_by` / `run_label` job parameters — so 04 stamps
    the same provenance on the runs table in both modes."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["SCHEMA"] = f"sttm_agent_{suffix}"
    env["OUT_VOLUME"] = f"sttm_out_{suffix}"
    env["PREVIEW_VOLUME"] = f"sttm_out_{suffix}"
    env["RAW_VOLUME"] = raw_volume
    env["STTM_LLM_PROVIDER"] = "anthropic"
    env.pop("STTM_MOCK_EXTRACTION", None)
    env["TRIGGERED_BY"] = (identity or {}).get("actor") or ident.ACTOR_UNKNOWN
    env["RUN_LABEL"] = run_label or suffix
    if not env.get(API_KEY_ENV_VAR, "").strip():
        key = _api_key_from_dotenv()
        if key:
            env[API_KEY_ENV_VAR] = key
    return env


def run_manifest(run: Run) -> dict:
    """The self-describing record that travels WITH the artifact set
    (`<set>/run_manifest.json`): what ran, over which bytes, started by whom,
    with what outcome. In databricks mode it is also uploaded next to the
    artifacts in the UC out dir, so the durable copy is self-describing
    without the app. The extraction's model/prompt/usage facts are 02's own
    sidecar (`extractions/<doc>.extraction_meta.json`); this is the app-level
    half."""
    return {
        "run_id": run.id,
        "suffix": run.suffix,
        "artifact_set": run.artifact_set,
        "doc_id": run.doc_id,
        "frd_file": run.frd_path.name,
        "frd_sha256": run.frd_sha256,
        "triggered_by": run.identity.get("actor"),
        "triggered_by_source": run.identity.get("source"),
        "app_mode": APP_MODE,
        "job_run_id": run.job_run_id,
        "run_page_url": run.run_page_url,
        "status": run.status,
        "error": run.error,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _write_run_manifest(run: Run, w=None) -> None:
    """Write the manifest into the local artifact set; in databricks mode
    also into the UC out dir. Never raises — the run's outcome is already
    decided and recorded; a failed manifest write is reported on the run's
    console, not turned into a failed run."""
    payload = json.dumps(run_manifest(run), indent=2, ensure_ascii=False)
    try:
        local_dir = LOCAL_ROOT / run.artifact_set
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "run_manifest.json").write_text(payload, encoding="utf-8")
        if IS_DATABRICKS_APP and w is not None:
            jobs_runner.upload_run_manifest(w, run.suffix, payload.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — reported, never fatal (see docstring)
        run._emit("console", f"WARNING: run_manifest.json not written ({type(exc).__name__}: {exc})")


def _record_run_finished(run: Run) -> None:
    try:
        audit.record("run.finished", run.identity, run_id=run.id, doc_id=run.doc_id,
                     artifact_set=run.artifact_set, status=run.status, error=run.error,
                     job_run_id=run.job_run_id, frd_sha256=run.frd_sha256)
    except audit.AuditWriteError as exc:
        # The run already happened; the START was recorded (or the run was
        # refused). Surface the failure loudly on the run's console.
        run._emit("console", f"WARNING: audit event run.finished not written — {exc}")


def _run_worker(run: Run, raw_volume: str) -> None:
    global _active_run_id
    try:
        run.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = _subprocess_env(run.suffix, raw_volume, identity=run.identity, run_label=run.id)
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
        # Release the single-run slot FIRST (terminal status == slot free is
        # the contract pollers rely on), then persist the outcome.
        with _active_lock:
            _active_run_id = None
        _write_run_manifest(run)
        _record_run_finished(run)


def _run_worker_databricks(run: Run) -> None:
    """Databricks-mode worker: stage the FRD to the run's UC raw dir,
    trigger the bundle job, poll it to a terminal state, then mirror the
    run's UC out-directory back so the results machinery works unchanged.
    Every step is jobs_runner's; this function only owns the Run lifecycle
    (same try/finally shape as the subprocess worker above)."""
    global _active_run_id
    w = None
    try:
        w = jobs_runner._workspace_client()
        job_id = jobs_runner.resolve_job_id(w)
        run._emit("console", f"staging {run.frd_path.name} -> {jobs_runner.raw_dir_for(run.suffix)}")
        jobs_runner.stage_frd(w, run.suffix, run.frd_path)
        run_id = jobs_runner.start_job_run(w, job_id, run.suffix,
                                           triggered_by=run.identity.get("actor"))
        try:
            url = w.jobs.get_run(run_id).run_page_url
        except Exception:  # noqa: BLE001 — the link is a convenience, never worth failing a run over
            url = None
        with run._lock:
            run.run_page_url = url
            run.job_run_id = run_id
        run._emit("console", f"job run {run_id} started" + (f" — {url}" if url else ""))
        jobs_runner.poll_job_run(w, run, run_id)
        n = jobs_runner.download_run_artifacts(w, run.suffix, LOCAL_ROOT / run.artifact_set)
        run._emit(
            "console",
            f"mirrored {n} artifact file(s) from {jobs_runner.out_dir_for(run.suffix)} "
            f"(durable copy stays in Unity Catalog)",
        )
        run._finish(STATUS_DONE)
    except Exception as exc:  # noqa: BLE001 — a stuck "running" spinner is the one outcome to prevent
        run._finish(STATUS_FAILED, f"{type(exc).__name__}: {exc}")
    finally:
        with _active_lock:
            _active_run_id = None
        _write_run_manifest(run, w)
        _record_run_finished(run)


def _check_eligible(doc_id: str) -> None:
    """Refuse a run on an FRD the corpus says is not generatable.

    THE RULE (Arjun, 2026-08-27): only an FRD that has a matching vendor data
    dictionary AND does not already have an approved STTM may be run.

    Enforced HERE, on the server, and not only in the picker — hiding a button
    is a presentation choice, and this is a rule about spending money and about
    not regenerating over an approved system of record. The picker is the
    convenience; this is the gate. The verdict itself comes from
    frdsttm.corpus.eligibility_of so there is exactly one implementation.

    SCOPE, and it is narrow on purpose. The rule is about FRDs THE CORPUS
    KNOWS. Two cases are deliberately let through:

    * **no corpus index at all** — local development and the offline smoke run
      against volumes with no index, and refusing there would break a path the
      rule has nothing to do with;
    * **a doc_id the index does not list** — an uploaded document, or a file
      staged by hand. "Has a dictionary and no STTM" is not a question that
      can be asked about a document the corpus has never paired, and answering
      it "no" would block the upload path over a rule that does not apply.

    That is why the lookup is `index["eligibility"].get(doc_id)` rather than
    `eligibility_of`, which is conservative BY DESIGN for the picker: there,
    an unknown doc must not render a live button. Here, an unknown doc is
    simply out of scope. Two different questions, two different defaults —
    kept apart deliberately.

    A CORRUPT index still raises through load_corpus_index, as everywhere else.
    """
    try:
        index = load_corpus_index(REFERENCE_DIR)
    except Exception:  # noqa: BLE001 — a corpus problem is not this run's error
        return
    if index is None:
        return
    verdict = index.get("eligibility", {}).get(doc_id)
    if verdict is None:
        return                      # not a corpus FRD — the rule does not apply
    if not verdict["generatable"]:
        raise RunPreflightError(f"{doc_id} cannot be generated. {verdict['reason']}")


def start_run(frd_rel: str, identity: dict | None = None) -> Run:
    global _active_run_id

    frd_path = _validate_frd(frd_rel)
    _check_eligible(frd_path.stem)
    # In databricks mode the Anthropic key lives in the workspace secret
    # scope and is checked by the job's own extract task (which fails loudly
    # without it); the app process neither has nor needs the key.
    if not IS_DATABRICKS_APP and needs_anthropic_key() and not api_key_present():
        raise RunPreflightError(
            f"{API_KEY_ENV_VAR} is not set (environment or repo .env). A live run "
            "makes billed Anthropic API calls and cannot start without it. "
            "(Set STTM_LLM_PROVIDER=databricks to use this workspace's "
            "Databricks-served Claude instead, which needs no Anthropic key.)"
        )

    with _active_lock:
        if _active_run_id is not None:
            raise RunConflictError(
                f"A live run is already in progress (id={_active_run_id}). "
                "One run at a time — wait for it to finish."
            )
        suffix = _new_suffix()
        log_path = LOGS_DIR / f"{suffix}.log"
        if IS_DATABRICKS_APP:
            run = Run(frd_path.stem, frd_path, suffix, log_path,
                      stages=jobs_runner.JOB_STAGES, identity=identity)
        else:
            # Stage the chosen document into a per-run raw dir: 01_frd_ingest
            # ingests its whole RAW_VOLUME, so the run must see exactly one
            # file — and the preloaded/frd_raw dir is never handed to a run
            # directly. (The databricks worker does the volume-side twin of
            # this staging itself.)
            raw_volume = f"{RAW_STAGING_VOLUME}/{suffix}"
            raw_dir = LOCAL_ROOT / raw_volume
            raw_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(frd_path, raw_dir / frd_path.name)
            run = Run(frd_path.stem, frd_path, suffix, log_path, identity=identity)
        # Recorded BEFORE the worker starts: if the audit write fails in
        # databricks mode the run does not start (fail-closed — see audit.py).
        try:
            audit.record("run.started", run.identity, run_id=run.id, doc_id=run.doc_id,
                         artifact_set=run.artifact_set, frd_file=frd_path.name,
                         frd_sha256=run.frd_sha256, frd_path=_rel(frd_path))
        except audit.AuditWriteError:
            raise
        _runs[run.id] = run
        _active_run_id = run.id

    if IS_DATABRICKS_APP:
        threading.Thread(target=_run_worker_databricks, args=(run,), daemon=True).start()
    else:
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


QUOTE_LIMIT = 200
_TRAILING_QUOTE = re.compile(r": '(.*)'$")


def _truncate_words(text: str, limit: int = QUOTE_LIMIT) -> str:
    """Full text up to `limit` chars, else a word-boundary cut + ellipsis —
    never a mid-word chop."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip()
    return f"{cut}…"


def _clean_note(note: str, full_rules: list[str]) -> str:
    """Repair a stored attribution-resolution note whose trailing rule quote
    was hard-sliced mid-word by the pipeline's old `rule[:90]` formatting
    (artifact sets written before the fix in 04_sttm_render._quote_rule
    still carry it — e.g. \"...moving it to the reje'\"). If the quote is a
    prefix of a known full rule, swap the full (cleanly truncated) rule back
    in; otherwise trim the quote itself at a word boundary."""
    m = _TRAILING_QUOTE.search(note)
    if not m:
        return note
    quoted = m.group(1)
    prefix = quoted.rstrip("…").strip()
    replacement = next((r for r in full_rules if r.strip().startswith(prefix)), None)
    if replacement is not None:
        display = _truncate_words(replacement)
    elif quoted and quoted[-1] in ".!?…\"'":
        # Ends like a complete sentence — treat as full text, cap at the limit.
        display = _truncate_words(quoted)
    else:
        # Old hard slice with no recoverable full text: the tail is almost
        # certainly a cut word — drop it and mark the elision.
        display = f"{quoted.rsplit(' ', 1)[0].rstrip()}…" if " " in quoted else f"{quoted}…"
    return f"{note[: m.start()]}: '{display}'"


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
    # Every full rule text this run knows, for repairing hard-sliced quotes
    # in notes written by the pre-fix pipeline (see _clean_note).
    full_rules = [
        r for f in feeds for r in (f.get("validation_rules") or [])
    ] + [f["recycle_rule"] for f in feeds if f.get("recycle_rule")] + [
        (a.get("context") or {}).get("rule")
        for a in detected_items
        if (a.get("context") or {}).get("rule")
    ]
    auto_confirmed = [
        _clean_note(n, full_rules) for n in p2.get("attribution_resolutions", [])
    ]
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
        # Template decision (2026-08-22, docs/TEMPLATE_ARCHITECTURE.md):
        # written by 04 into the v2 contract's provenance. None for artifact
        # sets rendered before the template architecture existed.
        "template": (v2.get("_provenance") or {}).get("template_decision"),
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
# Human-in-the-loop on a finished run (2026-08-22 evening)
# --------------------------------------------------------------------------- #
# The slides' step 3: after the agent runs, a person resolves what the gate
# could not settle, and the STTM is re-rendered with those decisions folded
# in. Mechanism — identical to the legacy review flow (orchestration.py), so
# there is ONE persistence format: a resolution is merged into the run's v1
# contract JSON (`_provenance.human_resolutions`, keyed by the stable
# ambiguity id) and a RE-RENDER re-runs stage 04 only, which applies them
# (`apply_human_resolutions`) ahead of every automatic path and rewrites the
# v2 contract + workbook + report. No re-extraction, hence no second billed
# call. Local mode: 04 as a subprocess with the run's own insulation env.
# Databricks mode: the edited contract is uploaded to the run's UC out dir
# and the render-only bundle job `frd_sttm_render` runs against the same
# suffix (resources/frd_sttm_render_job.yml); artifacts are mirrored back.

def _contract_v1_path(set_id: str, doc_id: str) -> Path:
    return _check_set_id(set_id) / "contracts" / f"{_check_doc_id(doc_id)}.contract.json"


def _review_items(set_id: str, doc_id: str) -> tuple[dict, list[dict]]:
    path = _contract_v1_path(set_id, doc_id)
    contract = _load_json(path)
    if contract is None:
        raise FileNotFoundError(f"no contract JSON for {doc_id!r} in {set_id}")
    return contract, [ap.to_gated_item_dict(contract, it) for it in ap.gated_items(contract)]


_rerender_lock = threading.Lock()
_rerenders: dict[str, dict] = {}   # set_id -> state


def _rerender_state(set_id: str) -> dict:
    with _rerender_lock:
        return dict(_rerenders.get(set_id) or {
            "state": "idle", "started_at": None, "finished_at": None,
            "error": None, "run_page_url": None})


def _set_rerender(set_id: str, **fields) -> None:
    with _rerender_lock:
        _rerenders.setdefault(set_id, {"state": "idle", "started_at": None,
                                       "finished_at": None, "error": None,
                                       "run_page_url": None}).update(fields)


def _suffix_of(set_id: str) -> str:
    return set_id[len("sttm_out_"):]


def _rerender_worker(set_id: str, doc_id: str, identity: dict | None = None) -> None:
    suffix = _suffix_of(set_id)
    identity = identity or {"actor": ident.ACTOR_UNKNOWN, "source": ident.SOURCE_UNKNOWN}
    job_run_id = None
    try:
        if IS_DATABRICKS_APP:
            w = jobs_runner._workspace_client()
            jobs_runner.upload_contract(w, suffix, doc_id,
                                        _contract_v1_path(set_id, doc_id).read_bytes())
            job_id = jobs_runner.resolve_render_job_id(w)
            run_id = jobs_runner.start_render_job(w, job_id, suffix,
                                                  triggered_by=identity.get("actor"))
            job_run_id = run_id
            try:
                url = w.jobs.get_run(run_id).run_page_url
            except Exception:  # noqa: BLE001 — the link is a convenience
                url = None
            _set_rerender(set_id, run_page_url=url)
            jobs_runner.wait_for_run(w, run_id)
            jobs_runner.download_run_artifacts(w, suffix, LOCAL_ROOT / set_id)
        else:
            env = _subprocess_env(suffix, f"{RAW_STAGING_VOLUME}/{suffix}",
                                  identity=identity, run_label=f"{suffix}:rerender")
            log_path = LOGS_DIR / f"{suffix}.rerender.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    [sys.executable, str(ROOT / "notebooks" / "04_sttm_render.py")],
                    cwd=str(ROOT), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                returncode = proc.wait(timeout=STAGE_TIMEOUT_SECONDS)
            if returncode != 0:
                raise RuntimeError(
                    f"04_sttm_render.py exited with code {returncode} — see {_rel(log_path)}")
        _set_rerender(set_id, state="done", finished_at=_now())
    except Exception as exc:  # noqa: BLE001 — a stuck 'running' is the one outcome to prevent
        _set_rerender(set_id, state="failed", finished_at=_now(),
                      error=f"{type(exc).__name__}: {exc}")
    finally:
        state = _rerender_state(set_id)
        try:
            audit.record("rerender.finished", identity, set_id=set_id, doc_id=doc_id,
                         status=state.get("state"), error=state.get("error"),
                         job_run_id=job_run_id)
        except audit.AuditWriteError as exc:
            _set_rerender(set_id, error=(state.get("error") or "") + f" [audit: {exc}]")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/api/demo/config")
def demo_config() -> dict:
    return {
        # The provider actually configured, not a hardcoded literal: with
        # STTM_LLM_PROVIDER=databricks this is "databricks", and reporting
        # "anthropic" there would be simply untrue.
        "provider": llm_provider() or "anthropic",
        # "local" = subprocess runs gated on a local API key; "databricks" =
        # Jobs-API runs, where the key lives in the workspace secret scope so
        # api_key_present is not a readiness signal (the frontend gates on
        # mode, and a missing secret fails loudly inside the extract task).
        "mode": APP_MODE,
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
async def demo_upload(request: Request, file: UploadFile) -> dict:
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
    who = ident.resolve_identity(request)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / safe
    dest.write_bytes(content)
    _audit_or_502("upload.received", who, file=safe, doc_id=dest.stem,
                  size_bytes=len(content), sha256=audit.sha256_of(dest))
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
def demo_start_run(body: StartDemoRunRequest, request: Request) -> dict:
    who = ident.resolve_identity(request)   # 401 in databricks mode without a forwarded identity
    try:
        run = start_run(body.frd, identity=who)
    except RunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunPreflightError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except audit.AuditWriteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return run.snapshot()


def _audit_or_502(kind: str, who: dict, **fields) -> dict:
    """Record an event or raise the caller's 502 (databricks-mode volume
    write failure). Used by the routes whose governed action is the
    recording itself (download, resolution, upload, sync trigger)."""
    try:
        return audit.record(kind, who, **fields)
    except audit.AuditWriteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/demo/audit")
def demo_audit(request: Request, limit: int = audit.LIST_LIMIT_DEFAULT, kind: str | None = None) -> dict:
    """The audit trail, newest first — who ran / resolved / re-rendered /
    downloaded what, when. Reading it is itself identity-gated in databricks
    mode (same 401 rule as every governed action). In databricks mode the
    UC volume is mirrored down first so events written by another App
    instance (or before a restart) are listed too."""
    ident.resolve_identity(request)
    if IS_DATABRICKS_APP:
        try:
            w = jobs_runner._workspace_client()
            jobs_runner.mirror_volume_dir(w, audit.volume_events_dir(), audit.local_events_dir())
        except Exception as exc:  # noqa: BLE001 — an unreadable trail is a 502 naming the fix, never an empty list
            raise HTTPException(
                status_code=502,
                detail=f"could not read the audit volume {audit.volume_events_dir()} "
                       f"({type(exc).__name__}: {exc}) — grant the app's service "
                       "principal READ VOLUME on it.") from exc
    events = audit.list_events(limit=limit, kind=kind)
    return {"events": events, "n_events": len(events), "kinds": list(audit.KINDS),
            "store": audit.volume_events_dir() if IS_DATABRICKS_APP else _rel(audit.local_events_dir())}


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
    if IS_DATABRICKS_APP:
        # Runs finished before an App restart live durably in the UC volume;
        # mirror any that are missing locally before scanning. An unreadable
        # volume is a 502 naming the fix, never an empty listing.
        try:
            jobs_runner.rehydrate_artifact_sets(LOCAL_ROOT)
        except jobs_runner.JobRunnerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
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
def demo_artifact_workbook(set_id: str, doc: str, request: Request) -> FileResponse:
    """Serve the rendered workbook — and record the HAND-OFF. The repo never
    writes to SharePoint (read-only by construction), so a reviewer taking
    the workbook out of the app is the moment a generated STTM leaves the
    governed boundary; the event carries the sha256 + size of exactly the
    bytes served so an uploaded workbook can be matched back to its run."""
    who = ident.resolve_identity(request)
    try:
        path = workbook_path(set_id, doc)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no rendered STTM workbook for {doc!r} in this artifact set",
        )
    _audit_or_502("workbook.downloaded", who, set_id=set_id, doc_id=doc,
                  file=f"{doc}.sttm.xlsx", sha256=audit.sha256_of(path),
                  size_bytes=path.stat().st_size)
    return FileResponse(
        str(path),
        filename=f"{doc}.sttm.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------------------------------------------------------------- #
# Routes — human-in-the-loop review of a finished run
# --------------------------------------------------------------------------- #
@router.get("/api/demo/artifacts/{set_id}/review")
def demo_review(set_id: str, doc: str) -> dict:
    """Every gated item of this run with its existing resolution (if any),
    plus the re-render state — the review panel's whole world."""
    try:
        _contract, items = _review_items(set_id, doc)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "set_id": set_id, "doc_id": doc, "items": items,
        "n_items": len(items),
        "n_resolved": sum(1 for it in items if it["resolution"]),
        "rerender": _rerender_state(set_id),
    }


class DemoResolution(BaseModel):
    ambiguity_id: str
    kind: str
    resolution_type: str
    chosen_candidate: str | None = None
    rationale: str | None = None
    candidates_snapshot: list[str] = []


@router.post("/api/demo/artifacts/{set_id}/resolutions")
def demo_submit_resolution(set_id: str, doc: str, body: DemoResolution, request: Request) -> dict:
    """Record ONE human resolution into the run's v1 contract — same
    validation and persistence as orchestration.py's run-scoped route, so
    04 applies it identically. Nothing is re-rendered here; that is the
    reviewer's explicit next click. `resolved_by` is the forwarded identity
    (identity.py) — the human-in-the-loop control is only a control if the
    human is named."""
    who = ident.resolve_identity(request)
    try:
        contract, items = _review_items(set_id, doc)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    by_id = {it["id"]: it for it in ap.gated_items(contract)}
    ambiguity = by_id.get(body.ambiguity_id)
    if ambiguity is None:
        raise HTTPException(status_code=400,
                            detail="ambiguity_id does not match any gated item in this run")
    if body.resolution_type not in ("candidate_pick", "free_text", "none_of_these"):
        raise HTTPException(status_code=400, detail=f"unknown resolution_type {body.resolution_type!r}")
    error = ap.validate_resolution_submission(
        ambiguity, body.resolution_type, body.chosen_candidate, body.rationale)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if body.resolution_type == "free_text" and ambiguity["has_candidates"]:
        # The pick is structural, not a convention: 04 never applies free
        # text to a candidate-having ambiguity, so accepting it here would
        # record a "resolution" that changes nothing. Client and server agree.
        raise HTTPException(
            status_code=400,
            detail="this ambiguity has candidates — pick one or choose none_of_these "
                   "(free text is recorded as rationale only, never applied)")
    with _rerender_lock:
        if (_rerenders.get(set_id) or {}).get("state") == "running":
            raise HTTPException(status_code=409, detail="a re-render is in progress — wait for it")
    record = {
        "ambiguity_id": body.ambiguity_id,
        "kind": body.kind,
        "resolution_type": body.resolution_type,
        "chosen_candidate": body.chosen_candidate,
        "rationale": body.rationale,
        "candidates_snapshot": body.candidates_snapshot or ambiguity["candidates"],
        "resolved_at": _now(),
        "resolved_by": who["actor"],
    }
    ap.merge_resolution(contract, record)
    path = _contract_v1_path(set_id, doc)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    _audit_or_502("resolution.recorded", who, set_id=set_id, doc_id=doc,
                  ambiguity_id=body.ambiguity_id, kind_of_ambiguity=body.kind,
                  resolution_type=body.resolution_type,
                  chosen_candidate=body.chosen_candidate,
                  has_rationale=bool(body.rationale))
    return ap.to_gated_item_dict(contract, ambiguity)


@router.post("/api/demo/artifacts/{set_id}/rerender", status_code=202)
def demo_rerender(set_id: str, doc: str, request: Request) -> dict:
    """Fold the recorded resolutions into the STTM: re-run stage 04 only
    (no model call, nothing billed) and return 202; poll GET .../review."""
    who = ident.resolve_identity(request)
    try:
        _contract, _items = _review_items(set_id, doc)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    with _rerender_lock:
        if (_rerenders.get(set_id) or {}).get("state") == "running":
            raise HTTPException(status_code=409, detail="a re-render is already running")
        _rerenders[set_id] = {"state": "running", "started_at": _now(), "finished_at": None,
                              "error": None, "run_page_url": None}
    try:
        audit.record("rerender.started", who, set_id=set_id, doc_id=doc)
    except audit.AuditWriteError as exc:
        _set_rerender(set_id, state="failed", finished_at=_now(), error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    threading.Thread(target=_rerender_worker, args=(set_id, doc, who), daemon=True).start()
    return _rerender_state(set_id)


@router.get("/api/demo/artifacts/{set_id}/rerender")
def demo_rerender_state(set_id: str) -> dict:
    try:
        _check_set_id(set_id)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _rerender_state(set_id)
