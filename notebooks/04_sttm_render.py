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


# COMMAND ----------

# MAGIC %run ./_reference_workbooks

# COMMAND ----------

# Reference-workbook parsing (both dialects), feed matching and the shared
# normalization helpers were factored VERBATIM into
# `src/frdsttm/reference_workbooks.py` (2026-08-22) so the similarity/corpus
# modules and the review app read dictionaries with exactly this stage's
# parser -- one parser, no drift. `%run` above is a Databricks-only magic --
# inert when this file executes as a plain script, so the names would never
# get defined locally without this explicit import.
if not IS_DATABRICKS:
    from _reference_workbooks import (  # noqa: F401
        _n,
        _nl,
        loose_tokens,
        match_feeds,
        parse_reference_workbook,
    )

# Importable in both modes: the %run shim above (Databricks) and the local
# import (script mode) both put src/ on sys.path first.
from frdsttm.corpus import (  # noqa: E402
    frd_features_from_index,
    load_corpus_index,
    own_reference_for,
    reference_features_from_index,
)
from frdsttm.similarity import (  # noqa: E402
    decide_templates,
    frd_features,
    merge_dictionaries,
    thresholds_from,
    workbook_features,
)

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


def _quote_rule(rule: str, limit: int = 200) -> str:
    """Rule text for a resolution message: full up to `limit` chars, else cut
    at a word boundary with an ellipsis — never mid-word (the old hard
    `rule[:90]` slice produced quotes like \"...moving it to the reje\")."""
    rule = rule.strip()
    if len(rule) <= limit:
        return rule
    cut = rule[:limit].rsplit(" ", 1)[0].rstrip()
    return f"{cut}…"


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
                    f"{sorted(cands)} not in its source dictionary: {_quote_rule(rule)!r}")
                continue
            if "recycle" in _nl(rule) and i not in recycle_feeds and recycle_feeds:
                resolutions.append(
                    f"removed recycle rule from {feed['feed_name']!r} — its dictionary "
                    f"carries no recycle marker: {_quote_rule(rule)!r}")
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

    # D2 fix (docs/LIVE_E2E_2026-08-07.md): an attribution ambiguity whose
    # candidate feeds already EQUAL the dictionary-confirmed set is settled
    # by the same evidence a removal uses -- the extraction agrees with the
    # dictionary, there is just nothing left to remove. Before this pass, a
    # dictionary-correct extraction stayed gated (zero removals -> the
    # clearing below never ran) while a spread-across-all-feeds extraction
    # cleared to PASS. Confirm-and-clear those individually; ambiguities the
    # dictionary cannot adjudicate (no known column token, or a partial
    # overlap) keep their human gate.
    n_removals = len(resolutions)
    confirmed_ids = set()
    for amb in prov.get("ambiguities", []):
        if amb.get("kind") != "attribution":
            continue
        ctx = amb.get("context") or {}
        rule = ctx.get("rule") or amb.get("text", "")
        cand = set(ctx.get("feed_indices") or [])
        if not cand:
            continue
        cols = {_nl(c) for c in _COL_TOKEN.findall(rule)} & all_cols
        if cols:
            dict_set = {i for i, cs in cols_by_feed.items() if cols & cs}
        elif "recycle" in _nl(rule):
            dict_set = set(recycle_feeds)
        else:
            continue
        if dict_set and cand == dict_set:
            confirmed_ids.add(amb["id"])
            resolutions.append(
                f"confirmed rule attribution on "
                f"{[contract['feeds'][j]['feed_name'] for j in sorted(cand)]} "
                f"— candidates match dictionary column membership exactly: "
                f"{_quote_rule(rule)!r}")

    prov["attribution_resolutions"] = resolutions
    if n_removals:
        # Removals re-attribute every rule deterministically -- the
        # pre-existing behavior clears all attribution ambiguities.
        prov["ambiguities"] = [a for a in prov.get("ambiguities", [])
                               if a["kind"] != "attribution"]
    elif confirmed_ids:
        prov["ambiguities"] = [a for a in prov.get("ambiguities", [])
                               if a["id"] not in confirmed_ids]
    if resolutions:
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
    """Demo (CV) dialect: FILE_DETAILS + VERSION_HISTORY + one MAPPING-* sheet per feed."""
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


