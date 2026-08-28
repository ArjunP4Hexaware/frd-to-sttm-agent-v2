"""
frdsttm.corpus — what is in the four volumes, and what may be generated.

`build_index` reads the FRD, VDD and reference-STTM directories and pairs
them BY NAME: ``FRD_<x>.docx`` ↔ ``VDD_<x>.xlsx`` ↔ ``STTM_<x>.xlsx``. No
similarity, no guessing — a wrongly paired dictionary would put another
vendor's columns on a source. The result is ``corpus_index.json`` in the
reference volume and, in Databricks, the ``frd_pairing`` table (one row per
FRD).

Eligibility, one rule: an FRD may be generated when it has a paired VDD.
An approved STTM does not block regeneration (the run never reads it), it
only marks the FRD as already mapped.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from frdsttm.dictionary import parse_dictionary_dir
from frdsttm.reference_layout import parse_reference_workbook

INDEX_NAME = "corpus_index.json"
INDEX_VERSION = 6
FRD_SUFFIXES = (".docx", ".pdf", ".md", ".markdown", ".txt")
_ROLE_TOKENS = {"frd", "sttm", "vdd", "dict"}


class CorpusIndexError(RuntimeError):
    pass


def name_key(name: str) -> str:
    """``FRD_Medicare_Expansion.docx`` and ``VDD_Medicare Expansion.xlsx`` →
    the same key. Strips extensions, role prefixes/suffixes, punctuation."""
    stem = str(name)
    while True:
        head, dot, tail = stem.rpartition(".")
        if not dot or not tail or not tail.isalnum() or len(tail) > 5:
            break
        stem = head
    tokens = [t for t in re.split(r"[^a-z0-9]+", stem.lower()) if t]
    while tokens and tokens[-1] in _ROLE_TOKENS:
        tokens.pop()
    while tokens and tokens[0] in _ROLE_TOKENS:
        tokens.pop(0)
    return "".join(tokens)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _by_key(names) -> dict[str, list[str]]:
    out: dict = {}
    for n in names:
        k = name_key(n)
        if k:
            out.setdefault(k, []).append(n)
    return out


def build_index(frds_dir: Path, vdds_dir: Path, reference_dir: Path) -> dict:
    frds_dir, vdds_dir, reference_dir = Path(frds_dir), Path(vdds_dir), Path(reference_dir)
    frds = sorted(p for p in frds_dir.iterdir() if p.is_file() and p.suffix.lower() in FRD_SUFFIXES
                  and not p.name.startswith("~$")) if frds_dir.is_dir() else []
    sttms = sorted(p for p in reference_dir.glob("*.xlsx") if not p.name.startswith("~$")) \
        if reference_dir.is_dir() else []
    parsed = parse_dictionary_dir(vdds_dir) if vdds_dir.is_dir() else {"dictionaries": {}, "errors": {}}

    vdd_by_key = _by_key(parsed["dictionaries"])
    sttm_by_key = _by_key(p.name for p in sttms)
    frd_by_key = _by_key(p.name for p in frds)

    vdds = {}
    for name, d in parsed["dictionaries"].items():
        vdds[name] = {"n_files": d["n_files"], "n_fields": d["n_fields"],
                      "files": [f["file_name_pattern"] for f in d["files"]],
                      "problems": [p["kind"] for p in d["problems"]],
                      "sha256": _sha(vdds_dir / name)}
    references = {}
    for p in sttms:
        try:
            parsed_wb = parse_reference_workbook(str(p))
            references[p.name] = {"dialect": parsed_wb["dialect"],
                                  "n_sources": len(parsed_wb["feeds"]),
                                  "n_columns": sum(len(f["fields"]) for f in parsed_wb["feeds"].values()),
                                  "sha256": _sha(p)}
        except Exception as exc:  # noqa: BLE001 — one bad workbook must not sink the index
            references[p.name] = {"dialect": None, "n_sources": 0, "n_columns": 0,
                                  "sha256": _sha(p), "error": f"{type(exc).__name__}: {exc}"}

    documents, used_vdd, used_sttm = {}, set(), set()
    for p in frds:
        key = name_key(p.name)
        vdd_names = vdd_by_key.get(key, [])
        sttm_names = sttm_by_key.get(key, [])
        ambiguous = len(frd_by_key.get(key, [])) > 1 or len(vdd_names) > 1 or len(sttm_names) > 1
        vdd = vdd_names[0] if len(vdd_names) == 1 and not ambiguous else None
        sttm = sttm_names[0] if len(sttm_names) == 1 and not ambiguous else None
        used_vdd.update(vdd_names if vdd else [])
        used_sttm.update(sttm_names if sttm else [])
        documents[p.stem] = {
            "frd": p.name, "sha256": _sha(p), "vdd": vdd, "sttm": sttm,
            "ambiguous_name": ambiguous,
            **eligibility(vdd, sttm, ambiguous, vdds.get(vdd) if vdd else None),
        }
    return {
        "version": INDEX_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "documents": documents,
        "vdds": vdds,
        "references": references,
        "unpaired_vdds": sorted(n for n in vdds if n not in used_vdd),
        "unpaired_references": sorted(p.name for p in sttms if p.name not in used_sttm),
        "vdd_errors": parsed["errors"],
    }


def eligibility(vdd, sttm, ambiguous, vdd_entry) -> dict:
    if ambiguous:
        return {"status": "ambiguous", "generatable": False,
                "reason": "More than one file shares this name — rename so FRD_/VDD_/STTM_ pair one-to-one."}
    if vdd is None:
        return {"status": "no_dictionary", "generatable": False,
                "reason": "No vendor data dictionary is paired with this FRD. Add VDD_<same name>.xlsx."}
    if vdd_entry and vdd_entry.get("n_fields", 0) == 0:
        return {"status": "no_dictionary", "generatable": False,
                "reason": f"{vdd} names no columns — the vendor returned an empty dictionary."}
    if sttm:
        return {"status": "mapped", "generatable": True,
                "reason": "Already has an approved STTM. A new draft reads only the FRD and the VDD, "
                          "never the approved workbook."}
    return {"status": "ready", "generatable": True, "reason": "FRD and vendor data dictionary present."}


def pairing_rows(index: dict) -> list[dict]:
    """One row per FRD — the `frd_pairing` table."""
    rows = []
    for doc_id, d in sorted(index["documents"].items()):
        v = index["vdds"].get(d["vdd"] or "", {})
        r = index["references"].get(d["sttm"] or "", {})
        rows.append({
            "doc_id": doc_id, "frd_file": d["frd"], "frd_sha256": d["sha256"],
            "vdd_file": d["vdd"], "vdd_files": int(v.get("n_files") or 0),
            "vdd_columns": int(v.get("n_fields") or 0),
            "sttm_file": d["sttm"], "sttm_columns": int(r.get("n_columns") or 0),
            "status": d["status"], "generatable": bool(d["generatable"]), "reason": d["reason"],
            "indexed_at": index["generated_at"],
        })
    return rows


def save_index(index: dict, reference_dir: Path) -> Path:
    path = Path(reference_dir) / INDEX_NAME
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def load_index(reference_dir: Path) -> dict | None:
    path = Path(reference_dir) / INDEX_NAME
    if not path.is_file():
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusIndexError(f"{path} is unreadable ({exc}) — rebuild the index") from exc
    if index.get("version") != INDEX_VERSION:
        raise CorpusIndexError(f"{path} is version {index.get('version')!r}, expected {INDEX_VERSION} — rebuild the index")
    return index
