# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — STTM Render: dictionary cross-check → derive → render → golden-pair eval
# MAGIC
# MAGIC Phase 5 of the FRD→STTM pipeline. Consumes the Phase 4 contracts and the
# MAGIC reference STTM workbooks, which play **two deliberately separated roles**:
# MAGIC
# MAGIC 1. Their **source-layout sections** stand in for the vendor source
# MAGIC    dictionary (the production input that will arrive as its own artifact).
# MAGIC 2. Their **stage/standard sections are ground truth for the eval only** —
# MAGIC    never an input to generation. Target mappings are DERIVED from
# MAGIC    contract + dictionary + explicit rules, then diffed against the
# MAGIC    reference to show exactly which decisions the pipeline can and cannot
# MAGIC    make on its own.
# MAGIC
# MAGIC Steps: **(a)** parse source dictionaries (both workbook dialects,
# MAGIC header-name-driven — the sheets disagree on column names/order);
# MAGIC **(b)** apply any saved review-app resolutions
# MAGIC (`_provenance.human_resolutions`) first — a human resolution is
# MAGIC authoritative and is never re-decided below — then resolve whatever's
# MAGIC left of the Phase 4 attribution ambiguities by dictionary column
# MAGIC membership (zip_code/member_id) and dictionary recycle markers;
# MAGIC **(c)** derive stage/standard mappings (1:1 names, String at stage,
# MAGIC segment-suffix table routing HDR/DTL/TRL); **(d)** render the STTM
# MAGIC workbook in the matching client dialect (sheet-per-table or single-sheet);
# MAGIC **(e)** cell-level eval against the reference workbook.

# COMMAND ----------

# MAGIC %pip install openpyxl
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# Local-mode support: see notebooks/01_frd_ingest.py's parameter cell for the
# full explanation of IS_DATABRICKS / _param(). Databricks execution below is
# unchanged from before this cell existed.
import os
from pathlib import Path

IS_DATABRICKS = "dbutils" in globals()
LOCAL_ROOT = Path(__file__).resolve().parent.parent / "local_dev_fixtures"


def _param(name: str, default: str) -> str:
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)
        return dbutils.widgets.get(name)
    return os.environ.get(name.upper(), default)


CATALOG = _param("catalog", "soham_workspace")
SCHEMA = _param("schema", "sttm_agent")
CONTRACTS_TABLE_NAME = _param("contracts_table", "frd_contracts")
RUNS_TABLE_NAME = _param("runs_table", "frd_sttm_runs")
REFERENCE_VOLUME = _param("reference_volume", "sttm_reference")
OUT_VOLUME = _param("out_volume", "sttm_out")

if IS_DATABRICKS:
    CONTRACTS_TABLE = f"{CATALOG}.{SCHEMA}.{CONTRACTS_TABLE_NAME}"
    RUNS_TABLE = f"{CATALOG}.{SCHEMA}.{RUNS_TABLE_NAME}"
    REFERENCE_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{REFERENCE_VOLUME}"
    OUT_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{OUT_VOLUME}"
else:
    CONTRACTS_TABLE = CONTRACTS_TABLE_NAME
    RUNS_TABLE = RUNS_TABLE_NAME
    REFERENCE_DIR = str(LOCAL_ROOT / REFERENCE_VOLUME)
    OUT_ROOT = str(LOCAL_ROOT / OUT_VOLUME)
