"""Build the FRD-to-STTM one-slide DATA GOVERNANCE architecture deck (PPTX).

    .venv/bin/pip install python-pptx pillow     # deck-building only, not runtime deps
    .venv/bin/python scripts/build_governance_onepager.py [output.pptx]

Default output: context/FRD-to-STTM-Agent-Data-Governance-Architecture.pptx

ONE slide for a NON-TECHNICAL reader: how data governance is implemented in
the production FRD→STTM agent, and where Collibra sits. Same "Signal" light
theme, background, fonts, colour code and helpers as the solution-architecture
one-pager (scripts/build_architecture_onepager.py; design in
scripts/deck_assets/ONEPAGER_DESIGN.md) so the two decks read as a pair.

LEFT TO RIGHT (v2, 2026-08-23, Arjun: must not repeat the solution
architecture's top-down bands) — the reader follows the data's journey:

  header     title + kicker
  stations   four cards, left → right, in the order a document travels:
             01 ENTERS (SharePoint → Unity Catalog: classified at the door)
             02 OPENED (only the BSA group, named sign-in)
             03 GENERATED (the one Claude call; every output traceable)
             04 DECIDED (a person decides) — each a governance question,
             each naming the technology that implements it
  audit bar  runs UNDER all four stations: every step writes a record
  crossings  under stations 03 / 04: the only two places client data leaves
             the platform (FRD text → Anthropic API; workbook → a person)
  register   a tall column on the RIGHT that everything flows into —
             Collibra (official mark from Wikimedia Commons,
             File:Collibra-Logo-RGB-FullColor.png), fed by Unity Catalog
  footer     the doctrine line + an honest status tag

Every statement is true of `staging` as of 2026-08-23 (docs/AI_GOVERNANCE.md
is the long-form record): the controls are built; Collibra registration and
owner/steward assignment are the client's pending steps and the status tag
says so.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_architecture_onepager import (  # noqa: E402
    CARD, CODE, CODE_DIM, EXT, EXT_DIM, GEN, H_IN, HAIR, HAIR2, HUMAN, HUMAN_DIM, INK, LLM, LLM_DIM,
    LLM_TINT, MONO, RAISED, REPO, SANS, T_MUTED, T_PRI, T_SEC, W_IN,
    add_icon, add_line, add_logo, add_rect, add_text, band_label, chip, crosshair, down_arrow,
    make_background,
)

OUT_DEFAULT = REPO / "context" / "FRD-to-STTM-Agent-Data-Governance-Architecture.pptx"

# The four stations — (accent, accent_dim, logos|icon, index, verb, question,
# title, body, technology line). Body text is for a non-technical reader:
# "digital fingerprint" not sha256, "cannot be edited afterwards" not
# append-only. The technology line names what implements the control.
STATIONS = [
    (CODE, CODE_DIM, ("sharepoint", "unitycatalog"), None, "01", "enters", "What data?",
     "Classified at the door",
     "Documents come from the client's SharePoint, read-only. In Databricks every table and "
     "folder carries an owner, a steward, its sensitivity (confidential; may name PHI fields), "
     "its source and its retention rule — set by the data owner.",
     "SharePoint · Unity Catalog tags & comments"),
    (HUMAN, HUMAN_DIM, ("entra", "databricks"), None, "02", "opened", "Who?",
     "Only named BSAs",
     "Only the client's BSA group — the people already allowed to read the data — can open the "
     "app. Each signs in with the company login; nothing runs, is decided or handed out without "
     "that name.",
     "Microsoft Entra ID group · Databricks Apps"),
    (LLM, LLM_DIM, ("claude",), None, "03", "generated", "Where from?",
     "Every output is traceable",
     "One AI call reads the FRD — the only one. Digital fingerprints tie the workbook to the "
     "exact FRD version, to that call (which model, which instructions) and to the person who "
     "asked.",
     "SHA-256 fingerprints · run manifests"),
    (HUMAN, HUMAN_DIM, (), "check", "04", "decided", "Who decides?",
     "A person decides",
     "The AI only proposes. Code checks every mapping against the FRD word for word and scores "
     "it against an approved example; a named reviewer settles what is unclear, approves and "
     "uploads.",
     "Grounding audit · golden-pair score · human-in-the-loop"),
]


def build(out: Path) -> Path:
    GEN.mkdir(parents=True, exist_ok=True)
    bg = make_background(GEN / "background.png")

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W_IN), Inches(H_IN)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_picture(str(bg), 0, 0, prs.slide_width, prs.slide_height)
    for cx, cy in ((0.3, 0.3), (W_IN - 0.3, 0.3), (0.3, H_IN - 0.3), (W_IN - 0.3, H_IN - 0.3)):
        crosshair(s, cx, cy)

    L, R = 0.6, W_IN - 0.6

    # ----- header ------------------------------------------------------------
    add_text(s, L, 0.38, 9, 0.24,
             [("HEXAWARE  ·  AMERIHEALTH CARITAS  ·  AGENT 01 / 03", {})],
             size=9, color=T_MUTED, font=SANS, bold=True, spacing=1.4)
    add_text(s, L, 0.62, 10.5, 0.55, [("FRD → STTM Agent", {"color": INK, "bold": True, "size": 28}),
                                     ("    data governance architecture", {"color": T_MUTED, "size": 15})], size=28)

    # ----- geometry: stations region (left) → register column (right) --------------------
    TOP, BOT = 1.45, 6.55
    REG_W = 2.6                       # the Collibra column
    REG_X = R - REG_W
    ARROW_GAP = 0.5                   # the big "registered →" gap
    SX0, SX1 = L, REG_X - ARROW_GAP   # stations region
    gap = 0.34
    sw = (SX1 - SX0 - 3 * gap) / 4
    SY, SH = TOP, 3.1
    AY, AH = SY + SH + 0.22, 0.8      # audit bar
    XY, XH = AY + AH + 0.18, BOT - (AY + AH + 0.18)   # crossings row

    # ----- the four stations, left → right ------------------------------------------------
    add_text(s, SX0, SY - 0.26, 6, 0.22, [("THE DOCUMENT'S JOURNEY  →", {})], size=9, color=T_MUTED,
             font=SANS, bold=True, spacing=1.4)
    for i, (acc, acc_dim, logos, icon, idx, verb, question, title, body, tech) in enumerate(STATIONS):
        x = SX0 + i * (sw + gap)
        is_llm = acc == LLM
        add_rect(s, x, SY, sw, SH, fill=(LLM_TINT if is_llm else RAISED), line=acc,
                 line_w=(1.25 if is_llm else 1.0), radius=0.05)
        # row 1: index + verb; row 2: the technology marks (left-aligned)
        add_text(s, x + 0.16, SY + 0.16, sw - 0.32, 0.2,
                 [(idx, {"font": MONO, "color": acc_dim}), ("  " + verb.upper(), {"color": acc_dim})],
                 size=8.5, bold=True, spacing=0.8)
        lx = x + 0.16
        for name in logos:
            lw = 0.95 if name in ("unitycatalog", "databricks", "claude") else 0.34
            add_logo(s, name, lx, SY + 0.44, lw, 0.32)
            lx += lw + 0.14
        if icon:
            add_icon(s, icon, x + 0.16, SY + 0.44, 0.32)
        # question, title, body, tech
        add_text(s, x + 0.16, SY + 0.9, sw - 0.32, 0.2, [(question.upper(), {})], size=8, color=acc_dim,
                 font=SANS, bold=True, spacing=1.0)
        add_text(s, x + 0.16, SY + 1.12, sw - 0.32, 0.26, [(title, {})], size=10.5, color=(LLM_DIM if is_llm else T_PRI), bold=True)
        add_text(s, x + 0.16, SY + 1.42, sw - 0.32, SH - 1.86, [(body, {})], size=8.5,
                 color=(LLM_DIM if is_llm else T_SEC), line_spacing=1.05)
        add_line(s, x + 0.16, SY + SH - 0.42, x + sw - 0.16, SY + SH - 0.42, color=HAIR2, width=0.5)
        add_text(s, x + 0.16, SY + SH - 0.36, sw - 0.32, 0.3, [(tech, {})], size=7.5, color=acc_dim,
                 font=SANS, bold=True)
        if i < 3:
            add_line(s, x + sw + 0.04, SY + SH / 2, x + sw + gap - 0.04, SY + SH / 2, color=T_MUTED,
                     width=1.25, head=True)

    # ----- the audit bar, under all four ---------------------------------------------------
    add_rect(s, SX0, AY, SX1 - SX0, AH, fill=RAISED, line=CODE, line_w=1.0, radius=0.05)
    add_icon(s, "shield", SX0 + 0.16, AY + 0.22, 0.36)
    add_text(s, SX0 + 0.64, AY + 0.13, 2.0, 0.2, [("WHAT HAPPENED?", {})], size=8, color=CODE_DIM,
             font=SANS, bold=True, spacing=1.0)
    add_text(s, SX0 + 0.64, AY + 0.35, 2.3, 0.26, [("Nothing is off the record", {})], size=10.5, color=T_PRI, bold=True)
    add_text(s, SX0 + 3.0, AY + 0.14, SX1 - SX0 - 3.2, 0.5,
             [("Every step above — each run, reviewer decision, re-render, download and sync — writes one "
               "record the moment it happens. The trail cannot be edited afterwards and reads like a table.", {})],
             size=8.5, color=T_SEC, line_spacing=1.05)
    add_text(s, SX0 + 3.0, AY + AH - 0.26, SX1 - SX0 - 3.2, 0.2,
             [("Audit trail in a Unity Catalog volume · Delta run log", {})], size=7.5, color=CODE_DIM, bold=True)

    # ----- the two boundary crossings, under stations 03 / 04 -----------------------------
    add_text(s, SX0, XY + 0.04, 2 * sw + gap, XH - 0.08,
             [("Client data crosses the platform boundary in exactly two places", {"bold": True, "color": T_PRI}),
              (" — both under the stations where they happen, both recorded.", {})],
             size=8.5, color=T_SEC, line_spacing=1.05)
    cx3 = SX0 + 2 * (sw + gap)
    add_rect(s, cx3, XY, sw, XH, fill=LLM_TINT, line=LLM, line_w=0.75, radius=0.08)
    add_text(s, cx3 + 0.12, XY + 0.08, sw - 0.24, XH - 0.12,
             [("1  FRD text → Anthropic API", {"bold": True}),
              (" — API key in a Databricks secret scope; within the client's data-handling review.", {})],
             size=7.5, color=LLM_DIM, line_spacing=1.04)
    cx4 = SX0 + 3 * (sw + gap)
    add_rect(s, cx4, XY, sw, XH, fill="fbf3dc", line=HUMAN, line_w=0.75, radius=0.08)
    add_text(s, cx4 + 0.12, XY + 0.08, sw - 0.24, XH - 0.12,
             [("2  workbook → a named person", {"bold": True}),
              (" — the download is recorded with the file's fingerprint; the agent never writes to SharePoint.", {})],
             size=7.5, color=HUMAN_DIM, line_spacing=1.04)

    # ----- the register column, right — everything flows into it ---------------------------
    add_rect(s, REG_X, TOP, REG_W, BOT - TOP, fill=CARD, line=EXT, line_w=1.25, radius=0.04)
    add_text(s, REG_X, TOP - 0.26, REG_W, 0.22, [("→  REGISTERED IN", {})], size=9, color=EXT_DIM,
             font=SANS, bold=True, spacing=1.4)
    add_logo(s, "collibra", REG_X + 0.25, TOP + 0.3, REG_W - 0.5, 0.5)
    add_text(s, REG_X + 0.25, TOP + 0.92, REG_W - 0.5, 0.22, [("GOVERNANCE REGISTER", {})], size=8, color=EXT_DIM,
             font=SANS, bold=True, spacing=1.0)
    add_text(s, REG_X + 0.25, TOP + 1.18, REG_W - 0.5, 1.9,
             [[("The client's data-governance catalog. The agent is registered here as an ", {}),
               ("AI asset", {"bold": True, "color": T_PRI}),
               (": what it is for, who owns and stewards it, how risky it is, the data it reads and "
                "writes, where that data flows, and the controls to the left — one place to answer "
                "\"what is this agent, and is it under control?\"", {})]],
             size=9, color=T_SEC, line_spacing=1.06)
    # how Unity Catalog feeds it
    ty = TOP + 3.25
    add_rect(s, REG_X + 0.25, ty, REG_W - 0.5, BOT - ty - 0.25, fill=RAISED, line=HAIR2, radius=0.08)
    add_logo(s, "unitycatalog", REG_X + 0.4, ty + 0.14, 1.1, 0.34)
    add_line(s, REG_X + 1.58, ty + 0.31, REG_X + 1.86, ty + 0.31, color=EXT, width=1.25, head=True)
    add_text(s, REG_X + 0.4, ty + 0.58, REG_W - 0.8, BOT - ty - 0.9,
             [("Collibra harvests Unity Catalog", {"bold": True, "color": T_PRI}),
              (" — owners, sensitivity and descriptions set in Databricks appear here automatically. "
               "Classified once, visible in both.", {})],
             size=8.5, color=T_SEC, line_spacing=1.04)
    # the big arrows: stations → register, audit → register
    for yy in (SY + SH / 2, AY + AH / 2):
        add_line(s, SX1 + 0.08, yy, REG_X - 0.08, yy, color=EXT, width=1.75, head=True)

    # ----- footer -----------------------------------------------------------------------
    FY = 7.08
    add_text(s, L, FY + 0.04, 9.4, 0.3,
             [("classified data", {"color": CODE_DIM, "bold": True}), ("   ·   ", {"color": HAIR2}),
              ("named people", {"color": HUMAN_DIM, "bold": True}), ("   ·   ", {"color": HAIR2}),
              ("traceable outputs", {"color": CODE_DIM, "bold": True}), ("   ·   ", {"color": HAIR2}),
              ("a person decides", {"color": HUMAN_DIM, "bold": True}), ("   ·   ", {"color": HAIR2}),
              ("a record of everything", {"color": CODE_DIM, "bold": True})], size=11)
    status = "controls built 2026-08-23 · Collibra entry pending"
    chip(s, R - (0.086 * len(status) + 0.4), FY + 0.02, status, color=T_MUTED, border=HAIR2, size=8)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    print(build(target))
