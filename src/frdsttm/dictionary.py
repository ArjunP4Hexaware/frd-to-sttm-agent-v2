"""
frdsttm.dictionary — the vendor data dictionary (VDD) parser.

The second of the two inputs. A vendor data
dictionary is one workbook per feed, named ``VDD_<name>.xlsx`` to match its
``FRD_<name>.docx``, holding what the FRD structurally cannot: every source
COLUMN. The FRD carries the feed-level frame (file pattern, format, target
schema/table per layer, load strategy, landing folder, DQ rules) and names
roughly two columns; one real STTM has ~410 column rows. That gap is the
whole reason this module exists — see docs/ARCHITECTURE.md.

Shape, fixed by templates/VDD_TEMPLATE.xlsx (tools/build_vdd_template.py) and
backward-compatible with the worked examples in sample_documents/:

    FILES sheet            one ROW per physical file the vendor delivers.
                           Core columns: File Name Pattern · File Title ·
                           Format · Delimiter · Header Row · Encoding ·
                           Delivery Cadence · Content Description · Field
                           Sheet. Optional: Null Representation (what a
                           missing value looks like — the FRD's reject rules
                           say "when X is NULL" without saying what null looks
                           like in a delimited file), and for multi-record
                           files Multi-Record-Type · Record Type Field ·
                           Record Type Values · Expected Field Count.
    <field sheet> × n      one sheet per FILES row, named by its Field Sheet
                           cell. One ROW per source column. Core columns:
                           Position · Field Name · Data Type · Length ·
                           Required (Y/N) · Description · Allowed Values /
                           Range · Example Value · PHI/PII (Y/N). Optional:
                           Key / Uniqueness (the match key an Upsert load
                           strategy needs — the FRD's own Business Key rows
                           read "NA" on the real documents), and Segment
                           (required on multi-record files).
    README                 ignored — prose for the vendor.

Doctrine this module is deliberately built to, all of it load-bearing:

* **Never invent.** A missing field sheet, an empty sheet or a row with no
  field name is recorded in ``problems`` and left EMPTY. Downstream that
  becomes a gated ambiguity naming the file, never a sparse render and never
  a guessed column. This is the grounding-audit rule applied to the source
  side.
* **Structure raises, content gates.** A workbook with no FILES sheet, or a
  FILES sheet whose header row cannot be located, raises
  :class:`DictionaryError` — an unreadable dictionary must never masquerade
  as an empty one. Anything the vendor merely left blank is a problem, not an
  exception.
* **Headers are FOUND, not assumed.** The header row is located by matching
  the known core headers, so a vendor who adds a title or logo row above the
  table still parses. Matching is case- and space-insensitive and tolerates
  the punctuation drift real vendors introduce (``PHI/PII (Y/N)`` vs
  ``PHI / PII``).
* **Unknown columns are kept, not dropped**, in each row's ``extra`` — a
  vendor who adds ``Source System`` has told us something, and silently
  discarding it is the same failure as inventing a value, in reverse.
* **Template example rows are removed and COUNTED.** The issued template
  ships two worked example rows carrying the literal marker
  ``delete once replaced``. A vendor who returns the template unedited would
  otherwise hand us a dictionary of examples that parses perfectly — the
  worst possible outcome. Such rows are dropped and reported in
  ``problems`` so a reviewer sees "this vendor returned the template".

No network, no model call, no Spark: deterministic code over one .xlsx, like
every other parser in this repo.
"""

from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

#: The naming convention, and one live alias: ``VDD_<name>.xlsx`` is the
#: convention; ``DICT_`` is accepted because the template already issued to
#: vendors is named that way. `frdsttm.corpus.name_key` strips both.
DICTIONARY_NAME_PREFIX = "VDD_"
DICTIONARY_NAME_PREFIXES = ("VDD_", "DICT_")
DICTIONARY_SUFFIXES = {".xlsx"}

#: The literal the issued template stamps on its worked example rows.
TEMPLATE_EXAMPLE_MARKER = "delete once replaced"

_IGNORED_SHEETS = {"readme", "read me", "instructions", "change log", "changelog"}


class DictionaryError(RuntimeError):
    """A dictionary workbook cannot be read as a dictionary at all.

    Distinct from an INCOMPLETE dictionary, which is an ordinary state: the
    vendor left cells blank, and the caller gates. Only structural failure —
    no FILES sheet, no locatable header row — raises.
    """


# --------------------------------------------------------------------------- #
# header matching
# --------------------------------------------------------------------------- #
def _norm(value) -> str:
    """Header identity: lower-cased, punctuation-stripped, space-collapsed.

    ``PHI/PII (Y/N)``, ``PHI / PII (Y/N)`` and ``phi pii y n`` all normalise
    to the same key, which is what real returned workbooks require.
    """
    if value is None:
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value).lower()).split())