RENDERED_DIR = f"{OUT_ROOT}/rendered"
CONTRACTS_DIR = f"{OUT_ROOT}/contracts"
REPORTS_DIR = f"{OUT_ROOT}/reports"
print(f"contracts: {CONTRACTS_TABLE}\nreference: {REFERENCE_DIR}\nrendered:  {RENDERED_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Core (pure Python — unit-testable as-is)

# COMMAND ----------

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

GENERATOR = "frd-sttm-agent phase5 v0.1"

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
# human resolution wiring — apply saved review-app resolutions
# (`_provenance.human_resolutions`, written by review_app_react/) ahead of
# the automatic paths below. Matching is by each record's `ambiguity_id`
# against the `id` on a GatedAmbiguity object stored directly in
# `_provenance.ambiguities` / `_provenance.grounding.advisory_flagged` (see
# notebooks/_models.py) -- 03_contract_build.py emits
# `id`/`kind`/`has_candidates`/`candidates`/`context` on the ambiguity itself
# now, so this file reads those fields directly instead of reverse-parsing
# display text (the regex-parsing this replaced was deleted from the review
# app's backend along with this change). Precedence: a human resolution,
# once structurally
# applicable, is authoritative and is never re-decided by the automatic
# dictionary cross-check below; when nothing has been reviewed for a given
# ambiguity, behavior is byte-for-byte the pre-existing automatic path (see
# resolve_attribution()'s `already_settled` param, empty by default).
#
# Branching is has_candidates-driven, uniform across all three kinds (not
# kind-name special-cased): a candidate-having ambiguity (attribution,
# disagreement) requires resolution_type == "candidate_pick" with a
# chosen_candidate still present in the ambiguity's current candidates to
# apply structurally; "none_of_these" is a deliberate, valid terminal state
# that leaves it gated for whatever automatic fallback exists (attribution
# only -- disagreement has none); "free_text" on a candidate-having
# ambiguity is malformed relative to this policy and is never applied,
# exactly like today's attribution-only refusal, now generalized to name
# whichever kind triggered it. A candidate-free ambiguity (advisory_
# grounding) has no candidate_pick to make, so "free_text" with a `rationale`
# IS the resolution, applied exactly as today's override_value was.
# --------------------------------------------------------------------------- #

# Verbatim port of 03_contract_build.py's norm() -- not imported, since that
# script runs pipeline driver code (table reads/writes) at module import
# time (see review_app_react/backend/ambiguity_parsing.py's docstring for
# the same constraint on the review app's own FastAPI backend, which can't
# import 03_contract_build.py as a library either). Regrouping ambiguous
# rules here must reproduce
# byte-identical `id`s to what 03_contract_build.py stored in
# `_provenance.ambiguities`, since that id is the human_resolutions lookup
# key -- if 03's norm() or _ambiguity_id() changes, this must change with it.
_ATTR_UNICODE_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", " ": " ",
})


