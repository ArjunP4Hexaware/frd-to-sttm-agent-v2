"""frdsttm.dictionary — the vendor data dictionary (VDD) parser and its pairing.

The third input's ingestion half (built 2026-08-27). Every workbook here is
SYNTHETIC, built in-process with openpyxl — the no-client-documents rule
applies to fixtures as hard as it applies to the repo, and these tests must
keep passing after Friday's purge of sample_documents/.

What is worth pinning, in the order the doctrine matters:

* **structure raises, content gates** — no FILES sheet is an exception; a
  vendor who left the descriptions blank is a `problems` entry;
* **never invent** — a missing field sheet yields an EMPTY column list plus a
  named problem, never a guess and never a silent skip;
* **the returned-template trap** — the issued template's worked example rows
  parse perfectly, so they are dropped and counted rather than ingested;
* **pairing is exact-name only** — attaching the wrong vendor's spec to a
  feed would put real column names, types and PHI flags on data they do not
  describe, which is fabrication, not a degraded answer.
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from frdsttm.corpus import (
    CORPUS_INDEX_VERSION,
    build_corpus_index,
    dictionary_for,
    eligibility_for,
    eligibility_of,
)
from frdsttm.dictionary import (
    DictionaryError,
    parse_dictionary_dir,
    parse_dictionary_workbook,
    source_layout,
)
from frdsttm.similarity import pair_dictionaries

FILES_HEADERS = ["File Name Pattern", "File Title", "Format", "Delimiter", "Header Row",
                 "Encoding", "Delivery Cadence", "Content Description", "Field Sheet",
                 "Multi-Record-Type", "Record Type Field", "Record Type Values",
                 "Expected Field Count", "Notes"]
FIELD_HEADERS = ["Position", "Field Name", "Data Type", "Length", "Required (Y/N)",
                 "Description", "Allowed Values / Range", "Example Value",
                 "PHI/PII (Y/N)", "Segment", "Notes"]


def _file_row(pattern, sheet, **kw):
    row = dict(zip(FILES_HEADERS, [pattern, "A Title", "Delimited text", "|", "Y",
                                   "UTF-8", "Weekly", "One row per thing", sheet,
                                   None, None, None, None, None]))
    row.update(kw)
    return [row[h] for h in FILES_HEADERS]


def _field_row(pos, name, **kw):
    row = dict(zip(FIELD_HEADERS, [pos, name, "String", 20, "Y", f"What {name} means",
                                   "Free text", "X1", "N", None, None]))
    row.update(kw)
    return [row[h] for h in FIELD_HEADERS]


def write_dictionary(path, files, sheets, *, files_preamble=(), extra_sheets=()):
    """Build a DICT_ workbook. `files_preamble` puts rows ABOVE the header row,
    which is how a vendor's title or logo band reaches us in practice."""
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("FILES")
    for row in files_preamble:
        ws.append(list(row))
    ws.append(FILES_HEADERS)
    for row in files:
        ws.append(list(row))
    for name, rows in sheets.items():
        s = wb.create_sheet(name)
        s.append(FIELD_HEADERS)
        for row in rows:
            s.append(list(row))
    for name in extra_sheets:
        wb.create_sheet(name).append(["prose for the vendor"])
    wb.save(str(path))
    return path


@pytest.fixture
def simple(tmp_path):
    return write_dictionary(
        tmp_path / "DICT_alpha.xlsx",
        [_file_row("ALPHA_CCYYMMDD.txt", "alpha_fields")],
        {"alpha_fields": [_field_row(1, "member_id", **{"PHI/PII (Y/N)": "Y"}),
                          _field_row(2, "zip_code"),
                          _field_row(3, "risk_score", **{"Data Type": "Decimal"})]},
    )


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #
def test_parses_files_and_fields(simple):
    d = parse_dictionary_workbook(simple)
    assert d["n_files"] == 1 and d["n_fields"] == 3
    assert d["files"][0]["file_name_pattern"] == "ALPHA_CCYYMMDD.txt"
    assert d["files"][0]["delimiter"] == "|"
    assert d["problems"] == []
    names = [f["name"] for f in d["fields"]["alpha_fields"]]
    assert names == ["member_id", "zip_code", "risk_score"]


def test_source_layout_is_keyed_by_file_pattern(simple):
    layout = source_layout(parse_dictionary_workbook(simple))
    assert list(layout) == ["ALPHA_CCYYMMDD.txt"]
    assert [f["name"] for f in layout["ALPHA_CCYYMMDD.txt"]] == \
        ["member_id", "zip_code", "risk_score"]


