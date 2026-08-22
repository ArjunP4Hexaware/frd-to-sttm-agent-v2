"""
SharePoint document picker for the client-demo tab.

Design: SharePoint is a *source of documents*, not a second pipeline. A
picked document is downloaded into the same demo-uploads directory an
uploaded .docx lands in, and returned in exactly the shape
`GET /api/demo/documents` emits — so `POST /api/demo/runs` starts it through
the existing, already-validated run path with no change to `start_run`,
`_validate_frd`, or the worker. There is deliberately no separate
"run from SharePoint" execution path to keep in sync.

Credential posture matches the rest of the app: the client secret is read
from the environment (or the repo `.env` locally; the secret scope in
Databricks), its presence is reported to the frontend as a boolean, and the
value is never returned, logged, or echoed in an error.

This module also carries the OUTBOUND half: the confirm-gated manual publish
endpoint (decided 2026-08-21) — the only way the review app ever writes to
SharePoint. See sharepoint_publish below.

Failure mapping — every SharePoint failure is someone else's to fix, so the
status code says whose:
  503  not configured yet (no tenant/app registration wired)
  502  Graph refused or is unreachable (permissions, tenant, network)
  400  the request asked for something invalid (bad name, wrong type,
       a publish without confirm:true)
  404  no rendered workbook for the requested (set, doc)
  413  the document is larger than the demo upload cap / Graph's simple-upload cap
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# The pipeline package lives in src/; the notebooks bootstrap it the same way.
_SRC = Path(__file__).resolve().parents[2] / "src"
if (_SRC / "frdsttm").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from demo import (  # noqa: E402  (after the sys.path bootstrap, by design)
    GOLDEN_DOC_ID,
    UPLOAD_ALLOWED_EXTENSIONS,
    UPLOAD_MAX_BYTES,
    UPLOADS_DIR,
    ArtifactRequestError,
    _rel,
    _UPLOAD_NAME,
    workbook_path,
)
from frdsttm.sharepoint import (  # noqa: E402
    GraphError,
    SharePointConfigError,
    build_client,
    load_config,
)

router = APIRouter()

# Only what 01_frd_ingest can actually parse from this app's run path.
PICKER_SUFFIXES = set(UPLOAD_ALLOWED_EXTENSIONS)


def _param(name: str, default: str) -> str:
    """Env-var accessor with the same names the notebooks' widgets use."""
    return os.environ.get(name.upper(), default)


def _client_secret() -> str:
    return os.environ.get("SHAREPOINT_CLIENT_SECRET", "")


def _client():
    """Config + authenticated client, or the caller's 503/502.

    Never leaks the secret: only SharePointConfigError's own text, which names
    remedies rather than values.
    """
    try:
        cfg = load_config(_param, _client_secret)
    except SharePointConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        return cfg, build_client(cfg)
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"SharePoint sign-in failed ({exc.code}). Check the app "
                   f"registration, its client secret, and admin consent."
                   + (f" Graph request-id {exc.request_id}." if exc.request_id else ""),
        ) from exc


@router.get("/api/demo/sharepoint/config")
def sharepoint_config() -> dict:
    """Whether the picker can be offered, and what it points at.

    Never raises: an unconfigured tenant is a normal state that hides the
    picker, not an error the demo tab has to handle. `configured` is derived
    from presence only — no secret value crosses this boundary.
    """
    try:
        cfg = load_config(_param, _client_secret)
    except SharePointConfigError:
        return {"configured": False, "site": None, "library": None,
                "frd_folder": None, "output_folder": None}
    return {
        "configured": True,
        "site": f"{cfg.host}{cfg.site_path}",
        "library": cfg.library,
        "frd_folder": cfg.frd_folder,
        "output_folder": cfg.output_folder,
    }


@router.get("/api/demo/sharepoint/documents")
def sharepoint_documents() -> dict:
    """List pickable FRDs in the configured library folder."""
    cfg, client = _client()
    try:
        items = client.list_documents(suffixes=PICKER_SUFFIXES)
    except SharePointConfigError as exc:      # library name wrong -> config problem
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not list {cfg.library!r} ({exc.code}). The app "
                   f"registration may lack read access to this site.",
        ) from exc
    return {
        "site": f"{cfg.host}{cfg.site_path}",
        "library": cfg.library,
        "folder": cfg.frd_folder,
        "documents": [
            {"item_id": i.item_id, "name": i.name, "size_bytes": i.size,
             "modified": i.modified, "web_url": i.web_url}
            for i in items
        ],
    }


