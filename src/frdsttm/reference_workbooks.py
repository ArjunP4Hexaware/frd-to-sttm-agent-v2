"""
frdsttm.reference_workbooks — reference STTM workbook parsing + feed matching.

Factored verbatim out of notebooks/04_sttm_render.py (2026-08-22) so the
similarity/corpus modules and the review app backend can read reference
workbooks with EXACTLY the dictionary the render stage uses — one parser,
no drift. 04_sttm_render consumes this module through the
notebooks/_reference_workbooks.py shim (%run in Databricks, plain import
locally), the same pattern as _models / _live_extraction.

Both client dialects are supported: sheet_per_table (FILE_DETAILS +
MAPPING-<TABLE> sheets) and single_sheet (CAQH-style wide sheet). See
SKILL.md "Stage 4" for the roles a parsed dictionary plays.
"""

from __future__ import annotations

import re
import unicodedata

from openpyxl import load_workbook

# --------------------------------------------------------------------------- #
# normalization
# --------------------------------------------------------------------------- #
_UNI = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                      "\u2010": "-", "\u2011": "-", "\u2013": "-", "\u2014": "-",
                      "\u00a0": " "})


def _n(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s)).translate(_UNI)).strip()


def _nl(s) -> str:
    return _n(s).lower()


_TRUE = {"yes", "y", "true"}


def _flag(s) -> bool:
    return _nl(s) in _TRUE


def _not_null(s) -> bool:
    return "not null" in _nl(s)


# --------------------------------------------------------------------------- #
# source-dictionary parsing
# --------------------------------------------------------------------------- #
# Source-side cell (normalized) that marks a trailing audit-column row.
# Mirrors CodeGen's `extractor.audit_source_markers: ["na"]`.
AUDIT_SOURCE_MARKER = "na"

_HEADER_ALIASES = {
    "source_column": ["database column name", "database name",
                      "client data table column name", "field name"],
    "description": ["description"],
    "sample": ["sample value", "example values"],
    "datatype": ["datatype", "data type"],
    "nullable_raw": ["null check"],
    "phi_raw": ["phi field", "phi/pii field", "pii"],
    "mandatory_raw": ["mandatory field", "mandatory", "mandatory column"],
    "comment": ["comment", "comments"],
    "length": ["length"],
    "fixed_length": ["field length (fixed width)"],
    "fixed_start": ["start position (fixed width)"],
    "fixed_end": ["end position (fixed width)"],
    "segment": ["segment (ex:header,trailer,detail)", "segment"],
    "business_rule": ["business rule"],
}

_TARGET_ALIASES = {
    "catalog": ["catalog"],
    "schema": ["schema"],
    "table": ["tablename", "table name"],
    "column": ["columnname", "column name"],
    "datatype": ["datatype", "data type"],
}


def _map_headers(headers, aliases, offset=0):
    out = {}
    for i, h in enumerate(headers):
        hn = _nl(h)
        if not hn:
            continue
        for field, names in aliases.items():
            if field not in out and any(hn == a or hn.startswith(a) for a in names):
                out[field] = i + offset
    return out


def _section_starts(label_row):
    """Column indices where 'Source', 'Stage Layer', 'Standard Layer' begin."""
    src = stage = std = None
    for i, v in enumerate(label_row):
        vn = _nl(v)
        if vn.startswith("source") and src is None:
            src = i
        elif "stage layer" in vn and stage is None:
            stage = i
        elif "standard layer" in vn and std is None:
            std = i
    return src, stage, std


def _row_vals(ws, r, width):
    return [c.value for c in ws[r][:width]] if r <= ws.max_row else [None] * width


def parse_reference_workbook(path):
    """Returns {"dialect": ..., "feeds": {table_key: feed_dict}, "meta": {...}}.

    feed_dict: {"sheet", "fields": [source-field dicts],
                "ref_targets": [{stage:{...}, standard:{...}} per field]}  # eval only

    Trailing rows whose source column is the audit marker "NA" are kept as
    fields (positional eval and similarity stay unchanged) but flagged
    `audit: True` (2026-08-22): they are the client's ETL audit columns
    (load timestamps, source file name, ...) — a workbook convention, not an
    FRD fact — and 04 derives their target identity from the template's own
    targets instead of the 1:1 source-name rule. CodeGen's `extract-sttm`
    requires at least one such row per mapping sheet.
    """
    wb = load_workbook(path, read_only=True)
    if any(s.startswith("MAPPING-") for s in wb.sheetnames):
        return _parse_sheet_per_table(wb)
    return _parse_single_sheet(wb)