def test_every_field_carries_its_file(simple):
    d = parse_dictionary_workbook(simple)
    assert {f["file"] for f in d["fields"]["alpha_fields"]} == {"ALPHA_CCYYMMDD.txt"}


def test_yes_no_is_tri_state_not_boolean(tmp_path):
    """A blank PHI flag means "the vendor did not say", which is NOT "no".

    Reading an unanswered flag as False is the single most expensive possible
    coercion in this parser: it would silently declare a column PHI-free.
    """
    path = write_dictionary(
        tmp_path / "DICT_tri.xlsx",
        [_file_row("T.txt", "t")],
        {"t": [_field_row(1, "a", **{"PHI/PII (Y/N)": "Y"}),
               _field_row(2, "b", **{"PHI/PII (Y/N)": "no"}),
               _field_row(3, "c", **{"PHI/PII (Y/N)": None})]},
    )
    phi = [f["phi"] for f in parse_dictionary_workbook(path)["fields"]["t"]]
    assert phi == [True, False, None]
    kinds = {p["kind"] for p in parse_dictionary_workbook(path)["problems"]}
    assert "missing_phi_flags" in kinds


def test_header_row_is_found_below_a_title_band(tmp_path):
    path = write_dictionary(
        tmp_path / "DICT_titled.xlsx",
        [_file_row("T.txt", "t")],
        {"t": [_field_row(1, "a")]},
        files_preamble=[["ACME DATA SERVICES"], [], ["Vendor spec v3.1"]],
    )
    assert parse_dictionary_workbook(path)["n_files"] == 1


def test_unknown_columns_are_kept_not_dropped(tmp_path):
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("FILES")
    ws.append(FILES_HEADERS)
    ws.append(_file_row("T.txt", "t"))
    s = wb.create_sheet("t")
    s.append(FIELD_HEADERS + ["Source System"])
    s.append(_field_row(1, "a") + ["FACETS"])
    wb.save(str(tmp_path / "DICT_extra.xlsx"))
    field = parse_dictionary_workbook(tmp_path / "DICT_extra.xlsx")["fields"]["t"][0]
    assert "FACETS" in field["extra"].values()


# --------------------------------------------------------------------------- #
# structure raises
# --------------------------------------------------------------------------- #
def test_no_files_sheet_raises(tmp_path):
    wb = Workbook()
    wb.active.title = "Sheet1"
    wb.active.append(["nothing useful"])
    wb.save(str(tmp_path / "DICT_broken.xlsx"))
    with pytest.raises(DictionaryError, match="no FILES sheet"):
        parse_dictionary_workbook(tmp_path / "DICT_broken.xlsx")


def test_unfindable_header_row_raises(tmp_path):
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("FILES")
    for _ in range(20):
        ws.append(["some", "prose", "with", "no", "headers"])
    wb.save(str(tmp_path / "DICT_nohdr.xlsx"))
    with pytest.raises(DictionaryError, match="no header row"):
        parse_dictionary_workbook(tmp_path / "DICT_nohdr.xlsx")


# --------------------------------------------------------------------------- #
# content gates — never invent
# --------------------------------------------------------------------------- #
def test_missing_field_sheet_gates_and_yields_no_columns(tmp_path):
    path = write_dictionary(
        tmp_path / "DICT_gap.xlsx",
        [_file_row("A.txt", "a_fields"), _file_row("B.txt", "b_fields")],
        {"a_fields": [_field_row(1, "x")]},          # b_fields never written
    )
    d = parse_dictionary_workbook(path)
    assert d["n_files"] == 2
    assert d["fields"]["b_fields"] == []
    problem = next(p for p in d["problems"] if p["kind"] == "field_sheet_missing")
    assert problem["file"] == "B.txt"
    # present-with-no-columns, not absent: the two states gate differently
    assert source_layout(d)["B.txt"] == []


def test_blank_descriptions_are_reported_with_the_column_names(tmp_path):
    path = write_dictionary(
        tmp_path / "DICT_nodesc.xlsx",
        [_file_row("A.txt", "a")],
        {"a": [_field_row(1, "x", Description=None),
               _field_row(2, "y"),
               _field_row(3, "z", Description=None)]},
    )
    p = next(x for x in parse_dictionary_workbook(path)["problems"]
             if x["kind"] == "missing_descriptions")
    assert p["columns"] == ["x", "z"]
    assert "2 of 3" in p["detail"]