class ImportRequest(BaseModel):
    item_id: str
    name: str


def _import_item(client, item_id: str, name: str) -> dict:
    """Download one library document into the uploads dir and return the
    document shape POST /api/demo/runs consumes — the shared core of the
    import route and the locate flow. The filename is sanitised with the
    same rule as an upload: a library file name is attacker-adjacent input,
    and it must never escape UPLOADS_DIR."""
    name = Path(name).name
    if not name:
        raise HTTPException(status_code=400, detail="document has no filename")
    if Path(name).suffix.lower() not in UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="only .docx FRDs can be run — 01_frd_ingest parses docx only",
        )

    try:
        payload = client.download_item(item_id)
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not download {name!r} from SharePoint ({exc.code}).",
        ) from exc

    if len(payload) > UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{name} is {len(payload)} bytes; the cap is {UPLOAD_MAX_BYTES}",
        )

    safe = _UPLOAD_NAME.sub("_", name)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / safe
    # Write-then-rename: a failed transfer must never leave a truncated .docx
    # that the run path would happily treat as runnable.
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)

    return {
        "path": _rel(dest),
        "name": safe,
        "doc_id": dest.stem,
        "source": "sharepoint",
        "is_golden": dest.stem == GOLDEN_DOC_ID,
        "size_bytes": len(payload),
    }


@router.post("/api/demo/sharepoint/import", status_code=201)
def sharepoint_import(body: ImportRequest) -> dict:
    """Download one library document into the uploads dir (see _import_item)."""
    _cfg, client = _client()
    return _import_item(client, body.item_id, body.name)


class LocateRequest(BaseModel):
    name: str


def _item_payload(item) -> dict:
    return {"item_id": item.item_id, "name": item.name, "size_bytes": item.size,
            "modified": item.modified, "web_url": item.web_url}


@router.post("/api/demo/sharepoint/locate")
def sharepoint_locate(body: LocateRequest) -> dict:
    """The app's primary entry point: the user names an FRD; the app finds it
    in the library itself (decided 2026-08-21 — no uploads).

    Matching is EXACT (case-insensitive, extension optional) or it is a
    human decision: anything else returns `candidates` for the user to pick
    from explicitly. There is deliberately no best-match auto-pick — running
    the pipeline against a similarly-named wrong document is the class of
    failure this repo's strict-doc-id rules exist to prevent.

    If the located FRD already has an STTM in the output folder (keyed on
    the rendered-workbook naming convention `<doc_id>.sttm.xlsx`), the
    response is `existing_sttm`: the app presents that workbook first —
    surfaced, never silently skipped (decided 2026-08-21). Since 2026-08-22
    the same response also carries the imported FRD (`document`), so the
    reviewer can deliberately REGENERATE despite the existing STTM (the
    corpus/eval flow depends on exactly this); publishing the regenerated
    workbook is a separate confirm-gated step that replaces the old one.
    Otherwise the FRD is imported and returned ready for the run path.
    """
    target = Path(body.name.strip()).name
    if not target:
        raise HTTPException(status_code=400, detail="give the FRD document's name")

    cfg, client = _client()
    try:
        items = client.list_documents(suffixes=PICKER_SUFFIXES)
    except SharePointConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not list {cfg.library!r} ({exc.code}).",
        ) from exc

    folded = target.casefold()
    exact = [i for i in items
             if i.name.casefold() == folded or Path(i.name).stem.casefold() == folded]
    if len(exact) != 1:
        partial = exact or [i for i in items if folded in i.name.casefold()]
        if not partial:
            raise HTTPException(
                status_code=404,
                detail=f"No document named {target!r} in "
                       f"{cfg.library}/{cfg.frd_folder or '<root>'} "
                       f"({len(items)} document(s) there).",
            )
        return {"status": "candidates",
                "candidates": [_item_payload(i) for i in partial]}
    frd = exact[0]

    sttm_name = f"{Path(frd.name).stem}.sttm.xlsx"
    try:
        rendered = client.list_documents(suffixes={".xlsx"}, folder=cfg.output_folder)
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Found {frd.name!r}, but could not check the output folder "
                   f"for an existing STTM ({exc.code}).",
        ) from exc
    existing = [i for i in rendered if i.name.casefold() == sttm_name.casefold()]
    if existing:
        # The FRD is imported HERE TOO (2026-08-22): regenerate-despite-
        # existing needs a runnable document, and importing is read-only —
        # the existing workbook is only ever replaced by an explicit,
        # confirm-gated publish of the new render. The UI presents the
        # existing STTM first; regeneration is a deliberate second step.
        return {
            "status": "existing_sttm",
            "frd": _item_payload(frd),
            "sttm": _item_payload(existing[0]),
            "document": _import_item(client, frd.item_id, frd.name),
        }

    return {"status": "ready", "document": _import_item(client, frd.item_id, frd.name)}


