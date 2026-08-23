"""Who is making this request — the one fact every governed action records.

Databricks Apps authenticates every request at the platform edge and
forwards the caller's identity to the app as request headers
(`X-Forwarded-Email`, `X-Forwarded-Preferred-Username`, `X-Forwarded-User`;
docs: databricks apps → "app authorization" → "user authorization headers").
The app process itself never sees a password or token it has to validate —
the platform already did — so "identity" here is reading those headers,
nothing more. Note `X-Forwarded-Access-Token` ALSO arrives on every request:
it is a bearer token for the user and is never read, logged, or persisted
by anything in this module.

Modes (the same `STTM_APP_MODE` knob demo.py / data_access.py switch on):

- databricks (the deployed App): the headers are a platform guarantee. A
  request WITHOUT them is a request that did not come through the Apps
  proxy (a process hitting the backend port directly, a misconfiguration),
  and every governed action refuses it (401) rather than recording an
  anonymous actor. `STTM_REQUIRE_IDENTITY=0` turns that refusal into a
  recorded `unknown` actor — an operator escape hatch, never the default.
- local (dev laptops, the offline test-suite): there is no proxy. The actor
  is `STTM_LOCAL_USER` if set, else the OS user (`USER` / `USERNAME`), else
  `local-dev`, and `source` says so, so a local audit record can never be
  mistaken for an authenticated one.

The result is a plain dict (JSON-safe, no secrets) so it can be embedded
verbatim in audit events and contract provenance:
    {"actor": "a.b@example.com", "source": "databricks_apps",
     "username": "a.b", "user_id": "12345"}
"""

from __future__ import annotations

import getpass
import os

from fastapi import HTTPException, Request

APP_MODE = os.environ.get("STTM_APP_MODE", "local")
IS_DATABRICKS_APP = APP_MODE == "databricks"

# "1" (default): in databricks mode an identity-less request is refused.
REQUIRE_IDENTITY = os.environ.get("STTM_REQUIRE_IDENTITY", "1").strip() not in ("0", "false", "no")

HEADER_EMAIL = "x-forwarded-email"
HEADER_USERNAME = "x-forwarded-preferred-username"
HEADER_USER_ID = "x-forwarded-user"

SOURCE_DATABRICKS = "databricks_apps"
SOURCE_LOCAL = "local"
SOURCE_UNKNOWN = "unknown"

ACTOR_UNKNOWN = "unknown"


def _local_actor() -> str:
    configured = os.environ.get("STTM_LOCAL_USER", "").strip()
    if configured:
        return configured
    for var in ("USER", "USERNAME"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — getuser can raise on exotic containers; fall through
        return "local-dev"


def identity_from_headers(headers) -> dict | None:
    """The forwarded identity if ANY of the three headers is present, else
    None. `headers` is any case-insensitive mapping (Starlette's `Headers`,
    or a plain dict of lowercase keys in tests)."""
    email = (headers.get(HEADER_EMAIL) or "").strip()
    username = (headers.get(HEADER_USERNAME) or "").strip()
    user_id = (headers.get(HEADER_USER_ID) or "").strip()
    if not (email or username or user_id):
        return None
    return {
        "actor": email or username or user_id,
        "source": SOURCE_DATABRICKS,
        "email": email or None,
        "username": username or None,
        "user_id": user_id or None,
    }


def resolve_identity(request: Request | None) -> dict:
    """The caller's identity for a governed action, or the 401 described in
    the module docstring. Never returns None: every caller gets either a
    real actor or an explicit `unknown` (local mode, or the escape hatch)."""
    forwarded = identity_from_headers(request.headers) if request is not None else None
    if forwarded is not None:
        return forwarded
    if IS_DATABRICKS_APP:
        if REQUIRE_IDENTITY:
            raise HTTPException(
                status_code=401,
                detail=(
                    "no forwarded identity on this request — Databricks Apps "
                    f"sets {HEADER_EMAIL} / {HEADER_USERNAME} / {HEADER_USER_ID} on "
                    "every proxied request, so this call did not come through the "
                    "Apps proxy. Governed actions (runs, resolutions, re-renders, "
                    "downloads, syncs) are refused without an actor; set "
                    "STTM_REQUIRE_IDENTITY=0 only as a deliberate operator override."
                ),
            )
        return {"actor": ACTOR_UNKNOWN, "source": SOURCE_UNKNOWN,
                "email": None, "username": None, "user_id": None}
    actor = _local_actor()
    return {"actor": actor, "source": SOURCE_LOCAL,
            "email": None, "username": actor, "user_id": None}


def actor_of(request: Request | None) -> str:
    """Shorthand for the one string most records need."""
    return resolve_identity(request)["actor"]
