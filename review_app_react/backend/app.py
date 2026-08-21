"""FastAPI backend for the React/AppKit gated-ambiguity review app.

Thin wrapper around data_access.py (the data-access layer, now a plain
same-directory module -- formerly imported from review_app/, retired once
this backend was its only remaining consumer) plus ambiguity_parsing.py
(logic ported from the old review_app/app.py's helpers). No gating,
grounding, or persistence logic is reimplemented here -- this process only
adds HTTP routing and request/response shaping around the existing Python
logic.

Structural-pick-required policy: same as the retired review_app/app.py used
to enforce -- a candidate-having ambiguity requires resolution_type
"candidate_pick" (with a chosen_candidate) or "none_of_these"; "free_text" is
only the real resolution mechanism for a candidate-free ambiguity
(advisory_grounding today). Enforced here too (not just client-side) so a
malformed submission is rejected with a 400 rather than silently accepted.

Local dev:   uvicorn app:app --reload --port 8000   (run from this directory)
             Vite's dev server (see ../frontend) proxies /api to this port.

Deployed:    this same app also serves the frontend's built static assets
             (see bottom of file) -- see ../app.yaml.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ambiguity_parsing as ap
import data_access as da
from demo import router as demo_router
from orchestration import router as orchestration_router
from sharepoint_routes import router as sharepoint_router

app = FastAPI(title="FRD->STTM Gated Ambiguity Review")
app.include_router(orchestration_router)
app.include_router(demo_router)
app.include_router(sharepoint_router)


# --------------------------------------------------------------------------- #
# Response/request models
# --------------------------------------------------------------------------- #
class DocumentSummary(BaseModel):
    doc_id: str
    status: str
    generated_from_frd: str | None = None
    feed_count: int
    item_count: int
    resolved_count: int


class DocumentListResponse(BaseModel):
    """Envelope around the document list, carrying the source it was read from.

    GET /api/documents used to return a bare `list[DocumentSummary]`, which
    made `[]` the entire answer for "everything is fine, there is nothing to
    review" -- a response indistinguishable, on the client, from a list that
    failed to load and defaulted to empty. Naming the scanned directory on
    the success path lets the UI prove the empty state: it can say which
    location was read and found to hold nothing, instead of showing the same
    blank panel a broken backend would produce.

    `contracts_dir` is the resolved path the scan actually globbed, taken
    from data_access.contracts_source() rather than reassembled here.
    """

    contracts_dir: str
    app_mode: str
    documents: list[DocumentSummary]


class GatedItemResolution(BaseModel):
    resolution_type: Literal["candidate_pick", "free_text", "none_of_these"]
    chosen_candidate: str | None = None
    rationale: str | None = None
    candidates_snapshot: list[str] = []
    resolved_at: str | None = None
    resolved_by: str | None = None


class GatedItem(BaseModel):
    id: str
    text: str
    # The FRD rule this ambiguity is about, pulled out of the contract's
    # structured `context.rule` (or, failing that, the first quoted span in
    # `text`) by ambiguity_parsing.rule_text(). Additive: `text` is unchanged
    # and remains the full technical rationale. None when no rule text could
    # be identified -- the client falls back to rendering `text`.
    rule_text: str | None = None
    kind: Literal["attribution", "disagreement", "advisory_grounding"]
    has_candidates: bool
    candidates: list[str]
    context: dict
    resolution: GatedItemResolution | None = None


class DocumentDetail(BaseModel):
    doc_id: str
    status: str
    generated_from_frd: str | None = None
    feed_count: int
    items: list[GatedItem]
    # Whether GET /api/documents/{doc_id}/workbook would serve a file. Lets
    # the detail view show an explicit "no workbook" state instead of an
    # absent or dead-looking button -- an absence the reader can act on.
    # Deliberately a plain availability flag: no cell counts, no eval or
    # coverage figure rides along with it.
    workbook_available: bool = False


class ResolutionSubmission(BaseModel):
    ambiguity_id: str
    kind: str
    resolution_type: Literal["candidate_pick", "free_text", "none_of_these"]
    chosen_candidate: str | None = None
    rationale: str | None = None
    candidates_snapshot: list[str] = []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_or_404(doc_id: str) -> dict:
    try:
        return da.load_contract(doc_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No contract JSON found for '{doc_id}'")


def _to_gated_item(contract: dict, raw: dict) -> GatedItem:
    d = ap.to_gated_item_dict(contract, raw)
    d["resolution"] = GatedItemResolution(**d["resolution"]) if d["resolution"] else None
    return GatedItem(**d)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/api/documents", response_model=DocumentListResponse)
def list_documents() -> DocumentListResponse:
    # 503, not an empty list: an unreadable contracts directory is a failure
    # of this service's backing store, and the one outcome this endpoint must
    # never produce is a 200 whose empty `documents` array was caused by a bad
    # path. The detail names the directory so the cause is on screen rather
    # than only in the server log.
    try:
        doc_ids = da.list_contract_doc_ids()
    except da.ContractsSourceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    summaries = []
    for doc_id in doc_ids:
        contract = da.load_contract(doc_id)
        items = ap.gated_items(contract)
        resolved = sum(1 for it in items if ap.existing_resolution(contract, it["id"]))
        summaries.append(
            DocumentSummary(
                doc_id=doc_id,
                status=contract.get("status", "?"),
                generated_from_frd=contract.get("generated_from_frd"),
                feed_count=len(contract.get("feeds", [])),
                item_count=len(items),
                resolved_count=resolved,
            )
        )
    return DocumentListResponse(
        contracts_dir=da.contracts_source(),
        app_mode=da.APP_MODE,
        documents=summaries,
    )


@app.get("/api/documents/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: str) -> DocumentDetail:
    contract = _load_or_404(doc_id)
    raw_items = ap.gated_items(contract)
    return DocumentDetail(
        doc_id=doc_id,
        status=contract.get("status", "?"),
        generated_from_frd=contract.get("generated_from_frd"),
        feed_count=len(contract.get("feeds", [])),
        items=[_to_gated_item(contract, it) for it in raw_items],
        # Resolved server-side so the client can state the absence of a
        # workbook as a fact rather than discover it by clicking. The
        # alternative -- probing the download endpoint from the browser --
        # is not available: HEAD is not routed here, so a probe would have
        # to GET and discard the whole file on every document view.
        workbook_available=da.rendered_workbook_path(doc_id) is not None,
    )


@app.get("/api/documents/{doc_id}/workbook")
def download_document_workbook(doc_id: str) -> FileResponse:
    """The rendered STTM workbook for an existing (already-reviewed) document.

    Separate from orchestration.py's /api/runs/{run_id}/sttm.xlsx, which
    serves the SAME kind of file for a live upload run keyed by an in-memory
    run_id. This one is keyed by doc_id off the contracts directory, so it
    still works for a document restored from an archive after a restart --
    the run route cannot, because its RUNS dict does not survive one.

    404 (never a placeholder or an empty file) when no workbook exists: a
    zero-byte or stand-in .xlsx would open in Excel as an empty workbook and
    read as "the pipeline produced nothing useful" rather than "nothing was
    produced at all". Those are different facts and the user is entitled to
    the real one.
    """
    # 404 on an unknown doc_id before looking for a file, so a typo'd id and
    # a real-but-unrendered document give distinguishable messages.
    _load_or_404(doc_id)

    path = da.rendered_workbook_path(doc_id)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No STTM workbook has been generated for '{doc_id}'. The contract exists, "
                f"but stage 04 (04_sttm_render.py) has not produced "
                f"{doc_id}.sttm.xlsx in the rendered output directory."
            ),
        )

    return FileResponse(
        str(path),
        filename=f"{doc_id}.sttm.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/documents/{doc_id}/resolutions", response_model=GatedItem)
def submit_resolution(doc_id: str, submission: ResolutionSubmission) -> GatedItem:
    contract = _load_or_404(doc_id)

    items_by_id = {it["id"]: it for it in ap.gated_items(contract)}
    ambiguity = items_by_id.get(submission.ambiguity_id)
    if ambiguity is None:
        raise HTTPException(
            status_code=400,
            detail="ambiguity_id does not match any gated item in this document's contract JSON.",
        )

    # Same structural-pick-required policy the UI enforces, checked again
    # here so a malformed submission (bypassing the client) is rejected
    # rather than silently persisted -- consistent with the pipeline's
    # "never silently guess" posture applied to this app's own input.
    error = ap.validate_resolution_submission(
        ambiguity, submission.resolution_type, submission.chosen_candidate, submission.rationale
    )
    if error:
        raise HTTPException(status_code=400, detail=error)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "ambiguity_id": submission.ambiguity_id,
        "kind": submission.kind,
        "resolution_type": submission.resolution_type,
        "chosen_candidate": submission.chosen_candidate,
        "rationale": submission.rationale,
        "candidates_snapshot": submission.candidates_snapshot or ambiguity["candidates"],
        "resolved_at": now,
        "resolved_by": None,
    }
    ap.merge_resolution(contract, record)
    da.save_contract(doc_id, contract)

    return _to_gated_item(contract, ambiguity)


# --------------------------------------------------------------------------- #
# Serve the built frontend (production / Databricks App mode). Absent during
# local dev, where Vite serves the frontend on its own port instead.
# --------------------------------------------------------------------------- #
class _NoStoreHTML(StaticFiles):
    """StaticFiles that forbids caching of the HTML shell only.

    WHY: `index.html` names the hashed bundle it loads
    (`/assets/index-<hash>.js`). Cache the shell and the browser keeps
    requesting an OLD hash after a rebuild -- a file that no longer exists on
    disk and 404s. React then never mounts and the page renders as a BLANK
    WHITE SCREEN, which is indistinguishable from the legitimate "connected,
    no documents" state and reproduces the exact defect the empty/error split
    in list_documents exists to eliminate. Observed live: a shell cached from
    an earlier build requested index-B1LYRq21.js (404) and threw
    "e.reduce is not a function" against the new response envelope, showing
    nothing at all.

    Only HTML is no-store. The hashed asset filenames under /assets/ are
    content-addressed, so they stay immutably cacheable -- a new build emits a
    new name and cannot collide. Scoping the header to the shell keeps that
    benefit while making a stale shell impossible.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", _NoStoreHTML(directory=str(_FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import os

    import uvicorn

    # Default stays 0.0.0.0 -- the deployed Databricks App context requires it
    # (see ../app.yaml). STTM_BIND_HOST=127.0.0.1 exists for locked-down local
    # machines (corporate Windows laptops especially), where binding all
    # interfaces triggers a firewall consent prompt / endpoint-security flag
    # the demo doesn't need: localhost serving is all the demo uses.
    uvicorn.run(
        app,
        host=os.environ.get("STTM_BIND_HOST", "0.0.0.0"),
        port=int(os.environ.get("DATABRICKS_APP_PORT", 8000)),
    )
