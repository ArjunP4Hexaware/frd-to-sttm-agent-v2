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

Failure mapping — every SharePoint failure is someone else's to fix, so the
status code says whose:
  503  not configured yet (no tenant/app registration wired)
  502  Graph refused or is unreachable (permissions, tenant, network)
  400  the request asked for something invalid (bad name, wrong type)
  413  the document is larger than the demo upload cap
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
    _rel,
    _UPLOAD_NAME,
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


@router.post("/api/demo/sharepoint/import", status_code=201)
def sharepoint_import(body: ImportRequest) -> dict:
    """Download one library document into the demo uploads dir.

    Returns the same document shape the picker and upload routes emit, so the
    frontend hands it straight to POST /api/demo/runs. The filename is
    sanitised with the same rule as an upload — a library file name is
    attacker-adjacent input, and it must never escape UPLOADS_DIR.
    """
    name = Path(body.name).name
    if not name:
        raise HTTPException(status_code=400, detail="document has no filename")
    if Path(name).suffix.lower() not in UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="only .docx FRDs can be run — 01_frd_ingest parses docx only",
        )

    _cfg, client = _client()
    try:
        payload = client.download_item(body.item_id)
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not download {name!r} from SharePoint ({exc.code}).",
        ) from exc

    if len(payload) > UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{name} is {len(payload)} bytes; the demo cap is {UPLOAD_MAX_BYTES}",
        )

    safe = _UPLOAD_NAME.sub("_", name)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / safe
    # Write-then-rename: a failed transfer must never leave a truncated .docx
    # that the picker would happily offer as runnable.
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