def _parse_sheet_per_table(wb):
    feeds, meta = {}, {}
    if "FILE_DETAILS" in wb.sheetnames:
        ws = wb["FILE_DETAILS"]
        rows = list(ws.iter_rows(values_only=True))
        if rows:
            hdr = [_nl(v) for v in rows[0]]
            meta["file_details"] = [
                {hdr[i]: _n(v) for i, v in enumerate(r) if i < len(hdr) and _n(v)}
                for r in rows[1:] if any(_n(v) for v in r)
            ]
    for name in wb.sheetnames:
        if not name.startswith("MAPPING-"):
            continue
        ws = wb[name]
        width = ws.max_column
        labels = _row_vals(ws, 1, width)
        headers = _row_vals(ws, 2, width)
        src_i, stage_i, std_i = _section_starts(labels)
        if src_i is None or stage_i is None:
            continue
        smap = _map_headers(headers[src_i:stage_i], _HEADER_ALIASES, src_i)
        gmap = _map_headers(headers[stage_i:(std_i or width)], _TARGET_ALIASES, stage_i)
        dmap = _map_headers(headers[std_i:], _TARGET_ALIASES, std_i) if std_i is not None else {}
        recycle_note = next((_n(h) for h in headers if "recycle" in _nl(h)), None)

        fields, ref_targets, table_key = [], [], None
        for r in ws.iter_rows(min_row=3, values_only=True):
            get = lambda m, k: _n(r[m[k]]) if k in m and m[k] < len(r) else ""
            col = get(smap, "source_column")
            if not col:
                continue
            fields.append({
                "source_column": col,
                "description": get(smap, "description"),
                "sample": get(smap, "sample"),
                "datatype": get(smap, "datatype") or "String",
                "nullable": not _not_null(get(smap, "nullable_raw")),
                "phi": _flag(get(smap, "phi_raw")),
                "mandatory": _flag(get(smap, "mandatory_raw")),
                "comment": get(smap, "comment"),
                "segment": "",
                "business_rule": "",
                "audit": _nl(col) == AUDIT_SOURCE_MARKER,
            })
            ref_targets.append({
                "stage": {k: get(gmap, k) for k in _TARGET_ALIASES},
                "standard": {k: get(dmap, k) for k in _TARGET_ALIASES},
            })
            table_key = table_key or _nl(get(gmap, "table"))
        if table_key:
            feeds[table_key] = {"sheet": name, "fields": fields,
                                "ref_targets": ref_targets,
                                "recycle_note": recycle_note}
    return {"dialect": "sheet_per_table", "feeds": feeds, "meta": meta}


def _parse_single_sheet(wb):
    for name in wb.sheetnames:
        ws = wb[name]
        header_r = label_r = None
        for r in range(1, min(ws.max_row, 30) + 1):
            vals = [_nl(v) for v in _row_vals(ws, r, ws.max_column)]
            if any(v.startswith("field name") for v in vals):
                header_r = r
                label_r = r - 1
                break
        if header_r is None:
            continue
        width = ws.max_column
        meta = {}
        for r in range(1, label_r):
            k, v = _n(ws.cell(r, 1).value), _n(ws.cell(r, 2).value)
            if k:
                meta[k] = v
        labels = _row_vals(ws, label_r, width)
        headers = _row_vals(ws, header_r, width)
        src_i, stage_i, std_i = _section_starts(labels)
        smap = _map_headers(headers[src_i:stage_i], _HEADER_ALIASES, src_i)
        gmap = _map_headers(headers[stage_i:std_i], _TARGET_ALIASES, stage_i)
        dmap = _map_headers(headers[std_i:], _TARGET_ALIASES, std_i)

        fields, ref_targets = [], []
        blanks = 0
        for r in ws.iter_rows(min_row=header_r + 1, values_only=True):
            get = lambda m, k: _n(r[m[k]]) if k in m and m[k] < len(r) else ""
            col = get(smap, "source_column")
            if not col:
                blanks += 1
                if blanks > 5:
                    break
                continue
            blanks = 0
            fields.append({
                "source_column": col,
                "description": get(smap, "description") or get(smap, "comment"),
                "sample": get(smap, "sample"),
                "datatype": get(smap, "datatype") or "String",
                "nullable": not _not_null(get(smap, "nullable_raw")),
                "phi": _flag(get(smap, "phi_raw")),
                "mandatory": _flag(get(smap, "mandatory_raw")),
                "comment": get(smap, "comment"),
                "length": get(smap, "length"),
                "fixed_length": get(smap, "fixed_length"),
                "fixed_start": get(smap, "fixed_start"),
                "fixed_end": get(smap, "fixed_end"),
                "segment": get(smap, "segment"),
                "business_rule": get(smap, "business_rule"),
                "audit": _nl(col) == AUDIT_SOURCE_MARKER,
            })
            ref_targets.append({
                "stage": {k: get(gmap, k) for k in _TARGET_ALIASES},
                "standard": {k: get(dmap, k) for k in _TARGET_ALIASES},
            })
        feed_key = _nl(name)
        return {"dialect": "single_sheet",
                "feeds": {feed_key: {"sheet": name, "fields": fields,
                                     "ref_targets": ref_targets,
                                     "recycle_note": None}},
                "meta": meta}
    return {"dialect": "single_sheet", "feeds": {}, "meta": {}}


# --------------------------------------------------------------------------- #
# match contract feeds <-> dictionary feeds
# --------------------------------------------------------------------------- #
def match_feeds(contract, dictionary):
    """Returns {contract feed index: dict feed_key}. Matches on stage tables
    (sheet-per-table) or falls back to the single dictionary feed."""
    out = {}
    dict_keys = list(dictionary["feeds"])
    for i, feed in enumerate(contract["feeds"]):
        tables = [_nl(t) for t in (feed.get("stage_target") or {}).get("tables", [])]
        hit = next((k for k in dict_keys if k in tables), None)
        if hit is None and len(dict_keys) == 1 and len(contract["feeds"]) == 1:
            hit = dict_keys[0]
        if hit is not None:
            out[i] = hit
    return out



# --------------------------------------------------------------------------- #
# loose token bag — the coarse similarity primitive 04's driver used for
# reference pairing, now shared with frdsttm.similarity
# --------------------------------------------------------------------------- #
def loose_tokens(s):
    return {t for t in re.findall(r"[a-z0-9]{4,}", str(s).lower())}
