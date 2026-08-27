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

from frdsttm.dictionary import parse_dictionary_dir
from frdsttm.reference_workbooks import parse_reference_workbook
from frdsttm.similarity import (
    deserialize_features,
    frd_features,
    pair_corpus,
    pair_dictionaries,
    serialize_features,
    workbook_features,
)

CORPUS_INDEX_NAME = "corpus_index.json"
# 2 (2026-08-22): entries carry content_sha256; pairs carry matched_by.
# 4 (2026-08-27, later): per-FRD `eligibility` — the ONE place that decides
#   which FRDs may be generated. See `eligibility_for`.
# 3 (2026-08-27): vendor data dictionaries — `dictionaries`,
#   `dictionary_pairs`, `unpaired_dictionaries`, `dictionary_errors`. The
#   bump is deliberate rather than additive-and-silent: a stage reading a v2
#   index would see no dictionaries and render a source side from the
#   template alone, which is exactly the implicit borrow the third input
#   exists to remove. Better to raise and be rebuilt.
CORPUS_INDEX_VERSION = 4


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
                       thresholds: dict, generated_at: str,
                       dictionary_dir: str | Path | None = None) -> dict:
    """Build the full index.

    ``frd_entries``: iterable of {"doc_id", "source_file", "content"} — the
    stage-01 shape (`frd_documents` rows locally or in UC), so the features
    are computed over exactly the text extraction sees. An optional
    ``content_sha256`` (fingerprint of the SOURCE FILE bytes, as the sync
    records it) is carried through; absent, the fingerprint is taken over
    the parsed text so every entry still has one.
    ``generated_at``: caller-supplied ISO timestamp (kept out of this module
    so index construction stays a pure function of its inputs).
    ``dictionary_dir``: the vendor-dictionary volume, or None when the third
    input is not in play — an absent directory is an ordinary state (no
    vendor has returned a DICT_ workbook yet), never an error.
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

    # -- the third input. Summary only: the index stays small enough to load
    # on every request, so it records WHICH dictionary describes a feed and
    # how complete it is, never the 399 column rows themselves. A consumer
    # that needs the columns parses the workbook it names.
    vdds, vdd_errors = {}, {}
    if dictionary_dir is not None and Path(dictionary_dir).is_dir():
        parsed = parse_dictionary_dir(dictionary_dir)
        vdd_errors = parsed["errors"]
        for name, d in parsed["dictionaries"].items():
            vdds[name] = {
                "n_files": d["n_files"],
                "n_fields": d["n_fields"],
                "files": [f["file_name_pattern"] for f in d["files"]],
                "n_problems": len(d["problems"]),
                "problem_kinds": sorted({p["kind"] for p in d["problems"]}),
                "content_sha256": hashlib.sha256(
                    (Path(dictionary_dir) / name).read_bytes()).hexdigest(),
            }
    vdd_pairing = pair_dictionaries(frds.keys(), vdds.keys())

    # -- the THREE-WAY mapping. An FRD's row in the corpus is now a triple:
    # the FRD, the vendor dictionary that describes its source files, and the
    # approved STTM if one exists. `eligibility` is the verdict derived from
    # it, computed HERE so the notebooks, the API and the picker cannot drift
    # into three different answers to "may this be generated?".
    eligibility = {
        doc_id: eligibility_for(doc_id, pairing["pairs"], vdd_pairing["pairs"])
        for doc_id in frds
    }

    return {
        "version": CORPUS_INDEX_VERSION,
        "generated_at": generated_at,
        "thresholds": dict(thresholds),
        "frds": frds,
        "references": references,
        "pairs": pairing["pairs"],
        "unmapped": pairing["unmapped"],
        "unpaired_references": pairing["unpaired_references"],
        "dictionaries": vdds,
        "dictionary_pairs": vdd_pairing["pairs"],
        "unpaired_dictionaries": vdd_pairing["unpaired_dictionaries"],
        "ambiguous_dictionaries": vdd_pairing["ambiguous"],
        "dictionary_errors": vdd_errors,
        "eligibility": eligibility,
        "generatable": sorted(d for d, e in eligibility.items() if e["generatable"]),
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


#: The three verdicts an FRD can have. Exactly one is generatable.
ELIGIBILITY_READY = "ready"                  # has a VDD, has no STTM
ELIGIBILITY_MAPPED = "mapped"                # already has an approved STTM
ELIGIBILITY_NO_DICTIONARY = "no_dictionary"  # no STTM, but no VDD either


def eligibility_for(doc_id: str, sttm_pairs: dict, dictionary_pairs: dict) -> dict:
    """May this FRD be generated, and if not, why not?

    The rule (Arjun, 2026-08-27): a reviewer may only start a run on an FRD
    that has a matching vendor dictionary AND does not already have an
    approved STTM. Both halves have a reason:

    * **no dictionary → not generatable.** Without one the source side of the
      workbook has no grounded input, so the run would render a frame and gate
      every column. That is the correct BEHAVIOUR when a run happens, but it
      is not worth a billed model call — better to ask the vendor first.
    * **already mapped → not generatable.** The approved STTM is the system of
      record. Re-running against it produced a self-referential accuracy
      figure (the approved workbook is also the template), and the earlier
      "regenerate anyway" control existed only for the 2026-08-24 demo.

    Returns ``{"status", "generatable", "reason", "dictionary", "reference"}``.
    ``reason`` is written for a person to read in the picker, not for a log.
    """
    reference = (sttm_pairs.get(doc_id) or {}).get("reference") \
        if isinstance(sttm_pairs.get(doc_id), dict) else sttm_pairs.get(doc_id)
    dictionary = dictionary_pairs.get(doc_id)
    if reference:
        return {
            "status": ELIGIBILITY_MAPPED, "generatable": False,
            "reason": "This FRD already has an approved STTM. The approved workbook is the "
                      "system of record — it is presented as-is, not regenerated.",
            "dictionary": dictionary, "reference": reference,
        }
    if not dictionary:
        return {
            "status": ELIGIBILITY_NO_DICTIONARY, "generatable": False,
            "reason": "No vendor data dictionary is paired with this FRD, so the source "
                      "columns cannot be grounded. Ask the vendor for VDD_<feed>.xlsx and "
                      "name it in the FRD's Structural Metadata › Source Data Dictionary row.",
            "dictionary": None, "reference": None,
        }
    return {
        "status": ELIGIBILITY_READY, "generatable": True,
        "reason": "Has a vendor data dictionary and no STTM yet — ready to map.",
        "dictionary": dictionary, "reference": None,
    }


def eligibility_of(index: dict, doc_id: str) -> dict:
    """This FRD's verdict from a built index, or a not-in-corpus refusal.

    An unknown doc_id is NOT generatable: the caller is asking about a
    document the corpus has never seen, and answering "sure" would let a run
    start on something the index cannot vouch for.
    """
    entry = index.get("eligibility", {}).get(doc_id)
    if entry:
        return entry
    return {
        "status": ELIGIBILITY_NO_DICTIONARY, "generatable": False,
        "reason": f"{doc_id!r} is not in the corpus index — run a sync, or rebuild the index.",
        "dictionary": None, "reference": None,
    }


def dictionary_for(index: dict, doc_id: str) -> str | None:
    """The vendor dictionary describing this FRD's source files, or None.

    None is the gating state, not a fallback: with no dictionary the source
    side of the STTM has no grounded input, and the run must say so by name
    rather than fill the columns from a matched template.
    """
    return index.get("dictionary_pairs", {}).get(doc_id)
