"""
frdsttm.frd_parsing — the FRD normalizer (docx/pdf/md/txt → markdown).

Factored verbatim out of notebooks/01_frd_ingest.py (2026-08-22) so the
review app backend (corpus bootstrap) and the corpus/similarity modules can
parse FRDs with EXACTLY the text the pipeline's ingest stage produces —
one parser, no drift. 01_frd_ingest consumes this module through the
notebooks/_frd_parsing.py shim (%run in Databricks, plain import locally),
the same pattern as _models / _live_extraction.

Behavioral contract (see the notebook header and SKILL.md "Stage 1"):
fidelity not summarization; SDT unwrapping; requirement-id bold markers;
TOC dropped but Header-styled body kept; sanity gates live in the caller.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Shared FRD label contract (contracts/frd_label_contract.json), loaded via
# frdsttm.label_contract — the versioned artifact this parser and the
# upstream brd-to-frd-agent renderer both key off.
from frdsttm.label_contract import PROJECT_ID_DIGITS_RE, REQ_ID_FAMILIES

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}

# Requirement-id families seen across the BRD (BR/REQ/FR) and the
# IS-Methodology FRD templates (SRQ/SIR/NFR/MDST), from the shared label
# contract. Reshaped to bold markers.
_REQUIREMENT_ID_RE = re.compile(
    r"^\s*((?:" + "|".join(REQ_ID_FAMILIES) + r")[-\s]?\d+)\b[\s:.—–-]*(.*)$", re.I
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


# 6-8 digit project id, per the shared label contract's digits pattern
# (see the contract bootstrap in the parser cell above).
_PROJECT_ID_RE = PROJECT_ID_DIGITS_RE


def infer_project_id(filename: str) -> str | None:
    """Project id from the filename when present (e.g. ..._1005034.docx)."""
    m = _PROJECT_ID_RE.search(filename)
    return m.group(1) if m else None
