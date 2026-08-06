# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — FRD Ingestion: docx → markdown → `frd_documents`
# MAGIC
# MAGIC Phase 2 of the FRD→STTM pipeline. Reads FRD files from the `frd_raw`
# MAGIC Unity Catalog volume, converts each to markdown (headings, tables,
# MAGIC bullets preserved in document order), and lands one row per document
# MAGIC in the Delta table **`<catalog>.<schema>.frd_documents`** — the table
# MAGIC the Agent Bricks Information Extraction agent reads (`content` column).
# MAGIC
# MAGIC Ported from `brd_to_frd_agent/src/ingestion/normalize.py` with three
# MAGIC fidelity fixes found by testing against the real IS-Methodology FRDs:
# MAGIC 1. **Custom heading styles** (`Document Heading N`) now map to `#` levels —
# MAGIC    the original only matched Word's built-in `Heading N`, yielding 0 headings.
# MAGIC 2. **`w:outlineLvl` fallback** for styles that don't name their level.
# MAGIC 3. **TOC noise dropped** (`toc N`, `TOC Header`) — but body paragraphs styled
# MAGIC    'Header' are KEPT (the template puts 'Project ID: NNNNNNN' in one).
# MAGIC 4. **Nested tables inside cells flattened inline** (cell.text drops them;
# MAGIC    the template's Region/LOB lists live there).
# MAGIC
# MAGIC Run on serverless compute. Re-running is safe: full refresh from the volume.

# COMMAND ----------

# MAGIC %pip install python-docx pypdf
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

from __future__ import annotations

# Local-mode support: when this file runs as a plain `python 01_frd_ingest.py`
# script (no Databricks runtime), `dbutils`/`spark` are never injected into
# globals(), so IS_DATABRICKS is False and every widget/Spark call below
# falls back to an env-var / local-filesystem equivalent. Inside an actual
# Databricks notebook, `dbutils` always exists, so this branch is a no-op --
# the Databricks execution path below is byte-for-byte what it was before.
import os
from pathlib import Path

IS_DATABRICKS = "dbutils" in globals()
LOCAL_ROOT = Path(__file__).resolve().parent.parent / "local_dev_fixtures"


def _param(name: str, default: str) -> str:
    """Widget value in Databricks; same-named (uppercased) env var locally."""
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)
        return dbutils.widgets.get(name)
    return os.environ.get(name.upper(), default)


CATALOG = _param("catalog", "soham_workspace")
SCHEMA = _param("schema", "sttm_agent")
RAW_VOLUME = _param("raw_volume", "frd_raw")
TABLE_NAME = _param("table", "frd_documents")
PREVIEW_VOL = _param("preview_volume", "sttm_out")  # optional .md copies for eyeballing; "" to skip

if IS_DATABRICKS:
    RAW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{RAW_VOLUME}"
    TABLE = f"{CATALOG}.{SCHEMA}.{TABLE_NAME}"
    PREVIEW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{PREVIEW_VOL}/parsed_frd" if PREVIEW_VOL else None
else:
    RAW_DIR = str(LOCAL_ROOT / RAW_VOLUME)
    TABLE = TABLE_NAME  # local tables are scoped by catalog/schema on disk instead; see _local_tables
    PREVIEW_DIR = str(LOCAL_ROOT / PREVIEW_VOL / "parsed_frd") if PREVIEW_VOL else None

print(f"raw:   {RAW_DIR}\ntable: {TABLE}\npreview: {PREVIEW_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Normalizer
# MAGIC Format conversion only (headings/tables/lists/paragraphs), no semantic
# MAGIC restructuring. docx fidelity notes:
# MAGIC - Tables → pipe tables; a merged cell's text is emitted once per span.
# MAGIC - Content controls (`<w:sdt>`) are recursed into — form-style templates
# MAGIC   put fillable requirement fields there; skipping them silently drops text.
# MAGIC - Requirement-id lines (BR/REQ/FR/SRQ/SIR/NFR/MDST + number) are reshaped
# MAGIC   to bold markers so they anchor cleanly for downstream extraction.

# COMMAND ----------

import re
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}

# Requirement-id families seen across the BRD (BR/REQ/FR) and the
# IS-Methodology FRD templates (SRQ/SIR/NFR/MDST). Reshaped to bold markers.
_REQUIREMENT_ID_RE = re.compile(
    r"^\s*((?:BR|REQ|FR|SRQ|SIR|NFR|MDST)[-\s]?\d+)\b[\s:.—–-]*(.*)$", re.I
)

# Styles that are navigation chrome, not document content. Only TOC styles:
# real page headers/footers live in separate document parts that the body
# walk never sees, and the IS-Methodology template styles a *body* paragraph
# ('Project ID: 1005034 ...') as 'Header' — skipping it drops the project id.
_SKIP_STYLE_RE = re.compile(r"^(toc\b|toc header$)", re.I)

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6,
}


class UnsupportedFormatError(ValueError):
    pass


