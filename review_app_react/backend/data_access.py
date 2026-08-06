"""Swappable data-access layer for the gated-ambiguity review app.

Two modes, selected by the `STTM_APP_MODE` env var ("local" default,
"databricks" the other option) -- the same fallback pattern
`notebooks/02_extract.py` already uses for its Anthropic API key
(`dbutils.secrets` in Databricks, env var locally): one interface, one
implementation swapped in behind it, no second app to maintain.

- `local`: reads/writes the real contract JSON files
  `03_contract_build.py` produces under
  `local_dev_fixtures/sttm_out/contracts/<doc_id>.contract.json` -- the
  exact file Part 2 of this task generates, no format conversion.
- `databricks`: reads/writes the same filename shape from a Unity Catalog
  volume (`/Volumes/<catalog>/<schema>/<out_volume>/contracts/...`) via the
  Databricks SDK's Files API. Implemented but not exercised against a live
  workspace in this session (see README's Local mode section for why).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_MODE = os.environ.get("STTM_APP_MODE", "local")

# -- local mode config ------------------------------------------------------
LOCAL_ROOT = Path(__file__).resolve().parents[2] / "local_dev_fixtures"
LOCAL_CONTRACTS_DIR = LOCAL_ROOT / os.environ.get("OUT_VOLUME", "sttm_out") / "contracts"


class ContractsSourceUnavailable(RuntimeError):
    """The contracts directory could not be read at all.

    Distinct from "the directory is readable and contains no contracts",
    which is a legitimate empty result. This exception exists because those
    two states used to be indistinguishable: `list_contract_doc_ids()`
    returned `[]` both when the scan found nothing AND when
    LOCAL_CONTRACTS_DIR did not exist -- so a mistyped OUT_VOLUME, a
    not-yet-created fixtures tree, or a permissions problem all rendered in
    the UI as the cheerful first-run "no documents yet" state. A wrong path
    must never be able to masquerade as an empty corpus; callers turn this
    into an error response instead (see app.py's list_documents).
    """


def contracts_source() -> str:
    """The directory the document scan actually globs, as a display string.

    Surfaced to the client so the empty state can name the exact location it
    found nothing in. Derived from the same module-level values the scan
    uses, never rebuilt from parts by the caller, so what the UI shows and
    what the scan reads cannot drift.
    """
    return str(LOCAL_CONTRACTS_DIR) if APP_MODE == "local" else _dbx_contracts_dir()

# -- databricks mode config --------------------------------------------------
DBX_CATALOG = os.environ.get("CATALOG", "soham_workspace")
DBX_SCHEMA = os.environ.get("SCHEMA", "sttm_agent")
DBX_OUT_VOLUME = os.environ.get("OUT_VOLUME", "sttm_out")


def _dbx_contracts_dir() -> str:
    return f"/Volumes/{DBX_CATALOG}/{DBX_SCHEMA}/{DBX_OUT_VOLUME}/contracts"


def list_contract_doc_ids() -> list[str]:
    """doc_ids with a contract JSON available, newest-modified first.

    Raises ContractsSourceUnavailable if the directory itself cannot be
    read. An empty list from this function therefore means one thing only:
    the directory was read successfully and held no contracts.
    """
    if APP_MODE == "local":
        if not LOCAL_CONTRACTS_DIR.is_dir():
            raise ContractsSourceUnavailable(
                f"The contracts directory does not exist: {LOCAL_CONTRACTS_DIR}. "
                f"Nothing was scanned, so this is not an empty document list -- it is "
                f"an unreadable source. Check STTM_APP_MODE (currently {APP_MODE!r}) "
                f"and OUT_VOLUME (currently {os.environ.get('OUT_VOLUME', 'sttm_out')!r}), "
                f"or create the directory."
            )
        try:
            # stat() inside the sort key is the other way this can fail (a
            # file vanishing mid-scan, an unreadable mount), so the sort is
            # inside the guard too, not just the glob.
            files = sorted(
                LOCAL_CONTRACTS_DIR.glob("*.contract.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError as exc:
            raise ContractsSourceUnavailable(
                f"The contracts directory exists but could not be read: "
                f"{LOCAL_CONTRACTS_DIR} ({exc.__class__.__name__}: {exc}). "
                f"This is an unreadable source, not an empty document list."
            ) from exc
        return [p.name[: -len(".contract.json")] for p in files]

    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    names = []
    for entry in w.files.list_directory_contents(_dbx_contracts_dir()):
        if entry.path.endswith(".contract.json"):
            names.append(Path(entry.path).name[: -len(".contract.json")])
    return names


def load_contract(doc_id: str) -> dict[str, Any]:
    if APP_MODE == "local":
        path = LOCAL_CONTRACTS_DIR / f"{doc_id}.contract.json"
        return json.loads(path.read_text(encoding="utf-8"))

    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    resp = w.files.download(f"{_dbx_contracts_dir()}/{doc_id}.contract.json")
    return json.loads(resp.contents.read())


LOCAL_RENDERED_DIR = LOCAL_ROOT / os.environ.get("OUT_VOLUME", "sttm_out") / "rendered"


def rendered_workbook_path(doc_id: str) -> Path | None:
    """The rendered STTM workbook for exactly this doc_id, or None.

    STRICT doc_id resolution, deliberately unlike 04_sttm_render.py:937's
    `max(refs, key=...token overlap...)`. That best-match has no minimum
    threshold, so it returns *some* workbook even on ZERO token overlap --
    acceptable there (it is pairing a contract against a reference dictionary
    within one known run) but catastrophic here: a user clicking "download"
    on document A would silently receive document B's workbook and have no
    way to tell. A download is an assertion that this file belongs to this
    document, so the filename must match exactly or we serve nothing.

    Returns None rather than raising: "no workbook for this doc" is an
    ordinary, expected state (a contract that never reached stage 04), which
    the caller renders as an explicit 404. Distinct from the contracts
    directory, whose absence is an ERROR (see ContractsSourceUnavailable) --
    there the directory is the app's backing store, whereas a missing
    rendered/ simply means nothing has been rendered yet.
    """
    if APP_MODE != "local":
        # Databricks mode serves from a UC volume via the SDK, not a local
        # path; no caller needs it yet, and returning a bogus local Path here
        # would be worse than declining. Callers turn None into a 404.
        return None

    # doc_id arrives from the URL. Reject anything that could escape the
    # rendered directory before it is ever joined to a path.
    if not doc_id or "/" in doc_id or "\\" in doc_id or doc_id.startswith("."):
        return None

    path = LOCAL_RENDERED_DIR / f"{doc_id}.sttm.xlsx"
    # resolve() + is_relative_to as belt-and-braces against symlink escapes.
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(LOCAL_RENDERED_DIR.resolve()):
            return None
    except OSError:
        return None

    return path if path.is_file() else None


def save_contract(doc_id: str, contract: dict[str, Any]) -> None:
    """Overwrite the contract JSON in place with the reviewer's resolutions
    merged in under `_provenance.human_resolutions` (see app.py)."""
    payload = json.dumps(contract, indent=2, ensure_ascii=False)

    if APP_MODE == "local":
        LOCAL_CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOCAL_CONTRACTS_DIR / f"{doc_id}.contract.json"
        path.write_text(payload, encoding="utf-8")
        return

    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    w.files.upload(
        f"{_dbx_contracts_dir()}/{doc_id}.contract.json",
        payload.encode("utf-8"),
        overwrite=True,
    )