def evaluate_cross_reference(contract, own_dictionary, feed_match_own):
    """Eval against the document's OWN reference workbook when the render was
    driven by a DIFFERENT template (exclude-own-reference cross-validation,
    2026-08-22). `evaluate_against_reference` zips rows positionally, which
    is only valid when the dictionary that derived the fields IS the eval
    reference; here rows are aligned by source column name instead, and the
    reference defines the denominator — a reference column the render never
    produced counts as unmatched cells, not as ignored.
    """
    report = {"feeds": [], "totals": {"cells": 0, "match": 0}}
    for i, key in feed_match_own.items():
        feed = contract["feeds"][i]
        derived_by_col = {_nl(f.get("source_column")): f
                          for f in (feed.get("fields") or [])}
        ref_feed = own_dictionary["feeds"][key]
        diffs, cells, match = [], 0, 0
        for src_f, rt in zip(ref_feed["fields"], ref_feed["ref_targets"]):
            col = _nl(src_f.get("source_column"))
            derived = derived_by_col.get(col)
            for layer in ("stage", "standard"):
                for attr in ("schema", "table", "column", "datatype"):
                    ref_v = _nl(rt[layer].get(attr))
                    if not ref_v:
                        continue
                    cells += 1
                    der_v = _nl((derived or {}).get(layer, {}).get(attr)) if derived else ""
                    if der_v == ref_v:
                        match += 1
                    elif len(diffs) < 8:
                        diffs.append(f"{col}.{layer}.{attr}: "
                                     f"derived={der_v!r} ref={ref_v!r}"
                                     + ("" if derived else " (column not rendered)"))
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


_loose_tokens = loose_tokens  # factored into frdsttm.reference_workbooks


refs = sorted(Path(REFERENCE_DIR).glob("*.xlsx"))
if not refs:
    # Pre-2026-08-22 this was a hard assert. Freeform mode makes an empty
    # reference library a WARNED state, not a fatal one: every document
    # renders best-effort from its contract alone, flagged, with no eval.
    print(f"WARNING: no reference workbooks in {REFERENCE_DIR} — every "
          f"document renders freeform (no dictionary, no eval). Import "
          f"reference STTMs via the review app's corpus bootstrap.")

# --------------------------------------------------------------------------- #
# Template architecture (2026-08-22, docs/TEMPLATE_ARCHITECTURE.md).
# The reference pick is a computed decision with exactly three modes:
#   single   — one workbook is the template (dialect + dictionary + eval ref)
#   amalgam  — top-k workbooks merge, first-wins per table key
#   freeform — nothing matched; best-effort render from the contract, FLAGGED
# Scores come from frdsttm.similarity over the corpus index the app's
# bootstrap wrote next to the workbooks; without an index, features are
# computed from the contract JSON (weaker, still deterministic). A document's
# OWN paired workbook is excluded from candidacy (cross-validation, decided
# 2026-08-22) and used for eval only — exclude_own_reference=0 restores the
# old self-referential behavior for debugging, never for demos.
# --------------------------------------------------------------------------- #
EXCLUDE_OWN = _param("exclude_own_reference", "1").strip().lower() in ("1", "true", "yes")
TEMPLATE_THRESHOLDS = thresholds_from(_param)

corpus_index = load_corpus_index(REFERENCE_DIR)  # None when absent; raises when corrupt
dicts_by_name = {p.name: parse_reference_workbook(str(p)) for p in refs}
wb_feats = reference_features_from_index(corpus_index) if corpus_index else {}
wb_feats = {n: f for n, f in wb_feats.items() if n in dicts_by_name}
for _name, _d in dicts_by_name.items():
    if _name not in wb_feats:
        wb_feats[_name] = workbook_features(_name, _d)

_FREEFORM_DICT = {"dialect": "sheet_per_table", "feeds": {}, "meta": {}, "feed_sources": {}}