def test_field_count_mismatch_is_reported(tmp_path):
    path = write_dictionary(
        tmp_path / "DICT_count.xlsx",
        [_file_row("A.txt", "a", **{"Expected Field Count": 5})],
        {"a": [_field_row(1, "x"), _field_row(2, "y")]},
    )
    p = next(x for x in parse_dictionary_workbook(path)["problems"]
             if x["kind"] == "field_count_mismatch")
    assert "declares 5" in p["detail"] and "holds 2" in p["detail"]


def test_multi_record_file_without_segments_is_gated(tmp_path):
    path = write_dictionary(
        tmp_path / "DICT_multi.xlsx",
        [_file_row("A.txt", "a", **{"Multi-Record-Type": "Y",
                                    "Record Type Field": "Position 1",
                                    "Record Type Values": "HDR/DTL/TRL"})],
        {"a": [_field_row(1, "x", Segment="Detail"), _field_row(2, "y")]},
    )
    d = parse_dictionary_workbook(path)
    assert d["files"][0]["multi_record"] is True
    assert any(p["kind"] == "missing_segments" for p in d["problems"])


def test_empty_field_sheet_is_gated(tmp_path):
    path = write_dictionary(tmp_path / "DICT_empty.xlsx",
                            [_file_row("A.txt", "a")], {"a": []})
    d = parse_dictionary_workbook(path)
    assert d["fields"]["a"] == []
    assert any(p["kind"] == "field_sheet_empty" for p in d["problems"])


def test_sheet_not_listed_on_files_is_reported(tmp_path):
    path = write_dictionary(
        tmp_path / "DICT_orphan.xlsx",
        [_file_row("A.txt", "a")],
        {"a": [_field_row(1, "x")], "b_orphan": [_field_row(1, "y")]},
    )
    p = next(x for x in parse_dictionary_workbook(path)["problems"]
             if x["kind"] == "sheet_not_listed")
    assert p["sheet"] == "b_orphan"


def test_readme_sheet_is_ignored_silently(tmp_path):
    path = write_dictionary(tmp_path / "DICT_readme.xlsx",
                            [_file_row("A.txt", "a")], {"a": [_field_row(1, "x")]},
                            extra_sheets=["README"])
    assert parse_dictionary_workbook(path)["problems"] == []


# --------------------------------------------------------------------------- #
# the returned-template trap
# --------------------------------------------------------------------------- #
def test_template_example_rows_are_dropped_and_counted(tmp_path):
    """A vendor who returns the issued template unedited must not look fine.

    The example rows parse perfectly — that is exactly what makes them
    dangerous. They are removed and the removal is reported, so a reviewer
    sees "this vendor returned the template", not "399 columns, all good".
    """
    marker = "Example row — delete once replaced."
    path = write_dictionary(
        tmp_path / "DICT_returned.xlsx",
        [_file_row("EX.txt", "ex", Notes="Example: delete once replaced"),
         _file_row("REAL.txt", "real")],
        {"ex": [_field_row(1, "member_id", Notes=marker)],
         "real": [_field_row(1, "actual_column")]},
    )
    d = parse_dictionary_workbook(path)
    assert [f["file_name_pattern"] for f in d["files"]] == ["REAL.txt"]
    assert any(p["kind"] == "template_example_rows" for p in d["problems"])


def test_trailing_prose_row_is_a_note_not_a_file(tmp_path):
    """Real returned workbooks carry an assumptions line under the table."""
    note = [None] * len(FILES_HEADERS)
    note[0] = "Assumptions: delimiter inferred from the extension; confirm with the vendor."
    path = write_dictionary(tmp_path / "DICT_note.xlsx",
                            [_file_row("A.txt", "a"), note],
                            {"a": [_field_row(1, "x")]})
    d = parse_dictionary_workbook(path)
    assert d["n_files"] == 1
    assert any(p["kind"] == "files_row_ignored" for p in d["problems"])


def test_a_row_naming_a_file_but_no_sheet_is_not_swallowed_as_prose(tmp_path):
    """The footnote rule must not eat a real row a vendor half-filled."""
    half = [None] * len(FILES_HEADERS)
    half[0], half[2] = "B.txt", "Delimited text"     # pattern + format, no sheet
    path = write_dictionary(tmp_path / "DICT_half.xlsx",
                            [_file_row("A.txt", "a"), half],
                            {"a": [_field_row(1, "x")]})
    d = parse_dictionary_workbook(path)
    assert d["n_files"] == 2
    assert any(p["kind"] == "no_field_sheet_named" for p in d["problems"])


