"""
frdsttm.completeness — "do we have everything to build the STTM?"

Runs after extraction, over three things: the extracted spec, the FRD text
it came from, and the parsed vendor data dictionary. Produces an
ASSESSMENT:

    status      ready | needs_input | cannot_generate
    blockers    what makes the STTM impossible (no dictionary, no sources…)
    questions   what the agent will not guess — each with options and/or a
                free-text answer, answered by the reviewer in the app
    sources     per source: the VDD file it maps to, target catalog/schema/
                tables per layer, and where each came from (FRD / standards)
    grounding   the audit that keeps the model honest: every identifier the
                model returned must appear verbatim in the FRD

Nothing here calls a model. Every value is either read from a document,
derived from the client's standards, or turned into a question.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from frdsttm import standards as std
from frdsttm.frd_parsing import PROJECT_ID_LINE_RE
from frdsttm.models import FrdIngestionSpec
from frdsttm.reference_layout import loose_tokens
from frdsttm.render import _column_mentions

STATUS_READY = "ready"
STATUS_NEEDS_INPUT = "needs_input"
STATUS_CANNOT = "cannot_generate"

KEEP = "Keep it as extracted"
REMOVE = "Remove it"
ALL_SOURCES = "All of them"
USE_DEFAULT_TYPE = "Use the ACFC default type"
LEAVE_TYPE_BLANK = "Leave the standard type blank"

#: Dictionary problems that leave a PAIRED source with no columns at all. The
#: sheet would render as nothing but the standards' audit rows — a workbook that
#: looks finished and maps nothing. `dictionary.py` records thirteen kinds; the
#: rest do not change what the workbook says, so by the rule above they are
#: neither a blocker nor a question, only a note.
_DICT_BLOCKING = {
    "field_sheet_missing": "names a field sheet that is not in the workbook",
    "field_sheet_empty": "names no columns",
    "field_sheet_unreadable": "could not be read",
}

# --------------------------------------------------------------------------- #
# text normalisation (unicode Word emits + markdown chrome)
# --------------------------------------------------------------------------- #
_UNICODE_MAP = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", " ": " ",
})


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", str(text)).translate(_UNICODE_MAP)
    t = re.sub(r"[*_#|`]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9_]+", norm(text)) if len(w) >= 4]


def question_id(kind: str, context: dict) -> str:
    key = json.dumps({"kind": kind, "context": context}, sort_keys=True)
    return f"{kind}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _question(kind, text, context, options=(), free_text=False, source=None):
    return {"id": question_id(kind, context), "kind": kind, "source": source,
            "text": text, "options": list(options), "free_text": free_text,
            "context": context, "answer": None}


# --------------------------------------------------------------------------- #
# grounding — strict fields verbatim, advisory prose by token overlap
# --------------------------------------------------------------------------- #
_ADVISORY_MIN_OVERLAP = 0.75
_REFERENCE_SPLIT_RE = re.compile(r"\s*[()\[\];|]\s*")

_STRICT_SCALARS = ("landing_location", "sttm_reference")
_STRICT_LISTS = ("file_name_patterns", "record_segments", "lobs", "requirement_ids")
_ADVISORY_SCALARS = ("recycle_rule", "history_backfill", "archive_retention", "phi_pii_notes")
_ADVISORY_LISTS = ("validation_rules", "load_windows_sla")

# What render.py actually writes into the workbook. A grounding miss on any
# other field cannot change a single cell, so it is a NOTE for the reviewer,
# never a question that gates the run (Arjun, 2026-08-28: only surface
# questions that affect how the STTM is written).
_ON_WORKBOOK = frozenset({
    "file_name_patterns", "lobs", "landing_location", "recycle_rule", "validation_rules",
    "frequency", "source_system", "domain", "sub_domain", "delimiter", "feed_name",
    "catalog", "schema", "tables",
})


def _strict_ok(value: str, ncontent: str) -> bool:
    if norm(value) in ncontent:
        return True
    parts = [p.strip(" ,") for p in _REFERENCE_SPLIT_RE.split(str(value))]
    parts = [p for p in parts if p]
    return len(parts) > 1 and all(norm(p) in ncontent for p in parts)


def _advisory_ok(value: str, ctokens: set) -> bool:
    toks = _tokens(value)
    if not toks:
        return True
    return sum(1 for t in toks if t in ctokens) / len(toks) >= _ADVISORY_MIN_OVERLAP


def grounding_audit(spec: dict, content: str) -> tuple[dict, list[dict]]:
    """Returns (summary, questions). A strict miss is a question, never a
    silent keep and never a silent drop."""
    ncontent = norm(content)
    ctokens = set(_tokens(content))
    strict_failed, advisory_flagged, questions, off_workbook = [], [], [], []
    n_strict = n_advisory = 0

    def strict(path, value, ctx, source):
        nonlocal n_strict
        if not value:
            return
        n_strict += 1
        if not _strict_ok(str(value), ncontent):
            strict_failed.append(f"{path}: {value!r}")
            if ctx["field"] not in _ON_WORKBOOK:
                off_workbook.append(f"{path}: {value!r} is not verbatim in the FRD — kept; it is not written to the workbook")
                return
            questions.append(_question(
                "unverified",
                f"The extracted {ctx['field'].replace('_', ' ')} {value!r} could not be found "
                f"verbatim in the FRD. Keep it, remove it, or type the correct value.",
                {**ctx, "value": str(value)}, options=(KEEP, REMOVE), free_text=True, source=source))

    def advisory(path, value, ctx, source):
        nonlocal n_advisory
        if not value:
            return
        n_advisory += 1
        if not _advisory_ok(str(value), ctokens):
            advisory_flagged.append(f"{path}: {value!r}")
            if ctx["field"] not in _ON_WORKBOOK:
                off_workbook.append(f"{path}: {value!r} only loosely matches the FRD — kept; it is not written to the workbook")
                return
            questions.append(_question(
                "weak_match",
                f"This {ctx['field'].replace('_', ' ')} only loosely matches the FRD's wording: "
                f"{value!r}. Keep it, remove it, or restate it.",
                {**ctx, "value": str(value)}, options=(KEEP, REMOVE), free_text=True, source=source))

    proj = spec.get("project") or {}
    for f in ("project_id", "project_name"):
        strict(f"project.{f}", proj.get(f), {"path": "project", "field": f}, None)
    for i, feed in enumerate(spec.get("feeds", [])):
        name = feed.get("feed_name") or f"source {i + 1}"
        p = f"feeds[{i}]"
        for f in _STRICT_LISTS:
            for v in feed.get(f) or []:
                strict(f"{p}.{f}", v, {"path": p, "field": f, "feed_index": i}, name)
        for f in _STRICT_SCALARS:
            strict(f"{p}.{f}", feed.get(f), {"path": p, "field": f, "feed_index": i}, name)
        for tkey in ("stage_target", "standard_target"):
            tgt = feed.get(tkey) or {}
            for f in ("catalog", "schema"):
                strict(f"{p}.{tkey}.{f}", tgt.get(f),
                       {"path": f"{p}.{tkey}", "field": f, "feed_index": i, "target": tkey}, name)
            for t in tgt.get("tables") or []:
                strict(f"{p}.{tkey}.tables", t,
                       {"path": f"{p}.{tkey}", "field": "tables", "feed_index": i, "target": tkey}, name)
        for f in _ADVISORY_LISTS:
            for v in feed.get(f) or []:
                advisory(f"{p}.{f}", v, {"path": p, "field": f, "feed_index": i}, name)
        for f in _ADVISORY_SCALARS:
            advisory(f"{p}.{f}", feed.get(f), {"path": p, "field": f, "feed_index": i}, name)

    summary = {"strict_checked": n_strict, "strict_failed": strict_failed,
               "advisory_checked": n_advisory, "advisory_flagged": advisory_flagged,
               "off_workbook": off_workbook}
    return summary, questions


# --------------------------------------------------------------------------- #
# deterministic enrichment + attribution
# --------------------------------------------------------------------------- #
_REGION_LOB_RE = re.compile(r"(REG#\d+)\s+(\d{4})")


def enrich(spec: dict, content: str) -> tuple[list[str], list[dict]]:
    """Facts a regex owns. Returns (notes, questions). Mutates spec."""
    notes, questions = [], []
    m = PROJECT_ID_LINE_RE.search(norm(content))
    if m:
        pid = m.group(1)
        proj = spec.setdefault("project", None) or {}
        spec["project"] = proj
        if not proj.get("project_id"):
            proj["project_id"] = pid
            notes.append(f"project_id {pid} taken from the FRD's 'Project ID' line (the model returned none)")
        elif proj["project_id"] != pid:
            # not on the workbook, and the FRD's own line is read by code: it wins
            notes.append(f"project_id: the model read {proj['project_id']!r}, the FRD's 'Project ID' "
                         f"line says {pid!r} — the FRD's line is used")
            proj["project_id"] = pid
    pairs = list(dict.fromkeys(f"{r} {c}" for r, c in _REGION_LOB_RE.findall(content)))
    if pairs:
        for feed in spec.get("feeds", []):
            if not feed.get("lobs"):
                feed["lobs"] = pairs
                notes.append(f"{feed.get('feed_name')}: {len(pairs)} Region/LOB pairs taken from the FRD's table")
    return notes, questions


def _rule_names_source(rule: str, feed: dict) -> bool:
    """Does the rule's own text name this source — one of its file patterns
    (the FRD's 'from the below files: a.csv; b.csv') or its feed name?"""
    text = norm(rule)
    names = list(feed.get("file_name_patterns") or []) + [feed.get("feed_name") or ""]
    return any(n and norm(n) in text for n in names)


def _drop_rule(feed: dict, rule: str) -> None:
    key = norm(rule)
    feed["validation_rules"] = [r for r in feed.get("validation_rules") or [] if norm(r) != key]
    if feed.get("recycle_rule") and norm(feed["recycle_rule"]) == key:
        feed["recycle_rule"] = None


def attribution_questions(spec: dict, columns_by_feed: dict | None = None) -> tuple[list[dict], list[str]]:
    """The same rule on several sources: the FRD's 'the below files' prose.
    Returns (questions, notes); mutates the spec where the documents settle it.

    A rule that names every source it sits on is settled by the FRD. A rule
    that names no column can only land in each source's description — it
    stays on all of them. A rule naming a column that exactly one source has
    belongs to that source, by the dictionary. Only a rule naming a column
    that several sources SHARE, with no file named, is a real question — and
    that one the agent will not answer."""
    feeds = spec.get("feeds", [])
    columns_by_feed = columns_by_feed or {}
    if len(feeds) < 2:
        return [], []
    seen: dict = {}
    for i, f in enumerate(feeds):
        for rule in (f.get("validation_rules") or []) + ([f["recycle_rule"]] if f.get("recycle_rule") else []):
            e = seen.setdefault(norm(rule), {"rule": rule, "names": [], "idx": []})
            e["names"].append(f.get("feed_name") or f"source {i + 1}")
            e["idx"].append(i)
    questions, notes = [], []
    for e in seen.values():
        if len(e["idx"]) < 2:
            continue
        rule, idx, short = e["rule"], e["idx"], e["rule"][:80]
        if all(_rule_names_source(rule, feeds[i]) for i in idx):
            notes.append(f"rule kept on all {len(idx)} sources — the FRD names each file: {short!r}")
            continue
        with_col = [i for i in idx if _column_mentions(rule, columns_by_feed.get(i) or [])]
        if not with_col:
            notes.append(f"rule names no column; kept on all {len(idx)} sources as source-level text: {short!r}")
            continue
        if len(with_col) == 1:
            owner = with_col[0]
            for i in idx:
                if i != owner:
                    _drop_rule(feeds[i], rule)
            notes.append(f"rule attributed to {feeds[owner].get('feed_name')!r} — the only source whose "
                         f"dictionary has the column it names: {short!r}")
            continue
        names = [feeds[i].get("feed_name") or f"source {i + 1}" for i in with_col]
        questions.append(_question(
            "attribution",
            f"This rule names a column that {len(names)} sources share ({', '.join(names)}) and the FRD "
            f"does not say which file it applies to. Which does it? — {rule[:200]!r}",
            {"rule": rule, "feed_indices": with_col, "names": names},
            options=tuple(names) + (ALL_SOURCES,)))
    return questions, notes


# --------------------------------------------------------------------------- #
# VDD file ↔ source pairing (global best-first, ties refuse)
# --------------------------------------------------------------------------- #
def _file_tokens(f):
    return loose_tokens(f.get("file_name_pattern") or "") | loose_tokens(f.get("field_sheet") or "")


def _score(want, have, weights):
    return sum(weights.get(w, 1.0) for w in want
               if any(w == h or w.startswith(h) or h.startswith(w) for h in have))


def pair_files(spec: dict, vdd: dict | None) -> tuple[dict, list[dict]]:
    """{feed_index: file record} plus a question per source it could not decide."""
    files = [f for f in (vdd or {}).get("files", []) if f.get("field_sheet")]
    feeds = spec.get("feeds", [])
    if not files or not feeds:
        return {}, []
    if len(files) == 1 and len(feeds) == 1:
        return {0: files[0]}, []
    have = [_file_tokens(f) for f in files]
    weights: dict = {}
    for toks in have:
        for t in toks:
            weights[t] = weights.get(t, 0) + 1
    weights = {k: 1.0 / v for k, v in weights.items()}
    cand = []
    for i, feed in enumerate(feeds):
        want = set()
        for pat in feed.get("file_name_patterns") or []:
            want |= loose_tokens(pat)
        want |= loose_tokens(feed.get("feed_name") or "")
        for j in range(len(files)):
            s = _score(want, have[j], weights)
            if s > 0:
                cand.append((s, i, j))
    cand.sort(key=lambda x: (-x[0], x[1], x[2]))
    out, used_i, used_j = {}, set(), set()
    for k, (s, i, j) in enumerate(cand):
        if i in used_i or j in used_j:
            continue
        rivals = [c for c in cand[k + 1:] if c[0] == s and c[1] not in used_i
                  and c[2] not in used_j and (c[1] == i) != (c[2] == j)]
        if rivals:
            continue
        out[i] = files[j]
        used_i.add(i)
        used_j.add(j)
    questions = []
    for i, feed in enumerate(feeds):
        if i in out:
            continue
        name = feed.get("feed_name") or f"source {i + 1}"
        remaining = [f["file_name_pattern"] for j, f in enumerate(files) if j not in used_j] or \
                    [f["file_name_pattern"] for f in files]
        questions.append(_question(
            "file_pairing",
            f"Which file in the vendor dictionary describes source {name!r} "
            f"({', '.join(feed.get('file_name_patterns') or ['no file pattern stated'])})?",
            {"feed_index": i, "candidates": remaining}, options=tuple(remaining), source=name))
    return out, questions


# --------------------------------------------------------------------------- #
# target side — the FRD wins, the standards fill, anything else is a question
# --------------------------------------------------------------------------- #
def derive_targets(spec: dict) -> tuple[list[dict], list[dict]]:
    """Per source, per layer: catalog / schema / tables with their origin."""
    sources, questions = [], []
    for i, feed in enumerate(spec.get("feeds", [])):
        name = feed.get("feed_name") or f"source {i + 1}"
        entry = {"feed_index": i, "feed_name": name, "layers": {}}
        for layer, key in (("stage", "stage_target"), ("standard", "standard_target")):
            tgt = feed.get(key) or {}
            lay = {"tables": list(tgt.get("tables") or []), "origin": {}}
            for attr in ("catalog", "schema"):
                if tgt.get(attr):
                    lay[attr] = tgt[attr]
                    lay["origin"][attr] = "frd"
                    continue
                value = std.catalog_for(layer) if attr == "catalog" else std.schema_for(layer, feed.get("domain"))
                if value:
                    lay[attr] = value
                    lay["origin"][attr] = f"standards v{std.NAMING_VERSION}"
                else:
                    lay[attr] = None
                    lay["origin"][attr] = "unknown"
                    if attr == "schema":
                        questions.append(_question(
                            "target_gap",
                            f"The {layer} schema for source {name!r} is stated neither in the FRD "
                            f"nor derivable from the naming standards (domain: {feed.get('domain')!r}). "
                            f"Pick the domain, or type the schema name.",
                            {"feed_index": i, "layer": layer, "attribute": attr},
                            options=std.known_terms("domains"), free_text=True, source=name))
                    else:
                        questions.append(_question(
                            "target_gap",
                            f"The {layer} catalog for source {name!r} is stated neither in the FRD "
                            f"nor in the naming standards. Type it, or leave it blank.",
                            {"feed_index": i, "layer": layer, "attribute": attr},
                            free_text=True, source=name))
            if not lay["tables"]:
                questions.append(_question(
                    "target_gap",
                    f"The FRD names no {layer} table for source {name!r}. Type the table name.",
                    {"feed_index": i, "layer": layer, "attribute": "tables"},
                    free_text=True, source=name))
            ls = tgt.get("load_strategy")
            lay["load_strategy"] = ls
            lay["load_strategy_code"] = std.normalize_load_strategy(ls)
            entry["layers"][layer] = lay
        sources.append(entry)
    return sources, questions


# --------------------------------------------------------------------------- #
# the assessment
# --------------------------------------------------------------------------- #
def assess(spec: dict, content: str, vdd: dict | None, vdd_name: str | None) -> dict:
    """Validate the spec (pydantic), ground it, pair it with the dictionary,
    derive the target side, and say whether the STTM can be built."""
    # drift guard; raises loudly. `source_file` is the run's annotation, not the model's.
    FrdIngestionSpec.model_validate({k: v for k, v in spec.items() if k != "source_file"})
    notes, questions = enrich(spec, content)
    grounding, gq = grounding_audit(spec, content)
    questions += gq
    notes += grounding.get("off_workbook", [])

    blockers = []
    if not spec.get("feeds"):
        blockers.append({"kind": "no_sources", "text": "The FRD names no source files to ingest — nothing to map."})
    if vdd is None:
        blockers.append({"kind": "no_dictionary",
                         "text": "No vendor data dictionary is paired with this FRD. Add VDD_<same name>.xlsx "
                                 "to the vdds volume — the source columns come only from it."})
    elif vdd.get("n_fields", 0) == 0:
        blockers.append({"kind": "empty_dictionary",
                         "text": f"{vdd_name} names no columns on any field sheet — nothing to map."})

    pairing, pq = pair_files(spec, vdd)
    questions += pq
    columns_by_feed = {i: [f["name"] for f in (vdd or {}).get("fields", {}).get(p.get("field_sheet") or "", [])]
                       for i, p in pairing.items()}
    aq, an = attribution_questions(spec, columns_by_feed)
    questions += aq
    notes += an
    sources, tq = derive_targets(spec)
    questions += tq
    for s in sources:
        frec = pairing.get(s["feed_index"])
        s["file"] = frec.get("file_name_pattern") if frec else None
        s["field_sheet"] = frec.get("field_sheet") if frec else None
        s["n_columns"] = len((vdd or {}).get("fields", {}).get(s["field_sheet"] or "", [])) if frec else 0
    if spec.get("feeds") and vdd is not None and not pairing and not pq:
        blockers.append({"kind": "no_file_pairing",
                         "text": "None of the dictionary's files could be matched to the FRD's sources."})

    if vdd is not None:
        db, dq, dn = dictionary_severity(vdd, sources)
        blockers += db
        questions += dq
        notes += dn

    # de-duplicate by id, keep first
    seen, uniq = set(), []
    for q in questions:
        if q["id"] not in seen:
            seen.add(q["id"])
            uniq.append(q)
    return {
        "status": _status(blockers, uniq),
        "blockers": blockers,
        "questions": uniq,
        "sources": sources,
        "grounding": grounding,
        "notes": notes,
    }


def dictionary_severity(vdd: dict, sources: list[dict]) -> tuple[list, list, list]:
    """Split the dictionary's problems into blockers, questions and notes.

    A problem only matters where it lands on a source the workbook will carry:
    the same empty sheet is fatal when a source is paired to it and merely worth
    saying when nothing uses it. Three kinds leave a paired source with no
    columns (blocker); a missing data type changes what the workbook says
    (question); everything else is a note, because no answer to it would write a
    different workbook.
    """
    blockers, questions, notes = [], [], []
    for p in vdd.get("problems", []):
        detail = p.get("detail")
        kind = p.get("kind")
        owners = [s for s in sources
                  if (p.get("sheet") and p["sheet"] == s.get("field_sheet"))
                  or (p.get("file") and p["file"] == s.get("file"))]
        if kind in _DICT_BLOCKING and owners:
            for s in owners:
                blockers.append({
                    "kind": kind,
                    "text": f"{s['feed_name']} is mapped to dictionary file "
                            f"{s.get('file') or 'an unnamed file'}, which {_DICT_BLOCKING[kind]}. "
                            f"That source has no columns to map, so the workbook would carry "
                            f"only the standards' audit rows for it.",
                })
            continue
        if kind == "missing_datatypes" and owners:
            cols = p.get("columns") or []
            for s in owners:
                first = ", ".join(cols[:5]) + ("…" if len(cols) > 5 else "")
                questions.append(_question(
                    "dictionary_types",
                    f"The vendor left the data type blank for {len(cols)} column(s) on "
                    f"{s['feed_name']} ({first}). Nothing in the dictionary or the standards "
                    f"says what they are. The standard-layer type falls back to the ACFC "
                    f"default ({std.stage_default_type()}), which reads exactly like a real "
                    f"type. What should the workbook say?",
                    {"sheet": s.get("field_sheet"), "file": s.get("file"), "columns": cols},
                    options=(USE_DEFAULT_TYPE, LEAVE_TYPE_BLANK),
                    source=s["feed_name"],
                ))
            continue
        notes.append(f"dictionary: {detail}")
    return blockers, questions, notes


def _status(blockers, questions) -> str:
    if blockers:
        return STATUS_CANNOT
    if any(q.get("answer") is None for q in questions):
        return STATUS_NEEDS_INPUT
    return STATUS_READY


# --------------------------------------------------------------------------- #
# answers
# --------------------------------------------------------------------------- #
def record_answer(assessment: dict, qid: str, value: str, by: str | None = None, at: str | None = None) -> dict:
    for q in assessment["questions"]:
        if q["id"] == qid:
            q["answer"] = {"value": value, "by": by, "at": at}
            assessment["status"] = _status(assessment["blockers"], assessment["questions"])
            return q
    raise KeyError(qid)


def apply_answers(spec: dict, assessment: dict) -> dict:
    """Fold the reviewer's answers into the spec and the derived targets.
    Returns {"pairing_override": {feed_index: file pattern},
             "blank_type_sheets": [sheet, ...]}. Mutates both."""
    overrides, blank_types = {}, set()
    feeds = spec.get("feeds", [])
    for q in assessment["questions"]:
        ans = (q.get("answer") or {}).get("value")
        if ans is None:
            continue
        ctx, kind = q["context"], q["kind"]
        if kind in ("unverified", "weak_match"):
            i, field = ctx.get("feed_index"), ctx["field"]
            holder = feeds[i] if i is not None else spec.setdefault("project", {})
            if ctx.get("target"):
                holder = holder.setdefault(ctx["target"], {})
            if ans == KEEP:
                continue
            if isinstance(holder.get(field), list):
                vals = holder.get(field) or []
                holder[field] = [v for v in vals if v != ctx["value"]] if ans == REMOVE else \
                    [ans if v == ctx["value"] else v for v in vals]
            else:
                holder[field] = None if ans == REMOVE else ans
        elif kind == "project_id":
            spec.setdefault("project", {})["project_id"] = ans
        elif kind == "attribution":
            if ans == ALL_SOURCES:
                continue
            rule = norm(ctx["rule"])
            for i in ctx["feed_indices"]:
                f = feeds[i]
                if (f.get("feed_name") or f"source {i + 1}") == ans:
                    continue
                f["validation_rules"] = [r for r in f.get("validation_rules") or [] if norm(r) != rule]
                if f.get("recycle_rule") and norm(f["recycle_rule"]) == rule:
                    f["recycle_rule"] = None
        elif kind == "dictionary_types":
            # Only the standard layer is affected: the stage layer is the
            # standards' default type for every column by design.
            if ans == LEAVE_TYPE_BLANK and ctx.get("sheet"):
                blank_types.add(ctx["sheet"])
        elif kind == "file_pairing":
            overrides[ctx["feed_index"]] = ans
        elif kind == "target_gap":
            src = next(s for s in assessment["sources"] if s["feed_index"] == ctx["feed_index"])
            lay = src["layers"][ctx["layer"]]
            if ctx["attribute"] == "tables":
                lay["tables"] = [t.strip() for t in ans.split(",") if t.strip()]
                lay["origin"]["tables"] = "reviewer"
            elif ctx["attribute"] == "schema" and ans in std.known_terms("domains"):
                lay["schema"] = std.schema_for(ctx["layer"], ans)
                lay["origin"]["schema"] = f"reviewer (domain {ans})"
            else:
                lay[ctx["attribute"]] = ans or None
                lay["origin"][ctx["attribute"]] = "reviewer"
    return {"pairing_override": overrides, "blank_type_sheets": sorted(blank_types)}
