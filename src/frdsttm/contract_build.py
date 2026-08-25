"""
frdsttm.contract_build — Phase-4 core: enrichment, grounding audit, gating.

Extracted VERBATIM from notebooks/03_contract_build.py's "Core" cell (which
was documented as "Pure Python -- no Spark dependencies -- so this cell is
unit-testable as-is"); the notebook now imports these names instead of
defining them inline (via the notebooks/_contract_build.py shim in
Databricks, or a direct import locally). Logic is unchanged; only the
model imports below were added for package context.
"""

from frdsttm.label_contract import PROJECT_ID_LINE_RE
from frdsttm.models import FrdIngestionSpec, GatedAmbiguity, Project  # noqa: F401

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import List

GENERATOR = "frd-sttm-agent phase4 v0.1"


# --------------------------------------------------------------------------- #
# Text normalization shared by enrichment + grounding. Handles the unicode the
# agent and Word both emit: smart quotes, non-breaking hyphens (U+2011), em/en
# dashes, and markdown formatting chars from the parsed content.
# --------------------------------------------------------------------------- #
_UNICODE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u00a0": " ",
})


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_UNICODE_MAP)
    t = re.sub(r"[*_#|`]", " ", t)          # markdown chrome
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9_]+", norm(text)) if len(w) >= 4]


# --------------------------------------------------------------------------- #
# Gated-ambiguity construction — see notebooks/_models.py's GatedAmbiguity /
# AmbiguityContext for the schema this builds. Every call site below (in
# attribution_check(), enrich(), and grounding_audit()) declares its own
# `kind` at construction time -- there is no later step that infers kind
# from the text, so there is no code path that can produce an ambiguity
# shape outside the three kinds GatedAmbiguity's Literal allows. `id` is a
# hash of kind+text+context, all of which are fully determined by the
# spec/content inputs, so re-running the pipeline against unchanged inputs
# reproduces the same id -- the join key a human_resolutions record uses to
# survive an unrelated re-run.
# --------------------------------------------------------------------------- #
def _ambiguity_id(kind: str, text: str, context: dict) -> str:
    key = json.dumps({"kind": kind, "text": text, "context": context}, sort_keys=True)
    return f"{kind}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _make_ambiguity(kind: str, text: str, candidates: List[str], context: dict) -> dict:
    return {
        "id": _ambiguity_id(kind, text, context),
        "kind": kind,
        "text": text,
        "has_candidates": bool(candidates),
        "candidates": list(candidates),
        "context": context,
    }


# --------------------------------------------------------------------------- #
# Deterministic enrichment — facts a regex owns, not an LLM.
# The project-id line pattern comes from the shared, versioned label
# contract (contracts/frd_label_contract.json — see frdsttm.label_contract),
# because it targets the 'Project ID: NNNNNNN' body line the upstream
# brd-to-frd-agent renderer emits. The Region/LOB pattern is this repo's
# own and stays local.
# --------------------------------------------------------------------------- #
_PROJECT_ID_RE = PROJECT_ID_LINE_RE
_REGION_LOB_RE = re.compile(r"(REG#\d+)\s+(\d{4})")


def enrich(spec: FrdIngestionSpec, content: str):
    """Returns (spec, enrichments, disagreements). Mutates spec in place."""
    enrichments, disagreements = [], []
    ncontent = norm(content)

    m = _PROJECT_ID_RE.search(ncontent)
    if m:
        regex_pid = m.group(1)
        if spec.project is None:
            spec.project = Project()
        if spec.project.project_id is None:
            spec.project.project_id = regex_pid
            enrichments.append(f"project_id={regex_pid} (regex; agent returned null)")
        elif spec.project.project_id != regex_pid:
            agent_pid = spec.project.project_id
            text = (
                f"project_id: agent said {agent_pid!r}, "
                f"content says {regex_pid!r} — kept agent value, flagged"
            )
            context = {"field": "project.project_id", "agent_value": agent_pid, "content_value": regex_pid}
            disagreements.append(_make_ambiguity("disagreement", text, [agent_pid, regex_pid], context))

    pairs = [f"{r} {c}" for r, c in _REGION_LOB_RE.findall(content)]
    if pairs:
        # de-dup preserving order
        pairs = list(dict.fromkeys(pairs))
        for feed in spec.feeds:
            if not feed.lobs:
                feed.lobs = pairs
                enrichments.append(
                    f"feed {feed.feed_name!r}: lobs={len(pairs)} Region/LOB pairs (regex; agent returned empty)"
                )
    return spec, enrichments, disagreements


# --------------------------------------------------------------------------- #
# Grounding audit — the string-audit pattern. STRICT fields must appear in the
# source (normalized substring). ADVISORY prose is token-overlap checked.
# Summaries/labels the schema invites the model to normalize are skipped.
# --------------------------------------------------------------------------- #
_ADVISORY_MIN_OVERLAP = 0.75