# --------------------------------------------------------------------------- #
# directory scan
# --------------------------------------------------------------------------- #
def test_one_unreadable_dictionary_does_not_sink_the_others(tmp_path):
    write_dictionary(tmp_path / "DICT_good.xlsx",
                     [_file_row("A.txt", "a")], {"a": [_field_row(1, "x")]})
    bad = Workbook()
    bad.active.append(["not a dictionary"])
    bad.save(str(tmp_path / "DICT_bad.xlsx"))
    out = parse_dictionary_dir(tmp_path)
    assert set(out["dictionaries"]) == {"DICT_good.xlsx"}
    assert "DICT_bad.xlsx" in out["errors"]


def test_excel_lock_files_are_skipped(tmp_path):
    write_dictionary(tmp_path / "DICT_open.xlsx",
                     [_file_row("A.txt", "a")], {"a": [_field_row(1, "x")]})
    (tmp_path / "~$DICT_open.xlsx").write_bytes(b"lock")
    out = parse_dictionary_dir(tmp_path)
    assert set(out["dictionaries"]) == {"DICT_open.xlsx"}
    assert out["errors"] == {}


# --------------------------------------------------------------------------- #
# pairing — exact name only
# --------------------------------------------------------------------------- #
def test_pairs_by_the_stem_after_the_prefix():
    out = pair_dictionaries(["FRD_alpha_feed"], ["DICT_alpha_feed.xlsx"])
    assert out["pairs"] == {"FRD_alpha_feed": "DICT_alpha_feed.xlsx"}
    assert out["unpaired_dictionaries"] == []


def test_a_dictionary_with_no_matching_frd_is_unpaired_not_guessed():
    out = pair_dictionaries(["FRD_alpha"], ["DICT_beta.xlsx"])
    assert out["pairs"] == {}
    assert out["unpaired_dictionaries"] == ["DICT_beta.xlsx"]


def test_ambiguous_keys_are_never_paired():
    """Two dictionaries keying the same — a guess here would attach one
    vendor's PHI flags to another vendor's feed."""
    out = pair_dictionaries(["FRD_alpha"], ["DICT_alpha.xlsx", "DICT_ALPHA.xlsx"])
    assert out["pairs"] == {}
    assert out["ambiguous"] == ["alpha"]


def test_similarity_is_deliberately_not_a_fallback():
    """Unlike STTM pairing. A near-miss name yields NO dictionary."""
    out = pair_dictionaries(["FRD_member_risk_weekly"], ["DICT_member_risk.xlsx"])
    assert out["pairs"] == {}


# --------------------------------------------------------------------------- #
# the corpus index
# --------------------------------------------------------------------------- #
def _index(tmp_path, dictionary_dir, thresholds=None):
    ref = tmp_path / "ref"
    ref.mkdir(exist_ok=True)
    return build_corpus_index(
        [{"doc_id": "FRD_alpha", "source_file": "FRD_alpha.docx", "content": "alpha feed"}],
        ref,
        thresholds or {"pair_min": 0.1, "pair_high": 0.5,
                       "template_single_min": 0.15, "template_amalgam_min": 0.08},
        generated_at="2026-08-27T00:00:00+00:00",
        dictionary_dir=dictionary_dir,
    )


def test_index_carries_the_dictionary_summary_not_its_rows(tmp_path):
    d = tmp_path / "dicts"
    d.mkdir()
    write_dictionary(d / "DICT_alpha.xlsx", [_file_row("A.txt", "a")],
                     {"a": [_field_row(1, "x"), _field_row(2, "y")]})
    index = _index(tmp_path, d)
    entry = index["dictionaries"]["DICT_alpha.xlsx"]
    assert entry["n_files"] == 1 and entry["n_fields"] == 2
    assert entry["files"] == ["A.txt"]
    assert entry["content_sha256"]
    assert "fields" not in entry           # the index stays request-cheap
    assert index["dictionary_pairs"] == {"FRD_alpha": "DICT_alpha.xlsx"}
    assert dictionary_for(index, "FRD_alpha") == "DICT_alpha.xlsx"


def test_index_without_a_dictionary_dir_is_the_ordinary_state(tmp_path):
    index = _index(tmp_path, None)
    assert index["dictionaries"] == {}
    assert index["dictionary_pairs"] == {}
    assert dictionary_for(index, "FRD_alpha") is None


