"""Build the FRD + Vendor Data Dictionary INPUT REQUIREMENTS deck (PPTX).

    ~/.virtualenvs/frdsttm/bin/python scripts/build_input_requirements_deck.py [out.pptx]

Default output: context/FRD-and-Dictionary-Input-Requirements.pptx

AUDIENCE: the BSAs who author ACFC's FRDs, and their EDO leads.
PURPOSE: say exactly what the agent needs from the FRD and from the vendor,
without asking for a single change to the ACFC FRD template.

**THREE SLIDES, HARD CAP (Arjun, 2026-08-27.)** The first version ran to 14 and
was cut on the ground that nobody presents 14 slides. What survived is chosen
by one rule: keep what a room cannot reconstruct for itself. So the fill-rate
EVIDENCE stayed (nobody has counted those three FRDs), the two dictionary
sheets stayed (nobody knows what to ask a vendor for), and everything that was
narration — the loop diagram, the authoring-defect list, the "can't it read a
sample file" objection, the closing checklist — went into the speaker notes or
went entirely. If a slide comes back, another has to leave.

  1  The division of labour + the one ask     — why, and what to do
  2  The FRD half: eleven rows, measured      — the evidence slide
  3  The VDD half: both sheets, column by column

Every claim is measured, not asserted:
  * fill rates are counted across the three real FRDs on hand (SD, CAQH, SFMC);
  * 399/399 and 22/115 come from contracts/naming_standards.json v1.1.0, which
    measured them against the two approved STTMs;
  * the dictionary column lists are the core columns of templates/DICT_TEMPLATE,
    built by scripts/build_dict_template.py, and now parsed for real by
    src/frdsttm/dictionary.py.

Style: the ACFC house style in scripts/acfc_theme.py (palette sampled live
from amerihealthcaritas.com; Segoe UI; square corners; flat bands; the client
LOGO deliberately never used).

Speaker notes are attached to each slide — the cut material lives there, so
the deck reads in three slides and still answers a follow-up question.

NO person names, e-mail addresses, or record-level content from any client
document appear on any slide.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acfc_theme import (  # noqa: E402 — the shared ACFC house style, one source
    BLUE, NAVY, SKY, RED, INK, SLATE, MUTED, BAND, HAIR, HAIR2, WHITE,
    TINT_BLUE, TINT_SKY, TINT_RED, SANS, MONO, W_IN, H_IN, L, R, CW, BOTTOM,
    rect, line, text, square, outline_chip, flag_bars, new_slide, head, foot,
    panel, panel_title, bullets, table, callout, notes, acfc_logo,
)

REPO = Path(__file__).resolve().parent.parent
OUT_DEFAULT = REPO / "context" / "FRD-and-Dictionary-Input-Requirements.pptx"


# --------------------------------------------------------------------------- #
# slides
# --------------------------------------------------------------------------- #
def s01_why(prs):
    s = new_slide(prs)
    # The mark sits on slide 1 only. Repeating a full lockup on every slide of
    # a three-slide deck is noise, and at the size a running header allows it
    # renders as mud (see acfc_theme.acfc_logo's caution).
    acfc_logo(s, R - 1.42, 0.38, 1.42)
    y = head(s, "the shape of the problem", "Two documents in — and one thing to change",
             "An STTM answers two kinds of question. The FRD answers one of them well and the "
             "other not at all — by design, not by neglect. Everything else the agent brings itself.")
    w, gap = 5.75, 0.33
    specs = [
        (BLUE, TINT_BLUE, "document 1 · authored by ACFC", "The FRD", "the feed-level frame",
         ["File name pattern and format", "Delimiter, header row, encoding",
          "Landing folder in ADLS", "Target schema + table, per layer",
          "Load strategy, per layer", "DQ, reject and recycle rules"],
         "Names ~2 columns."),
        (SKY, TINT_SKY, "document 2 · supplied by the vendor", "The Vendor Data Dictionary",
         "every source column",
         ["Column name and position", "Data type and length",
          "Required / nullable", "Description — what it means",
          "Allowed values and ranges", "PHI / PII flag"],
         "Its STTM has ~410 column rows."),
    ]
    for i, (c, tint, kick, title, sub, items, note) in enumerate(specs):
        x = L + i * (w + gap)
        panel(s, x, y, w, 2.72, accent=c, fill=tint)
        panel_title(s, x, y, w, title, kicker=kick, accent=BLUE if c == SKY else c)
        text(s, x + 0.30, y + 0.84, w - 0.5, 0.24, [(sub, {})],
             size=10.5, color=BLUE if c == SKY else c, font=SANS, bold=True)
        for j, item in enumerate(items):
            col, row = j % 2, j // 2
            square(s, x + 0.30 + col * 2.72, y + 1.22 + row * 0.34, 0.075, c)
            text(s, x + 0.48 + col * 2.72, y + 1.15 + row * 0.34, 2.5, 0.28, [(item, {})],
                 size=9.4, color=SLATE, font=SANS)
        line(s, x + 0.30, y + 2.30, x + w - 0.26, y + 2.30, color=HAIR2)
        text(s, x + 0.30, y + 2.40, w - 0.52, 0.24, [(note, {})],
             size=9.3, color=NAVY, font=SANS, bold=True)

    # The standards are NOT a third input (Arjun, 2026-08-27): they are
    # transcribed into the agent as versioned contracts. Drawn as a strip
    # beneath the two documents, not as a third card beside them.
    ky = y + 2.90
    panel(s, L, ky, CW, 0.82, accent=NAVY, fill=BAND)
    text(s, L + 0.32, ky + 0.12, 4.4, 0.22, [("BUILT INTO THE AGENT — NOT HANDED OVER", {})],
         size=8.2, color=NAVY, font=SANS, bold=True, spacing=1.1)
    text(s, L + 0.32, ky + 0.34, CW - 0.7, 0.44,
         [[("ACFC's naming + engineering standards", {"bold": True, "color": NAVY}),
           ("  are versioned contracts the agent ships with, hashed into every run. So is the ", {}),
           ("term catalog", {"bold": True, "color": NAVY}),
           (" — the warehouse's own column vocabulary, harvested from approved STTMs when they "
            "sync. Nobody attaches either to a feed, and ", {}),
           ("a run never opens an STTM", {"bold": True, "color": RED}),
           (".", {})]],
         size=10, color=SLATE, font=SANS, line_spacing=1.18)

    yy = y + 3.78
    panel(s, L, yy, CW, 1.22, accent=SKY, fill=TINT_SKY)
    text(s, L + 0.32, yy + 0.18, CW - 0.7, 0.30,
         [("The one thing to change — and it is not the template.", {})],
         size=15, color=NAVY, font=SANS, bold=True, spacing=-0.2)
    text(s, L + 0.32, yy + 0.54, CW - 0.7, 0.58,
         [[("Ask the vendor for ", {"color": SLATE}),
           ("VDD_<feed>.xlsx", {"font": MONO, "bold": True, "color": BLUE}),
           (", put it beside ", {"color": SLATE}),
           ("FRD_<feed>.docx", {"font": MONO, "bold": True, "color": BLUE}),
           (" in the library, and name it in the FRD row that already exists for it: ", {"color": SLATE}),
           ("Structural Metadata › Source Data Dictionary", {"font": MONO, "bold": True, "color": NAVY}),
           (". No new section, no new field, no re-approval of the template.", {"color": SLATE})]],
         size=11, color=SLATE, font=SANS, line_spacing=1.22)
    foot(s, 1, "docs/THREE_INPUT_ARCHITECTURE.md · the agent ingests VDD_ workbooks as of 2026-08-27")
    notes(s, """