@router.get("/api/demo/sharepoint/sttm/{item_id}")
def sharepoint_existing_sttm(item_id: str):
    """Serve an EXISTING published STTM workbook for viewing/download.

    The item id must belong to the output folder's current listing — this
    endpoint re-verifies that before downloading, so it can never be used to
    proxy arbitrary library items the app's identity happens to read."""
    from fastapi.responses import Response

    cfg, client = _client()
    try:
        rendered = client.list_documents(suffixes={".xlsx"}, folder=cfg.output_folder)
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not list the output folder ({exc.code}).",
        ) from exc
    match = [i for i in rendered if i.item_id == item_id]
    if not match:
        raise HTTPException(
            status_code=404,
            detail="no such workbook in the SharePoint output folder",
        )
    try:
        payload = client.download_item(item_id)
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not download {match[0].name!r} ({exc.code}).",
        ) from exc
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_UPLOAD_NAME.sub("_", match[0].name)}"'},
    )


class PublishRequest(BaseModel):
    set_id: str
    doc_id: str
    confirm: bool = False


@router.post("/api/demo/sharepoint/publish")
def sharepoint_publish(body: PublishRequest) -> dict:
    """Publish ONE reviewed workbook to the library's output folder.

    This endpoint is the manual publish gate decided 2026-08-21: publishing
    is something a reviewer does on purpose, per document, after looking at
    the result — never a side effect of rendering. The pipeline job ends at
    render for the same reason (resources/frd_sttm_job.yml). Three
    consequences in the shape here:

    - `confirm: true` is required, exactly like the billed-run gate in the
      sibling repos' demo apps. A missing/false confirm is a 400, so no
      client can publish by accident with a bare POST.
    - One document per call. There is deliberately no publish-all: the
      review happened per document, so the publish is per document.
    - The workbook is addressed by (set_id, doc_id) through demo.workbook_path,
      which enforces the demo/live_e2e artifact-set family and strict doc-id
      matching — the same validation the download endpoint trusts. Curated
      baseline paths can never be addressed, and there is no fuzzy match.

    Failure mapping matches the rest of this module: 503 unconfigured,
    502 Graph refused, 400 bad request, 404 no such workbook.
    """
    if body.confirm is not True:
        raise HTTPException(
            status_code=400,
            detail="publishing writes to the client's SharePoint library and "
                   "requires an explicit {\"confirm\": true}",
        )
    try:
        path = workbook_path(body.set_id, body.doc_id)
    except ArtifactRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no rendered STTM workbook for {body.doc_id!r} in "
                   f"{body.set_id!r} — run the pipeline (or re-render) first",
        )

    cfg, client = _client()
    try:
        result = client.upload_file(path)
    except GraphError as exc:
        raise HTTPException(
            status_code=413 if exc.status == 413 else 502,
            detail=f"Could not publish {path.name!r} to SharePoint ({exc.code})."
                   + (f" Graph request-id {exc.request_id}." if exc.request_id else ""),
        ) from exc

    return {
        "published": True,
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "web_url": result.get("webUrl"),
        "target": f"{cfg.host}{cfg.site_path}/{cfg.library}"
                  + (f"/{cfg.output_folder}" if cfg.output_folder else ""),
    }
