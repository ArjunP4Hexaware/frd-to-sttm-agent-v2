"""
frdsttm.corpus — the FRD/STTM corpus index.

One JSON artifact, ``corpus_index.json``, living IN THE REFERENCE VOLUME
(`sttm_reference` in Unity Catalog; `local_dev_fixtures/sttm_reference/`
locally) next to the workbooks it indexes — so the notebooks read it from
the same /Volumes path they already read references from, and it travels
with them. Built by the SharePoint sync (frdsttm.sync — the scheduled
`frd_sttm_sharepoint_sync` job and the review app's "Sync now"), or by any
caller with parsed FRDs + a reference dir; consumed by:

- stage 02 (retrieved exemplars for the extraction prompt),
- stage 04 (template decision + exclude-own-reference eval),
- the review app (unmapped-FRD list, pairing display).

Everything here is deterministic code over parsed artifacts — no model
calls, no network. Absence of the index is a legitimate state everywhere:
each consumer falls back to its pre-corpus behavior (02: no exemplar
block; 04: legacy filename-token reference pick; app: corpus panel shows
"not synced yet"). A corrupt index, by contrast, raises — a half-readable
index must never silently degrade a run (see docs/TEMPLATE_ARCHITECTURE.md).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from frdsttm.reference_workbooks import parse_reference_workbook
from frdsttm.similarity import (
    deserialize_features,
    frd_features,
    pair_corpus,
    serialize_features,
    workbook_features,
)

CORPUS_INDEX_NAME = "corpus_index.json"
# 2 (2026-08-22): entries carry content_sha256; pairs carry matched_by.
CORPUS_INDEX_VERSION = 2


class CorpusIndexError(RuntimeError):
    """The index file exists but cannot be used. Distinct from 'absent',
    which is an ordinary fallback state, never an error."""


def parse_reference_dir(reference_dir: str | Path) -> dict:
    """{workbook_name: parsed dictionary} for every .xlsx in the directory.
    A workbook that fails to parse raises — a template library with a
    silently-missing member would skew every decision made against it."""
    out = {}
    for path in sorted(Path(reference_dir).glob("*.xlsx")):
        out[path.name] = parse_reference_workbook(str(path))
    return out


def build_corpus_index(frd_entries, reference_dir: str | Path,
                       thresholds: dict, generated_at: str) -> dict:
    """Build the full index.

    ``frd_entries``: iterable of {"doc_id", "source_file", "content"} — the
    stage-01 shape (`frd_documents` rows locally or in UC), so the features
    are computed over exactly the text extraction sees. An optional
    ``content_sha256`` (fingerprint of the SOURCE FILE bytes, as the sync
    records it) is carried through; absent, the fingerprint is taken over
    the parsed text so every entry still has one.
    ``generated_at``: caller-supplied ISO timestamp (kept out of this module
    so index construction stays a pure function of its inputs).
    """
    reference_dir = Path(reference_dir)
    dictionaries = parse_reference_dir(reference_dir)

    frd_feats, frds = {}, {}
    for e in frd_entries:
        doc_id = e["doc_id"]
        feat = frd_features(doc_id, e["content"])
        frd_feats[doc_id] = feat
        frds[doc_id] = {
            "source_file": e.get("source_file", ""),
            "n_chars": len(e["content"]),
            "content_sha256": e.get("content_sha256")
            or hashlib.sha256(e["content"].encode("utf-8")).hexdigest(),
            "features": serialize_features(feat),
        }

    wb_feats, references = {}, {}
    for name, dictionary in dictionaries.items():
        feat = workbook_features(name, dictionary)
        wb_feats[name] = feat
        references[name] = {
            "dialect": dictionary["dialect"],
            "n_feeds": len(dictionary.get("feeds", {})),
            "n_columns": sum(len(f.get("fields", []))
                             for f in dictionary.get("feeds", {}).values()),
            "content_sha256": hashlib.sha256(
                (reference_dir / name).read_bytes()).hexdigest(),
            "features": serialize_features(feat),
        }

    pairing = pair_corpus(frd_feats, wb_feats, thresholds)

    return {
        "version": CORPUS_INDEX_VERSION,
        "generated_at": generated_at,
        "thresholds": dict(thresholds),
        "frds": frds,
        "references": references,
        "pairs": pairing["pairs"],
        "unmapped": pairing["unmapped"],
        "unpaired_references": pairing["unpaired_references"],
    }


def save_corpus_index(index: dict, reference_dir: str | Path) -> Path:
    path = Path(reference_dir) / CORPUS_INDEX_NAME
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8")
    return path


def load_corpus_index(reference_dir: str | Path) -> dict | None:
    """The index, or None when absent (the ordinary fallback state)."""
    path = Path(reference_dir) / CORPUS_INDEX_NAME
    if not path.is_file():
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusIndexError(
            f"corpus index at {path} exists but is unreadable "
            f"({exc.__class__.__name__}: {exc}) — rebuild it (run the "
            f"SharePoint sync, or a reindex); refusing to run as if no corpus "
            f"existed"
        ) from exc
    if index.get("version") != CORPUS_INDEX_VERSION:
        raise CorpusIndexError(
            f"corpus index at {path} has version {index.get('version')!r}, "
            f"this code expects {CORPUS_INDEX_VERSION} — rebuild it (run the "
            f"SharePoint sync, or a reindex)"
        )
    return index


def frd_features_from_index(index: dict, doc_id: str) -> dict | None:
    entry = index.get("frds", {}).get(doc_id)
    return deserialize_features(entry["features"]) if entry else None


def reference_features_from_index(index: dict) -> dict:
    return {name: deserialize_features(e["features"])
            for name, e in index.get("references", {}).items()}


def own_reference_for(index: dict, doc_id: str) -> str | None:
    """The workbook name this doc is paired with (its ground truth), or None."""
    pair = index.get("pairs", {}).get(doc_id)
    return pair["reference"] if pair else None
