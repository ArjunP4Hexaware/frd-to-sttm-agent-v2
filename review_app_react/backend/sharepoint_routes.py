"""
SharePoint configuration probe + the shared Graph client factory.

Since 2026-08-22 this module is small on purpose. The app no longer looks
anything up in SharePoint on the request path: the picker reads the corpus
index (corpus_routes.py), which the SharePoint SYNC maintains, and nothing
in the app writes to SharePoint at all — the former locate / import /
existing-STTM / publish routes are gone with that decision. What remains:

- `_client()`  — config + authenticated client for the one caller that still
                 talks to Graph from the app: the local-mode "Sync now".
- GET /api/demo/sharepoint/config — whether a tenant is wired, and which
                 folders it points at (so the UI can tell the reviewer WHERE
                 to upload a finished STTM).

Credential posture matches the rest of the app: the client secret is read
from the environment (or the repo `.env` locally; the secret scope in
Databricks), its presence is reported to the frontend as a boolean, and the
value is never returned, logged, or echoed in an error.

Failure mapping — every SharePoint failure is someone else's to fix, so the
status code says whose:
  503  not configured yet (no tenant/app registration wired)
  502  Graph refused or is unreachable (permissions, tenant, network)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

# The pipeline package lives in src/; the notebooks bootstrap it the same way.
# corpus_routes imports THIS module first for exactly this side effect —
# keep the bootstrap here.
_SRC = Path(__file__).resolve().parents[2] / "src"
if (_SRC / "frdsttm").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from frdsttm.local_folder import (  # noqa: E402
    LocalFolderConfigError,
    load_local_folder_config,
)
from frdsttm.sharepoint import (  # noqa: E402
    GraphError,
    SharePointConfigError,
    build_client,
    load_config,
)

router = APIRouter()


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
    """Whether a document source is wired, and what it points at.

    Kept at this path and shape because the frontend already gates "Sync now"
    and the "where do I upload the finished STTM" text on it; `source` is the
    one added field, so a caller that ignores it behaves exactly as before.

    `source` is "local_folder" (the 2026-08-24 demo stand-in: a folder on the
    reviewer's machine, no Graph access) or "sharepoint", and the local folder
    WINS when both are set — same precedence as corpus_routes._source_client,
    which is what actually runs the sync. Deciding it in two places would be a
    bug waiting to happen, so this reads as documentation OF that function.

    Never raises: no source configured is a normal state (the UI hides "Sync
    now" and names the gap), not an error the page has to handle. `configured`
    is derived from presence only — no secret value crosses this boundary.
    `sttm_folder` is where the reviewer puts a finished workbook — the folder
    the sync watches.
    """
    try:
        local = load_local_folder_config(_param)
    except LocalFolderConfigError:
        pass
    else:
        return {
            "configured": True,
            "source": "local_folder",
            "site": str(local.root),
            "library": local.root.name,
            "frd_folder": local.frd_folder,
            "sttm_folder": local.sttm_folder,
        }
    try:
        cfg = load_config(_param, _client_secret)
    except SharePointConfigError:
        return {"configured": False, "source": None, "site": None,
                "library": None, "frd_folder": None, "sttm_folder": None}
    return {
        "configured": True,
        "source": "sharepoint",
        "site": f"{cfg.host}{cfg.site_path}",
        "library": cfg.library,
        "frd_folder": cfg.frd_folder,
        "sttm_folder": cfg.sttm_folder,
    }
