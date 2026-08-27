"""Build the FRD→STTM one-slide DATA GOVERNANCE ARCHITECTURE deck (PPTX).

    ~/.virtualenvs/frdsttm/bin/python scripts/build_governance_onepager.py [out.pptx]

Default output: context/FRD-to-STTM-Agent-Data-Governance-Architecture.pptx

ONE slide for a NON-TECHNICAL reader: how governance is actually implemented.
It reads LEFT → RIGHT (Arjun, 2026-08-23, deliberately not the solution deck's
top-down bands): four stations in the order a document travels, the audit trail
as a bar beneath all four, the two boundary crossings under the stations where
they happen, and the Collibra register as a tall column on the right that
everything flows into.

REDESIGNED 2026-08-27 (Arjun), on two axes:

1. STYLE — the "Signal" theme is retired for this deck in favour of the ACFC
   house style in scripts/acfc_theme.py (the client's own palette sampled from
   their site, Segoe UI, square corners, flat bands, no background art).
   scripts/deck_assets/ONEPAGER_DESIGN.md remains the record of the old system.

2. ARCHITECTURE — matched to the solution deck's two changes:
   * station 01 now says THREE INPUTS enter (FRD, vendor dictionary, standards
     contracts) and that they enter UNITY CATALOG, whichever loader brought
     them — SharePoint is one of three loaders, not the request path;
   * station 03's provenance now includes `standards_sha256`, so the trail
     records which revision of the client's standards decided a render's
     target side, alongside which FRD version and which model call.

Full record: docs/AI_GOVERNANCE.md. Every control named here maps to code in
that document; the open human decisions live in its §8 and are NOT claimed here
as settled.
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
    rect, line, text, square, outline_chip, flag_bars, logo, icon, arrow,
    acfc_logo,
)

REPO = Path(__file__).resolve().parent.parent
OUT_DEFAULT = REPO / "context" / "FRD-to-STTM-Agent-Data-Governance-Architecture.pptx"

L, R = 0.72, W_IN - 0.72

#            accent  logos                     icon    idx   verb        question
STATIONS = [
    (BLUE, ("unitycatalog",), None, "01", "enters", "What data?",
     "Classified at the door",
     "Two documents per feed — the FRD and the vendor data dictionary — land in frd_raw and "
     "vdd_raw, where every folder and table carries an owner, a steward, its sensitivity, its "
     "source and its retention rule. The standards and the column vocabulary are config inside "
     "the agent, not documents anyone hands over.",
     "Unity Catalog tags & comments"),
    (NAVY, ("entra", "databricks"), None, "02", "opened", "Who?",
     "Only named BSAs",
     "Only the client's BSA group — the people already allowed to read this data — can open the "
     "app. Each signs in with the company login. Nothing runs, is decided or handed out without "
     "that name attached.",
     "Microsoft Entra ID group · Databricks Apps"),
    (RED, ("claude",), None, "03", "generated", "Where from?",
     "Every output is traceable",
     "One AI call reads the FRD — the only one in the system. Fingerprints tie the finished "
     "workbook to the exact FRD version, to that call (which model, which instructions) and to "
     "the revision of the client's standards that decided its target side.",
     "SHA-256 · run manifests · standards_sha256"),
    (NAVY, (), "check", "04", "decided", "Who decides?",
     "A person decides",
     "The AI only proposes. Code checks every mapping against the FRD word for word and scores "
     "it against an approved example. Anything it cannot ground is left blank and flagged, never "
     "guessed. A named reviewer settles those and approves.",
     "Grounding audit · gating · human-in-the-loop"),
]


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
          ("    data governance architecture", {"color": MUTED, "size": 13})], size=24)
    x = L + 1.72
    for t, c in (("classified data", BLUE), ("named people", NAVY),
                 ("traceable outputs", BLUE), ("a person decides", NAVY)):
        x = outline_chip(s, x, 1.02, t, color=c, size=8)
    text(s, R - 2.5, 0.42, 2.5, 0.52,
         [[("Built by Hexaware", {"bold": True, "color": NAVY})],
          [("rebuilt inside the ACFC environment", {"color": MUTED})]],
         size=8.2, color=MUTED, font=SANS, align=PP_ALIGN.RIGHT, line_spacing=1.18)

    # ---- geometry ------------------------------------------------------------
    TOP, BOT = 1.78, 6.92
    REG_W = 2.70
    REG_X = R - REG_W
    SX0, SX1 = L, REG_X - 0.46
    gap = 0.26
    sw = (SX1 - SX0 - 3 * gap) / 4
    SY, SH = TOP, 2.86
    AY, AH = SY + SH + 0.20, 0.78
    XY = AY + AH + 0.20
    XH = BOT - XY

    # ---- the four stations, left → right -------------------------------------
    text(s, SX0, SY - 0.26, 6, 0.22, [("THE DOCUMENT'S JOURNEY  →", {})],
         size=8.5, color=MUTED, font=SANS, bold=True, spacing=1.4)
    for i, (acc, logos, ico, idx, verb, question, title, body, tech) in enumerate(STATIONS):
        x = SX0 + i * (sw + gap)
        is_llm = acc == RED
        rect(s, x, SY, sw, SH, fill=TINT_RED if is_llm else BAND, line=acc,
             line_w=1.25 if is_llm else 1.0)
        rect(s, x, SY, sw, 0.05, fill=acc)
        text(s, x + 0.16, SY + 0.16, sw - 0.32, 0.2,
             [(idx, {"font": MONO}), ("  " + verb.upper(), {})],
             size=8.5, color=acc, bold=True, spacing=0.9)
        lx = x + 0.16
        for name in logos:
            lw = 0.86 if name in ("unitycatalog", "databricks", "claude") else 0.30
            logo(s, name, lx, SY + 0.42, lw, 0.28)
            lx += lw + 0.12
        if ico:
            icon(s, ico, x + 0.16, SY + 0.42, 0.28)
        text(s, x + 0.16, SY + 0.80, sw - 0.32, 0.2, [(question.upper(), {})],
             size=7.8, color=acc, font=SANS, bold=True, spacing=1.0)
        text(s, x + 0.16, SY + 1.00, sw - 0.32, 0.36, [(title, {})],
             size=9.8, color=NAVY, font=SANS, bold=True, spacing=-0.15, line_spacing=1.05)
        text(s, x + 0.16, SY + 1.38, sw - 0.32, SH - 1.76, [(body, {})],
             size=7.6, color=SLATE, font=SANS, line_spacing=1.08)
        line(s, x + 0.16, SY + SH - 0.36, x + sw - 0.16, SY + SH - 0.36, color=HAIR2)
        text(s, x + 0.16, SY + SH - 0.30, sw - 0.32, 0.26, [(tech, {})],
             size=7.2, color=acc, font=SANS, bold=True, line_spacing=1.08)
        if i < 3:
            arrow(s, x + sw + 0.02, SY + SH / 2, x + sw + gap - 0.02, SY + SH / 2,
                  color=NAVY, width=1.5)

    # ---- the audit bar, under all four ---------------------------------------
    rect(s, SX0, AY, SX1 - SX0, AH, fill=WHITE, line=BLUE)
    rect(s, SX0, AY, 0.05, AH, fill=BLUE)
    icon(s, "shield", SX0 + 0.18, AY + 0.22, 0.32)
    text(s, SX0 + 0.62, AY + 0.14, 2.2, 0.2, [("WHAT HAPPENED?", {})],
         size=7.8, color=BLUE, font=SANS, bold=True, spacing=1.0)
    text(s, SX0 + 0.62, AY + 0.34, 2.4, 0.24, [("Nothing is off the record", {})],
         size=10.2, color=NAVY, font=SANS, bold=True, spacing=-0.15)
    text(s, SX0 + 3.20, AY + 0.14, SX1 - SX0 - 3.40, 0.46,
         [("Every step above — each run, reviewer decision, re-render, download and sync — writes "
           "one record the moment it happens. The trail cannot be edited afterwards and reads "
           "like a table.", {})],
         size=8.0, color=SLATE, font=SANS, line_spacing=1.10)
    text(s, SX0 + 3.20, AY + AH - 0.24, SX1 - SX0 - 3.40, 0.2,
         [("Audit trail in a Unity Catalog volume · Delta run log", {})],
         size=7.2, color=BLUE, font=SANS, bold=True)

    # ---- the two boundary crossings ------------------------------------------
    text(s, SX0, XY + 0.02, 2 * sw + gap - 0.16, 0.50,
         [[("Client data crosses the platform boundary in exactly two places",
            {"bold": True, "color": NAVY})],
          [("— both shown under the station where they happen, both recorded.", {"color": SLATE})]],
         size=8.2, color=SLATE, font=SANS, line_spacing=1.12)
    oy = XY + 0.56
    text(s, SX0, oy, 2 * sw + gap - 0.16, 0.2, [("STILL OPEN — FOR THE CLIENT, NOT FOR CODE", {})],
         size=7.4, color=RED, font=SANS, bold=True, spacing=1.0)
    oy += 0.22
    for item in ("Named data owner and steward",
                 "The model-vendor data path against the BAA posture",
                 "Retention for generated artefacts and the audit trail"):
        square(s, SX0 + 0.02, oy + 0.05, 0.07, RED)
        text(s, SX0 + 0.20, oy - 0.02, 2 * sw + gap - 0.38, 0.24, [(item, {})],
             size=7.6, color=SLATE, font=SANS)
        oy += 0.22
    cx3 = SX0 + 2 * (sw + gap)
    rect(s, cx3, XY, sw, XH, fill=TINT_RED, line=RED)
    text(s, cx3 + 0.14, XY + 0.10, sw - 0.28, XH - 0.16,
         [[("1   FRD text → Anthropic", {"bold": True, "color": RED})],
          [("The credential lives in a Databricks secret scope — or, on the workspace route, is "
            "the workspace's own. Within the client's data-handling review.", {"color": SLATE})]],
         size=7.4, color=SLATE, font=SANS, line_spacing=1.10)
    cx4 = SX0 + 3 * (sw + gap)
    rect(s, cx4, XY, sw, XH, fill=TINT_BLUE, line=NAVY)
    text(s, cx4 + 0.14, XY + 0.10, sw - 0.28, XH - 0.16,
         [[("2   workbook → a named person", {"bold": True, "color": NAVY})],
          [("The download is recorded with the file's fingerprint and the person's name. The "
            "agent never writes back to the library itself.", {"color": SLATE})]],
         size=7.4, color=SLATE, font=SANS, line_spacing=1.10)

    # ---- the register column, right ------------------------------------------
    rect(s, REG_X, TOP, REG_W, BOT - TOP, fill=BAND, line=NAVY)
    rect(s, REG_X, TOP, REG_W, 0.05, fill=SKY)
    text(s, REG_X, TOP - 0.28, REG_W, 0.22, [("→  REGISTERED IN", {})],
         size=8.5, color=NAVY, font=SANS, bold=True, spacing=1.4)
    logo(s, "collibra", REG_X + 0.24, TOP + 0.26, REG_W - 0.48, 0.44)
    text(s, REG_X + 0.24, TOP + 0.86, REG_W - 0.48, 0.2, [("GOVERNANCE REGISTER", {})],
         size=7.8, color=NAVY, font=SANS, bold=True, spacing=1.0)
    text(s, REG_X + 0.24, TOP + 1.08, REG_W - 0.48, 0.92,
         [[("The client's data-governance catalog. The agent is registered here as an ", {}),
           ("AI asset", {"bold": True, "color": NAVY}),
           (" — one place to answer “what is this agent, and is it under control?”", {})]],
         size=8.4, color=SLATE, font=SANS, line_spacing=1.12)
    ty = TOP + 2.16
    rect(s, REG_X + 0.24, ty, REG_W - 0.48, 1.24, fill=WHITE, line=HAIR2)
    logo(s, "unitycatalog", REG_X + 0.38, ty + 0.14, 1.00, 0.28)
    arrow(s, REG_X + 1.50, ty + 0.28, REG_X + 1.80, ty + 0.28, color=NAVY, width=1.5)
    text(s, REG_X + 0.38, ty + 0.50, REG_W - 0.76, 0.70,
         [("Collibra harvests Unity Catalog", {"bold": True, "color": NAVY}),
          (" — owners, sensitivity and descriptions set in Databricks appear here "
           "automatically. Classified once, visible in both.", {})],
         size=7.8, color=SLATE, font=SANS, line_spacing=1.08)
    ry = ty + 1.44
    text(s, REG_X + 0.24, ry, REG_W - 0.48, 0.2, [("WHAT IS REGISTERED", {})],
         size=7.8, color=NAVY, font=SANS, bold=True, spacing=1.0)
    ry += 0.26
    for item in ("What the agent is for, and its risk tier",
                 "Who owns it and who stewards it",
                 "The data it reads and writes, and where it flows",
                 "The five controls shown to the left"):
        square(s, REG_X + 0.26, ry + 0.055, 0.075, SKY)
        text(s, REG_X + 0.46, ry - 0.02, REG_W - 0.72, 0.34, [(item, {})],
             size=7.9, color=SLATE, font=SANS, line_spacing=1.08)
        ry += 0.29
    for yy in (SY + SH / 2, AY + AH / 2):
        arrow(s, SX1 + 0.06, yy, REG_X - 0.06, yy, color=NAVY, width=2.0)

    # ---- the doctrine bar, bottom edge ---------------------------------------
    rect(s, 0, 7.24, W_IN, 0.26, fill=NAVY)
    text(s, L, 7.29, R - L, 0.20,
         [("CLASSIFIED DATA", {"color": WHITE, "bold": True}), ("     ·     ", {"color": "6b7fa8"}),
          ("NAMED PEOPLE", {"color": WHITE, "bold": True}), ("     ·     ", {"color": "6b7fa8"}),
          ("TRACEABLE OUTPUTS", {"color": WHITE, "bold": True}), ("     ·     ", {"color": "6b7fa8"}),
          ("A PERSON DECIDES", {"color": WHITE, "bold": True}), ("     ·     ", {"color": "6b7fa8"}),
          ("A RECORD OF EVERYTHING", {"color": WHITE, "bold": True})],
         size=8, color=WHITE, font=SANS, spacing=1.0)
    st = "controls built 2026-08-23 · Collibra entry pending"
    text(s, R - 4.8, 7.00, 4.8, 0.20, [(st.upper(), {})],
         size=7.2, color=MUTED, font=SANS, bold=True, spacing=0.9, align=PP_ALIGN.RIGHT)

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    print(build(target))