def _attr_norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_ATTR_UNICODE_MAP)
    t = re.sub(r"[*_#|`]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _ambiguity_id(kind: str, text: str, context: dict) -> str:
    """Verbatim port of 03_contract_build.py's _ambiguity_id()."""
    key = json.dumps({"kind": kind, "text": text, "context": context}, sort_keys=True)
    return f"{kind}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _attribution_groups(contract):
    """Reproduces attribution_check()'s grouping (03_contract_build.py) from
    the contract's CURRENT feed state, keyed by the same `id` 03 would
    produce for an unchanged contract. Must run before resolve_attribution()
    mutates validation_rules/recycle_rule, since it needs each feed's
    original (still-duplicated) rule set. If a human_resolutions record's
    ambiguity_id isn't a key in the returned dict, the underlying rule set
    has changed since this ambiguity was gated -- the same staleness
    condition the pre-id-based version of this function detected via text
    matching. Returns {id: {rule, feed_indices, feed_names}}."""
    seen = {}
    for i, feed in enumerate(contract["feeds"]):
        rules = list(feed.get("validation_rules") or [])
        if feed.get("recycle_rule"):
            rules = rules + [feed["recycle_rule"]]
        for rule in rules:
            entry = seen.setdefault(_attr_norm(rule), {"rule": rule, "feed_indices": [], "feed_names": []})
            entry["feed_indices"].append(i)
            entry["feed_names"].append(feed["feed_name"])
    groups = {}
    for entry in seen.values():
        if len(entry["feed_indices"]) > 1:
            text = (f"rule applied to {len(entry['feed_names'])} feeds ({', '.join(entry['feed_names'])}) — "
                    f"attribution unconfirmed pending source dictionary: {entry['rule'][:120]!r}")
            context = {"feed_names": entry["feed_names"], "feed_indices": entry["feed_indices"], "rule": entry["rule"]}
            aid = _ambiguity_id("attribution", text, context)
            groups[aid] = {"rule": entry["rule"], "feed_indices": entry["feed_indices"],
                           "feed_names": entry["feed_names"]}
    return groups


# Feed-scoped advisory fields we're confident map 1:1 onto a free-text
# override: plain Optional[str] fields (direct overwrite) vs List[str]
# fields (replace the one flagged element, found by exact value match --
# never the whole list, which would silently drop every other entry).
_ADVISORY_SCALAR_FIELDS = {"recycle_rule", "history_backfill", "archive_retention",
                          "phi_pii_notes", "landing_location", "sttm_reference"}
_ADVISORY_LIST_FIELDS = {"validation_rules", "load_windows_sla"}


def _apply_advisory_override(contract, context, rationale):
    """Returns (applied, target, reason_not_applied). `context` is the
    GatedAmbiguity.context 03_contract_build.py stored on this
    advisory_grounding ambiguity -- feed_index/field are only present when
    the flagged path is feed-scoped (see grounding_audit()'s advisory()
    closure); top-level paths (in_scope/out_of_scope/system_interfaces/
    open_items, acd(name), project.*) carry `path` only and are
    intentionally out of scope here, unchanged from today -- no real
    fixture exercises them and their shapes vary too much to generalize
    with confidence."""
    idx, field = context.get("feed_index"), context.get("field")
    if idx is None or field is None:
        return False, None, f"advisory path shape not recognized for structural write-back: {context.get('path')!r}"
    if idx >= len(contract["feeds"]):
        return False, None, f"feed index {idx} out of range for this contract"
    feed = contract["feeds"][idx]
    target = f"feeds[{idx}].{field}"
    if field in _ADVISORY_SCALAR_FIELDS:
        feed[field] = rationale
        return True, target, None
    if field in _ADVISORY_LIST_FIELDS:
        original_value = context.get("original_value")
        values = list(feed.get(field) or [])
        for j, v in enumerate(values):
            if v == original_value:
                values[j] = rationale
                feed[field] = values
                return True, f"{target}[{j}]", None
        return False, None, f"original flagged value no longer present in {target}"
    return False, None, f"advisory field {field!r} not in the known scalar/list write-back set"


def _apply_disagreement_override(contract, field, value):
    """The only disagreement source today (enrich() in 03_contract_build.py)
    is project.project_id. Only called for a structural candidate_pick now
    (see apply_human_resolutions() below) -- free text is no longer applied
    for this kind, since it has real candidates (agent value vs. content
    value) and this policy requires a pick from candidate-having ambiguities."""
    if field == "project.project_id":
        contract.setdefault("project", {})["project_id"] = value
        return True, "project.project_id", None
    return False, None, f"disagreement field {field!r} has no known target mapping"


def apply_human_resolutions(contract):
    """Applies `_provenance.human_resolutions` before the automatic paths
    below ever run. Branches on `ambiguity.has_candidates`, not on kind name
    (see module-level comment above for the full policy):

    - has_candidates + resolution_type == "candidate_pick" (chosen_candidate
      still among the ambiguity's current candidates): apply structurally
      per kind -- attribution keeps the rule only on the chosen feed and
      strips it from the others in its cross-feed group; disagreement writes
      the chosen value into the one scalar field named in context.field.
    - has_candidates + resolution_type == "none_of_these": NOT applied, by
      design -- a deliberate rejection of every listed candidate, left gated
      for whatever automatic fallback exists (attribution's dictionary
      cross-check; disagreement has none).
    - has_candidates + anything else (resolution_type == "free_text", or a
      chosen_candidate that no longer matches): NOT applied -- a structural
      pick was required and not provided. This generalizes what was
      previously an attribution-only refusal to every candidate-having kind
      (disagreement included, a real behavior change -- see summary).
    - not has_candidates (advisory_grounding today): resolution_type ==
      "free_text" with a `rationale` IS the resolution -- there's nothing
      else it could be. Applied via the kind's known write-back path.

    Staleness: if a resolution's `candidates_snapshot` no longer matches the
    ambiguity's current `candidates`, the entry is flagged `stale` in the
    audit *before* the branches above run -- a stale candidate_pick is still
    attempted if chosen_candidate remains valid, but the staleness is always
    visible in resolution_audit either way.

    Returns (settled_rules, audit) where settled_rules is a
    {(feed_index, normalized_rule_text)} set resolve_attribution() must treat
    as already-decided (never remove, regardless of dictionary evidence), and
    audit is the list of resolution_audit entries this call produced. Mutates
    `contract` and `_provenance.ambiguities` / `_provenance.grounding.
    advisory_flagged` / `_provenance.resolution_audit` in place.
    """
    prov = contract.setdefault("_provenance", {})
    audit = prov.setdefault("resolution_audit", [])
    human_resolutions = prov.get("human_resolutions") or []
    if not human_resolutions:
        return set(), audit

    ambiguities_by_id = {a["id"]: a for a in prov.get("ambiguities", [])}
    advisory_by_id = {a["id"]: a for a in prov.get("grounding", {}).get("advisory_flagged", [])}
    attribution_groups_by_id = _attribution_groups(contract)
    settled_rules = set()

    for hr in human_resolutions:
        aid = hr["ambiguity_id"]
        kind = hr.get("kind")
        resolution_type = hr.get("resolution_type")
        chosen = hr.get("chosen_candidate")
        rationale = hr.get("rationale")
        snapshot = hr.get("candidates_snapshot") or []

        ambiguity = ambiguities_by_id.get(aid) or advisory_by_id.get(aid)
        entry = {
            "ambiguity_id": aid, "kind": kind,
            "ambiguity_text": ambiguity["text"] if ambiguity else None,
            "resolution_source": "human", "resolution_type": resolution_type,
            "chosen_candidate": chosen, "rationale": rationale,
            "applied": False, "target": None, "reason_not_applied": None,
            "stale": False, "stale_detail": None,
        }

        if ambiguity is None:
            entry["reason_not_applied"] = (
                "no gated ambiguity with this id found in the current contract "
                "(may have already been cleared by a prior resolution, or the "
                "contract was regenerated since this resolution was recorded)")
            audit.append(entry)
            continue

        current_candidates = ambiguity["candidates"]
        if snapshot and set(snapshot) != set(current_candidates):
            entry["stale"] = True
            entry["stale_detail"] = (
                f"candidates changed since this resolution was recorded: "
                f"was {snapshot}, now {current_candidates}")

        if ambiguity["has_candidates"]:
            if resolution_type == "candidate_pick" and chosen in current_candidates:
                if kind == "attribution":
                    group = attribution_groups_by_id.get(aid)
                    if group is None:
                        entry["reason_not_applied"] = (
                            "no matching cross-feed rule group found in the current contract "
                            "(validation_rules/recycle_rule may have changed since this "
                            "ambiguity was gated)")
                    else:
                        rule_norm = _attr_norm(group["rule"])
                        for i in group["feed_indices"]:
                            feed = contract["feeds"][i]
                            if feed["feed_name"] == chosen:
                                settled_rules.add((i, rule_norm))
                                continue
                            feed["validation_rules"] = [
                                r for r in feed.get("validation_rules", []) if _attr_norm(r) != rule_norm]
                            if feed.get("recycle_rule") and _attr_norm(feed["recycle_rule"]) == rule_norm:
                                feed["recycle_rule"] = None
                        entry["applied"] = True
                        entry["target"] = f"feeds[*].validation_rules/recycle_rule (kept only on {chosen!r})"
                elif kind == "disagreement":
                    field = ambiguity["context"].get("field")
                    entry["applied"], entry["target"], entry["reason_not_applied"] = \
                        _apply_disagreement_override(contract, field, chosen)
                else:
                    entry["reason_not_applied"] = f"ambiguity kind {kind!r} has candidates but no known structural write-back path"
            elif resolution_type == "none_of_these":
                entry["reason_not_applied"] = (
                    "reviewer explicitly rejected every listed candidate -- left gated for "
                    + ("the automatic dictionary cross-check to decide feed membership"
                       if kind == "attribution" else "manual resolution; no automatic fallback for this kind"))
            else:
                bad_pick = f"chosen_candidate {chosen!r} not among {current_candidates}" if chosen is not None else "free text instead of a pick"
                entry["reason_not_applied"] = (
                    f"structural pick required and not provided ({bad_pick}) -- this ambiguity "
                    f"has known candidates, so a free-text rationale alone has no confident "
                    f"structural mapping; rationale recorded, "
                    + ("automatic dictionary cross-check still decides feed membership"
                       if kind == "attribution" else "still gated"))
        else:
            if resolution_type == "free_text" and rationale:
                if kind == "advisory_grounding":
                    entry["applied"], entry["target"], entry["reason_not_applied"] = \
                        _apply_advisory_override(contract, ambiguity["context"], rationale)
                else:
                    entry["reason_not_applied"] = f"ambiguity kind {kind!r} has no candidates but no known free-text write-back path"
            else:
                entry["reason_not_applied"] = (
                    "no rationale present (unexpected -- this ambiguity has no candidates, "
                    "free text is the only possible resolution)")

        audit.append(entry)

    # A human explicitly reviewed every ambiguity/advisory flag with a saved
    # resolution, whether or not it was structurally applicable -- clear it
    # from the "needs review" lists the same way resolve_attribution() below
    # clears automatically-resolved ones. Which value ended up in the
    # contract (human vs. automatic-fallback) is exactly what
    # `resolution_audit` records.
    resolved_ids = {hr["ambiguity_id"] for hr in human_resolutions}
    prov["ambiguities"] = [a for a in prov.get("ambiguities", []) if a["id"] not in resolved_ids]
    if prov.get("grounding", {}).get("advisory_flagged"):
        prov["grounding"]["advisory_flagged"] = [
            a for a in prov["grounding"]["advisory_flagged"] if a["id"] not in resolved_ids]
    if (contract.get("status") == "PASS_WITH_FLAGS" and not prov["ambiguities"]
            and not prov.get("grounding", {}).get("advisory_flagged")):
        contract["status"] = "PASS"

    return settled_rules, audit


# --------------------------------------------------------------------------- #
# attribution resolution
# --------------------------------------------------------------------------- #
_COL_TOKEN = re.compile(r"\b([A-Za-z][A-Za-z0-9_]{2,})\s+column\b", re.I)


def resolve_attribution(contract, dictionary, feed_match, already_settled=frozenset()):
    """Clears cross-feed rule ambiguities using dictionary column membership.
    Mutates the contract; returns list of resolution strings.

    `already_settled` is a {(feed_index, normalized_rule_text)} set of rules
    apply_human_resolutions() has already decided by human candidate-pick --
    those are kept unconditionally, never re-evaluated against the
    dictionary, so a human decision can never be silently overridden by the
    automatic cross-check. Empty by default, which reproduces the exact
    pre-existing behavior when nothing has been human-resolved."""
    cols_by_feed = {i: {_nl(f["source_column"]) for f in dictionary["feeds"][k]["fields"]}
                    for i, k in feed_match.items()}
    recycle_feeds = {i for i, k in feed_match.items()
                     if dictionary["feeds"][k]["recycle_note"]}
    all_cols = set().union(*cols_by_feed.values()) if cols_by_feed else set()
    resolutions = []

    for i, feed in enumerate(contract["feeds"]):
        if i not in cols_by_feed:
            continue
        kept = []
        for rule in feed.get("validation_rules", []):
            if (i, _attr_norm(rule)) in already_settled:
                kept.append(rule)
                continue
            cands = {_nl(c) for c in _COL_TOKEN.findall(rule)} & all_cols
            if cands and not (cands & cols_by_feed[i]):
                resolutions.append(
                    f"removed rule from {feed['feed_name']!r} — column(s) "
                    f"{sorted(cands)} not in its source dictionary: {rule[:90]!r}")
                continue
            if "recycle" in _nl(rule) and i not in recycle_feeds and recycle_feeds:
                resolutions.append(
                    f"removed recycle rule from {feed['feed_name']!r} — its dictionary "
                    f"carries no recycle marker: {rule[:90]!r}")
                continue
            kept.append(rule)
        feed["validation_rules"] = kept
        if feed.get("recycle_rule") and recycle_feeds and i not in recycle_feeds \
                and (i, _attr_norm(feed["recycle_rule"])) not in already_settled:
            resolutions.append(
                f"cleared recycle_rule on {feed['feed_name']!r} — dictionary recycle "
                f"marker present only on {[contract['feeds'][j]['feed_name'] for j in sorted(recycle_feeds)]}")
            feed["recycle_rule"] = None

    prov = contract.setdefault("_provenance", {})
    prov["attribution_resolutions"] = resolutions
    if resolutions:
        prov["ambiguities"] = [a for a in prov.get("ambiguities", [])
                               if a["kind"] != "attribution"]
        if contract.get("status") == "PASS_WITH_FLAGS" and not prov["ambiguities"] \
                and not prov.get("grounding", {}).get("advisory_flagged"):
            contract["status"] = "PASS"
    return resolutions


# --------------------------------------------------------------------------- #
# derive target mappings (generation — reference targets are never read here)
# --------------------------------------------------------------------------- #
_SEGMENT_SUFFIX = {"header": "_HDR", "detail": "_DTL", "trailer": "_TRL"}


def _table_for_segment(tables, segment):
    seg = _nl(segment)
    suffix = _SEGMENT_SUFFIX.get(seg)
    if suffix:
        for t in tables:
            if t.upper().endswith(suffix):
                return t
    return tables[0] if tables else ""


def derive_field_mappings(contract, dictionary, feed_match):
    """Attach derived stage/standard mappings to each matched feed's fields.
    Rules (the pipeline's explicit, contestable defaults):
      - stage: 1:1 column name, String datatype, schema/catalog from contract,
        table chosen by record segment suffix (HDR/DTL/TRL) when present.
      - standard: same 1:1 name; schema/catalog from contract when stated,
        else null (the FRD may genuinely not state it); String datatype.
    """
    for i, key in feed_match.items():
        feed = contract["feeds"][i]
        stg = feed.get("stage_target") or {}
        std = feed.get("standard_target") or {}
        derived = []
        for f in dictionary["feeds"][key]["fields"]:
            derived.append({
                **f,
                "stage": {"catalog": stg.get("catalog"), "schema": stg.get("schema"),
                          "table": _table_for_segment(stg.get("tables", []), f.get("segment")),
                          "column": f["source_column"], "datatype": "String"},
                "standard": {"catalog": std.get("catalog"), "schema": std.get("schema"),
                             "table": _table_for_segment(std.get("tables", []), f.get("segment"))
                                      or _table_for_segment(stg.get("tables", []), f.get("segment")),
                             "column": f["source_column"], "datatype": "String"},
            })
        feed["fields"] = derived
    return contract


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #
_HDR_FILL = PatternFill("solid", fgColor="D9E1F2")
_SEC_FILL = PatternFill("solid", fgColor="BDD7EE")
_BOLD = Font(bold=True)


def _style_row(ws, r, n_cols, fill):
    for c in range(1, n_cols + 1):
        cell = ws.cell(r, c)
        cell.font = _BOLD
        cell.fill = fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def render_sheet_per_table(contract, out_path):
    """MIDS dialect: FILE_DETAILS + VERSION_HISTORY + one MAPPING-* sheet per feed."""
    wb = Workbook()
    ws = wb.active
    ws.title = "FILE_DETAILS"
    ws.append(["Vendor", "FileName", "File Description", "Location", "Frequency"])
    _style_row(ws, 1, 5, _HDR_FILL)
    for feed in contract["feeds"]:
        ws.append([feed.get("source_system") or "",
                   "; ".join(feed.get("file_name_patterns", [])),
                   "", feed.get("landing_location") or "", feed.get("frequency") or ""])

    vh = wb.create_sheet("VERSION_HISTORY")
    vh.append(["Version", "Date", "Author", "Change Description"])
    _style_row(vh, 1, 4, _HDR_FILL)
    vh.append(["0.1", datetime.now(timezone.utc).date().isoformat(), GENERATOR,
               f"Auto-generated from {contract.get('generated_from_frd', 'FRD')}"])

    src_headers = ["Database column Name", "NULL CHECK", "Description", "Sample Value",
                   "DataType", "PHI Field", "Mandatory Field"]
    tgt_headers = ["Schema", "TableName", "ColumnName", "DataType"]
    for feed in contract["feeds"]:
        fields = feed.get("fields") or []
        if not fields:
            continue
        table = (feed.get("stage_target") or {}).get("tables", [feed["feed_name"]])[0]
        ws = wb.create_sheet(f"MAPPING-{table.upper()}"[:31])
        n_cols = 7 + 4 + 1 + 4
        ws.cell(1, 1, "Source File Layout")
        ws.cell(1, 8, "Stage Layer")
        ws.cell(1, 13, "Standard Layer")
        _style_row(ws, 1, n_cols, _SEC_FILL)
        for c, h in enumerate(src_headers + tgt_headers + [""] + tgt_headers, 1):
            ws.cell(2, c, h)
        _style_row(ws, 2, n_cols, _HDR_FILL)
        for f in fields:
            ws.append([
                f["source_column"],
                "Not NULL" if not f["nullable"] else "NULL",
                f.get("description", ""), f.get("sample", ""), f.get("datatype", "String"),
                "Yes" if f["phi"] else "No", "Yes" if f["mandatory"] else "No",
                f["stage"]["schema"], f["stage"]["table"], f["stage"]["column"], f["stage"]["datatype"],
                "",
                f["standard"]["schema"], f["standard"]["table"], f["standard"]["column"], f["standard"]["datatype"],
            ])
        ws.freeze_panes = "A3"
        for c in range(1, n_cols + 1):
            ws.column_dimensions[get_column_letter(c)].width = 22
    wb.save(out_path)
    return out_path


def render_single_sheet(contract, out_path, sheet_name="mapping"):
    """CAQH dialect: metadata block + one wide sheet (source | stage | standard)."""
    wb = Workbook()
    feed = contract["feeds"][0]
    ws = wb.active
    ws.title = sheet_name[:31]
    meta_rows = [
        ("File(s)", "\n".join(feed.get("file_name_patterns", []))),
        ("File Generator", feed.get("source_system") or ""),
        ("File Location", feed.get("landing_location") or ""),
        ("LOB", ",".join(re.sub(r"^REG#\d+\s+", "", l) for l in feed.get("lobs", []))),
        ("File frequency", feed.get("frequency") or ""),
        ("Domain", (feed.get("domain") or "").upper()),
        ("Sub-Domain", (feed.get("sub_domain") or "")),
        ("File type", f"{feed.get('file_format') or ''}"
                      + (f" ({feed.get('delimiter')} delimited)" if feed.get("delimiter") else "")),
    ]
    for k, v in meta_rows:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = _BOLD
    src_headers = ["#", "Field Name", "Data Type", "Length",
                   "Field Length\n(fixed width)", "Start position\n(fixed width)",
                   "End Position\n(fixed width)", "Segment", "PII", "Comments", "Business Rule"]
    tgt_headers = ["Catalog", "Schema", "TableName", "ColumnName", "DataType",
                   "Mandatory\nColumn", "Primary Key", "Field Description"]
    label_r = ws.max_row + 1
    ws.cell(label_r, 1, "Source Layout")
    ws.cell(label_r, len(src_headers) + 1, "Stage Layer")
    ws.cell(label_r, len(src_headers) + len(tgt_headers) + 1, "Standard Layer")
    n_cols = len(src_headers) + 2 * len(tgt_headers)
    _style_row(ws, label_r, n_cols, _SEC_FILL)
    for c, h in enumerate(src_headers + tgt_headers + tgt_headers, 1):
        ws.cell(label_r + 1, c, h)
    _style_row(ws, label_r + 1, n_cols, _HDR_FILL)

    for idx, f in enumerate(contract["feeds"][0].get("fields") or [], 1):
        def tgt(t):
            return [t.get("catalog") or "", t.get("schema") or "", t.get("table") or "",
                    t.get("column") or "", t.get("datatype") or "String",
                    "Yes" if f["mandatory"] else "", "", f.get("description", "")]
        ws.append([idx, f["source_column"], f.get("datatype", ""), f.get("length", ""),
                   f.get("fixed_length", ""), f.get("fixed_start", ""), f.get("fixed_end", ""),
                   f.get("segment", ""), "Yes" if f["phi"] else "", f.get("comment", ""),
                   f.get("business_rule", "")]
                  + tgt(f["stage"]) + tgt(f["standard"]))
    ws.freeze_panes = ws.cell(label_r + 2, 1).coordinate
    for c in range(1, n_cols + 1):
        ws.column_dimensions[get_column_letter(c)].width = 18
    wb.save(out_path)
    return out_path


def render_contract(contract, dictionary, out_path):
    if dictionary["dialect"] == "sheet_per_table":
        return render_sheet_per_table(contract, out_path)
    sheet = next(iter(dictionary["feeds"].values()))["sheet"]
    return render_single_sheet(contract, out_path, sheet_name=sheet)


# --------------------------------------------------------------------------- #
# golden-pair eval: derived mappings vs reference targets
# --------------------------------------------------------------------------- #
def evaluate_against_reference(contract, dictionary, feed_match):
    report = {"feeds": [], "totals": {"cells": 0, "match": 0}}
    for i, key in feed_match.items():
        feed = contract["feeds"][i]
        ref = dictionary["feeds"][key]["ref_targets"]
        diffs, cells, match = [], 0, 0
        for f, rt in zip(feed.get("fields") or [], ref):
            for layer in ("stage", "standard"):
                for attr in ("schema", "table", "column", "datatype"):
                    ref_v = _nl(rt[layer].get(attr))
                    if not ref_v:
                        continue
                    cells += 1
                    der_v = _nl(f[layer].get(attr))
                    if der_v == ref_v:
                        match += 1
                    elif len(diffs) < 8:
                        diffs.append(f"{f['source_column']}.{layer}.{attr}: "
                                     f"derived={der_v!r} ref={ref_v!r}")
        pct = round(100 * match / cells, 1) if cells else 100.0
        report["feeds"].append({"feed": feed["feed_name"], "cells": cells,
                                "match": match, "pct": pct, "sample_diffs": diffs})
        report["totals"]["cells"] += cells
        report["totals"]["match"] += match
    t = report["totals"]
    t["pct"] = round(100 * t["match"] / t["cells"], 1) if t["cells"] else 100.0
    return report

# COMMAND ----------

# MAGIC %md
# MAGIC ## Driver: contract × reference workbook → resolve → derive → render → eval

# COMMAND ----------

from pathlib import Path


def _loose_tokens(s):
    return {t for t in re.findall(r"[a-z0-9]{4,}", s.lower())}


refs = sorted(Path(REFERENCE_DIR).glob("*.xlsx"))
assert refs, f"No reference workbooks found in {REFERENCE_DIR}"

# Read contracts from the CONTRACTS_DIR JSON files, not the frd_contracts
# Delta table. The table is a snapshot from 03_contract_build.py's run, at
# which point `_provenance.human_resolutions` doesn't exist yet -- the
# review app (review_app_react/) writes reviewer resolutions into the
# same-named JSON file under CONTRACTS_DIR, in both local mode
# (plain filesystem) and Databricks mode (UC volume, which is just as
# directly Path-readable -- see OUT_ROOT/CONTRACTS_DIR above). Reading the
# table here would silently make every human resolution invisible to this
# notebook; this was confirmed empirically while building the human-
# resolution wiring below (docs/STANDUP_NOTES.md has the before/after).
_contract_paths = sorted(Path(CONTRACTS_DIR).glob("*.contract.json"))
assert _contract_paths, f"No contract JSONs found in {CONTRACTS_DIR}"

contracts = {}
for _p in _contract_paths:
    doc_id = _p.name[: -len(".contract.json")]
    contracts[doc_id] = json.loads(_p.read_text(encoding="utf-8"))
print(f"{len(contracts)} contract(s), {len(refs)} reference workbook(s)")

Path(RENDERED_DIR).mkdir(parents=True, exist_ok=True)
Path(CONTRACTS_DIR).mkdir(parents=True, exist_ok=True)
Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)