def _strict_ok(value: str, ncontent: str) -> bool:
    return norm(value) in ncontent


# A document may cite its STTM by more than one name -- "STTM-X.xlsx (Report
# V1.0.xlsx)", "A; B" -- and the model faithfully returns both in ONE string
# that, as a whole, appears nowhere in the source (observed 2026-08-25 on a
# real FRD: each name grounded, the combination failed strict and FAILed the
# run). Split on the alias/list separators and ground each name on its own.
# Commas are NOT separators: real workbook names contain them.
_REFERENCE_SPLIT_RE = re.compile(r"\s*[()\[\];|]\s*")


def split_reference_names(value: str) -> list[str]:
    parts = [p.strip(" ,") for p in _REFERENCE_SPLIT_RE.split(str(value))]
    return [p for p in parts if p]


def _strict_ok_any_split(value: str, ncontent: str) -> bool:
    """Whole value grounded, or every separately-cited name grounded."""
    if _strict_ok(value, ncontent):
        return True
    parts = split_reference_names(value)
    return len(parts) > 1 and all(_strict_ok(p, ncontent) for p in parts)


def _advisory_ok(value: str, content_token_set: set) -> bool:
    toks = _tokens(value)
    if not toks:
        return True
    found = sum(1 for t in toks if t in content_token_set)
    return found / len(toks) >= _ADVISORY_MIN_OVERLAP


def grounding_audit(spec: FrdIngestionSpec, content: str, enrichments: List[str]):
    """Returns dict with strict_failed / advisory_flagged / counts."""
    ncontent = norm(content)
    ctokens = set(_tokens(content))
    strict_failed, advisory_flagged = [], []
    n_strict = n_advisory = 0

    def strict(path: str, value, *, split_names: bool = False):
        nonlocal n_strict
        if not value:
            return
        n_strict += 1
        ok = _strict_ok_any_split if split_names else _strict_ok
        if not ok(str(value), ncontent):
            strict_failed.append(f"{path}: {value!r}")

    def advisory(path: str, value, *, feed_index: int = None, field: str = None):
        """Appends a structured `advisory_grounding` GatedAmbiguity (never
        candidates -- this check flags prose, it never offers a discrete
        pick) when `path`/`field` is feed-scoped (feed_index is not None),
        04_sttm_render.py's apply_human_resolutions() can write a free-text
        resolution straight back into contract['feeds'][feed_index][field]
        without re-parsing `text`; top-level paths (in_scope, acd(name),
        etc.) carry `path` only, unchanged from today -- still out of scope
        for structural write-back."""
        nonlocal n_advisory
        if not value:
            return
        n_advisory += 1
        if not _advisory_ok(str(value), ctokens):
            text = f"{path}: {value!r}"
            context = {"path": path, "original_value": str(value)}
            if feed_index is not None:
                context["feed_index"] = feed_index
                context["field"] = field
            advisory_flagged.append(_make_ambiguity("advisory_grounding", text, [], context))

    if spec.project:
        strict("project.project_id", spec.project.project_id)
        strict("project.project_name", spec.project.project_name)
    for i, f in enumerate(spec.feeds):
        p = f"feeds[{i}]({f.feed_name})"
        for pat in f.file_name_patterns:
            strict(f"{p}.file_name_patterns", pat)
        for seg in f.record_segments:
            strict(f"{p}.record_segments", seg)
        strict(f"{p}.landing_location", f.landing_location)
        for lob in f.lobs:
            strict(f"{p}.lobs", lob)
        for rid in f.requirement_ids:
            strict(f"{p}.requirement_ids", rid)
        strict(f"{p}.sttm_reference", f.sttm_reference, split_names=True)
        for tgt_name, tgt in (("stage_target", f.stage_target), ("standard_target", f.standard_target)):
            if tgt is None:
                continue
            strict(f"{p}.{tgt_name}.catalog", tgt.catalog)
            strict(f"{p}.{tgt_name}.schema", tgt.schema_)
            for t in tgt.tables:
                strict(f"{p}.{tgt_name}.tables", t)
        for rule in f.validation_rules:
            advisory(f"{p}.validation_rules", rule, feed_index=i, field="validation_rules")
        advisory(f"{p}.recycle_rule", f.recycle_rule, feed_index=i, field="recycle_rule")
        for s in f.load_windows_sla:
            advisory(f"{p}.load_windows_sla", s, feed_index=i, field="load_windows_sla")
        advisory(f"{p}.history_backfill", f.history_backfill, feed_index=i, field="history_backfill")
        advisory(f"{p}.archive_retention", f.archive_retention, feed_index=i, field="archive_retention")
        advisory(f"{p}.phi_pii_notes", f.phi_pii_notes, feed_index=i, field="phi_pii_notes")
    for s in spec.in_scope:
        advisory("in_scope", s)
    for s in spec.out_of_scope:
        advisory("out_of_scope", s)
    for s in spec.system_interfaces:
        advisory("system_interfaces", s)
    for s in spec.open_items:
        advisory("open_items", s)
    for a in spec.assumptions_constraints_dependencies:
        advisory(f"acd({a.name})", a.description)

    return {
        "strict_checked": n_strict,
        "strict_failed": strict_failed,
        "advisory_checked": n_advisory,
        "advisory_flagged": advisory_flagged,
    }