def test_index_version_bumped_for_the_third_input():
    """v3 added the dictionaries; v4 (same day) added per-FRD eligibility.

    The bump is deliberate each time rather than additive-and-silent: a stage
    reading an older index would see no dictionaries, or no eligibility, and
    would happily generate an FRD the rule now forbids.
    """
    assert CORPUS_INDEX_VERSION == 4


def test_unreadable_dictionary_is_named_in_the_index(tmp_path):
    d = tmp_path / "dicts"
    d.mkdir()
    bad = Workbook()
    bad.active.append(["not a dictionary"])
    bad.save(str(d / "DICT_alpha.xlsx"))
    index = _index(tmp_path, d)
    assert "DICT_alpha.xlsx" in index["dictionary_errors"]
    assert index["dictionary_pairs"] == {}


# --------------------------------------------------------------------------- #
# eligibility — the one rule that decides what may be generated
# --------------------------------------------------------------------------- #
def test_ready_needs_a_dictionary_and_no_sttm():
    v = eligibility_for("FRD_a", {}, {"FRD_a": "VDD_a.xlsx"})
    assert v["status"] == "ready" and v["generatable"] is True
    assert v["dictionary"] == "VDD_a.xlsx" and v["reference"] is None


def test_an_already_mapped_frd_is_not_generatable():
    """The approved STTM is the system of record; regenerating over it also
    produced a self-referential accuracy figure, because the approved workbook
    is the template the render borrows from."""
    v = eligibility_for("FRD_a", {"FRD_a": {"reference": "STTM_a.xlsx"}},
                        {"FRD_a": "VDD_a.xlsx"})
    assert v["status"] == "mapped" and v["generatable"] is False
    assert "system of record" in v["reason"]
    # the dictionary is still reported — the screen shows both documents
    assert v["dictionary"] == "VDD_a.xlsx"


def test_mapped_beats_no_dictionary():
    """An FRD with an STTM but no dictionary is MAPPED, not blocked: it has
    nothing to wait for, and telling a reviewer to chase a vendor for a feed
    that is already mapped would be noise."""
    v = eligibility_for("FRD_a", {"FRD_a": {"reference": "STTM_a.xlsx"}}, {})
    assert v["status"] == "mapped" and v["generatable"] is False


def test_no_dictionary_is_not_generatable_and_says_what_to_ask_for():
    v = eligibility_for("FRD_a", {}, {})
    assert v["status"] == "no_dictionary" and v["generatable"] is False
    assert "VDD_<feed>.xlsx" in v["reason"]


def test_a_plain_string_pair_is_accepted_too():
    """pair_corpus yields {doc: {reference: ...}}; a caller holding the flat
    {doc: name} shape must not silently read as unmapped."""
    assert eligibility_for("FRD_a", {"FRD_a": "STTM_a.xlsx"}, {})["status"] == "mapped"


def test_an_unknown_doc_id_is_never_generatable():
    """Asking about a document the corpus has never seen must not answer yes."""
    v = eligibility_of({"eligibility": {}}, "FRD_ghost")
    assert v["generatable"] is False and "not in the corpus index" in v["reason"]


def test_index_carries_eligibility_and_the_generatable_list(tmp_path):
    d = tmp_path / "dicts"
    d.mkdir()
    write_dictionary(d / "VDD_alpha.xlsx", [_file_row("A.txt", "a")],
                     {"a": [_field_row(1, "x")]})
    index = _index(tmp_path, d)
    assert index["generatable"] == ["FRD_alpha"]
    assert index["eligibility"]["FRD_alpha"]["status"] == "ready"


def test_no_dictionary_means_nothing_is_generatable(tmp_path):
    index = _index(tmp_path, None)
    assert index["generatable"] == []
    assert index["eligibility"]["FRD_alpha"]["status"] == "no_dictionary"


def test_vdd_and_dict_prefixes_key_identically(tmp_path):
    """VDD_ is the convention; DICT_ is the template already issued to vendors.
    Both must pair to the same FRD — and two spellings of the same feed must
    NOT both pair, which the ambiguity rule already prevents."""
    from frdsttm.similarity import name_key
    assert name_key("VDD_alpha.xlsx") == name_key("DICT_alpha.xlsx") == name_key("FRD_alpha.docx")
    out = pair_dictionaries(["FRD_alpha"], ["VDD_alpha.xlsx", "DICT_alpha.xlsx"])
    assert out["pairs"] == {} and out["ambiguous"] == ["alpha"]
