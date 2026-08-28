"""
frdsttm.dictionary_repair — when a vendor data dictionary does not fit the
template, Claude normalises it into the template shape.

The code parser (`dictionary.py`) runs first: exact, free, and tolerant of
renamed headers, extra columns and title rows. This module is the fallback
for a workbook it cannot read at all (no FILES sheet, no locatable header)
or reads as empty. The whole workbook is rendered as text, sheet by sheet,
and the model returns the template structure. Guard: every column name it
returns must appear verbatim in the workbook text — a name the workbook
does not contain is dropped and reported, never rendered.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_ROWS_PER_SHEET = 800
MAX_CELL_CHARS = 200


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepairedFile(_M):
    file_name_pattern: str = Field(description="The delivered file's name pattern, verbatim from the workbook.")
    file_title: Optional[str] = Field(default=None, description="A short title if the workbook gives one.")
    format: Optional[str] = Field(default=None, description="csv, psv, txt, fixed-width… verbatim if stated.")
    delimiter: Optional[str] = Field(default=None, description="The field delimiter if stated.")
    header_row: Optional[bool] = Field(default=None, description="True if the file carries a header row, False if stated not to, null if unknown.")
    field_sheet: str = Field(description="The name of the sheet that lists this file's columns, exactly as the workbook names it.")
    multi_record: Optional[bool] = Field(default=None, description="True if the file mixes record types (header/detail/trailer).")


class RepairedField(_M):
    position: Optional[int] = Field(default=None, description="1-based position if stated.")
    name: str = Field(description="The column name EXACTLY as written in the workbook. Never invent one.")
    datatype: Optional[str] = Field(default=None, description="The type as written (varchar, int, decimal…).")
    length: Optional[str] = Field(default=None, description="Length/size if stated.")
    required: Optional[bool] = Field(default=None, description="True for Y/yes/required/not null, False for N/no/optional, null if not stated.")
    description: Optional[str] = Field(default=None, description="The column description as written.")
    allowed_values: Optional[str] = Field(default=None, description="Allowed values or range as written.")
    example: Optional[str] = Field(default=None, description="An example or sample value as written.")
    phi: Optional[bool] = Field(default=None, description="True if flagged PHI/PII, False if flagged not, null if not stated.")
    segment: Optional[str] = Field(default=None, description="Record segment (Header/Detail/Trailer) if stated.")


class RepairedDictionary(_M):
    """A vendor data dictionary normalised into the template shape. Use ONLY what the workbook text contains: never invent a file, a column, a type or a flag. Leave unknown cells null."""

    files: List[RepairedFile] = Field(description="One entry per delivered file. If the workbook lists no files explicitly, one file per column sheet, using the sheet name as the file name pattern.")
    fields: dict[str, List[RepairedField]] = Field(description="Per field_sheet name: the columns in that sheet, in order.")


SYSTEM_PROMPT = (
    "You normalise a vendor's data dictionary spreadsheet into a fixed structure. The workbook was "
    "written by a vendor and may not follow the template: headers may be renamed, merged, split "
    "across rows, or missing; sheets may be organised differently. Recover the structure faithfully. "
    "Copy every column name exactly as written. Use null for anything the workbook does not state. "
    "Never invent a file, a column, a data type or a flag."
)


def workbook_as_text(path: str | Path) -> str:
    wb = load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            parts.append(f"=== SHEET: {ws.title} ===")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS_PER_SHEET:
                    parts.append(f"... ({ws.max_row - MAX_ROWS_PER_SHEET} more rows)")
                    break
                cells = ["" if c is None else str(c).replace("\n", " ")[:MAX_CELL_CHARS] for c in row]
                if any(cells):
                    parts.append(" | ".join(cells).rstrip(" |"))
    finally:
        wb.close()
    return "\n".join(parts)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def build_prompt(text: str) -> str:
    return (
        "Normalise the vendor data dictionary below into the JSON structure described by this schema. "
        "Respond with ONLY one JSON object (no markdown fences, no prose).\n\nJSON SCHEMA:\n"
        + json.dumps(RepairedDictionary.model_json_schema(), indent=2)
        + "\n\nWORKBOOK (one line per row, cells separated by ' | '):\n" + text
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def repair_dictionary(client, path: str | Path, *, model: str, max_tokens: int = 64000) -> tuple[dict, dict]:
    """Returns (parsed dictionary in `dictionary.parse_dictionary_workbook`'s
    shape, meta). Raises on refusal, truncation or a schema mismatch."""
    path = Path(path)
    text = workbook_as_text(path)
    with client.messages.stream(model=model, max_tokens=max_tokens, system=SYSTEM_PROMPT,
                                messages=[{"role": "user", "content": build_prompt(text)}]) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason in {"refusal", "max_tokens"}:
        raise RuntimeError(f"{path.name}: dictionary normalisation stopped with {msg.stop_reason!r}")
    raw = "".join(b.text for b in msg.content if b.type == "text")
    try:
        rep = RepairedDictionary.model_validate_json(_strip_fences(raw))
    except ValidationError as exc:
        raise RuntimeError(f"{path.name}: the model's dictionary JSON does not match the template shape — {exc.error_count()} error(s)") from exc

    ntext = _norm(text)
    problems = [{"kind": "normalised_by_model", "file": None,
                 "detail": "the workbook did not fit the template; Claude normalised it into the template "
                           "shape. Every column name below was verified verbatim against the workbook."}]
    fields: dict[str, list] = {}
    dropped = []
    for sheet, cols in rep.fields.items():
        out = []
        for f in cols:
            if _norm(f.name) not in ntext:
                dropped.append(f.name)
                continue
            out.append({"position": f.position, "name": f.name, "datatype": f.datatype, "length": f.length,
                        "required": f.required, "description": f.description, "allowed_values": f.allowed_values,
                        "example": f.example, "phi": f.phi, "segment": f.segment, "key": None, "notes": None,
                        "start_position": None, "end_position": None, "extra": {}, "file": None})
        fields[sheet] = out
    if dropped:
        problems.append({"kind": "model_columns_not_in_workbook", "file": None,
                         "detail": f"{len(dropped)} column name(s) the model returned are not in the workbook and were dropped: "
                                   + ", ".join(dropped[:8]), "columns": dropped})
    files = []
    for f in rep.files:
        rec = {"file_name_pattern": f.file_name_pattern, "file_title": f.file_title, "format": f.format,
               "delimiter": f.delimiter, "header_row": f.header_row, "encoding": None, "cadence": None,
               "description": None, "field_sheet": f.field_sheet, "multi_record": f.multi_record,
               "record_type_field": None, "record_type_values": None, "expected_field_count": None,
               "null_representation": None, "text_qualifier": None, "line_ending": None,
               "trailer_record": None, "notes": None, "extra": {}}
        for col in fields.get(f.field_sheet, []):
            col["file"] = f.file_name_pattern
        if f.field_sheet not in fields:
            problems.append({"kind": "field_sheet_missing", "file": f.file_name_pattern,
                             "detail": f"the model named field sheet {f.field_sheet!r}, which it returned no columns for"})
            fields[f.field_sheet] = []
        files.append(rec)
    parsed = {"workbook": path.name, "files": files, "fields": fields, "n_files": len(files),
              "n_fields": sum(len(v) for v in fields.values()), "problems": problems,
              "normalised_by_model": True}
    u = msg.usage
    meta = {"model": model, "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0), "dropped": dropped}
    return parsed, meta