runs = []
for doc_id, contract in contracts.items():
    dt = _loose_tokens(doc_id)
    ref = max(refs, key=lambda p: len(_loose_tokens(p.stem) & dt))
    dictionary = parse_reference_workbook(str(ref))
    fm = match_feeds(contract, dictionary)
    if not fm:
        print(f"SKIP {doc_id}: no feed matched dictionary {ref.name}")
        continue

    human_settled, human_audit = apply_human_resolutions(contract)
    human_audit = list(human_audit)  # snapshot -- resolution_audit gets automatic entries appended next
    resolutions = resolve_attribution(contract, dictionary, fm, already_settled=human_settled)
    contract["_provenance"]["resolution_audit"].extend(
        {"ambiguity_id": None, "kind": None, "ambiguity_text": None, "resolution_source": "automatic",
         "resolution_type": None, "applied": True, "target": None, "detail": r} for r in resolutions)
    derive_field_mappings(contract, dictionary, fm)
    out_xlsx = str(Path(RENDERED_DIR) / f"{doc_id}.sttm.xlsx")
    render_contract(contract, dictionary, out_xlsx)
    ev = evaluate_against_reference(contract, dictionary, fm)

    (Path(CONTRACTS_DIR) / f"{doc_id}.contract.v2.json").write_text(
        json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    n_human_applied = sum(1 for a in human_audit if a["applied"])
    lines = [f"# STTM render — {doc_id}", "",
             f"**Status: {contract['status']}** | dialect: {dictionary['dialect']} | "
             f"reference: {ref.name}",
             f"**Golden-pair eval: {ev['totals']['pct']}%** "
             f"({ev['totals']['match']}/{ev['totals']['cells']} target cells match the reference)", ""]
    if human_audit:
        lines += ["## Resolution audit (human vs. automatic)"]
        for a in human_audit:
            status = "applied" if a["applied"] else f"NOT applied — {a['reason_not_applied']}"
            stale_note = " [STALE: candidates changed since this resolution was recorded]" if a.get("stale") else ""
            text_preview = (a.get("ambiguity_text") or "")[:100]
            lines.append(f"- [human] {a['kind']} ({a.get('resolution_type')}): {text_preview!r} -> {status}{stale_note}"
                         + (f" (target: {a['target']})" if a["target"] else ""))
        lines += [""]
    if resolutions:
        lines += ["## Attribution resolutions (dictionary cross-check, automatic)"] + \
                 [f"- {r}" for r in resolutions] + [""]
    lines += ["## Per-feed eval"]
    for f in ev["feeds"]:
        lines.append(f"- **{f['feed']}**: {f['pct']}% ({f['match']}/{f['cells']})")
        for d in f["sample_diffs"]:
            lines.append(f"    - {d}")
    (Path(REPORTS_DIR) / f"{doc_id}.phase5.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"{contract['status']:6s} {doc_id}: eval {ev['totals']['pct']}%, "
          f"{n_human_applied}/{len(human_audit)} human resolution(s) applied, "
          f"{len(resolutions)} attribution resolution(s) -> {Path(out_xlsx).name}")
    runs.append({"doc_id": doc_id, "status": contract["status"],
                 "dialect": dictionary["dialect"], "reference": ref.name,
                 "n_resolutions": len(resolutions),
                 "n_human_resolutions": len(human_audit),
                 "n_human_resolutions_applied": n_human_applied,
                 "eval_pct": float(ev["totals"]["pct"]),
                 "eval_cells": ev["totals"]["cells"],
                 "rendered_path": out_xlsx,
                 "run_at": datetime.now(timezone.utc)})

if IS_DATABRICKS:
    from pyspark.sql import types as T

    rschema = T.StructType([
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("status", T.StringType(), False),
        T.StructField("dialect", T.StringType(), False),
        T.StructField("reference", T.StringType(), False),
        T.StructField("n_resolutions", T.IntegerType(), False),
        T.StructField("n_human_resolutions", T.IntegerType(), False),
        T.StructField("n_human_resolutions_applied", T.IntegerType(), False),
        T.StructField("eval_pct", T.DoubleType(), False),
        T.StructField("eval_cells", T.IntegerType(), False),
        T.StructField("rendered_path", T.StringType(), False),
        T.StructField("run_at", T.TimestampType(), False),
    ])
    spark.createDataFrame(runs, rschema).write.mode("overwrite").option(
        "overwriteSchema", "true").saveAsTable(RUNS_TABLE)
    display(spark.table(RUNS_TABLE))
else:
    from _local_tables import read_table, write_table

    write_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, RUNS_TABLE_NAME, runs)
    for _r in read_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, RUNS_TABLE_NAME):
        print(_r)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reading the eval
# MAGIC The %% is not a score to maximize blindly — the diff list is the point.
# MAGIC Expected gaps with current derivation rules: **standard-layer type casts**
# MAGIC (String vs Decimal/int — an analyst decision the FRD doesn't state) and,
# MAGIC for the single-sheet dialect, **target column renaming conventions**
# MAGIC (e.g. TPL_* codes) plus the standard schema the FRD omits. Those gaps are
# MAGIC the honest backlog: naming-convention config per client, and a typed
# MAGIC gold-layer dictionary input.
