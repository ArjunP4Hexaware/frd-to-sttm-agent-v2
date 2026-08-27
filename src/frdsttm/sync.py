"""
frdsttm.sync — keep the FRD and STTM volumes in step with SharePoint, and
rebuild the corpus index from whatever the volumes hold.

This is the "set-up, then stay in sync" half of the agent (decided
2026-08-22, replacing the app's one-shot corpus *bootstrap*). It runs when
the review app STARTS UP and on its "Sync now" control (decided 2026-08-22
late evening — not on a schedule; the bundle job is only the databricks-mode
execution target):

    SharePoint FRD folder  ──list/download──▶  frd_raw volume
    SharePoint STTM folder ──list/download──▶  sttm_reference volume
    SharePoint VDD folder  ──list/download──▶  vdd_raw volume
                                                   │
                                  reindex: parse + pair + corpus_index.json

Two entry points, both deterministic and model-free:

- ``sync_from_sharepoint``: list both library folders, download only what is
  NEW or CHANGED since the last sync (Graph item id + eTag + modified +
  size, recorded in ``sync_manifest.json`` next to the corpus index), delete
  local copies of items that left the library, then reindex. The first run
  is the bulk load; every later run is the incremental "stay in sync".
  Triggered by the review app at start-up and from its Corpus panel; in
  databricks mode that trigger runs the bundle job
  (resources/frd_sttm_sync_job.yml, no schedule) — same code either way.
  The library names every FRD ``FRD_<name>.<ext>`` and every STTM
  ``STTM_<name>.xlsx``; ``frd_prefix`` / ``reference_prefix`` filter the
  listings to that convention and COUNT what they ignore.
- ``reindex``: rebuild the corpus index from the files already in the two
  volumes, touching no network. What the sync ends with; also the offline
  path when a tenant is not wired yet (smoke fixtures, local development).

Read-only by construction: this module never writes to SharePoint. The
finished STTM goes back to the library by a PERSON uploading it; the next
sync pulls it in and pairs it with its FRD (name match first, similarity
second — see frdsttm.similarity.pair_corpus).

Safety rules, mirroring frdsttm.sharepoint:
- every download is written to a ``.part`` file and renamed, so a failed
  transfer never leaves a truncated document for 01 to parse;
- only files THIS module synced (per the manifest) are ever deleted — a
  file someone staged by hand in the volume is left alone;
- one unreadable document never sinks the corpus: per-file parse failures
  are collected and RETURNED in the summary (``skipped``), never dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from frdsttm.corpus import build_corpus_index, save_corpus_index
from frdsttm.dictionary import DICTIONARY_NAME_PREFIXES, DICTIONARY_SUFFIXES
from frdsttm.frd_parsing import SUPPORTED_SUFFIXES, normalize_to_markdown

MANIFEST_NAME = "sync_manifest.json"
MANIFEST_VERSION = 1

REFERENCE_SUFFIXES = {".xlsx"}

# The library's naming convention (learned 2026-08-22): every FRD is
# ``FRD_<name>.<ext>``, every STTM ``STTM_<name>.xlsx``. These are the
# prefixes the CALLERS (00_sharepoint_sync widget, the app's env var) pass by
# default; the library function itself defaults to NO filter so a library
# that predates the convention still syncs by suffix alone. A set prefix is
# matched case-insensitively and every listed file that lacks it is counted
# in the summary (``frd_ignored`` / ``reference_ignored``) — visible, never
# silent.
FRD_NAME_PREFIX = "FRD_"
REFERENCE_NAME_PREFIX = "STTM_"
# The third input (2026-08-27). Same convention, same stem: DICT_<name>.xlsx
# pairs to FRD_<name>.docx. It is harvested into its OWN volume rather than
# alongside the STTMs, because corpus.parse_reference_dir globs every .xlsx
# in the reference directory and would try to read a vendor dictionary as a
# template workbook — two different shapes, one glob. The volume is `vdd_raw`,
# named to sit beside `frd_raw`: two raw source documents, two raw volumes.
DICTIONARY_PREFIX = DICTIONARY_NAME_PREFIXES

# Same sanitiser the review app applies to uploads: a library file name is
# attacker-adjacent input and must never escape the destination directory.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class SyncError(RuntimeError):
    """A sync-level failure with an operator-actionable message."""


def safe_name(name: str) -> str:
    base = Path(name).name
    if not base:
        raise SyncError("library item has no file name")
    return _SAFE_NAME.sub("_", base)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def empty_manifest() -> dict:
    return {"version": MANIFEST_VERSION, "synced_at": None, "items": {}}


def load_manifest(reference_dir: str | Path) -> dict:
    """The manifest, or an empty one. A corrupt manifest is NOT fatal: the
    worst case is a full re-download, which is the safe direction — but it
    is reported (``reset`` in the summary), never hidden."""
    path = Path(reference_dir) / MANIFEST_NAME
    if not path.is_file():
        return empty_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        m = empty_manifest()
        m["reset"] = f"{path} was unreadable — treated as a first sync"
        return m
    if data.get("version") != MANIFEST_VERSION or not isinstance(data.get("items"), dict):
        m = empty_manifest()
        m["reset"] = f"{path} has an unknown shape — treated as a first sync"
        return m
    return data


def save_manifest(manifest: dict, reference_dir: str | Path) -> Path:
    path = Path(reference_dir) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest.pop("reset", None)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8")
    return path


def manifest_by_local_name(manifest: dict, kind: str) -> dict:
    """{local file name: manifest entry} for one kind ('frd' | 'reference')
    — the lookup the review app uses to attach web links and modified
    stamps to corpus entries."""
    return {e["local_name"]: e for e in manifest.get("items", {}).values()
            if e.get("kind") == kind}


def _changed(item, previous: dict | None) -> bool:
    if previous is None:
        return True
    etag = getattr(item, "etag", "") or ""
    if etag and previous.get("etag"):
        return etag != previous["etag"]
    return (item.modified != previous.get("modified")
            or int(item.size or 0) != int(previous.get("size") or 0))


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #
def _write_atomic(dest: Path, payload: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)


def _sync_folder(client, items, kind: str, dest_dir: Path, manifest: dict,
                 summary: dict, skipped: list) -> None:
    """Bring one destination directory in step with one library listing."""
    previous_items = manifest.get("items", {})
    new_items: dict = {}
    for item in items:
        local_name = safe_name(item.name)
        dest = dest_dir / local_name
        prev = previous_items.get(item.item_id)
        entry = {
            "kind": kind,
            "name": item.name,
            "local_name": local_name,
            "etag": getattr(item, "etag", "") or "",
            "modified": item.modified,
            "size": int(item.size or 0),
            "web_url": item.web_url,
        }
        if prev is not None and prev.get("kind") == kind and not _changed(item, prev) \
                and dest.is_file():
            entry["content_sha256"] = prev.get("content_sha256", "")
            new_items[item.item_id] = entry
            summary[f"{kind}_unchanged"] += 1
            continue
        try:
            payload = client.download_item(item.item_id)
        except Exception as exc:  # noqa: BLE001 — one failed download must not sink the sync
            skipped.append({"name": item.name, "kind": kind,
                            "error": f"download failed ({exc.__class__.__name__}: {exc})"})
            if prev is not None and prev.get("kind") == kind:
                new_items[item.item_id] = prev  # keep the last good copy
            continue
        if item.size and len(payload) != int(item.size):
            skipped.append({"name": item.name, "kind": kind,
                            "error": f"downloaded {len(payload)} bytes, library reports {item.size}"})
            if prev is not None and prev.get("kind") == kind:
                new_items[item.item_id] = prev
            continue
        _write_atomic(dest, payload)
        entry["content_sha256"] = sha256_bytes(payload)
        new_items[item.item_id] = entry
        summary[f"{kind}_downloaded"] += 1

    # Items of this kind that left the library: remove OUR local copy only.
    listed_ids = {i.item_id for i in items}
    for item_id, prev in previous_items.items():
        if prev.get("kind") != kind or item_id in listed_ids:
            continue
        stale = dest_dir / prev["local_name"]
        if stale.is_file():
            stale.unlink()
        summary[f"{kind}_removed"] += 1

    # Replace this kind's entries; leave the other kind's untouched.
    kept = {k: v for k, v in previous_items.items() if v.get("kind") != kind}
    kept.update(new_items)
    manifest["items"] = kept


def filter_by_prefix(items, prefix: str | tuple[str, ...]) -> tuple[list, int]:
    """Keep the items whose name starts with ``prefix`` (case-insensitive);
    return (kept, ignored_count). An empty prefix keeps everything.

    Accepts SEVERAL prefixes (2026-08-27) so one kind can carry a convention
    and a live alias at the same time: vendor dictionaries are ``VDD_`` by
    convention but ``DICT_`` on the template already issued to vendors, and a
    file someone was asked to fill in must not stop syncing because we renamed
    the convention afterwards.
    """
    prefixes = (prefix,) if isinstance(prefix, str) else tuple(prefix)
    prefixes = tuple(p.lower() for p in prefixes if p)
    if not prefixes:
        return list(items), 0
    kept = [i for i in items if str(i.name).lower().startswith(prefixes)]
    return kept, len(items) - len(kept)


def sync_from_sharepoint(client, *, frd_dir: str | Path, reference_dir: str | Path,
                         reference_folder: str | None, thresholds: dict,
                         now_iso: str, frd_suffixes=SUPPORTED_SUFFIXES,
                         reference_suffixes=REFERENCE_SUFFIXES,
                         frd_prefix: str = "", reference_prefix: str = "",
                         dictionary_dir: str | Path | None = None,
                         dictionary_folder: str | None = None,
                         dictionary_prefix: str = "",
                         dictionary_suffixes=DICTIONARY_SUFFIXES) -> dict:
    """One sync pass: list → download new/changed → drop departed → reindex.

    ``client`` is a frdsttm.sharepoint.SharePointClient (or a test stub with
    ``list_documents(suffixes=, folder=)`` and ``download_item(item_id)``).
    ``reference_folder`` None means the client's configured FRD folder.
    ``frd_prefix`` / ``reference_prefix`` (e.g. ``FRD_`` / ``STTM_``) restrict
    each listing to the library's naming convention; ignored files are
    counted in the summary. Graph failures on the LISTING propagate (the
    caller maps them); per-item download failures are collected in
    ``skipped``.

    ``dictionary_dir`` None skips the third kind entirely, which is the
    ordinary state until vendors start returning DICT_ workbooks — a library
    with no dictionary folder must sync exactly as it did before, not fail.
    When it IS set, dictionaries are listed, downloaded and cleaned up by the
    same code path as the other two kinds: one implementation, three kinds.
    """
    frd_dir, reference_dir = Path(frd_dir), Path(reference_dir)
    frd_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)

    frd_items, frd_ignored = filter_by_prefix(
        client.list_documents(suffixes=set(frd_suffixes)), frd_prefix)
    ref_items, ref_ignored = filter_by_prefix(
        client.list_documents(suffixes=set(reference_suffixes), folder=reference_folder),
        reference_prefix)

    dict_items, dict_ignored = [], 0
    if dictionary_dir is not None:
        dictionary_dir = Path(dictionary_dir)
        dictionary_dir.mkdir(parents=True, exist_ok=True)
        dict_items, dict_ignored = filter_by_prefix(
            client.list_documents(suffixes=set(dictionary_suffixes),
                                  folder=dictionary_folder),
            dictionary_prefix)

    manifest = load_manifest(reference_dir)
    summary = {
        "frd_listed": len(frd_items), "reference_listed": len(ref_items),
        "frd_ignored": frd_ignored, "reference_ignored": ref_ignored,
        "frd_prefix": frd_prefix, "reference_prefix": reference_prefix,
        "frd_downloaded": 0, "frd_unchanged": 0, "frd_removed": 0,
        "reference_downloaded": 0, "reference_unchanged": 0, "reference_removed": 0,
        "dictionary_listed": len(dict_items), "dictionary_ignored": dict_ignored,
        "dictionary_prefix": dictionary_prefix,
        "dictionary_downloaded": 0, "dictionary_unchanged": 0, "dictionary_removed": 0,
    }
    if manifest.get("reset"):
        summary["manifest_reset"] = manifest["reset"]
    skipped: list[dict] = []

    _sync_folder(client, frd_items, "frd", frd_dir, manifest, summary, skipped)
    _sync_folder(client, ref_items, "reference", reference_dir, manifest, summary, skipped)
    if dictionary_dir is not None:
        _sync_folder(client, dict_items, "dictionary", Path(dictionary_dir),
                     manifest, summary, skipped)

    index, parse_skipped = reindex(frd_dir, reference_dir, thresholds, now_iso,
                                   dictionary_dir=dictionary_dir)
    skipped.extend(parse_skipped)

    manifest["synced_at"] = now_iso
    save_manifest(manifest, reference_dir)

    return {**summary, "skipped": skipped, "index": index, "synced_at": now_iso}


# --------------------------------------------------------------------------- #
# reindex
# --------------------------------------------------------------------------- #
def reindex(frd_dir: str | Path, reference_dir: str | Path, thresholds: dict,
            now_iso: str, frd_suffixes=SUPPORTED_SUFFIXES,
            dictionary_dir: str | Path | None = None) -> tuple[dict, list]:
    """Rebuild + persist corpus_index.json from the files in the volumes.

    No network. Returns (index, skipped): one unparsable FRD is reported,
    not fatal. An unparsable reference WORKBOOK raises (a template library
    with a silently-missing member would skew every decision against it —
    frdsttm.corpus.parse_reference_dir's rule, kept).
    """
    frd_dir, reference_dir = Path(frd_dir), Path(reference_dir)
    reference_dir.mkdir(parents=True, exist_ok=True)
    entries, skipped = [], []
    if frd_dir.is_dir():
        for path in sorted(frd_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in frd_suffixes:
                continue
            try:
                entries.append({
                    "doc_id": path.stem,
                    "source_file": path.name,
                    "content": normalize_to_markdown(str(path)),
                    "content_sha256": sha256_bytes(path.read_bytes()),
                })
            except Exception as exc:  # noqa: BLE001 — reported, never dropped
                skipped.append({"name": path.name, "kind": "frd",
                                "error": f"{exc.__class__.__name__}: {exc}"})
    index = build_corpus_index(entries, reference_dir, thresholds, generated_at=now_iso,
                               dictionary_dir=dictionary_dir)
    save_corpus_index(index, reference_dir)
    return index, skipped
