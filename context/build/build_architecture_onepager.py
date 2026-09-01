"""Build the FRD→STTM one-slide SOLUTION ARCHITECTURE deck (PPTX).

    ~/.virtualenvs/frdsttm/bin/python scripts/build_architecture_onepager.py [out.pptx]

Default output: context/FRD-to-STTM-Agent-Solution-Architecture.pptx

REDESIGNED 2026-08-27 (Arjun), on two axes at once:

1. STYLE — the "Signal" theme (dark-then-white canvas, algorithmic flow-field
   background, Claude-orange / turquoise / gold / indigo semantic code) is
   retired for this deck in favour of the ACFC house style in
   scripts/acfc_theme.py: the client's own blue/navy/flag-red palette sampled
   from their site, Segoe UI, square corners, flat bands, no background art.
   scripts/deck_assets/ONEPAGER_DESIGN.md remains the record of the old system.

2. ARCHITECTURE — two real changes since the previous version:

   a. TWO DOCUMENT INPUTS. The vendor data dictionary (VDD_<source>.xlsx) is a
      first-class input beside the FRD, and the client's naming + engineering
      standards are versioned contracts hashed into every run's provenance.
      Honesty rule kept from the old deck: the standards contracts ARE wired
      (02's extraction_meta, 04's target derivation, frd_sttm_runs.
      standards_sha256); the DICT_ ingestion is DESIGNED, NOT BUILT, and the
      slide says so with a "designed" chip rather than implying it ships.

   b. THE APP READS UNITY CATALOG, NOT SHAREPOINT. In the deployed App
      `IS_DATABRICKS_APP` short-circuits every sync path to
      backend/jobs_runner.py, and reads mirror the volumes down with the SDK
      (`corpus_routes._mirror_from_uc` → `jobs_runner.mirror_corpus`).
      `corpus_routes._source_client` — the SharePoint / local-folder chooser —
      is not consulted there at all. SharePoint is therefore one of three
      interchangeable LOADERS upstream of the volumes, behind a single duck
      type, not the request path. Today's loader is the direct volume push
      (tools/push_local_source_to_volumes.py); Graph access never arrived.

Every statement on the slide is true of `staging` as of 2026-08-27, and each
element that is designed rather than built carries a chip saying so.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acfc_theme import (  # noqa: E402
    BLUE, NAVY, SKY, RED, INK, SLATE, MUTED, BAND, HAIR, HAIR2, WHITE,
    TINT_BLUE, TINT_SKY, TINT_RED, SANS, MONO, W_IN, H_IN,
    rect, line, text, square, outline_chip, flag_bars, logo, icon, arrow, rgb,
    acfc_logo,
)

REPO = Path(__file__).resolve().parent.parent
OUT_DEFAULT = REPO / "context" / "FRD-to-STTM-Agent-Solution-Architecture.pptx"

L, R = 0.72, W_IN - 0.72
CW = R - L


# --------------------------------------------------------------------------- #
def band(s, y, h, label, *, accent=BLUE, fill=BAND):
    rect(s, L, y, CW, h, fill=fill)
    rect(s, L, y, 0.05, h, fill=accent)
    text(s, L + 0.24, y + 0.13, 5.0, 0.2, [(label.upper(), {})],
         size=8.5, color=accent, font=SANS, bold=True, spacing=1.4)


def step_down(s, y1, y2, caption, *, x=None):
    ax = x if x is not None else L + 2.2
    arrow(s, ax, y1, ax, y2, color=NAVY, width=1.75)
    text(s, ax + 0.20, y1 + (y2 - y1) / 2 - 0.11, CW - 2.0, 0.24, [(caption, {})],
         size=9.5, color=SLATE, font=SANS, bold=True)


def tile(s, x, y, w, h, *, name, sub, mark=None, ico=None, status=None,
         status_color=BLUE, accent=BLUE):
    rect(s, x, y, w, h, fill=WHITE, line=HAIR2)
    rect(s, x, y, w, 0.04, fill=accent)
    if mark:
        logo(s, mark, x + 0.16, y + 0.18, 0.34, 0.34)
    elif ico:
        icon(s, ico, x + 0.16, y + 0.18, 0.34)
    text(s, x + 0.58, y + 0.16, w - 0.74, 0.24, [(name, {})],
         size=10.2, color=NAVY, font=MONO, bold=True)
    if status:
        text(s, x + 0.58, y + 0.40, w - 0.74, 0.20, [(status.upper(), {})],
             size=7.4, color=status_color, font=SANS, bold=True, spacing=1.0)
    text(s, x + 0.16, y + 0.62, w - 0.32, 0.46, [(sub, {})],
         size=8.2, color=SLATE, font=SANS, line_spacing=1.12)


def build(out: Path) -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W_IN), Inches(H_IN)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    rect(s, 0, 0, W_IN, H_IN, fill=WHITE)
    rect(s, 0, 0, W_IN, 0.20, fill=NAVY)
    rect(s, 0, 0, 0.10, H_IN, fill=BLUE)

    # ---- header: the client's own mark (cleared for use 2026-08-27) --------
    acfc_logo(s, L, 0.34, 1.30)
    line(s, L + 1.52, 0.38, L + 1.52, 1.26, color=HAIR2)
    text(s, L + 1.72, 0.38, 8.4, 0.24,
         [("AMERIHEALTH CARITAS  ·  AI-IN-ENGINEERING  ·  AGENT 01 / 03", {})],
         size=9, color=BLUE, font=SANS, bold=True, spacing=1.4)
    text(s, L + 1.72, 0.60, 9.2, 0.42,
         [("FRD → STTM Agent", {"color": NAVY, "bold": True, "size": 24, "spacing": -0.4}),
          ("    solution architecture", {"color": MUTED, "size": 13})], size=24)
    x = L + 1.72
    for t, c in (("two document inputs", BLUE), ("unity catalog is the store", BLUE),
                 ("one model call", RED)):
        x = outline_chip(s, x, 1.02, t, color=c, size=8)
    text(s, R - 2.5, 0.42, 2.5, 0.52,
         [[("Built by Hexaware", {"bold": True, "color": NAVY})],
          [("rebuilt inside the ACFC environment", {"color": MUTED})]],
         size=8.2, color=MUTED, font=SANS, align=PP_ALIGN.RIGHT, line_spacing=1.18)

    # ---- band A: EXACTLY TWO inputs -----------------------------------------
    # Two, not four (Arjun, 2026-08-27). ACFC hands over exactly two documents
    # per source. The naming/engineering standards and the approved-STTM corpus
    # are the AGENT'S OWN — versioned config it ships with, and a corpus it
    # maintains — so they belong inside the platform band, not on the input
    # row. On the input row they read as "four things you must supply", which
    # is both wrong and the opposite of the point.
    AY, AH = 1.58, 1.44
    band(s, AY, AH, "Inputs · the only two documents the agent is given")
    tw = (CW - 0.48 - 0.24) / 2
    tiles = [
        dict(mark="word", name="FRD_<source>.docx", accent=BLUE,
             sub="The source-level frame — file pattern and format, target schema and table per "
                 "layer, load strategy, landing folder, DQ and recycle rules. Authored and "
                 "approved by ACFC's BSAs.",
             status="live", status_color=BLUE),
        dict(mark="excel", name="VDD_<source>.xlsx", accent=SKY,
             sub="The vendor data dictionary — every source column: name, position, type, "
                 "length, required, description, allowed values, PHI. Supplied by the vendor.",
             status="ingested · not yet read by stages 02–04", status_color=RED),
    ]
    for i, t in enumerate(tiles):
        tile(s, L + 0.24 + i * (tw + 0.24), AY + 0.30, tw, 1.02, **t)

    # ---- band B: loaders → Unity Catalog ------------------------------------
    step_down(s, AY + AH, 3.22,
              "every input lands in Unity Catalog first — the agent never reads a document over the network")
    BY, BH = 3.22, 1.34
    band(s, BY, BH, "Document loaders  →  the store", accent=NAVY)
    text(s, L + 0.24, BY + 0.34, 5.6, 0.22,
         [("Three interchangeable loaders, one interface", {})],
         size=10.2, color=NAVY, font=SANS, bold=True)
    loaders = [
        ("sharepoint", "SharePoint library", "Graph, read-only", "not yet wired", RED),
        (None, "Local documents folder", "STTM_LOCAL_SOURCE_DIR", "local mode", BLUE),
        (None, "Direct volume push", "push_local_source_to_volumes.py", "in use today", BLUE),
    ]
    ly = BY + 0.60
    for mark, name, sub, st, sc in loaders:
        if mark:
            logo(s, mark, L + 0.26, ly - 0.01, 0.20, 0.20)
        else:
            square(s, L + 0.30, ly + 0.055, 0.085, BLUE)
        text(s, L + 0.58, ly - 0.02, 1.94, 0.20, [(name, {})],
             size=9.2, color=NAVY, font=SANS, bold=True)
        text(s, L + 2.56, ly - 0.01, 2.28, 0.20, [(sub, {})],
             size=8.0, color=MUTED, font=MONO)
        text(s, L + 4.86, ly - 0.01, 1.00, 0.20, [(st.upper(), {})],
             size=7.4, color=sc, font=SANS, bold=True, spacing=0.8, align=PP_ALIGN.RIGHT)
        ly += 0.24

    arrow(s, L + 6.02, BY + BH / 2, L + 6.46, BY + BH / 2, color=NAVY, width=2.0)
    ux = L + 6.60
    uw = R - 0.24 - ux
    rect(s, ux, BY + 0.22, uw, BH - 0.44, fill=WHITE, line=NAVY)
    logo(s, "unitycatalog", ux + 0.16, BY + 0.36, 1.16, 0.26)
    text(s, ux + 1.46, BY + 0.36, uw - 1.62, 0.22,
         [("Unity Catalog — volumes + Delta", {})],
         size=9.4, color=NAVY, font=SANS, bold=True)
    text(s, ux + 0.16, BY + 0.68, uw - 0.32, 0.22,
         [("frd_raw · vdd_raw · sttm_reference · sttm_out_app · sttm_audit", {})],
         size=8.2, color=BLUE, font=MONO, bold=True)
    text(s, ux + 0.16, BY + 0.90, uw - 0.32, 0.22,
         [[("The review app reads THIS.", {"bold": True, "color": NAVY}),
           ("  SharePoint is not on the request path.", {"color": SLATE})]],
         size=8.4, color=SLATE, font=SANS)

    # ---- band C: the Databricks platform ------------------------------------
    step_down(s, BY + BH, 4.72, "the app triggers the job; the job reads and writes the volumes natively")
    CY, CH = 4.72, 1.84
    band(s, CY, CH, "Databricks platform", accent=BLUE)
    logo(s, "databricks", L + 0.22, CY + 0.42, 1.16, 0.26)

    jx, jw = L + 1.58, 6.44
    rect(s, jx, CY + 0.26, jw, 1.02, fill=WHITE, line=HAIR2)
    text(s, jx + 0.16, CY + 0.34, jw - 0.32, 0.20,
         [("Generation job", {"bold": True, "color": NAVY, "size": 10}),
          ("   Databricks Jobs · serverless · stages 01–04", {"size": 8.4})],
         size=8.4, color=SLATE)
    stages = [("01", "Ingest", "docx → markdown", False),
              ("02", "Extract", "the one model call", True),
              ("03", "Audit · gate", "verbatim grounding", False),
              ("04", "Render", "STTM + contract", False)]
    sg = 0.09
    sw = (jw - 0.32 - 3 * sg) / 4
    sy = CY + 0.58
    for i, (idx, name, sub, is_llm) in enumerate(stages):
        x = jx + 0.16 + i * (sw + sg)
        rect(s, x, sy, sw, 0.62, fill=TINT_RED if is_llm else BAND,
             line=RED if is_llm else HAIR2, line_w=1.25 if is_llm else 1.0)
        c = RED if is_llm else BLUE
        text(s, x + 0.10, sy + 0.06, sw - 0.2, 0.18, [(idx, {})],
             size=8.5, color=c, font=MONO, bold=True)
        text(s, x + 0.10, sy + 0.23, sw - 0.2, 0.20, [(name, {})],
             size=9.4, color=NAVY, font=SANS, bold=True)
        text(s, x + 0.10, sy + 0.43, sw - 0.2, 0.18, [(sub, {})],
             size=7.4, color=RED if is_llm else MUTED, font=SANS)
        if is_llm:
            logo(s, "claude", x + sw - 0.62, sy + 0.05, 0.52, 0.14)
        if i < 3:
            arrow(s, x + sw + 0.005, sy + 0.31, x + sw + sg - 0.005, sy + 0.31,
                  color=MUTED, width=1.0)

    ax_, aw = jx + jw + 0.20, R - 0.24 - (jx + jw + 0.20)
    rect(s, ax_, CY + 0.26, aw, 1.02, fill=WHITE, line=HAIR2)
    text(s, ax_ + 0.16, CY + 0.34, aw - 0.32, 0.20,
         [("Review app", {"bold": True, "color": NAVY, "size": 10}),
          ("   Databricks Apps", {"size": 8.4})], size=8.4, color=SLATE)
    logo(s, "fastapi", ax_ + 0.16, CY + 0.62, 0.66, 0.15)
    logo(s, "react", ax_ + 0.92, CY + 0.58, 0.20, 0.20)
    text(s, ax_ + 0.16, CY + 0.86, aw - 0.32, 0.40,
         [("Pick an FRD · run (confirm-gated, billed) · resolve what the agent gated · "
           "re-render · download.", {})],
         size=8.2, color=SLATE, font=SANS, line_spacing=1.10)

    # ---- what the agent BRINGS, inside the platform band --------------------
    # The point Arjun made explicit 2026-08-27: the client's standards are not
    # a document anyone hands over with a source — they are transcribed into the
    # agent as versioned contracts. Same for the approved-STTM corpus. Drawing
    # them here rather than on the input row is the difference between "three
    # things you must supply" and "two documents; the agent brings the rest".
    ky = CY + 1.30
    rect(s, L + 0.22, ky, CW - 0.46, 0.50, fill=WHITE, line=HAIR2)
    rect(s, L + 0.22, ky, 0.05, 0.50, fill=NAVY)
    text(s, L + 0.44, ky + 0.06, 2.4, 0.22, [("BUILT INTO THE AGENT", {})],
         size=7.8, color=NAVY, font=SANS, bold=True, spacing=1.2)
    text(s, L + 0.44, ky + 0.26, 2.4, 0.22, [("not handed over per run", {})],
         size=7.4, color=MUTED, font=SANS)
    text(s, L + 2.90, ky + 0.06, CW - 3.20, 0.22,
         [[("Standards contracts", {"bold": True, "color": NAVY}),
           ("  naming + engineering, hashed into every run", {}),
           ("      ·      ", {"color": HAIR2}),
           ("Term catalog", {"bold": True, "color": NAVY}),
           ("  the column vocabulary, harvested at sync", {})]],
         size=8.0, color=SLATE, font=SANS)
    text(s, L + 2.90, ky + 0.27, CW - 3.20, 0.22,
         [[("Approved-STTM corpus", {"bold": True, "color": NAVY}),
           ("  layout only, plus the after-the-fact eval.  ", {}),
           ("A RUN NEVER OPENS AN STTM.", {"bold": True, "color": RED})]],
         size=8.0, color=SLATE, font=SANS)

    # ---- band D: human review + outputs -------------------------------------
    step_down(s, CY + CH, 6.68, "draft STTM .xlsx  +  source contract .json   →   to a named person",
              x=L + 1.4)
    hy, hw = 6.68, 5.90
    rect(s, L, hy, hw, 0.54, fill=WHITE, line=BLUE)
    icon(s, "user", L + 0.16, hy + 0.14, 0.28)
    text(s, L + 0.54, hy + 0.08, hw - 0.70, 0.44,
         [[("A person decides.", {"bold": True, "color": NAVY})],
          [("The reviewer resolves every gated item, approves, and returns the workbook — "
            "the next sync pairs it.", {"color": SLATE})]],
         size=8.2, color=SLATE, font=SANS, line_spacing=1.10)
    ox = L + hw + 0.22
    ow = R - ox
    rect(s, ox, hy, ow, 0.54, fill=WHITE, line=NAVY)
    logo(s, "excel", ox + 0.13, hy + 0.15, 0.26, 0.26)
    text(s, ox + 0.46, hy + 0.10, 1.48, 0.40,
         [[("STTM workbook", {"bold": True, "color": NAVY})], [(".xlsx · client dialect", {})]],
         size=8.2, color=SLATE, font=SANS, line_spacing=1.05)
    logo(s, "json", ox + 1.98, hy + 0.15, 0.24, 0.24)
    text(s, ox + 2.30, hy + 0.10, 1.40, 0.40,
         [[("Source contract", {"bold": True, "color": NAVY})], [("contract.json", {})]],
         size=8.2, color=SLATE, font=SANS, line_spacing=1.05)
    arrow(s, ox + 3.74, hy + 0.29, ox + 4.00, hy + 0.29, color=NAVY, width=1.5)
    text(s, ox + 4.12, hy + 0.10, ow - 4.26, 0.40,
         [[("CodeGen agent", {"bold": True, "color": BLUE})],
          [("Code Review agent", {"bold": True, "color": BLUE})]],
         size=8.2, color=SLATE, font=SANS, line_spacing=1.05)

    # ---- the doctrine bar, bottom edge ---------------------------------------
    rect(s, 0, 7.24, W_IN, 0.26, fill=NAVY)
    # No mark on the doctrine bar: at the height this band allows the full
    # lockup renders as mud (the helper's own caution). The masthead carries
    # the mark; repeating it small makes it worse, not more branded.
    text(s, L, 7.30, CW, 0.20,
         [("ONE ANTHROPIC CALL PER DOCUMENT", {"color": "ffd9dc", "bold": True}),
          ("     ·     ", {"color": "6b7fa8"}),
          ("EVERYTHING ELSE IS DETERMINISTIC CODE", {"color": WHITE, "bold": True}),
          ("     ·     ", {"color": "6b7fa8"}),
          ("A PERSON APPROVES", {"color": WHITE, "bold": True})],
         size=7.8, color=WHITE, font=SANS, spacing=1.0)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    print(build(target))