def _own_reference(doc_id):
    """The doc's own paired workbook: corpus pairing first, exact-stem naming
    convention (<doc_id>.sttm.xlsx / <doc_id>.xlsx) as the index-less guard."""
    own = own_reference_for(corpus_index, doc_id) if corpus_index else None
    if own is None:
        lowered = {n: Path(n).stem.lower() for n in dicts_by_name}
        for n, stem in lowered.items():
            if stem in (f"{doc_id.lower()}.sttm", doc_id.lower()):
                own = n
                break
    return own if own in dicts_by_name else None


def _target_features(doc_id, contract):
    feat = frd_features_from_index(corpus_index, doc_id) if corpus_index else None
    if feat is None:
        feat = frd_features(doc_id, json.dumps(contract, ensure_ascii=False))
    return feat

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
    own_ref = _own_reference(doc_id)
    exclude = {own_ref} if (EXCLUDE_OWN and own_ref) else set()
    if wb_feats:
        decision = decide_templates(_target_features(doc_id, contract), wb_feats,
                                    TEMPLATE_THRESHOLDS, exclude=exclude)
    else:
        decision = {"mode": "freeform", "selections": [], "ranked": [],
                    "thresholds": {}}
    order = [sel["reference"] for sel in decision["selections"]]
    if decision["mode"] == "freeform":
        dictionary = dict(_FREEFORM_DICT)
    elif len(order) == 1:
        dictionary = dicts_by_name[order[0]]
    else:
        dictionary = merge_dictionaries(dicts_by_name, order)
    fm = match_feeds(contract, dictionary)
    if not fm and decision["mode"] != "freeform":
        # The scored template did not structurally match any feed. Demote to
        # freeform and KEEP rendering — the pre-2026-08-22 behavior (silent
        # SKIP) left the document with no workbook at all.
        decision = {**decision, "mode": "freeform", "demoted_from": order}
        order = []
        dictionary = dict(_FREEFORM_DICT)
        fm = {}

    human_settled, human_audit = apply_human_resolutions(contract)
    human_audit = list(human_audit)  # snapshot -- resolution_audit gets automatic entries appended next
    resolutions = resolve_attribution(contract, dictionary, fm, already_settled=human_settled)
    contract["_provenance"]["resolution_audit"].extend(
        {"ambiguity_id": None, "kind": None, "ambiguity_text": None, "resolution_source": "automatic",
         "resolution_type": None, "applied": True, "target": None, "detail": r} for r in resolutions)
    derive_field_mappings(contract, dictionary, fm)
    out_xlsx = str(Path(RENDERED_DIR) / f"{doc_id}.sttm.xlsx")
    render_contract(contract, dictionary, out_xlsx)

    # Eval precedence: the doc's OWN workbook is the ground truth whenever it
    # exists (name-aligned cross eval — valid even though the dictionary that
    # drove the render was a different template). Otherwise the positional
    # eval against the chosen template, matching pre-2026-08-22 semantics.
    # Freeform with no own reference has nothing honest to eval against.
    ev = None
    ev_reference = None
    if own_ref is not None:
        fm_own = match_feeds(contract, dicts_by_name[own_ref])
        if fm_own:
            ev = evaluate_cross_reference(contract, dicts_by_name[own_ref], fm_own)
            ev_reference = own_ref
    if ev is None and fm:
        ev = evaluate_against_reference(contract, dictionary, fm)
        ev_reference = " + ".join(order)

    contract["_provenance"]["template_decision"] = {
        "mode": decision["mode"],
        "selections": decision["selections"],
        "ranked": decision["ranked"][:5],
        "own_reference": own_ref,
        "own_excluded": bool(exclude),
        "eval_reference": ev_reference,
        "feed_sources": dictionary.get("feed_sources", {}),
        "thresholds": decision.get("thresholds", {}),
        **({"demoted_from": decision["demoted_from"]}
           if "demoted_from" in decision else {}),
    }

    (Path(CONTRACTS_DIR) / f"{doc_id}.contract.v2.json").write_text(
        json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    n_human_applied = sum(1 for a in human_audit if a["applied"])
    template_desc = {
        "single": f"template: {order[0]}" if order else "template: (none)",
        "amalgam": "amalgam of: " + ", ".join(order),
        "freeform": "FREEFORM — no template matched"
                    + (f" (demoted from {', '.join(decision['demoted_from'])})"
                       if "demoted_from" in decision else ""),
    }[decision["mode"]]
    eval_line = (
        f"**Golden-pair eval vs {ev_reference}: {ev['totals']['pct']}%** "
        f"({ev['totals']['match']}/{ev['totals']['cells']} target cells match the reference)"
        if ev else
        "**No eval** — no paired reference workbook for this document"
    )
    lines = [f"# STTM render — {doc_id}", "",
             f"**Status: {contract['status']}** | dialect: {dictionary['dialect']} | "
             f"{template_desc}",
             eval_line, ""]
    lines += ["## Template decision",
              f"- mode: **{decision['mode']}**"
              + (f" | own reference {own_ref} excluded from candidacy" if exclude else "")]
    for r in decision["ranked"][:5]:
        marker = " (excluded — own reference)" if r["excluded"] else ""
        chosen = " ← chosen" if r["reference"] in order else ""
        lines.append(f"- {r['reference']}: score {r['score']:.3f} "
                     f"(columns {r['components']['columns']:.2f}, tables "
                     f"{r['components']['tables']:.2f}, prose "
                     f"{r['components']['tokens']:.2f}){marker}{chosen}")
    if dictionary.get("feed_sources") and decision["mode"] == "amalgam":
        lines += [f"- sheet {k!r} from {v}" for k, v in
                  sorted(dictionary["feed_sources"].items())]
    lines += [""]
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
    if ev:
        lines += ["## Per-feed eval"]
        for f in ev["feeds"]:
            lines.append(f"- **{f['feed']}**: {f['pct']}% ({f['match']}/{f['cells']})")
            for d in f["sample_diffs"]:
                lines.append(f"    - {d}")
    (Path(REPORTS_DIR) / f"{doc_id}.phase5.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"{contract['status']:6s} {doc_id}: [{decision['mode']}] "
          f"eval {str(ev['totals']['pct']) + '%' if ev else 'n/a'}, "
          f"{n_human_applied}/{len(human_audit)} human resolution(s) applied, "
          f"{len(resolutions)} attribution resolution(s) -> {Path(out_xlsx).name}")
    runs.append({"doc_id": doc_id, "status": contract["status"],
                 "dialect": dictionary["dialect"],
                 # comma-joined template list, or the mode marker when none
                 "reference": ", ".join(order) if order else "(freeform)",
                 "template_mode": decision["mode"],
                 "eval_reference": ev_reference or "",
                 "n_resolutions": len(resolutions),
                 "n_human_resolutions": len(human_audit),
                 "n_human_resolutions_applied": n_human_applied,
                 # -1.0 / 0 are the explicit "no eval" sentinels: the columns
                 # stay non-nullable in both writers, and a dashboard can
                 # filter eval_pct >= 0 without NULL semantics.
                 "eval_pct": float(ev["totals"]["pct"]) if ev else -1.0,
                 "eval_cells": ev["totals"]["cells"] if ev else 0,
                 "rendered_path": out_xlsx,
                 "run_at": datetime.now(timezone.utc)})

if IS_DATABRICKS:
    from pyspark.sql import types as T

    rschema = T.StructType([
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("status", T.StringType(), False),
        T.StructField("dialect", T.StringType(), False),
        T.StructField("reference", T.StringType(), False),
        T.StructField("template_mode", T.StringType(), False),
        T.StructField("eval_reference", T.StringType(), False),
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


# COMMAND ----------

if not IS_DATABRICKS:
    # Local-mode exit guard (2026-08-22). On the py3.14 venv the interpreter can
    # deadlock at SHUTDOWN in C finalizers (deltalake/pyarrow) after every line
    # above has run and every artifact is written and closed — observed on 03
    # as a >13-minute hang at 0% CPU, while the same file exits instantly under
    # runpy. The review app's local-mode runner waits on process exit, so a
    # hang here is a failed demo run. Exit explicitly: nothing in these stages
    # relies on atexit handlers. Never reached in Databricks (no process to
    # exit — the notebook task returns normally).
    import os as _os
    import sys as _sys
    _sys.stdout.flush()
    _sys.stderr.flush()
    _os._exit(0)
