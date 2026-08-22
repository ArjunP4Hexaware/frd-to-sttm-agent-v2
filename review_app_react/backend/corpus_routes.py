"""Corpus endpoints — bootstrap, listing, and the unmapped-FRD surface.

The review-app half of the template architecture (2026-08-22,
docs/TEMPLATE_ARCHITECTURE.md). Three routes:

- GET  /api/demo/corpus            — index summary (absent is a normal state)
- POST /api/demo/corpus/bootstrap  — crawl SharePoint, land FRDs + reference
                                     STTMs, build + persist corpus_index.json
- GET  /api/demo/corpus/frds       — per-FRD pairing detail for the UI list

Bootstrap makes ZERO model calls (ingest-all ≠ extract-all — parsing and
pairing are deterministic; the billed extraction happens only when a
generation is requested). It is still confirm-gated: it writes into the
reference/raw volumes and overwrites the corpus index, and a demo operator
should do that on purpose, mirroring the publish gate's shape.

Mode behavior follows the rest of the backend (STTM_APP_MODE):
- local: everything under local_dev_fixtures/ (frd_raw, sttm_reference).
- databricks: SAME local layout (the App container's disk is where runs
  stage FRDs from), plus each reference workbook and the index are ALSO
  uploaded to the UC reference volume so the bundle job's 02/04 read them
  from /Volumes. On App restart the local copies rehydrate from UC.

Reference STTMs are listed from their own SharePoint folder
(SHAREPOINT_REFERENCE_FOLDER, defaulting to the FRD folder — the client
keeps FRDs and their approved STTMs side by side). Deliberately NOT the
output folder: that is where this agent PUBLISHES, and treating published
output as reference input would feed the agent its own generations.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from demo import IS_DATABRICKS_APP, LOCAL_ROOT, PRELOADED_DIR, _rel
from sharepoint_routes import _UPLOAD_NAME, _client

# ORDER IS LOAD-BEARING: sharepoint_routes' import above bootstraps src/ onto
# sys.path (the deployed App does not pip-install the frdsttm package), so
# the frdsttm imports below MUST come after it. Do not let a formatter sort
# them ahead.
from frdsttm.corpus import (  # noqa: E402
    CORPUS_INDEX_NAME,
    CorpusIndexError,
    build_corpus_index,
    load_corpus_index,
    save_corpus_index,
)
from frdsttm.frd_parsing import SUPPORTED_SUFFIXES, normalize_to_markdown
from frdsttm.sharepoint import GraphError, SharePointConfigError
from frdsttm.similarity import thresholds_from

router = APIRouter()

REFERENCE_VOLUME = os.environ.get("STTM_REFERENCE_VOLUME", "sttm_reference")
REFERENCE_DIR = LOCAL_ROOT / REFERENCE_VOLUME

# Same accessor shape the notebooks' _param has, so thresholds resolve from
# the same-named env vars here and from widgets there.
_thresholds = lambda: thresholds_from(  # noqa: E731
    lambda name, default: os.environ.get(name.upper(), default))


def _reference_folder(cfg) -> str:
    return os.environ.get("SHAREPOINT_REFERENCE_FOLDER", cfg.frd_folder)


def _uc_reference_dir() -> str:
    catalog = os.environ.get("CATALOG", "soham_workspace")
    schema = os.environ.get("SCHEMA", "sttm_agent")
    return f"/Volumes/{catalog}/{schema}/{REFERENCE_VOLUME}"


def _index_or_none():
    """Local index; in databricks mode rehydrate from UC when local is gone
    (App restart wiped the container disk). Corrupt raises — never degrade."""
    try:
        index = load_corpus_index(REFERENCE_DIR)
    except CorpusIndexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if index is not None or not IS_DATABRICKS_APP:
        return index
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        payload = w.files.download(
            f"{_uc_reference_dir()}/{CORPUS_INDEX_NAME}").contents.read()
    except Exception:
        return None  # nothing in UC either — legitimately unbuilt
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    (REFERENCE_DIR / CORPUS_INDEX_NAME).write_bytes(payload)
    try:
        return load_corpus_index(REFERENCE_DIR)
    except CorpusIndexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _summary(index) -> dict:
    if index is None:
        return {"built": False, "generated_at": None, "n_frds": 0,
                "n_references": 0, "n_pairs": 0, "n_unmapped": 0,
                "unpaired_references": []}
    return {
        "built": True,
        "generated_at": index["generated_at"],
        "n_frds": len(index["frds"]),
        "n_references": len(index["references"]),
        "n_pairs": len(index["pairs"]),
        "n_unmapped": len(index["unmapped"]),
        "unpaired_references": index["unpaired_references"],
    }


@router.get("/api/demo/corpus")
def corpus_summary() -> dict:
    return _summary(_index_or_none())


@router.get("/api/demo/corpus/frds")
def corpus_frds() -> dict:
    """Every corpus FRD with its pairing verdict, ready to run.

    `path` is repo-root-relative — the exact shape POST /api/demo/runs takes,
    so the frontend's generate button reuses the existing billed-run gate
    unchanged. Unbuilt corpus → 200 with an empty list plus built=false
    (empty-vs-unreachable stays distinguishable: a corrupt index is a 502).
    """
    index = _index_or_none()
    if index is None:
        return {"built": False, "frds": []}
    frds = []
    for doc_id, entry in sorted(index["frds"].items()):
        pair = index["pairs"].get(doc_id)
        source_name = Path(entry["source_file"]).name
        local = PRELOADED_DIR / source_name
        frds.append({
            "doc_id": doc_id,
            "name": source_name,
            "path": _rel(local) if local.is_file() else None,
            "runnable": local.is_file(),
            "paired": pair is not None,
            "reference": pair["reference"] if pair else None,
            "score": pair["score"] if pair else None,
            "confidence": pair["confidence"] if pair else None,
            "components": pair["components"] if pair else None,
        })
    return {"built": True, "frds": frds}


class BootstrapRequest(BaseModel):
    confirm: bool = False


@router.post("/api/demo/corpus/bootstrap", status_code=201)
def corpus_bootstrap(body: BootstrapRequest) -> dict:
    """Crawl SharePoint, land every FRD and reference STTM, build the index.

    Idempotent: re-running re-downloads and rebuilds the whole index (cheap
    at this scale, and the only way a renamed/removed library file ever
    leaves the corpus). Zero model calls. Per-file parse failures are
    collected and RETURNED (`skipped`), not silently dropped and not fatal —
    one malformed workbook must not block the corpus, but its absence must
    be visible.
    """
    if body.confirm is not True:
        raise HTTPException(
            status_code=400,
            detail="corpus bootstrap downloads the SharePoint library into the "
                   "raw/reference volumes and rebuilds the corpus index — it "
                   "requires an explicit {\"confirm\": true}",
        )
    cfg, client = _client()
    ref_folder = _reference_folder(cfg)

    try:
        frd_items = client.list_documents(suffixes=set(SUPPORTED_SUFFIXES))
        ref_items = client.list_documents(suffixes={".xlsx"}, folder=ref_folder)
    except GraphError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not list the library for bootstrap ({exc.code}).",
        ) from exc

    PRELOADED_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    skipped: list[dict] = []
    frd_files: list[Path] = []
    for item in frd_items:
        safe = _UPLOAD_NAME.sub("_", Path(item.name).name)
        try:
            payload = client.download_item(item.item_id)
        except GraphError as exc:
            skipped.append({"name": item.name, "error": f"download failed ({exc.code})"})
            continue
        dest = PRELOADED_DIR / safe
        dest.write_bytes(payload)
        frd_files.append(dest)

    ref_files: list[Path] = []
    for item in ref_items:
        safe = _UPLOAD_NAME.sub("_", Path(item.name).name)
        try:
            payload = client.download_item(item.item_id)
        except GraphError as exc:
            skipped.append({"name": item.name, "error": f"download failed ({exc.code})"})
            continue
        dest = REFERENCE_DIR / safe
        dest.write_bytes(payload)
        ref_files.append(dest)

    entries = []
    for path in frd_files:
        try:
            entries.append({
                "doc_id": path.stem,
                "source_file": str(path),
                "content": normalize_to_markdown(str(path)),
            })
        except Exception as exc:  # one bad parse must not sink the corpus
            skipped.append({"name": path.name,
                            "error": f"{exc.__class__.__name__}: {exc}"})

    try:
        index = build_corpus_index(
            entries, REFERENCE_DIR, _thresholds(),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"corpus index build failed: {exc.__class__.__name__}: {exc}",
        ) from exc
    save_corpus_index(index, REFERENCE_DIR)

    uploaded_to_uc = 0
    if IS_DATABRICKS_APP:
        # The bundle job's 02/04 read references + index from /Volumes; the
        # local copies above are for the app's own listing and run staging.
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        uc_dir = _uc_reference_dir()
        try:
            w.files.create_directory(uc_dir)
            for path in [*ref_files, REFERENCE_DIR / CORPUS_INDEX_NAME]:
                w.files.upload(f"{uc_dir}/{path.name}", path.read_bytes(),
                               overwrite=True)
                uploaded_to_uc += 1
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"corpus built locally but the UC upload to {uc_dir} "
                       f"failed ({exc.__class__.__name__}: {exc}) — the bundle "
                       f"job would not see it; fix the volume grant and re-run "
                       f"bootstrap.",
            ) from exc

    return {
        **_summary(index),
        "frd_folder": cfg.frd_folder,
        "reference_folder": ref_folder,
        "n_frd_files": len(frd_files),
        "n_reference_files": len(ref_files),
        "uploaded_to_uc": uploaded_to_uc,
        "skipped": skipped,
    }


# Config probe so the frontend can hide the bootstrap control exactly like
# the SharePoint picker hides (same "unconfigured is normal" contract).
@router.get("/api/demo/corpus/config")
def corpus_config() -> dict:
    try:
        cfg, _ = _client()
    except HTTPException:
        return {"available": False, "reference_folder": None}
    except SharePointConfigError:
        return {"available": False, "reference_folder": None}
    return {"available": True, "reference_folder": _reference_folder(cfg)}
