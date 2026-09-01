"""
frdsttm.frd_parsing — the FRD parser (.docx / .pdf / .md / .txt → markdown).

Fidelity, not summarisation: headings, tables (pipe tables), bullets and
paragraphs in document order. Content controls (<w:sdt>) are unwrapped,
nested tables inside cells are flattened inline, TOC entries are dropped,
requirement ids (SRQ226433 …) become bold markers so the model can anchor
on them.

The labels below describe the client's FRD template (templates/FRD_TEMPLATE
.docx). If the template changes, change them here.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}

#: Requirement-id families the FRD template uses.
REQ_ID_FAMILIES = ("BR", "REQ", "FR", "SRQ", "SIR", "NFR", "MDST")
#: "Project ID: 1005034" — 6-8 digits.
PROJECT_ID_LINE_RE = re.compile(r"project\s*id\s*:?\s*(\d{6,8})")
PROJECT_ID_DIGITS_RE = re.compile(r"(\d{6,8})")
#: Section headings the template carries, for reference.
SECTION_LABELS = {
    "in_scope": "In Scope",
    "out_of_scope": "Out of Scope",
    "assumptions_constraints_dependencies": "Assumptions, Constraints & Dependencies",
    "data_ingestion_requirements": "Data Ingestion Requirements",
    "data_quality": "Data Quality",
    "technical_metadata": "Technical Metadata",
    "administrative_metadata": "Administrative Metadata",
}

_REQUIREMENT_ID_RE = re.compile(
    r"^\s*((?:" + "|".join(REQ_ID_FAMILIES) + r")[-\s]?\d+)\b[\s:.—–-]*(.*)$", re.I
)
_SKIP_STYLE_RE = re.compile(r"^(toc\b|toc header$)", re.I)
_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6,
}


class FrdParseError(ValueError):
    """The file is not a usable FRD (unsupported type, empty, no structure)."""


def _read_text_forgiving(path: Path) -> str:
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
    raise FrdParseError(
        f"Unsupported file type '{suffix}'. Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def infer_project_id(filename: str) -> str | None:
    m = PROJECT_ID_DIGITS_RE.search(filename)
    return m.group(1) if m else None


def parse_frd(path: str | Path) -> dict:
    """One FRD file → {doc_id, source_file, content, content_sha256, project_id,
    heading_count, table_count}. Raises FrdParseError when the result is not a
    usable document (near-empty, or a .docx with no headings)."""
    path = Path(path)
    md = normalize_to_markdown(str(path))
    heading_count = sum(1 for line in md.splitlines() if line.startswith("#"))
    table_count = sum(1 for line in md.splitlines() if line.startswith("| ---"))
    if len(md) < 500:
        raise FrdParseError(f"{path.name}: suspiciously small ({len(md)} chars) — not a usable FRD")
    if path.suffix.lower() == ".docx" and heading_count == 0:
        raise FrdParseError(f"{path.name}: no headings recognised — heading styles not understood")
    return {
        "doc_id": path.stem,
        "source_file": path.name,
        "content": md,
        "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "project_id": infer_project_id(path.name),
        "heading_count": heading_count,
        "table_count": table_count,
    }