WHY THE ROW IS EMPTY TODAY — the circular reference. Structural Metadata >
Source Data Dictionary, verbatim in all three real FRDs on hand:
  SD    "File and field descriptions are mentioned in the mapping document."
  SFMC  the same sentence, word for word - it is the blank template's default text
  CAQH  "Refer CAQH STTM"
The STTM is the agent's OUTPUT. An input that points at the output is a circular
reference; no prompt closes it, and no model should be asked to invent 410 column
names and descriptions.

IF A VENDOR CANNOT SUPPLY ONE, that is a real answer too. The agent renders the
feed-level frame and raises one gated question naming the file whose columns it
could not ground - a workbook with a visible gap, never one with invented names.

THE STANDARDS ARE NOT A PER-RUN INPUT. They are versioned config in the agent
(contracts/naming_standards.json + engineering_standards.json), hashed into every
run as standards_sha256. Nobody hands them over with an FRD.

THE PAIRING ALREADY WORKS: the sync pairs FRD_X to STTM_X by the stem after the
prefix. VDD_X joins the same convention, into its own volume (vdd_raw), and
the picker shows the pairing with a column count.
""")


def s02_frd(prs):
    s = new_slide(prs)
    y = head(s, "the FRD half", "Structural Metadata — the eleven rows you already have",
             "“Filled” counts the three real FRDs on hand. Nothing below asks for a new row.")
    cols = [("template row", 2.55, {"bold": True, "color": NAVY, "size": 9.8}),
            ("what it decides in the STTM", 5.35, {"size": 9.5}),
            ("filled", 0.72, {"size": 9.5, "font": MONO, "bold": True, "align": PP_ALIGN.CENTER}),
            ("how to fill it", 3.21, {"size": 9.5, "color": MUTED})]
    G = {"color": RED, "bold": True}
    rows = [
        ["Object/data Format", "How the file is parsed at all — format and delimiter",
         [("3 / 3", {"color": BLUE})], "Give the delimiter literally: “Pipe ( | ) .txt”"],
        ["Target Schema", "Stage and standard schema; catalog if known",
         [("3 / 3", {"color": BLUE})], "Name BOTH layers — “Stage: … / Standard: …”"],
        ["Target Table Name", "The target tables, one per record segment",
         [("2 / 3", {"color": BLUE})], [("Tables, never file names", G)]],
        ["Domain and Sub-domain", "Schema derivation, and the landing-path shape",
         [("3 / 3", {"color": BLUE})], "Use a term from the EDO domain table"],
        ["Load Strategy STG", "How the stage table is loaded",
         [("3 / 3", {"color": BLUE})], "One of the four sanctioned strategies"],
        ["Load Strategy STD", "How the standard table is loaded",
         [("3 / 3", {"color": BLUE})], "“Upsert” = Update Else Insert; both accepted"],
        ["Load Strategy Consumption", "Out of scope for an ingest feed",
         [("1 / 3", {"color": MUTED})], "“Not Applicable” beats a blank"],
        ["Archive Schedule", "Retention note carried onto the workbook",
         [("1 / 3", {"color": MUTED})], "State files and data separately"],
        ["Source Data Dictionary",
         [("The vendor dictionary the source side is built from", {"bold": True, "color": NAVY})],
         [("0 / 3", {"color": RED})], [("Name the VDD_<feed>.xlsx here", {"font": MONO, **G})]],
        ["ADLS Location", "Where the landing-zone check looks for the file",
         [("2 / 3", {"color": BLUE})],
         [("mftlanding/inbound/<domain>/<sub>/<vendor>", {"font": MONO, "size": 8.8})]],
        ["Inbound File Folder Path", "History vs incremental delivery route",
         [("2 / 3", {"color": BLUE})], [("A route, not a direction", G)]],
    ]
    table(s, L, y, cols, rows, bottom=6.02, size=9.4)
    callout(s, L, 6.10, CW, 0.70,
            [("Zero of three ", {"bold": True, "color": RED}),
             ("FRDs name a data dictionary — every one names the STTM instead. That single row is "
              "the difference between a mapping the agent DERIVES and a mapping it COPIES.", {})])
    foot(s, 2, "Row names verbatim from FRD_Enhanced_Metadata_Template.docx")
    notes(s, """