#: {canonical key: (normalised header, ...)} for the FILES sheet.
FILE_COLUMNS = {
    "file_name_pattern": ("file name pattern", "file name", "file pattern"),
    "file_title": ("file title", "title"),
    "format": ("format", "object data format"),
    "delimiter": ("delimiter", "delimeter"),
    "header_row": ("header row", "has header row"),
    "encoding": ("encoding",),
    "cadence": ("delivery cadence", "cadence", "frequency"),
    "description": ("content description", "description"),
    "field_sheet": ("field sheet", "fields sheet", "sheet"),
    # optional — multi-record-type files
    "multi_record": ("multi record type", "multi record"),
    "record_type_field": ("record type field",),
    "record_type_values": ("record type values",),
    "expected_field_count": ("expected field count", "field count"),
    "null_representation": ("null representation", "null value", "missing value"),
    "text_qualifier": ("text qualifier",),
    "line_ending": ("line ending",),
    "trailer_record": ("trailer control record", "trailer record"),
    "notes": ("notes", "note"),
}

#: {canonical key: (normalised header, ...)} for a field sheet.
FIELD_COLUMNS = {
    "position": ("position", "ordinal", "seq", "sequence"),
    "name": ("field name", "column name", "name"),
    "datatype": ("data type", "datatype", "type"),
    "length": ("length", "size"),
    "required": ("required y n", "required", "mandatory", "nullable"),
    "description": ("description", "definition"),
    "allowed_values": ("allowed values range", "allowed values", "valid values", "domain"),
    "example": ("example value", "example", "sample value", "sample"),
    "phi": ("phi pii y n", "phi pii", "phi", "pii"),
    # optional
    "key": ("key uniqueness", "key", "primary key", "unique key"),
    "segment": ("segment", "record type"),
    "business_name": ("business name",),
    "precision": ("precision",),
    "scale": ("scale",),
    "format": ("format pattern", "format"),
    "default": ("default value", "default"),
    "start_position": ("start position",),
    "end_position": ("end position",),
    "notes": ("notes", "note"),
}

#: Without these, the sheet is not the table we are looking for.
_FILES_REQUIRED = ("file_name_pattern", "field_sheet")
_FIELDS_REQUIRED = ("name",)


def _header_map(spec: dict) -> dict:
    """{normalised header: canonical key}, first spelling wins on collision."""
    out: dict = {}
    for key, spellings in spec.items():
        for s in spellings:
            out.setdefault(s, key)
    return out


_FILES_HEADERS = _header_map(FILE_COLUMNS)
_FIELDS_HEADERS = _header_map(FIELD_COLUMNS)


def _find_header_row(rows, headers: dict, required: tuple) -> tuple[int, dict]:
    """Locate the header row in the first few rows of a sheet.

    Returns (row_index, {column_index: canonical key}). Scans a bounded
    window rather than assuming row 1, so a vendor's title or logo row does
    not break the parse. Raises when no row carries every required column —
    structure, not content.
    """
    best = (None, {}, -1)
    for i, row in enumerate(rows[:12]):
        mapping = {}
        for j, cell in enumerate(row):
            key = headers.get(_norm(cell))
            if key is not None and key not in mapping.values():
                mapping[j] = key
        hit = len(mapping)
        if all(k in mapping.values() for k in required) and hit > best[2]:
            best = (i, mapping, hit)
    if best[0] is None:
        raise DictionaryError(
            f"no header row found in the first 12 rows — expected a row naming "
            f"{', '.join(required)}. Columns may be renamed but not removed; "
            f"re-issue templates/VDD_TEMPLATE.xlsx to the vendor."
        )
    return best[0], best[1]


