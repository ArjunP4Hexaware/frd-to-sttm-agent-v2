"""Upload -> pipeline-run -> review -> rendered-STTM orchestration.

Lets a user upload an FRD .docx here and get a rendered STTM .xlsx back,
with a review step in between if the pipeline gates on unresolved
ambiguities. First-demo scope, deliberately simplified in two ways (per the
task this was built against -- not oversights):

- Mock extraction only, demo-fixture-scoped: `_mock_extractions.py`'s
  two-tier matcher (see notebooks/_mock_extractions.py) is a *general*
  matcher that also correctly recognizes the CAQH fixture (existing
  terminal-driven runs of the pipeline depend on that). This orchestration
  layer is narrower: an upload is only accepted into the demo flow if it
  resolves specifically to the demo_frd mock spec (see `_assert_demo_scoped`
  below) -- any other document (including a perfectly legitimate CAQH
  upload) is refused with a clear, structured error, consistent with "this
  first working demo is demo-fixture-only."
- In-memory run-state tracking (`RUNS` below), no external queue or
  database. A backend restart loses all in-flight run state; fine for a
  local single-process demo, not something to build on for anything real.

Per-run isolation: every pipeline stage is already fully parameterized by
env vars (see notebooks/01-04's `_param()` cells), so each run gets its own
`RAW_VOLUME` (so 01_frd_ingest.py's full-refresh `frd_documents` table
never sees another run's file), its own `SCHEMA` (so the local Delta-table-
equivalent warehouse paths never collide), and its own `OUT_VOLUME` (so
extractions/contracts/reports/rendered never collide). `REFERENCE_VOLUME`
is deliberately left at its shared default -- it's read-only input, not
per-run state.

Every notebook executes its full body at module top level with no `main()`
guard (needed so Databricks can run them as notebooks), so each stage is
invoked as a subprocess (`python notebooks/0N_....py`), not an in-process
import.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import ambiguity_parsing as ap
import data_access as da
import eval_report as er

# "uvicorn.error" rather than __name__: this backend is always started under
# uvicorn (see app.py's docstring), and uvicorn attaches a handler to that
# logger at INFO. A plain __name__ logger inherits a root that has no handler,
# so INFO records would be dropped entirely and ERROR would only surface via
# logging.lastResort -- and a promotion that fails silently is indistinguish-
# able from the feature not working at all, which is the one outcome this
# hook must never produce.
_log = logging.getLogger("uvicorn.error")

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTEBOOKS_DIR = REPO_ROOT / "notebooks"
LOCAL_ROOT = REPO_ROOT / "local_dev_fixtures"

# notebooks/_mock_extractions.py (and its own `_models` import) is a plain,
# Databricks-magic-free module -- safe to import directly, unlike
# 01-04_*.py which run pipeline driver code at import time (see this
# module's docstring). Only its tier-1 filename matcher is reused here;
# see _assert_demo_scoped().
sys.path.insert(0, str(NOTEBOOKS_DIR))
from _mock_extractions import _tier1_filename_match  # noqa: E402

# The only fixture this orchestration demo accepts -- see module docstring.
_DEMO_KEY = "demo_frd"

RunStatus = Literal[
    "pending", "running_ingest", "running_extract", "running_contract_build",
    "running_sttm_render", "gated", "done", "error",
]

_STAGE_STATUS: dict[str, RunStatus] = {
    "01_frd_ingest.py": "running_ingest",
    "02_extract.py": "running_extract",
    "03_contract_build.py": "running_contract_build",
    "04_sttm_render.py": "running_sttm_render",
}

# In-memory only, by design (see module docstring) -- keyed by run_id.
RUNS: dict[str, dict] = {}

router = APIRouter()


# --------------------------------------------------------------------------- #
# Run-state helpers
# --------------------------------------------------------------------------- #
def _get_run_or_404(run_id: str) -> dict:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run with run_id={run_id!r}")
    return run


def _assert_demo_scoped(doc_id: str) -> None:
    """This orchestration demo is demo-fixture-only (see module docstring) --
    refuse an upload whose filename doesn't confidently resolve to the demo_frd
    mock spec via the same tier-1 filename matcher notebooks/_mock_extractions.py
    uses, rather than silently running a CAQH (or unrelated) upload through
    the pipeline and surprising the reviewer with the wrong mock data
    downstream.

    Filename-only (tier 1), not the full two-tier match: reading the
    uploaded .docx's real content here would mean duplicating
    01_frd_ingest.py's docx-to-markdown conversion in this module, which
    that notebook can't expose as an import (its whole body runs at import
    time -- the same constraint documented throughout this file and
    04_sttm_render.py). Tier 1 alone is sufficient for this demo's actual
    inputs: the demo/CAQH fixture filenames, and demo_frd filename variants
    (e.g. a re-uploaded "... (1).docx" copy), all resolve unambiguously by
    filename. A genuinely neutral filename with real demo-fixture *content*
    would pass this gate incorrectly -- see notebooks/_mock_extractions.py's
    `mock_spec_for()` for the full two-tier match, which is still what
    actually selects the mock spec during 02_extract.py and would still
    raise MockSpecNotFoundError for a non-fixture upload even if this
    filename-only gate let it through."""
    match = _tier1_filename_match(doc_id)
    if match != _DEMO_KEY:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This demo's mock-extraction pipeline is scoped to the demo fixture only. "
                f"'{doc_id}' did not match it by filename "
                f"(matched: {match!r} instead)."
            ),
        )


# --------------------------------------------------------------------------- #
# Subprocess pipeline execution
# --------------------------------------------------------------------------- #
def _run_env(run: dict) -> dict:
    """Build the subprocess env for one pipeline stage.

    Always mock: upload-driven runs are pinned to `STTM_MOCK_EXTRACTION=1`
    regardless of what the parent process exported. Stage 2 runs here as a
    subprocess (see _run_stage), so this dict is the only thing standing
    between this app and a live billed extraction. An inherited
    STTM_LLM_PROVIDER is cleared deliberately: a live provider alongside the
    mock flag is the conflict 02_extract.py refuses to resolve.
    """
    env = os.environ.copy()
    env["RAW_VOLUME"] = f"uploads/{run['run_id']}"
    env["SCHEMA"] = run["schema"]
    env["OUT_VOLUME"] = f"sttm_out_runs/{run['run_id']}"
    env["STTM_MOCK_EXTRACTION"] = "1"
    env.pop("STTM_LLM_PROVIDER", None)
    return env


def _run_stage(stage_file: str, env: dict) -> tuple[bool, str]:
    # sys.executable, not a bare "python": the backend is normally started
    # via the repo venv's interpreter, and on a stock macOS PATH there is no
    # `python` binary at all (only `python3`) unless that venv happens to be
    # activated in the shell. A bare "python" raises FileNotFoundError inside
    # the BackgroundTask, which leaves the run pinned in a running_* status
    # forever -- an eternal progress spinner with no error. Running the same
    # interpreter that hosts this backend is both correct and stable
    # regardless of how the server was launched.
    proc = subprocess.run(
        [sys.executable, str(NOTEBOOKS_DIR / stage_file)],
        env=env, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-40:])
        return False, tail or f"{stage_file} exited with code {proc.returncode}"
    return True, ""


def _background(fn, run_id: str) -> None:
    """Wrapper every background pipeline entrypoint is registered through.

    The stage helpers below only set an 'error' status for the failure they
    anticipate (a stage exiting non-zero). Anything raising instead --
    subprocess.TimeoutExpired from the 300s cap, a FileNotFoundError for a
    missing interpreter, a malformed contract JSON -- would otherwise
    propagate out of the BackgroundTask and leave the run pinned in a
    non-terminal running_* status with error=None, which the frontend
    renders as a progress spinner that never resolves. Converting that into
    a terminal 'error' means an unexpected failure is at least visible.
    """
    try:
        fn(run_id)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad; see docstring
        run = RUNS.get(run_id)
        if run is not None:
            run["status"] = "error"
            run["error"] = f"Unexpected {type(exc).__name__} while running the pipeline:\n{exc}"


def _run_pipeline_through_gate(run_id: str) -> None:
    """Background-task entrypoint (registered as a plain, non-async
    callable -- FastAPI runs those in a worker thread via BackgroundTasks,
    so the blocking subprocess.run() calls below never stall the event loop
    that GET /status polling depends on). Runs stages 01-03; stops at
    'gated' if the resulting contract still has unresolved ambiguities,
    otherwise proceeds straight through to render."""
    run = RUNS[run_id]
    env = _run_env(run)

    for stage_file in ("01_frd_ingest.py", "02_extract.py", "03_contract_build.py"):
        run["status"] = _STAGE_STATUS[stage_file]
        ok, message = _run_stage(stage_file, env)
        if not ok:
            run["status"] = "error"
            run["error"] = f"{stage_file} failed:\n{message}"
            return

    contract_path = Path(run["out_root"]) / "contracts" / f"{run['doc_id']}.contract.json"
    if not contract_path.is_file():
        run["status"] = "error"
        run["error"] = (
            f"03_contract_build.py produced no contract for doc_id={run['doc_id']!r} "
            f"-- see reports/{run['doc_id']}.report.md (likely a schema-validation FAIL)."
        )
        return

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if ap.gated_items(contract):
        run["status"] = "gated"
        return

    _run_render_stage(run_id)


def _promote_contract(run: dict) -> None:
    """Copy a completed run's contract into the shared directory the landing
    document list scans, so a finished run shows up under "Existing documents".

    Per-run isolation (see module docstring) means a run's outputs live under
    `sttm_out_runs/<run_id>/`, which `GET /api/documents` never looks at -- it
    scans `data_access.LOCAL_CONTRACTS_DIR` only. This is the one deliberate
    bridge between the two. The destination is taken from `da` rather than
    rebuilt here so the two paths cannot drift apart.

    Sources `<out_root>/contracts/<doc_id>.contract.json`, NOT the
    `.contract.v2.json` that 04_sttm_render.py writes alongside it:

    - the landing glob is `*.contract.json`, which does not match the v2 name
      (data_access.list_contract_doc_ids); and
    - only the v1 still lists the gated ambiguities next to
      `_provenance.human_resolutions`, so it is the one that makes
      list_documents() report 3 items / 3 resolved. apply_human_resolutions()
      empties `_provenance.ambiguities` on its way to the v2, which would
      render as a "0 of 0 resolved" card instead.

    Best-effort by design. A promotion failure is logged at ERROR and
    swallowed: the run itself has already succeeded and every
    /api/runs/<run_id>/* route still serves its outputs from out_root, so
    failing the run here would misreport what happened. Local mode only,
    like the rest of this module.
    """
    src = Path(run["out_root"]) / "contracts" / f"{run['doc_id']}.contract.json"
    dest_dir = da.LOCAL_CONTRACTS_DIR
    dest = dest_dir / f"{run['doc_id']}.contract.json"
    tmp_path: Path | None = None
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Stage into the destination directory, then os.replace: same
        # filesystem, so the swap is atomic and the landing glob can never
        # observe a partially written contract. The temp name is both dotted
        # and .tmp-suffixed so it cannot match `*.contract.json` even for the
        # instant it exists.
        with tempfile.NamedTemporaryFile(
            "wb", dir=dest_dir, prefix=f".{run['doc_id']}.contract.",
            suffix=".tmp", delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(src.read_bytes())
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, dest)
        tmp_path = None
        _log.info("Promoted contract into the document list: %s -> %s", src, dest)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad; see docstring
        _log.error(
            "Failed to promote contract %s -> %s: %r -- the run itself succeeded "
            "and its outputs remain available under %s; only the Existing-documents "
            "listing is affected.",
            src, dest, exc, run["out_root"],
        )
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _run_render_stage(run_id: str) -> None:
    run = RUNS[run_id]
    run["status"] = _STAGE_STATUS["04_sttm_render.py"]
    ok, message = _run_stage("04_sttm_render.py", _run_env(run))
    if not ok:
        run["status"] = "error"
        run["error"] = f"04_sttm_render.py failed:\n{message}"
        return
    run["status"] = "done"
    # Only reachable on a successful render. The FAIL path returns from
    # _run_pipeline_through_gate before this function is ever called, a gated
    # run stops there too, and a render that exits non-zero returns above.
    _promote_contract(run)


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class UploadResponse(BaseModel):
    run_id: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: RunStatus
    doc_id: str
    source_filename: str
    error: str | None = None


class ResolutionSubmission(BaseModel):
    ambiguity_id: str
    kind: str
    resolution_type: Literal["candidate_pick", "free_text", "none_of_these"]
    chosen_candidate: str | None = None
    rationale: str | None = None
    candidates_snapshot: list[str] = []


class RunCoverageResponse(BaseModel):
    """Counts only -- no percentage and no display string. The percentage is
    a presentation concern (rounding, locale digit grouping), so the client
    formats it; shipping a pre-formatted string from here would put two
    roundings of the same number in two places."""

    run_id: str
    doc_id: str
    available: bool
    matched_cells: int | None = None
    total_cells: int | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Routes -- upload & run lifecycle
# --------------------------------------------------------------------------- #
@router.post("/api/uploads", response_model=UploadResponse)
async def create_upload(file: UploadFile = File(...)) -> UploadResponse:
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported for this demo.")

    run_id = str(uuid.uuid4())
    doc_id = Path(file.filename).stem
    raw_dir = LOCAL_ROOT / "uploads" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / file.filename
    dest.write_bytes(await file.read())

    RUNS[run_id] = {
        "run_id": run_id,
        "doc_id": doc_id,
        "source_filename": file.filename,
        "status": "pending",
        "error": None,
        "raw_dir": str(raw_dir),
        "out_root": str(LOCAL_ROOT / "sttm_out_runs" / run_id),
        "schema": f"demo_run_{run_id}",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return UploadResponse(run_id=run_id)


@router.post("/api/runs/{run_id}/start", response_model=RunStatusResponse)
def start_run(run_id: str, background_tasks: BackgroundTasks) -> RunStatusResponse:
    run = _get_run_or_404(run_id)
    if run["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Run is already {run['status']!r}; cannot start again.")

    _assert_demo_scoped(run["doc_id"])

    run["status"] = "running_ingest"
    run["error"] = None
    background_tasks.add_task(_background, _run_pipeline_through_gate, run_id)
    return RunStatusResponse(
        run_id=run_id, status=run["status"], doc_id=run["doc_id"], source_filename=run["source_filename"]
    )


@router.post("/api/runs/{run_id}/continue", response_model=RunStatusResponse)
def continue_run(run_id: str, background_tasks: BackgroundTasks) -> RunStatusResponse:
    """Resumes a 'gated' run into the render stage. Not blocked on every
    ambiguity being resolved -- whatever the reviewer has resolved gets
    applied by 04_sttm_render.py; anything left is surfaced by the results
    screen's unresolved-items union query (see get_run_contract below)."""
    run = _get_run_or_404(run_id)
    if run["status"] != "gated":
        raise HTTPException(status_code=409, detail=f"Run is {run['status']!r}, not 'gated'; nothing to continue.")

    run["status"] = "running_sttm_render"
    run["error"] = None
    background_tasks.add_task(_background, _run_render_stage, run_id)
    return RunStatusResponse(
        run_id=run_id, status=run["status"], doc_id=run["doc_id"], source_filename=run["source_filename"]
    )


@router.get("/api/runs/{run_id}/status", response_model=RunStatusResponse)
def get_run_status(run_id: str) -> RunStatusResponse:
    run = _get_run_or_404(run_id)
    return RunStatusResponse(
        run_id=run_id, status=run["status"], doc_id=run["doc_id"],
        source_filename=run["source_filename"], error=run.get("error"),
    )


# --------------------------------------------------------------------------- #
# Routes -- review step (pre-render contract, while status == 'gated')
# --------------------------------------------------------------------------- #
def _run_contract_path(run: dict) -> Path:
    return Path(run["out_root"]) / "contracts" / f"{run['doc_id']}.contract.json"


@router.get("/api/runs/{run_id}/document")
def get_run_document(run_id: str) -> dict:
    run = _get_run_or_404(run_id)
    path = _run_contract_path(run)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No contract available yet for this run.")
    contract = json.loads(path.read_text(encoding="utf-8"))
    raw_items = ap.gated_items(contract)
    return {
        "doc_id": run["doc_id"],
        "status": contract.get("status", "?"),
        "generated_from_frd": contract.get("generated_from_frd"),
        "feed_count": len(contract.get("feeds", [])),
        "items": [ap.to_gated_item_dict(contract, it) for it in raw_items],
    }


@router.post("/api/runs/{run_id}/resolutions")
def submit_run_resolution(run_id: str, submission: ResolutionSubmission) -> dict:
    run = _get_run_or_404(run_id)
    path = _run_contract_path(run)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No contract available yet for this run.")
    contract = json.loads(path.read_text(encoding="utf-8"))

    items_by_id = {it["id"]: it for it in ap.gated_items(contract)}
    ambiguity = items_by_id.get(submission.ambiguity_id)
    if ambiguity is None:
        raise HTTPException(
            status_code=400,
            detail="ambiguity_id does not match any gated item in this run's contract JSON.",
        )

    error = ap.validate_resolution_submission(
        ambiguity, submission.resolution_type, submission.chosen_candidate, submission.rationale
    )
    if error:
        raise HTTPException(status_code=400, detail=error)

    record = {
        "ambiguity_id": submission.ambiguity_id,
        "kind": submission.kind,
        "resolution_type": submission.resolution_type,
        "chosen_candidate": submission.chosen_candidate,
        "rationale": submission.rationale,
        "candidates_snapshot": submission.candidates_snapshot or ambiguity["candidates"],
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resolved_by": None,
    }
    ap.merge_resolution(contract, record)
    path.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    return ap.to_gated_item_dict(contract, ambiguity)


# --------------------------------------------------------------------------- #
# Routes -- results (post-render contract + rendered workbook)
# --------------------------------------------------------------------------- #
def _unresolved_items(contract: dict) -> list[dict]:
    """Union of (a) anything still present in the post-render
    ambiguities/advisory_flagged lists, and (b) resolution_audit entries
    where applied == False -- NOT a single-list check. 04_sttm_render.py's
    apply_human_resolutions() removes an ambiguity from list (a) as soon as
    *any* human resolution is recorded for it, even a "structural pick
    required and not provided" one that was never actually applied (see
    that function's docstring) -- so list (a) alone silently hides
    resolved-but-not-applied items as if they were fully handled."""
    prov = contract.get("_provenance", {})
    still_present = ap.gated_items(contract)
    out = []
    seen_ids = {a["id"] for a in still_present}
    for a in still_present:
        out.append({
            "ambiguity_id": a["id"], "kind": a["kind"], "text": a["text"],
            "reason": "not yet reviewed", "source": "still_gated",
        })
    for entry in prov.get("resolution_audit", []):
        if entry.get("applied") is False and entry["ambiguity_id"] not in seen_ids:
            seen_ids.add(entry["ambiguity_id"])
            out.append({
                "ambiguity_id": entry["ambiguity_id"], "kind": entry.get("kind"),
                "text": entry.get("ambiguity_text"),
                "reason": entry.get("reason_not_applied") or "resolution recorded but not applied",
                "source": "resolution_not_applied",
            })
    return out


@router.get("/api/runs/{run_id}/contract")
def get_run_contract(run_id: str) -> dict:
    run = _get_run_or_404(run_id)
    out_root = Path(run["out_root"])
    doc_id = run["doc_id"]
    v2_path = out_root / "contracts" / f"{doc_id}.contract.v2.json"

    if not v2_path.is_file():
        # Explicit FAIL branch: 03_contract_build.py's schema-validation
        # failure path writes only a report.md, no contract.json at all --
        # detect that here rather than letting a missing-file read 500 or
        # silently returning an empty/misleading body.
        report_path = out_root / "reports" / f"{doc_id}.report.md"
        report_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else None
        raise HTTPException(
            status_code=422,
            detail={
                "run_id": run_id, "doc_id": doc_id, "status": "FAIL",
                "reason": "No contract JSON was produced for this document -- the "
                          "pipeline gated it FAIL before a contract could be built.",
                "report_md": report_text,
            },
        )

    contract = json.loads(v2_path.read_text(encoding="utf-8"))
    return {
        "run_id": run_id, "doc_id": doc_id, "status": contract.get("status", "?"),
        "contract": contract, "unresolved": _unresolved_items(contract),
    }


@router.get("/api/runs/{run_id}/coverage", response_model=RunCoverageResponse)
def get_run_coverage(run_id: str) -> RunCoverageResponse:
    """Golden-pair eval totals for this run's rendered workbook, read from
    the phase5 render report (see eval_report.py for why that artifact and
    not the runs table, and for how the parse defends itself).

    Deliberately *not* an error route. Every reason this figure can be
    missing -- the run hasn't rendered yet, 03_contract_build.py gated the
    document FAIL so 04_sttm_render.py never ran, the feeds didn't match a
    reference workbook, the report is unreadable or reworded -- is a normal,
    expected state, and the client's job in all of them is identical: render
    no tile. Returning 200 with available=false keeps those out of the
    client's error path so a missing number can never blank the Results
    screen or make the workbook download unreachable. An unknown run_id is
    still a 404, via the same _get_run_or_404 every sibling route uses.

    Read-only: touches nothing but the report file, and does not go near the
    FAIL branch in get_run_contract().
    """
    run = _get_run_or_404(run_id)
    doc_id = run["doc_id"]

    totals = er.read_eval_totals(run["out_root"], doc_id)
    if totals is None:
        return RunCoverageResponse(
            run_id=run_id, doc_id=doc_id, available=False,
            reason="No mapping-coverage figure is available for this run.",
        )

    matched_cells, total_cells = totals
    return RunCoverageResponse(
        run_id=run_id, doc_id=doc_id, available=True,
        matched_cells=matched_cells, total_cells=total_cells,
    )


@router.get("/api/runs/{run_id}/sttm.xlsx")
def download_sttm(run_id: str) -> FileResponse:
    run = _get_run_or_404(run_id)
    path = Path(run["out_root"]) / "rendered" / f"{run['doc_id']}.sttm.xlsx"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Rendered workbook not available yet.")
    return FileResponse(
        str(path), filename=f"{run['doc_id']}.sttm.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