FOUR AUTHORING HABITS THAT BREAK THE DERIVATION - all four are in FRDs that have
already been approved and mapped, so none of this is hypothetical:

1  FILE NAMES IN THE TARGET-TABLE ROW. One FRD leaves Object Name blank and puts
   demographics_package_YYYY_MM.csv under Target Table Name. The agent then looks
   for a target table whose name is a .csv, and has nothing to match a delivered
   file against. Object Name = the file patterns. Target Table Name = the tables.

2  DQ RULES IN THE PROSE, "NA" IN THE ROWS. The same FRD answers "NA" to all three
   HARD data-quality rows while its Description carries two real reject rules and a
   7-day recycle window. A rule reachable only by reading prose must be gated.

3  A BLANK LANDING FOLDER. Two of three give the ADLS path; the third leaves it
   empty and answers the adjacent row with "Inbound" - a direction, not a location.
   The shape is guessable; where PHI actually lands is not something to guess.

4  A DOMAIN OUTSIDE THE CLIENT'S OWN VOCABULARY. One FRD's domain is not in the EDO
   naming standard's domain table, so the stage schema cannot be derived from it.
   The agent falls through to the FRD's wording and gates rather than inventing.

THE OTHER BLOCKS THE AGENT READS: Descriptive Metadata > Object Name (the file
NAME PATTERNS - blank in 2 of 3) and Frequency. Technical Metadata > Business
Rules, Filter Criteria, PII Fields, Critical Data Elements - a rule lands on the
STTM row whose column it NAMES, so name the columns. Data Quality > the three HARD
rows. Vendor Metadata > Vendor Name and Abbreviation.
""")


def s03_vdd(prs):
    s = new_slide(prs)
    y = head(s, "the VDD half", "What the Vendor Data Dictionary must contain",
             "One Excel workbook per feed, two kinds of sheet. Nothing in it is an ACFC decision — "
             "every cell is something the vendor already knows about its own file.")
    w, gap = 5.75, 0.33

    # ---- FILES
    panel(s, L, y, w, 3.90, accent=BLUE, fill=TINT_BLUE)
    panel_title(s, L, y, w, "FILES — one row per delivered file",
                kicker="sheet 1 · usually 1–5 rows")
    files = [
        ("File Name Pattern", "matches a delivered file to this feed"),
        ("File Title", "the human name; names the sheet it maps into"),
        ("Format", "delimited · fixed-width · csv · json"),
        ("Delimiter", "the literal character"),
        ("Header Row", "Y/N — does row 1 carry COLUMN NAMES?"),
        ("Encoding", "UTF-8, Latin-1"),
        ("Delivery Cadence", "cross-checked against the FRD"),
        ("Content Description", "what ONE ROW of this file means"),
        ("Field Sheet", "the tab holding this file's columns"),
        ("Null Representation", "what a MISSING value looks like in the file"),
    ]
    ty = y + 0.86
    for name, what in files:
        square(s, L + 0.32, ty + 0.065, 0.07, BLUE)
        text(s, L + 0.50, ty - 0.02, 1.90, 0.24, [(name, {})],
             size=9.0, color=NAVY, font=MONO, bold=True)
        text(s, L + 2.46, ty - 0.02, w - 2.78, 0.24, [(what, {})],
             size=8.8, color=SLATE, font=SANS)
        ty += 0.240
    line(s, L + 0.32, y + 3.50, L + w - 0.28, y + 3.50, color=HAIR2)
    text(s, L + 0.32, y + 3.58, w - 0.62, 0.28,
         [[("Multi-record files add:", {"bold": True, "color": NAVY}),
           ("  Multi-Record-Type · Record Type Field · Record Type Values · "
            "Expected Field Count", {"color": SLATE})]],
         size=8.4, color=SLATE, font=SANS)

    # ---- field sheets
    x2 = L + w + gap
    panel(s, x2, y, w, 3.90, accent=SKY, fill=TINT_SKY)
    panel_title(s, x2, y, w, "Field sheets — one row per source column",
                kicker="sheet 2 · one tab per file", accent=BLUE)
    NO, YES = {"color": RED, "bold": True}, {"color": BLUE, "bold": True}
    fields = [
        ("Position", [("from the file", YES)]),
        ("Field Name", [("only if row 1 has headers", NO)]),
        ("Data Type", [("guessable, not knowable", NO)]),
        ("Length", [("not from the data", NO)]),
        ("Required (Y/N)", [("not from the data", NO)]),
        ("Description", [("never — meaning is not in the data", NO)]),
        ("Allowed Values / Range", [("never — a sample is not a domain", NO)]),
        ("Example Value", [("the vendor's, never a real member's", YES)]),
        ("PHI / PII (Y/N)", [("never — a compliance answer", NO)]),
        ("Key / Uniqueness", [("never — the match key an Upsert needs", NO)]),
        ("Segment", [("multi-record files only", {"color": MUTED})]),
    ]
    ty = y + 0.86
    for name, mark in fields:
        square(s, x2 + 0.32, ty + 0.065, 0.07, RED if mark[0][1].get("color") == RED else BLUE)
        text(s, x2 + 0.50, ty - 0.02, 1.94, 0.24, [(name, {})],
             size=9.0, color=NAVY, font=MONO, bold=True)
        text(s, x2 + 2.50, ty - 0.02, w - 2.82, 0.24, [mark],
             size=8.6, color=SLATE, font=SANS)
        ty += 0.240
    line(s, x2 + 0.32, y + 3.50, x2 + w - 0.28, y + 3.50, color=HAIR2)
    text(s, x2 + 0.32, y + 3.58, w - 0.62, 0.24,
         [("Blue = a data file could tell us.   Red = only the vendor can.", {})],
         size=8.4, color=MUTED, font=SANS)

    callout(s, L, y + 4.10, CW, 0.84,
            [[("Eight of these eleven can never be recovered from a data file.",
               {"bold": True, "color": RED})],
             [("They exist only in the vendor's specification. Where the dictionary is missing "
               "them the agent leaves the cell blank and raises a named question — it will not "
               "write a description, a null rule or a key it inferred.", {})]])
    foot(s, 3, "templates/DICT_TEMPLATE_v1.3.xlsx — issue it to the vendor as-is; src/frdsttm/dictionary.py parses what comes back")
    notes(s, """
