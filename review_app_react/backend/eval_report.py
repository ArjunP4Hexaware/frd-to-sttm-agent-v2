"""Reads the already-computed golden-pair eval figure out of a run's
`<doc_id>.phase5.md` render report.

This module only *reads* a number 04_sttm_render.py already computed and
printed. It does not recompute, approximate, or reconstruct it -- see
`evaluate_against_reference()` in that notebook for the computation, which
is deliberately untouched.

Why parse markdown rather than read a table
-------------------------------------------
04_sttm_render.py does write a structured sink (the `frd_sttm_runs`
Delta table: `eval_pct`, `eval_cells`, ...), and that would normally be the
better source. It is not usable here for two reasons:

1. It carries the denominator (`eval_cells`) and the rounded percentage,
   but **not the numerator**. Recovering the match count from a percentage
   rounded to one decimal is a reconstruction, not a reading: at 3288 cells
   a +-0.05pp rounding band spans ~3.3 cells, so the recovered numerator
   can be off by one or two. That is exactly the kind of quietly-wrong
   number this figure must never be.
2. Reading it would add `deltalake`/`pyarrow` to a backend whose
   requirements.txt is four lines, and in Databricks mode the table is a UC
   table this process has no Spark session for.

`phase5.md` is the only artifact carrying the numerator and denominator as
written. **This parse is therefore a maintenance liability**: it is coupled
to one f-string in 04_sttm_render.py (the `**Golden-pair eval: ...**` line).
If that line's wording changes, this returns "unavailable" -- by design, it
degrades to nothing rather than to a wrong number. The durable fix is to
add an `eval_match` column to the runs table and read that instead.

The parse is defended three ways: the pattern is anchored to a whole line,
exactly one match is required, and the percentage printed on the line must
agree with the percentage recomputed from the numerator and denominator.
A partial or coerced match fails all the way to None.
"""

from __future__ import annotations

import re
from pathlib import Path

# Matches, as one whole line:
#   **Golden-pair eval: 94.1%** (3094/3288 target cells match the reference)
# Whitespace-tolerant everywhere it can be, anchored at both ends so a
# truncated or reworded line does not partially match.
# Two shapes, one regex. Before the template architecture (2026-08-22) 04
# wrote `**Golden-pair eval: 95.5%** (…)`; since then it names the reference
# workbook: `**Golden-pair eval vs <reference>: 95.5%** (…)`. This parser
# only knew the first, so the App's eval panel was blank on every templated
# run for five days — found 2026-08-27 on the first real-pair workspace run
# (phase5 said 95.5%, the results payload said `available: False`). The
# reference name is matched lazily up to the LAST `: <pct>%` so names with
# spaces, hyphens, underscores or dots all pass; it is captured, not used.
_EVAL_LINE = re.compile(
    r"^\*\*Golden-pair\s+eval(?:\s+vs\s+(?P<ref>.+?))?:\s*(?P<pct>\d+(?:\.\d+)?)\s*%\*\*\s*"
    r"\(\s*(?P<match>\d+)\s*/\s*(?P<cells>\d+)\s+target\s+cells\s+match\s+the\s+reference\s*\)\s*$",
    re.MULTILINE,
)


def phase5_report_path(out_root: Path | str, doc_id: str) -> Path:
    """Same reports/<doc_id>.<suffix>.md layout orchestration.py already uses
    for the FAIL path's report.md."""
    return Path(out_root) / "reports" / f"{doc_id}.phase5.md"


def parse_eval_totals(text: str) -> tuple[int, int] | None:
    """(matched_cells, total_cells) from a phase5.md body, or None.

    None on every abnormality -- line absent, reworded, duplicated,
    zero-denominator, numerator above denominator, or a printed percentage
    that disagrees with the one implied by the two counts. Callers render
    nothing on None; there is deliberately no partial or best-effort return.
    """
    matches = list(_EVAL_LINE.finditer(text))
    if len(matches) != 1:
        # 0 = absent or reworded; >1 = ambiguous, refuse to pick.
        return None

    m = matches[0]
    try:
        printed_pct = float(m.group("pct"))
        matched_cells = int(m.group("match"))
        total_cells = int(m.group("cells"))
    except ValueError:
        return None

    if total_cells <= 0 or matched_cells < 0 or matched_cells > total_cells:
        return None

    # Self-consistency: the line carries the same fact twice (a percentage
    # and the pair it came from). Requiring them to agree is what makes a
    # silently-misparsed number essentially impossible -- it would have to
    # misread the counts in exactly the way that reproduces the percentage.
    # Same round(..., 1) the notebook used to print it.
    if round(100 * matched_cells / total_cells, 1) != printed_pct:
        return None

    return matched_cells, total_cells


def read_eval_totals(out_root: Path | str, doc_id: str) -> tuple[int, int] | None:
    """Same contract as parse_eval_totals(), reading from disk.

    Returns None (never raises) when the report is missing or unreadable --
    the FAIL path never produces one, and neither does a run whose feeds
    didn't match a reference workbook.
    """
    path = phase5_report_path(out_root, doc_id)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_eval_totals(text)