def _read_text_forgiving(path: Path) -> str:
    """Read text without exploding on real-world encodings (UTF-8 BOM,
    cp1252, UTF-16). Never raises; normalizes newlines to '\\n'."""
    data = path.read_bytes()
    text = None
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if text is None:
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _heading_level(style_name: str) -> int | None:
    """Markdown '#' count for a paragraph style, else None.
    'Title' -> 1; any '... heading N ...' -> N+1 (built-in 'Heading 2' and
    template styles like 'Document Heading 2' alike); ordinal-word styles
    ('3rd Level Heading') -> level+1. Title stays the sole level-1."""
    name = style_name.strip().lower()
    if name == "title":
        return 1
    m = re.search(r"heading\s*(\d+)\b", name)
    if m:
        return min(int(m.group(1)) + 1, 6)
    if "heading" in name:
        for word, level in _ORDINAL_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", name):
                return min(level + 1, 6)
    return None


def _outline_level(par) -> int | None:
    """Fallback when the style name doesn't reveal a level: Word's
    w:outlineLvl, checked on the paragraph then its style. lvl 0 -> '##'
    (consistent with Heading 1 -> '##'; Title remains the sole '#')."""
    from docx.oxml.ns import qn

    sources = [par._p.pPr]
    style_el = getattr(par.style, "_element", None)
    if style_el is not None:
        sources.append(style_el.find(qn("w:pPr")))
    for src in sources:
        if src is None:
            continue
        o = src.find(qn("w:outlineLvl"))
        if o is not None:
            return min(int(o.get(qn("w:val"))) + 2, 6)
    return None


def _is_bullet_style(style_name: str) -> bool:
    n = style_name.strip().lower()
    return "list" in n or "bullet" in n


def _iter_block_items(document):
    """Yield ('paragraph', Paragraph)/('table', Table) in true document
    order, recursing into content controls (<w:sdt>) whose sdtContent
    hides paragraphs/tables from the flat body lists."""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def walk(parent):
        for child in parent.iterchildren():
            if child.tag == qn("w:p"):
                yield "paragraph", Paragraph(child, document)
            elif child.tag == qn("w:tbl"):
                yield "table", Table(child, document)
            elif child.tag == qn("w:sdt"):
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    yield from walk(content)

    yield from walk(document.element.body)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _cell_content(cell, depth: int = 0) -> str:
    """Cell text plus any nested tables' content. python-docx's cell.text
    covers only the cell's own paragraphs — a table nested *inside* the cell
    (the IS-Methodology template does this, e.g. its Region/LOB code list)
    is invisible to it and was silently dropped. Flatten nested tables
    inline: cells joined with ' ', rows with '; '. Depth-capped."""
    parts = [cell.text]
    if depth < 2:
        for nt in cell.tables:
            rows = []
            for row in nt.rows:
                vals = [c.text.strip() for c in row.cells if c.text.strip()]
                if vals:
                    rows.append(" ".join(vals))
            if rows:
                parts.append("; ".join(rows))
    return _md_cell(" ".join(p for p in parts if p))


def _table_to_markdown(table) -> list[str]:
    """Pipe-table conversion. A merged cell surfaces once per grid position
    with the same underlying <w:tc>; emit its text only on first appearance.
    Identity via `is` against a kept-alive list (id() is unsafe on lxml's
    transient wrappers)."""
    rows = []
    seen_tcs: list = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            tc = getattr(cell, "_tc", cell)
            if any(tc is s for s in seen_tcs):
                cells.append("")
            else:
                seen_tcs.append(tc)
                cells.append(_cell_content(cell))
        rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, *body = rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return lines


def _requirement_marker(text: str) -> str | None:
    m = _REQUIREMENT_ID_RE.match(text)
    if not m:
        return None
    raw_id, rest = m.group(1), m.group(2).strip()
    norm_id = re.sub(r"[-\s]+", "-", raw_id.strip()).upper()
    return f"**{norm_id} — {rest}**" if rest else f"**{norm_id}**"


def docx_to_markdown(filepath: str) -> str:
    import docx

    document = docx.Document(filepath)
    out: list[str] = []

    for kind, block in _iter_block_items(document):
        if kind == "table":
            table_md = _table_to_markdown(block)
            if table_md:
                out.append("")
                out.extend(table_md)
                out.append("")
            continue

        text = block.text.strip()
        if not text:
            continue

        style = block.style.name if block.style else "Normal"
        if _SKIP_STYLE_RE.match(style.strip()):
            continue

        level = _heading_level(style)
        if level is None:
            level = _outline_level(block)
        if level is not None:
            out.append("")
            out.append("#" * level + " " + text)
            out.append("")
        elif _is_bullet_style(style):
            out.append(f"- {text}")
        else:
            marker = _requirement_marker(text)
            out.append(marker if marker else text)
            out.append("")

    return _collapse_blank_lines("\n".join(out)).strip() + "\n"


def pdf_to_markdown(filepath: str) -> str:
    """Best-effort: prose survives, tables flatten (pypdf has no layout
    model). Fine for prose FRDs; use docx sources whenever available."""
    from pypdf import PdfReader

    reader = PdfReader(filepath)
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip():
                lines.append("")
                continue
            marker = _requirement_marker(line)
            lines.append(marker if marker else line)
    return _collapse_blank_lines("\n".join(lines)).strip() + "\n"


