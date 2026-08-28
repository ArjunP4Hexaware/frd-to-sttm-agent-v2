"""
frdsttm.pipeline — the whole agent, as three functions over four folders.

    run_extract(paths, run_id, doc_id, ...)   FRD + VDD → run.json
                                              (extract, assess, and — if
                                              nothing is missing — render)
    run_render(paths, run_id)                 answers folded in → .xlsx
    reindex(paths)                            the four folders → corpus_index.json
                                              (+ the frd_pairing rows)

`paths` is the four volumes (or four local folders — the code does not
care). Each run is ONE directory under the output folder:

    output_sttms/<run_id>/run.json      everything the run knows, incl. answers
    output_sttms/<run_id>/<doc_id>.xlsx the STTM, once rendered
    output_sttms/<run_id>/report.md     a human-readable summary

The notebook and the review app both call these; neither contains logic.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from frdsttm import completeness, corpus, extract as ex, render, standards as std
from frdsttm.dictionary import DictionaryError, parse_dictionary_workbook
from frdsttm.frd_parsing import parse_frd
from frdsttm.reference_layout import layout_of

RUN_FILE = "run.json"
REPORT_FILE = "report.md"
STATUS_FAILED = "failed"
STATUS_RENDERED = "rendered"


@dataclass(frozen=True)
class Paths:
    frds: Path
    vdds: Path
    reference: Path
    output: Path

    @classmethod
    def under(cls, root: str | Path, frds="frds", vdds="vdds", reference="reference_sttms",
              output="output_sttms") -> "Paths":
        root = Path(root)
        return cls(root / frds, root / vdds, root / reference, root / output)

    def run_dir(self, run_id: str) -> Path:
        return self.output / run_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id(existing: set[str] | None = None) -> str:
    base = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    rid, n = base, 1
    while existing and rid in existing:
        n += 1
        rid = f"{base}-{n}"
    return rid


def load_run(paths: Paths, run_id: str) -> dict:
    path = paths.run_dir(run_id) / RUN_FILE
    if not path.is_file():
        raise FileNotFoundError(f"no run {run_id!r} under {paths.output}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_run(paths: Paths, run: dict) -> Path:
    d = paths.run_dir(run["run_id"])
    d.mkdir(parents=True, exist_ok=True)
    path = d / RUN_FILE
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    (d / REPORT_FILE).write_text(report_md(run), encoding="utf-8")
    return path


def _find_frd(paths: Paths, doc_id: str) -> Path:
    for p in sorted(paths.frds.iterdir()):
        if p.is_file() and p.stem == doc_id and p.suffix.lower() in corpus.FRD_SUFFIXES:
            return p
    raise FileNotFoundError(f"no FRD named {doc_id!r} in {paths.frds}")


def _find_vdd(paths: Paths, doc_id: str) -> Path | None:
    key = corpus.name_key(doc_id)
    hits = [p for p in sorted(paths.vdds.glob("*.xlsx"))
            if not p.name.startswith("~$") and corpus.name_key(p.name) == key] if paths.vdds.is_dir() else []
    return hits[0] if len(hits) == 1 else None


def _own_reference(paths: Paths, doc_id: str) -> Path | None:
    key = corpus.name_key(doc_id)
    hits = [p for p in sorted(paths.reference.glob("*.xlsx"))
            if not p.name.startswith("~$") and corpus.name_key(p.name) == key] if paths.reference.is_dir() else []
    return hits[0] if len(hits) == 1 else None


def choose_layout(paths: Paths, doc_id: str, n_sources: int) -> dict | None:
    """An approved workbook whose SHAPE fits (one sheet per source file when
    there are several sources, one wide sheet otherwise), preferring another
    feed's workbook; the feed's own approved STTM only as the last structural
    resort. Content is never read from it."""
    want = "sheet_per_table" if n_sources > 1 else "single_sheet"
    own = _own_reference(paths, doc_id)
    candidates = [p for p in sorted(paths.reference.glob("*.xlsx")) if not p.name.startswith("~$")] \
        if paths.reference.is_dir() else []
    others = [p for p in candidates if own is None or p.name != own.name]
    for group in (others, [own] if own else []):
        for p in group:
            try:
                lay = layout_of(str(p))
            except Exception:  # noqa: BLE001 — an unreadable workbook is not a layout
                continue
            if lay.get("dialect") == want and (lay.get("sheets") or lay.get("sheet")):
                return lay
    return None


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #
def run_extract(paths: Paths, run_id: str, doc_id: str, *, provider: str = "databricks",
                model: str = ex.DEFAULT_MODEL, max_tokens: int = ex.DEFAULT_MAX_TOKENS,
                client=None, triggered_by: str | None = None, render_if_ready: bool = True) -> dict:
    run = {"run_id": run_id, "doc_id": doc_id, "created_at": _now(), "triggered_by": triggered_by,
           "provider": provider, "model": model, "status": "extracting", "error": None,
           "standards_sha256": std.standards_sha256(), "naming_version": std.NAMING_VERSION,
           "engineering_version": std.ENGINEERING_VERSION}
    save_run(paths, run)
    try:
        frd_path = _find_frd(paths, doc_id)
        frd = parse_frd(frd_path)
        run["frd"] = {k: frd[k] for k in ("source_file", "content_sha256", "project_id",
                                          "heading_count", "table_count")}
        run["frd"]["chars"] = len(frd["content"])
        vdd_path = _find_vdd(paths, doc_id)
        vdd = None
        run["vdd"] = None
        if vdd_path is not None:
            try:
                vdd = parse_dictionary_workbook(vdd_path)
                run["vdd"] = {"file": vdd_path.name, "n_files": vdd["n_files"], "n_fields": vdd["n_fields"],
                              "files": [f["file_name_pattern"] for f in vdd["files"]],
                              "problems": vdd["problems"]}
            except DictionaryError as exc:
                run["vdd"] = {"file": vdd_path.name, "error": str(exc), "n_files": 0, "n_fields": 0,
                              "files": [], "problems": []}
        if client is None:
            client = ex.build_client(provider)
        model_name = ex.databricks_model_name(model) if provider == "databricks" else model
        spec, meta = ex.extract(client, doc_id, frd["content"], model=model_name, max_tokens=max_tokens)
        run["extraction"] = json.loads(spec.model_dump_json(by_alias=True))
        run["extraction"]["source_file"] = frd["source_file"]
        run["extraction_meta"] = meta
        run["assessment"] = completeness.assess(run["extraction"], frd["content"], vdd, run["vdd"]["file"] if run["vdd"] else None)
        run["status"] = run["assessment"]["status"]
        save_run(paths, run)
        if render_if_ready and run["status"] == completeness.STATUS_READY:
            return run_render(paths, run_id)
        return run
    except Exception as exc:  # noqa: BLE001 — the run records its own failure
        run["status"] = STATUS_FAILED
        run["error"] = f"{type(exc).__name__}: {exc}"
        run["traceback"] = traceback.format_exc()
        save_run(paths, run)
        raise


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def run_render(paths: Paths, run_id: str) -> dict:
    run = load_run(paths, run_id)
    if run.get("assessment", {}).get("blockers"):
        raise ValueError("this run cannot be rendered: " +
                         "; ".join(b["text"] for b in run["assessment"]["blockers"]))
    try:
        vdd_path = paths.vdds / run["vdd"]["file"]
        vdd = parse_dictionary_workbook(vdd_path)
        spec = json.loads(json.dumps(run["extraction"]))
        assessment = json.loads(json.dumps(run["assessment"]))
        applied = completeness.apply_answers(spec, assessment)
        layout = choose_layout(paths, run["doc_id"], len(spec.get("feeds") or []))
        out = paths.run_dir(run_id) / f"{run['doc_id']}.xlsx"
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            info = render.render_workbook(spec, assessment["sources"], vdd, tmp_path, layout=layout,
                                          pairing_override=applied["pairing_override"])
            shutil.copyfile(tmp_path, out)          # sequential write: UC volumes reject seeks
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        run["applied"] = {"extraction": spec, "sources": assessment["sources"],
                          "pairing_override": applied["pairing_override"]}
        run["render"] = {**info, "workbook": out.name, "rendered_at": _now(),
                         "sha256": corpus._sha(out),
                         "unanswered": [q["id"] for q in assessment["questions"] if q.get("answer") is None]}
        run["status"] = STATUS_RENDERED
        run["error"] = None
        save_run(paths, run)
        return run
    except Exception as exc:  # noqa: BLE001
        run["status"] = STATUS_FAILED
        run["error"] = f"{type(exc).__name__}: {exc}"
        run["traceback"] = traceback.format_exc()
        save_run(paths, run)
        raise


def answer(paths: Paths, run_id: str, question_id: str, value: str, by: str | None = None) -> dict:
    run = load_run(paths, run_id)
    completeness.record_answer(run["assessment"], question_id, value, by=by, at=_now())
    if run["status"] in (completeness.STATUS_NEEDS_INPUT, completeness.STATUS_READY):
        run["status"] = run["assessment"]["status"]
    save_run(paths, run)
    return run


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
def reindex(paths: Paths) -> dict:
    index = corpus.build_index(paths.frds, paths.vdds, paths.reference)
    paths.reference.mkdir(parents=True, exist_ok=True)
    corpus.save_index(index, paths.reference)
    return index


def list_runs(paths: Paths) -> list[dict]:
    out = []
    if not paths.output.is_dir():
        return out
    for d in sorted(paths.output.iterdir(), reverse=True):
        f = d / RUN_FILE
        if d.is_dir() and f.is_file():
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append(summary(r))
    return out


def summary(run: dict) -> dict:
    a = run.get("assessment") or {}
    qs = a.get("questions") or []
    return {"run_id": run["run_id"], "doc_id": run["doc_id"], "status": run["status"],
            "created_at": run.get("created_at"), "triggered_by": run.get("triggered_by"),
            "error": run.get("error"), "n_sources": len((run.get("extraction") or {}).get("feeds") or []),
            "n_questions": len(qs), "n_answered": sum(1 for q in qs if q.get("answer")),
            "n_blockers": len(a.get("blockers") or []),
            "workbook": (run.get("render") or {}).get("workbook"),
            "rendered_at": (run.get("render") or {}).get("rendered_at")}


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def report_md(run: dict) -> str:
    lines = [f"# STTM run {run['run_id']} — {run['doc_id']}", "",
             f"**Status: {run['status']}**  ·  created {run.get('created_at')}  ·  "
             f"model {run.get('model')} via {run.get('provider')}", ""]
    if run.get("error"):
        lines += ["## Error", "", f"```\n{run['error']}\n```", ""]
    if run.get("frd"):
        f = run["frd"]
        lines += [f"FRD: `{f['source_file']}` ({f['chars']:,} chars, {f['heading_count']} headings, "
                  f"{f['table_count']} tables)"]
    v = run.get("vdd")
    lines += [f"VDD: `{v['file']}` ({v['n_files']} files, {v['n_fields']} columns)" if v
              else "VDD: **none paired**", ""]
    a = run.get("assessment")
    if a:
        g = a["grounding"]
        lines += [f"Grounding: {g['strict_checked'] - len(g['strict_failed'])}/{g['strict_checked']} "
                  f"identifiers verbatim in the FRD; {len(g['advisory_flagged'])} of {g['advisory_checked']} "
                  f"prose fields loosely matched", ""]
        if a["blockers"]:
            lines += ["## Cannot generate"] + [f"- {b['text']}" for b in a["blockers"]] + [""]
        lines += ["## Sources"]
        for s in a["sources"]:
            st, sd = s["layers"]["stage"], s["layers"]["standard"]
            lines.append(f"- **{s['feed_name']}** ← `{s.get('file') or '?'}` ({s.get('n_columns', 0)} columns) → "
                         f"stage {st.get('catalog') or '?'}.{st.get('schema') or '?'}.{','.join(st['tables']) or '?'} / "
                         f"standard {sd.get('catalog') or '?'}.{sd.get('schema') or '?'}.{','.join(sd['tables']) or '?'}")
        lines.append("")
        if a["questions"]:
            lines += ["## Questions"]
            for q in a["questions"]:
                ans = q.get("answer")
                lines.append(f"- [{'x' if ans else ' '}] ({q['kind']}) {q['text']}"
                             + (f" → **{ans['value']}**" if ans else ""))
            lines.append("")
        if a.get("notes"):
            lines += ["## Notes"] + [f"- {n}" for n in a["notes"]] + [""]
    r = run.get("render")
    if r:
        lines += ["## Workbook", f"`{r['workbook']}` — {r['n_rows']} rows, dialect {r['dialect']}"
                  + (f", layout from `{r['layout_from']}`" if r.get("layout_from") else " (built-in layout)")]
        for src, n in r.get("rows_per_source", {}).items():
            lines.append(f"- {src}: {n} rows from `{r.get('files_per_source', {}).get(src)}`")
        if r.get("unpromoted_types"):
            lines.append(f"- vendor types with no standard-layer promotion rule (kept as stage type): "
                         f"{', '.join(r['unpromoted_types'])}")
        for sheet, cols in (r.get("unfilled_columns") or {}).items():
            lines.append(f"- {sheet}: template columns left empty — {', '.join(cols)}")
        for p in r.get("rule_placement") or []:
            for col, rules in p["by_column"].items():
                for rule in rules:
                    lines.append(f"- {p['source']} › {col}: {rule[:140]!r}")
            if p["recycle"]:
                lines.append(f"- {p['source']} › recycle rule → {p['recycle']['column'] or 'source-level cell'}")
            for rule in p["unattributed"]:
                lines.append(f"- {p['source']} › source-level: {rule[:140]!r}")
        if r.get("unanswered"):
            lines.append(f"- rendered with {len(r['unanswered'])} question(s) unanswered")
        lines.append("")
    return "\n".join(lines)
