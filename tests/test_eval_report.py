"""eval_report.parse_eval_totals — the ONLY reader of 04's golden-pair line.

The results payload's `eval` block and the run list's `eval_pct` both come
from this one regex over `reports/<doc_id>.phase5.md`. It knew only the
pre-template line shape, so from 2026-08-22 (when 04 started naming the
reference workbook on that line) until 2026-08-27 every templated run's
eval was silently `available: False` in the App while the report said 95%.
These tests pin both shapes and the refusals.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "review_app_react" / "backend"))

import eval_report as er  # noqa: E402

TAIL = "%** (3141/3288 target cells match the reference)"


@pytest.mark.parametrize("line", [
    # the shape 04 writes since the template architecture — reference named
    "**Golden-pair eval vs STTM_Synthetic_Feed-ABC_-_Vendor_Name.xlsx: 95.5" + TAIL,
    # …with spaces, dots and hyphens in the name (local-folder file names)
    "**Golden-pair eval vs STTM_Synthetic Feed - Vendor Name v2.1.xlsx: 95.5" + TAIL,
    # the pre-template shape must keep parsing (older artifact sets replay)
    "**Golden-pair eval: 95.5" + TAIL,
    # whitespace tolerance
    "**Golden-pair   eval  vs  X.xlsx :  95.5 %**  ( 3141 / 3288  target cells match the reference )",
])
def test_parses_both_line_shapes(line):
    text = f"# STTM render — doc\n\n**Status: PASS** | dialect: sheet_per_table\n{line}\n\n## Next\n"
    assert er.parse_eval_totals(text) == (3141, 3288)


@pytest.mark.parametrize("line", [
    "**No eval** — no paired reference workbook for this document",
    "**Golden-pair eval vs X.xlsx: 95.5** (3141/3288 target cells match the reference)",  # missing %
    "**Golden-pair eval vs X.xlsx: 90.0** (3141/3288 target cells match the reference)",  # pct disagrees
    "**Golden-pair eval vs X.xlsx: 100.0** (3300/3288 target cells match the reference)",  # num > denom
    "**Golden-pair eval vs X.xlsx: 0.0** (0/0 target cells match the reference)",  # zero denominator
])
def test_refuses_absent_reworded_or_inconsistent(line):
    assert er.parse_eval_totals(f"**Status: PASS**\n{line}\n") is None


def test_refuses_two_eval_lines():
    line = "**Golden-pair eval vs X.xlsx: 95.5" + TAIL
    assert er.parse_eval_totals(f"{line}\n{line}\n") is None


def test_read_from_disk_layout(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "doc.phase5.md").write_text(
        "**Golden-pair eval vs STTM_X.xlsx: 95.5" + TAIL + "\n", encoding="utf-8")
    assert er.read_eval_totals(tmp_path, "doc") == (3141, 3288)
    assert er.read_eval_totals(tmp_path, "missing") is None


# --------------------------------------------------------------------------
# the FUNCTIONAL line (2026-08-27 evening) — rows, not cells; naming and
# cosmetic counts ride along and never fold into the score
# --------------------------------------------------------------------------
FUNC = "**Functional eval vs STTM_X.xlsx: 98.5%** (405/411 rows structurally correct; 3 named differently, 82 cosmetic)"


def test_functional_line_parses_with_its_side_counts():
    ev = er.parse_eval(f"# STTM render — doc\n\n**Status: PASS**\n{FUNC}\n")
    assert ev == {"kind": "functional", "match": 405, "cells": 411, "pct": 98.5,
                  "naming": 3, "cosmetic": 82}
    assert er.parse_eval_totals(FUNC + "\n") == (405, 411)


def test_functional_line_without_side_counts_still_parses():
    ev = er.parse_eval("**Functional eval vs X.xlsx: 100.0%** (4/4 rows structurally correct)\n")
    assert ev["kind"] == "functional" and (ev["match"], ev["cells"]) == (4, 4)
    assert ev["naming"] is None and ev["cosmetic"] is None


def test_old_cells_line_reports_its_kind():
    ev = er.parse_eval("**Golden-pair eval vs X.xlsx: 95.5" + TAIL + "\n")
    assert ev["kind"] == "cells" and (ev["match"], ev["cells"]) == (3141, 3288)


def test_functional_line_refuses_inconsistent_percentage():
    assert er.parse_eval("**Functional eval vs X.xlsx: 90.0%** (405/411 rows structurally correct; 3 named differently, 82 cosmetic)\n") is None