def normalize_to_markdown(filepath: str) -> str:
    path = Path(filepath)
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return _read_text_forgiving(path)
    if suffix == ".docx":
        return docx_to_markdown(filepath)
    if suffix == ".pdf":
        return pdf_to_markdown(filepath)
    raise UnsupportedFormatError(
        f"Unsupported file type '{suffix}'. Supported: "
        f"{', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


_PROJECT_ID_RE = re.compile(r"(\d{6,8})")


def infer_project_id(filename: str) -> str | None:
    """Project id from the filename when present (e.g. ..._1005034.docx)."""
    m = _PROJECT_ID_RE.search(filename)
    return m.group(1) if m else None

# COMMAND ----------

# MAGIC %md
# MAGIC ## Convert every file in `frd_raw`
# MAGIC Volumes are FUSE-mounted, so plain-Python file I/O works. Files with
# MAGIC unsupported extensions are reported and skipped, never fatal.

# COMMAND ----------

raw = Path(RAW_DIR)
assert raw.is_dir(), f"Volume path not found: {RAW_DIR} — check catalog/schema/volume widgets."

rows, skipped = [], []
for p in sorted(raw.iterdir()):
    if not p.is_file():
        continue
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        skipped.append(p.name)
        continue
    md = normalize_to_markdown(str(p))
    rows.append({
        "doc_id": p.stem,
        "project_id": infer_project_id(p.name),
        "source_file": p.name,
        "file_type": p.suffix.lower().lstrip("."),
        "char_count": len(md),
        "heading_count": sum(1 for l in md.splitlines() if l.startswith("#")),
        "table_count": sum(1 for l in md.splitlines() if l.startswith("| ---")),
        "content": md,
        "parsed_at": datetime.now(timezone.utc),
    })
    print(f"parsed {p.name}: {len(md):,} chars, "
          f"{rows[-1]['heading_count']} headings, {rows[-1]['table_count']} tables")

if skipped:
    print(f"skipped (unsupported type): {skipped}")
assert rows, f"No supported files found in {RAW_DIR} — upload the FRDs first."

# COMMAND ----------

# MAGIC %md
# MAGIC ## Land the Delta table
# MAGIC Full refresh (overwrite): the volume is the source of truth, so a rerun
# MAGIC after adding/replacing files rebuilds the table idempotently. A sanity
# MAGIC gate fails the run if any document parsed to (near-)empty content or
# MAGIC lost all structure — a mangled doc here is a doc the extraction agent
# MAGIC can't rescue.

# COMMAND ----------

for r in rows:
    assert r["char_count"] > 500, f"{r['source_file']}: suspiciously small ({r['char_count']} chars)"
    if r["file_type"] == "docx":
        assert r["heading_count"] > 0, f"{r['source_file']}: 0 headings — heading styles not recognized"

if IS_DATABRICKS:
    from pyspark.sql import types as T

    schema = T.StructType([
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("project_id", T.StringType(), True),
        T.StructField("source_file", T.StringType(), False),
        T.StructField("file_type", T.StringType(), False),
        T.StructField("char_count", T.LongType(), False),
        T.StructField("heading_count", T.LongType(), False),
        T.StructField("table_count", T.LongType(), False),
        T.StructField("content", T.StringType(), False),
        T.StructField("parsed_at", T.TimestampType(), False),
    ])

    df = spark.createDataFrame(rows, schema=schema)
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLE)
    print(f"wrote {df.count()} row(s) to {TABLE}")
else:
    from _local_tables import write_table

    write_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, TABLE_NAME, rows)
    print(f"wrote {len(rows)} row(s) to local table {CATALOG}.{SCHEMA}.{TABLE_NAME}")

# COMMAND ----------

_preview_cols = ("doc_id", "project_id", "file_type", "char_count", "heading_count", "table_count", "parsed_at")
if IS_DATABRICKS:
    display(spark.table(TABLE).select(*_preview_cols))
else:
    from _local_tables import read_table

    for _r in read_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, TABLE_NAME):
        print({k: _r[k] for k in _preview_cols})

# COMMAND ----------

# MAGIC %md
# MAGIC ## Optional: markdown copies for eyeballing
# MAGIC Written to `sttm_out/parsed_frd/` so you can open the conversions and
# MAGIC spot-check the requirement tables before pointing the agent at them.
# MAGIC Set the `preview_volume` widget to "" to skip.

# COMMAND ----------

if PREVIEW_DIR:
    out_dir = Path(PREVIEW_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        (out_dir / f"{r['doc_id']}.md").write_text(r["content"], encoding="utf-8")
    print(f"wrote {len(rows)} preview file(s) to {PREVIEW_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next (Phase 3)
# MAGIC **Agents → Create Agent → Information Extraction → Select table** →
# MAGIC `frd_documents`, column **`content`**. Then paste the mapping-contract
# MAGIC JSON schema as the extraction schema and run against these documents.
