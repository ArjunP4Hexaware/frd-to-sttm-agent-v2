"""
frdsttm.local_folder — a local directory standing in for the SharePoint
library, for the 2026-08-24 demo.

Why this exists (decided 2026-08-24, Venu): SharePoint/Graph access is not
available in time for the demo. Instead the two FRD/STTM pairs live in an
ordinary folder on the reviewer's machine and the app is pointed at it.

What it deliberately is NOT: a second sync engine. ``sync_from_sharepoint``
already takes ``client`` as a duck type — its docstring names the contract
("a test stub with ``list_documents(suffixes=, folder=)`` and
``download_item(item_id)``") — so the whole downstream half (incremental
manifest, atomic writes, departed-file cleanup, reindex, pairing) is reached
unchanged by supplying a different client. This module is only that client.
Everything the sync knows how to do it still does; only the origin of the
bytes changes.

Consequences of reusing the sync path rather than reading the folder
directly, all of them wanted:

- the FRD/STTM split is the SAME prefix convention as the library
  (``FRD_<name>.<ext>`` / ``STTM_<name>.xlsx``), so a folder that works here
  works unchanged once the tenant is wired;
- editing a file in the folder and re-syncing picks the edit up, because
  ``etag`` below is derived from size + mtime;
- deleting a file from the folder removes the synced copy on the next sync,
  via the manifest's departed-item rule.

The folder is read-only to this code: nothing here writes, moves, or renames
anything under the source directory, matching the "this repo never writes to
SharePoint" rule it stands in for.

Emits ``SharePointItem`` rather than a parallel type on purpose — it is the
shape ``frdsttm.sync`` already destructures, and a second near-identical
dataclass would be one more thing to keep in step.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from frdsttm.sharepoint import SharePointItem


class LocalFolderConfigError(RuntimeError):
    """No usable local source folder. Mirrors SharePointConfigError: the text
    names the remedy, and an unset variable is an ordinary state (the caller
    falls back), never a crash."""


#: Env var naming the folder. Unset means "no local source" — the app then
#: falls back to SharePoint, and failing that to a network-free reindex.
SOURCE_DIR_VAR = "STTM_LOCAL_SOURCE_DIR"


@dataclass(frozen=True)
class LocalFolderConfig:
    """Where the documents are. ``frd_folder`` / ``sttm_folder`` exist so this
    reports through the same shape as SharePointConfig; for a flat folder they
    are both the root, which is exactly the demo layout (each FRD sitting
    beside its STTM)."""

    root: Path

    @property
    def frd_folder(self) -> str:
        return str(self.root)

    @property
    def sttm_folder(self) -> str:
        return str(self.root)


def load_local_folder_config(param=None) -> LocalFolderConfig:
    """Resolve the source folder, or raise LocalFolderConfigError.

    ``param`` is the notebooks'/app's env accessor (name, default) so this
    resolves from the same place widgets do; omitted, it reads os.environ.
    ``~`` is expanded — the demo folder is under the reviewer's home.
    """
    if param is None:
        def param(name: str, default: str) -> str:
            return os.environ.get(name.upper(), default)

    raw = (param(SOURCE_DIR_VAR, "") or "").strip()
    if not raw:
        raise LocalFolderConfigError(
            f"no local source folder configured — set {SOURCE_DIR_VAR} to the "
            f"folder holding the FRD_*/STTM_* documents"
        )
    root = Path(raw).expanduser()
    if not root.exists():
        raise LocalFolderConfigError(
            f"{SOURCE_DIR_VAR} points at {root}, which does not exist"
        )
    if not root.is_dir():
        raise LocalFolderConfigError(
            f"{SOURCE_DIR_VAR} points at {root}, which is not a directory"
        )
    return LocalFolderConfig(root=root)


def _iso_mtime(st) -> str:
    """UTC ISO-8601, matching Graph's lastModifiedDateTime, so the manifest's
    fallback change check (modified + size) behaves identically."""
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()


class LocalFolderClient:
    """The SharePoint client's read surface, over a directory.

    ``item_id`` is the file NAME, not a path: the folder is flat by design and
    the name is what the sync already sanitises, records in the manifest, and
    matches on. It is stable across syncs (a Graph id's job), which is what
    the incremental check needs.

    ``etag`` is ``"<size>-<mtime_ns>"``. ``sync._changed`` prefers etag when
    both sides have one, so an edited document re-downloads and an untouched
    one is skipped — the same behaviour the Graph eTag buys.
    """

    def __init__(self, cfg: LocalFolderConfig):
        self.cfg = cfg

    def _resolved_root(self) -> Path:
        return self.cfg.root.resolve()

    def list_documents(self, suffixes: set[str] | None = None,
                       folder: str | None = None) -> list[SharePointItem]:
        """Files directly in the source folder, sorted by name.

        Subdirectories are skipped, not walked — the library listing this
        stands in for is one folder deep, and a nested file would sync into a
        flat volume under a name that no longer says where it came from.
        ``folder`` is accepted and ignored: the flat demo folder holds both
        kinds, and the caller separates them by name prefix (the same
        ``filter_by_prefix`` the library listing goes through).
        """
        root = self._resolved_root()
        items: list[SharePointItem] = []
        for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if suffixes is not None and path.suffix.lower() not in suffixes:
                continue
            st = path.stat()
            items.append(SharePointItem(
                item_id=path.name,
                name=path.name,
                size=int(st.st_size),
                modified=_iso_mtime(st),
                web_url=path.as_uri(),
                etag=f"{st.st_size}-{st.st_mtime_ns}",
            ))
        return items

    def download_item(self, item_id: str) -> bytes:
        """Read one file's bytes by name.

        ``item_id`` reaching here came from our own listing, but it is treated
        as untrusted anyway: the resolved path must sit directly inside the
        source folder, so a crafted id can never read outside it. Same posture
        as sync.safe_name, which guards the write side.
        """
        root = self._resolved_root()
        candidate = (root / Path(item_id).name).resolve()
        if candidate.parent != root:
            raise LocalFolderConfigError(
                f"refusing to read {item_id!r}: outside the source folder"
            )
        if not candidate.is_file():
            raise FileNotFoundError(f"{candidate} is no longer in the source folder")
        return candidate.read_bytes()