# --------------------------------------------------------------------------- #
# Attribution check — identical rules on multiple feeds are an unresolved
# ambiguity (the FRD's "below files" prose). Resolution belongs to the STTM
# source-dictionary cross-check (v2); until then: gate for human review.
# --------------------------------------------------------------------------- #
def attribution_check(spec: FrdIngestionSpec) -> List[dict]:
    if len(spec.feeds) < 2:
        return []
    ambiguities = []
    seen: dict = {}
    for i, f in enumerate(spec.feeds):
        for rule in f.validation_rules + ([f.recycle_rule] if f.recycle_rule else []):
            entry = seen.setdefault(norm(rule), {"rule": rule, "feeds": [], "feed_indices": []})
            entry["feeds"].append(f.feed_name)
            entry["feed_indices"].append(i)
    for entry in seen.values():
        if len(entry["feeds"]) > 1:
            text = (
                f"rule applied to {len(entry['feeds'])} feeds ({', '.join(entry['feeds'])}) — "
                f"attribution unconfirmed pending source dictionary: {entry['rule'][:120]!r}"
            )
            context = {"feed_names": entry["feeds"], "feed_indices": entry["feed_indices"], "rule": entry["rule"]}
            ambiguities.append(_make_ambiguity("attribution", text, entry["feeds"], context))
    # column-conditioned rules on feeds are the highest-signal subset
    return ambiguities


# --------------------------------------------------------------------------- #
# Assemble contract + report
# --------------------------------------------------------------------------- #
def build_contract(doc_id: str, source_file: str, raw_extraction: dict, content: str) -> dict:
    errors = []
    try:
        spec = FrdIngestionSpec.model_validate(raw_extraction)
    except Exception as exc:  # pydantic ValidationError formatted for the report
        return {
            "doc_id": doc_id,
            "status": "FAIL",
            "errors": [f"schema validation: {exc}"],
            "contract": None,
            "report_md": f"# Contract build — {doc_id}\n\n**FAIL** — extraction does not match the contract model:\n\n```\n{exc}\n```\n",
        }

    spec, enrichments, disagreements = enrich(spec, content)
    audit = grounding_audit(spec, content, enrichments)
    ambiguities = attribution_check(spec) + disagreements

    # Fail loud on the pipeline's own output, not just on human input: every
    # ambiguity/advisory-flag this build produced must fit GatedAmbiguity's
    # schema (in particular its three-way `kind` Literal) -- since kind is
    # always declared at construction (see _make_ambiguity() call sites
    # above), this should never fire, but a schema-drift guard here matches
    # the same "never silently guess" posture FrdIngestionSpec.model_validate
    # above already applies to the LLM's output.
    for item in ambiguities + audit["advisory_flagged"]:
        GatedAmbiguity.model_validate(item)

    if audit["strict_failed"]:
        status = "FAIL"
        errors = [f"ungrounded strict field: {s}" for s in audit["strict_failed"]]
    elif ambiguities or audit["advisory_flagged"]:
        status = "PASS_WITH_FLAGS"
    else:
        status = "PASS"

    contract = {
        "contract_name": f"{doc_id} feed-level mapping contract",
        "generated_from_frd": source_file,
        "generated_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "status": status,
        **json.loads(spec.model_dump_json(by_alias=True)),
        "_provenance": {
            "enrichments": enrichments,
            "ambiguities": ambiguities,
            "grounding": audit,
        },
    }

    lines = [f"# Contract build — {doc_id}", "",
             f"**Status: {status}** | feeds: {len(spec.feeds)} | "
             f"strict grounding: {audit['strict_checked'] - len(audit['strict_failed'])}/{audit['strict_checked']} | "
             f"advisory: {audit['advisory_checked'] - len(audit['advisory_flagged'])}/{audit['advisory_checked']}", ""]
    if enrichments:
        lines += ["## Deterministic enrichments"] + [f"- {e}" for e in enrichments] + [""]
    if errors:
        lines += ["## Errors (gate: FAIL)"] + [f"- {e}" for e in errors] + [""]
    if ambiguities:
        lines += ["## Ambiguities for human review"] + [f"- {a['text']}" for a in ambiguities] + [""]
    if audit["advisory_flagged"]:
        lines += ["## Advisory: low grounding overlap"] + [f"- {a['text']}" for a in audit["advisory_flagged"]] + [""]
    report_md = "\n".join(lines)

    return {"doc_id": doc_id, "status": status, "errors": errors,
            "contract": contract, "report_md": report_md}