"CAN'T THE AGENT JUST READ A SAMPLE FILE?" It can, and it does - as a CHECK, never
as the source of the mapping. Given the FRD's ADLS Location, code (not the model -
the rows are PHI) confirms the field count, the delimiter and the encoding, flags a
column the dictionary missed and one the file does not have. It fills NO names for
a headerless file and no meaning for any file.
The worked case: one real feed is a headerless, pipe-delimited file carrying three
record types in one stream. Reading it yields "101 fields in the detail record" and
not one field name.

WHAT ONE DICTIONARY ROW BECOMES. Position 14 / Member ID / varchar / 20 / Y /
"Health-plan member identifier" / M000123456 / PHI Y  ->  eleven STTM cells: six
straight from the vendor's row (source column, datatype+length, description,
sample value, mandatory, PHI), four derived from the standards plus the FRD's
frame (stage catalog/schema/table, stage column+datatype, standard schema/table,
standard datatype) - and exactly one that is neither: the TARGET COLUMN NAME.

THE TARGET COLUMN NAME, MEASURED - and this is the one cell neither document
supplies. The agent decides from the FRD ALONE whether a feed renames: if the
sub-domain appears in the target table names, the columns carry it too. That holds
on both real feeds (CAQH renames 115/115, SD 4/411).

What the reviewer then receives, on the renaming feed: 22 of 115 exact, 63 more
recognisable renames - so 85 of 115 (74%) are a NAME EDIT, not a rebuild. Mean
similarity 0.79. TPL_FILE_SEQUENCE_NUMBER -> TPL_FILE_SEQ_NO is the shape of it;
the row, table, position and source field are already correct. One "miss",
TPL_DATA_SET_ID vs the approved TPL_DATE_SET_ID, is a typo in the client's own
workbook. On a feed that does not rename, the agent is at 388/391.

Asking a model to GENERATE the name scored 0 of 115 - and marked 71 of those wrong
answers high confidence. Given a real catalog to match against it scored 95/101
with a high band right 92 of 92. So the missing input is not a document and not a
better prompt: it is read access to the existing warehouse columns.

SCALE: the worked dictionary built from one approved STTM holds 399 fields across
three files (86 / 267 / 46). That is the volume the FRD cannot carry.
""")


def build(out: Path) -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W_IN), Inches(H_IN)
    for fn in (s01_why, s02_frd, s03_vdd):     # THREE. See the module docstring.
        fn(prs)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    print(build(target))