# --------------------------------------------------------------------------- #
# cell coercion
# --------------------------------------------------------------------------- #
def _text(value) -> str | None:
    """A trimmed string, or None for anything the vendor left effectively blank."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


_TRUE = {"y", "yes", "true", "t", "1"}
_FALSE = {"n", "no", "false", "f", "0"}


def _yn(value) -> bool | None:
    """Y/N as a bool, or None when unanswered — which is a real answer.

    ``None`` must never be read as False: "the vendor did not say whether
    this column carries PHI" and "the vendor said it does not" are different
    facts, and only one of them is safe to act on.
    """
    s = _text(value)
    if s is None:
        return None
    low = s.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    return None


def _int(value) -> int | None:
    s = _text(value)
    if s is None:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _is_template_example(row: dict) -> bool:
    return TEMPLATE_EXAMPLE_MARKER in (row.get("notes") or "").lower()


def _is_footnote(row: dict) -> bool:
    """A FILES row that is prose under the table rather than a file.

    True only when EVERY other core cell is blank — a vendor who names a file
    and forgets the field sheet still fills the format or the delimiter, so
    this cannot swallow a real row.
    """
    return not any(row.get(k) for k in ("file_title", "format", "delimiter", "field_sheet"))


def _row_values(ws) -> list:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _read_table(rows, start: int, mapping: dict, spec: dict) -> list[dict]:
    """Rows below the header as canonical dicts, unknown columns kept in `extra`."""
    out = []
    for raw in rows[start + 1:]:
        rec: dict = {k: None for k in spec}
        extra: dict = {}
        empty = True
        for j, cell in enumerate(raw):
            value = _text(cell)
            if value is None:
                continue
            empty = False
            key = mapping.get(j)
            if key is None:
                extra[f"col_{j + 1}"] = value
            else:
                rec[key] = value
        if empty:
            continue
        rec["extra"] = extra
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #
def parse_dictionary_workbook(path: str | Path) -> dict:
    """Parse one ``DICT_*.xlsx`` into a structured source layout.

    Returns::

        {"workbook": str, "files": [file...], "fields": {sheet: [field...]},
         "n_files": int, "n_fields": int, "problems": [problem...]}

    where a ``file`` carries the FILES columns above plus ``field_sheet``,
    and a ``field`` carries the field-sheet columns plus ``extra``.

    ``problems`` is the gating surface: each entry is
    ``{"kind", "file"|"sheet", "detail"}`` and every one of them must reach
    the reviewer as a named question rather than being filled in.
    """
    path = Path(path)
    problems: list[dict] = []
    try:
        wb = load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — mapped to one operator-facing error
        raise DictionaryError(
            f"{path.name} could not be opened as a workbook "
            f"({exc.__class__.__name__}: {exc})"
        ) from exc

    try:
        sheet_by_norm = {_norm(n): n for n in wb.sheetnames}
        files_sheet = sheet_by_norm.get("files")
        if files_sheet is None:
            raise DictionaryError(
                f"{path.name} has no FILES sheet (sheets: {', '.join(wb.sheetnames)}). "
                f"Every vendor dictionary starts with one row per delivered file; "
                f"re-issue templates/VDD_TEMPLATE.xlsx to the vendor."
            )

        rows = _row_values(wb[files_sheet])
        hdr, mapping = _find_header_row(rows, _FILES_HEADERS, _FILES_REQUIRED)
        raw_files = _read_table(rows, hdr, mapping, FILE_COLUMNS)

        files, examples = [], 0
        for rec in raw_files:
            if _is_template_example(rec):
                examples += 1
                continue
            if not rec.get("file_name_pattern"):
                problems.append({"kind": "file_without_pattern", "file": None,
                                 "detail": "a FILES row has no File Name Pattern"})
                continue
            if _is_footnote(rec):
                # Real returned workbooks carry trailing prose under the table
                # — an assumptions note, a contact line. A row whose ONLY
                # populated core cell is the first one is that, not a file:
                # reading it as one invents a feed out of a sentence.
                problems.append({
                    "kind": "files_row_ignored", "file": None,
                    "detail": f"a FILES row naming no title, format, delimiter or field "
                              f"sheet was read as a note, not a file: "
                              f"{rec['file_name_pattern'][:80]!r}",
                })
                continue
            rec["header_row"] = _yn(rec.get("header_row"))
            rec["multi_record"] = _yn(rec.get("multi_record"))
            rec["trailer_record"] = _yn(rec.get("trailer_record"))
            files.append(rec)
        if examples:
            problems.append({
                "kind": "template_example_rows", "file": None,
                "detail": f"{examples} FILES row(s) were the issued template's worked "
                          f"examples and were dropped — the vendor may have returned "
                          f"the template unedited",
            })

        fields: dict[str, list] = {}
        for rec in files:
            sheet_name = rec.get("field_sheet")
            pattern = rec["file_name_pattern"]
            if not sheet_name:
                problems.append({"kind": "no_field_sheet_named", "file": pattern,
                                 "detail": "the FILES row names no field sheet"})
                continue
            actual = sheet_by_norm.get(_norm(sheet_name))
            if actual is None:
                problems.append({
                    "kind": "field_sheet_missing", "file": pattern,
                    "detail": f"the FILES row names field sheet {sheet_name!r}, which "
                              f"is not in the workbook",
                })
                fields[sheet_name] = []
                continue
            fields[sheet_name] = _parse_field_sheet(
                wb[actual], sheet_name, pattern, rec, problems)

        # Field sheets the vendor filled but never listed on FILES: their
        # columns exist but belong to no file, so nothing can consume them.
        listed = {_norm(f.get("field_sheet") or "") for f in files}
        for norm_name, actual in sheet_by_norm.items():
            if norm_name in _IGNORED_SHEETS or norm_name == "files" or norm_name in listed:
                continue
            if norm_name.startswith("fields template"):
                continue
            problems.append({
                "kind": "sheet_not_listed", "sheet": actual,
                "detail": f"sheet {actual!r} is not named by any FILES row and was "
                          f"not read — add a FILES row for it, or delete it",
            })
    finally:
        wb.close()

    return {
        "workbook": path.name,
        "files": files,
        "fields": fields,
        "n_files": len(files),
        "n_fields": sum(len(v) for v in fields.values()),
        "problems": problems,
    }


def _parse_field_sheet(ws, sheet_name: str, file_pattern: str, file_rec: dict,
                       problems: list) -> list[dict]:
    rows = _row_values(ws)
    try:
        hdr, mapping = _find_header_row(rows, _FIELDS_HEADERS, _FIELDS_REQUIRED)
    except DictionaryError as exc:
        problems.append({"kind": "field_sheet_unreadable", "file": file_pattern,
                         "sheet": sheet_name, "detail": str(exc)})
        return []

    out, examples = [], 0
    for rec in _read_table(rows, hdr, mapping, FIELD_COLUMNS):
        if _is_template_example(rec):
            examples += 1
            continue
        if not rec.get("name"):
            continue                      # a formatted-but-empty template row
        rec["position"] = _int(rec.get("position"))
        rec["required"] = _yn(rec.get("required"))
        rec["phi"] = _yn(rec.get("phi"))
        rec["start_position"] = _int(rec.get("start_position"))
        rec["end_position"] = _int(rec.get("end_position"))
        rec["file"] = file_pattern
        out.append(rec)

    if examples:
        problems.append({
            "kind": "template_example_rows", "file": file_pattern, "sheet": sheet_name,
            "detail": f"{examples} field row(s) were the issued template's worked "
                      f"examples and were dropped",
        })
    if not out:
        problems.append({"kind": "field_sheet_empty", "file": file_pattern,
                         "sheet": sheet_name,
                         "detail": f"field sheet {sheet_name!r} names no columns"})
        return out

    # Content gaps. Each is a reviewer question, never something to fill in:
    # a description or a PHI flag cannot be recovered from a data file, and
    # guessing either is the failure this whole input exists to prevent.
    for key, kind in (("description", "missing_descriptions"),
                      ("datatype", "missing_datatypes"),
                      ("required", "missing_required_flags"),
                      ("phi", "missing_phi_flags")):
        blank = [f["name"] for f in out if f.get(key) is None]
        if blank:
            problems.append({
                "kind": kind, "file": file_pattern, "sheet": sheet_name,
                "detail": f"{len(blank)} of {len(out)} columns have no {key} "
                          f"(first: {', '.join(blank[:5])})",
                "columns": blank,
            })

    if file_rec.get("multi_record") and any(f.get("segment") is None for f in out):
        problems.append({
            "kind": "missing_segments", "file": file_pattern, "sheet": sheet_name,
            "detail": "the file is multi-record-type but some columns name no segment, "
                      "so they cannot be routed to a target table",
        })

    expected = _int(file_rec.get("expected_field_count"))
    if expected is not None and expected != len(out):
        problems.append({
            "kind": "field_count_mismatch", "file": file_pattern, "sheet": sheet_name,
            "detail": f"FILES declares {expected} fields, the sheet holds {len(out)}",
        })
    return out


def parse_dictionary_dir(dictionary_dir: str | Path) -> dict:
    """{workbook name: parsed dictionary} for every ``.xlsx`` in a directory.

    ONE unreadable dictionary does not sink the rest: it is returned under
    ``errors`` so the index still builds and the reviewer sees exactly which
    vendor spec is unusable.
    """
    out, errors = {}, {}
    for path in sorted(Path(dictionary_dir).glob("*.xlsx")):
        if path.name.startswith("~$"):        # an open-in-Excel lock file
            continue
        try:
            out[path.name] = parse_dictionary_workbook(path)
        except DictionaryError as exc:
            errors[path.name] = str(exc)
    return {"dictionaries": out, "errors": errors}


def source_layout(parsed: dict) -> dict:
    """{file pattern: [column, ...]} — the shape the pipeline consumes.

    A file whose field sheet was missing or empty maps to ``[]`` — present in
    the layout, with no columns. An absent key means the dictionary never
    mentioned the file; an empty list means it mentioned it and could not
    describe it.
    """
    by_sheet = parsed.get("fields", {})
    return {f["file_name_pattern"]: list(by_sheet.get(f.get("field_sheet") or "", []))
            for f in parsed.get("files", [])}
