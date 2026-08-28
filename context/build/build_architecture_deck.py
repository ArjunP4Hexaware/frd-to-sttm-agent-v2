"""Build the FRD-to-STTM architecture deck (PPTX) — two slides, ACFC style.

    .venv/bin/pip install python-pptx     # not a runtime dep; deck-building only
    .venv/bin/python scripts/build_architecture_deck.py [output.pptx]

Default output: context/FRD_to_STTM_Agent_Architecture.pptx

Slide 1 — how the agent is SET UP inside the AmeriHealth environment:
    one-time bulk load of every FRD and STTM from SharePoint into the right
    Unity Catalog volumes → pair as many FRDs to STTMs as possible → then stay
    in sync automatically whenever a new approved FRD or a new STTM lands in
    SharePoint.
Slide 2 — what happens when a PERSON uses the app: pick an unmapped FRD →
    the agent runs → human-in-the-loop review → the STTM is presented → last
    edits / approval → the person uploads it to SharePoint themselves, and
    the app pulls it back into Databricks.

Visual system (2026-08-22): this deck deliberately matches the ACFC
Program Overview deck (`../context/ACFC_AI_in_Engineering_Program_Overview.pptx`),
NOT the older navy/teal `deck_lib` used by scripts/build_acfc_deck.py — that
family was judged off-brand. Everything below is lifted from that deck:
    - 16:9 at 13.333" × 7.5"; Calibri throughout
    - slide chrome (blue header band, flag motif, "AmeriHealth Caritas"
      footer rule) is the deck's own background image, kept verbatim in
      scripts/deck_assets/acfc_slide_chrome.jpeg
    - palette: 0067B1 brand blue · 0093D0 LLM/cyan · 78A22F code/green ·
      F8971D human/orange · 8D64AA external-system/purple · 2E3350 heading
      ink · 5A6875 body · 8C99A4 muted · F4F6F8 panel · D9DEE3 hairline
    - cards are 3%-radius rounded rectangles; icons are white Lucide glyphs
      (scripts/deck_assets/icons/*.png, rendered from react-icons/lu at
      256 px) on colored circles
Layout is absolute-positioned; text length drives fit — shorten copy
rather than growing boxes. Audience is non-technical: plain words, no
file names, no API names.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "deck_assets"
ICONS = ASSETS / "icons"
CHROME = ASSETS / "acfc_slide_chrome.jpeg"

# ------------------------------------------------------------------ palette
BLUE = RGBColor(0x00, 0x67, 0xB1)      # brand blue — titles, Databricks platform
CYAN = RGBColor(0x00, 0x93, 0xD0)      # LLM step
GREEN = RGBColor(0x78, 0xA2, 0x2F)     # deterministic code
ORANGE = RGBColor(0xF8, 0x97, 0x1D)    # human decision
PURPLE = RGBColor(0x8D, 0x64, 0xAA)    # external system of record (SharePoint)
INK = RGBColor(0x2E, 0x33, 0x50)       # headings
BODY = RGBColor(0x5A, 0x68, 0x75)      # body copy
MUTED = RGBColor(0x8C, 0x99, 0xA4)     # italic sub-lines, legend
FAINT = RGBColor(0x9A, 0xA5, 0xAE)     # page number
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PANEL = RGBColor(0xF4, 0xF6, 0xF8)     # grey panel
HAIR = RGBColor(0xD9, 0xDE, 0xE3)      # hairline border
ARROW = RGBColor(0xAE, 0xB8, 0xC1)     # step arrows
TINT = RGBColor(0xEA, 0xF3, 0xFA)      # pale blue callout
TINT_LINE = RGBColor(0xCB, 0xE2, 0xF3)

FONT = "Calibri"
W, H = Inches(13.333), Inches(7.5)
LEFT = Inches(0.36)
CONTENT_W = W - 2 * LEFT
ROUND = 3077 / 50000  # roundRect adj used throughout the ACFC deck

KIND = {  # legend key -> (color, label)
    "llm": (CYAN, "AI model step (Claude)"),
    "code": (GREEN, "Automatic — plain code, no AI"),
    "human": (ORANGE, "A person decides"),
    "platform": (BLUE, "Databricks platform"),
    "external": (PURPLE, "SharePoint — system of record"),
}


# ------------------------------------------------------------------ primitives
def shape(s, kind, x, y, w, h, fill=None, line=None, lw=1.0, adj=None):
    sh = s.shapes.add_shape(kind, int(x), int(y), int(w), int(h))
    # python-pptx autoshapes carry a theme <p:style> (effectRef → drop shadow).
    # The ACFC deck's shapes have no style block at all; drop it so the fill
    # and line set below are the whole story and nothing casts a shadow.
    style = sh._element.find(qn("p:style"))
    if style is not None:
        sh._element.remove(style)
    if adj is not None and sh.adjustments:
        sh.adjustments[0] = adj
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(lw)
    sh.shadow.inherit = False
    return sh


def card(s, x, y, w, h, fill=WHITE, line=HAIR):
    return shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill, line=line,
                 adj=ROUND)


def circle(s, x, y, d, color):
    return shape(s, MSO_SHAPE.OVAL, x, y, d, d, fill=color, line=color)


def text(s, x, y, w, h, txt, size=10, color=BODY, bold=False, italic=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=None, line_spacing=None):
    tb = s.shapes.add_textbox(int(x), int(y), int(w), int(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Emu(0)
    tf.vertical_anchor = anchor
    for i, ln in enumerate(txt.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if line_spacing:
            p.line_spacing = line_spacing
        r = p.add_run()
        r.text = ln
        f = r.font
        f.name = FONT
        f.size = Pt(size)
        f.bold = bold
        f.italic = italic
        f.color.rgb = color
        if spacing:
            r._r.get_or_add_rPr().set("spc", str(spacing))
    return tb


def icon_circle(s, x, y, d, color, icon):
    """Colored circle with a white Lucide glyph centred in it."""
    circle(s, x, y, d, color)
    g = d * 0.56
    s.shapes.add_picture(str(ICONS / f"{icon}.png"), int(x + (d - g) / 2),
                         int(y + (d - g) / 2), int(g), int(g))


def number_circle(s, x, y, d, color, n):
    circle(s, x, y, d, color)
    text(s, x, y, d, d, str(n), size=11, color=WHITE, bold=True,
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def right_arrow(s, x, y, d=Inches(0.14)):
    shape(s, MSO_SHAPE.RIGHT_ARROW, x, y, d, d, fill=ARROW, line=ARROW)


def legend(s, x, y, keys, gap=Inches(0.3)):
    for k in keys:
        col, label = KIND[k]
        shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y + Inches(0.05), Inches(0.15),
              Inches(0.15), fill=col, line=col, adj=0.2)
        w = Inches(0.12 * len(label) * 0.62 + 0.3)
        text(s, x + Inches(0.22), y, w, Inches(0.24), label, size=8, color=BODY,
             anchor=MSO_ANCHOR.MIDDLE)
        x += Inches(0.22) + w + gap


# ------------------------------------------------------------------ deck
class Deck:
    def __init__(self):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = W, H
        self.blank = self.prs.slide_layouts[6]
        self.n = 0

    def slide(self, band_title, kicker, heading, notes=None):
        s = self.prs.slides.add_slide(self.blank)
        self.n += 1
        s.shapes.add_picture(str(CHROME), 0, 0, int(W), int(H))  # ACFC chrome
        text(s, LEFT, Inches(0.20), Inches(9.96), Inches(0.55), band_title, size=20,
             color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        text(s, LEFT, Inches(0.86), Inches(8.0), Inches(0.24), kicker, size=9,
             color=BLUE, bold=True, anchor=MSO_ANCHOR.MIDDLE, spacing=200)
        text(s, LEFT, Inches(1.08), CONTENT_W, Inches(0.42), heading, size=19,
             color=INK, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        text(s, Inches(12.35), Inches(6.53), Inches(0.6), Inches(0.2), str(self.n),
             size=8, color=FAINT, align=PP_ALIGN.RIGHT)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def save(self, path):
        self.prs.save(path)


# ------------------------------------------------------------------ slide 1: set-up
def slide_setup(deck):
    s = deck.slide(
        "FRD → STTM AGENT — HOW IT IS SET UP",
        "AGENT 1 OF 2  ·  INSIDE THE AMERIHEALTH ENVIRONMENT  ·  ONCE, THEN AUTOMATIC",
        "Load every FRD and STTM once, pair what can be paired, then stay in sync "
        "with SharePoint on its own",
        notes=(
            "Set-up is entirely automatic code — no AI model is called at any point "
            "on this slide. SharePoint remains the system of record; Databricks "
            "(Unity Catalog) holds the working copies. Built 2026-08-22: one sync "
            "job (notebooks/00_sharepoint_sync.py, resources/frd_sttm_sync_job.yml) "
            "does the bulk load on its first tick and the incremental sync on every "
            "later tick (eTag/modified/size change detection, manifest in the "
            "reference volume); the same job is what the app's 'Sync now' triggers. "
            "Pairing is exact name match first, deterministic similarity second. "
            "The agent never writes to SharePoint — read grant only."
        ),
    )
    # three phase rows, each: number circle + label (left) → panel of cards (right)
    rows = [
        ("One-time bulk load",
         "Done once, when the\nagent is switched on",
         [
             ("external", "cloud_download", "Pull every FRD and STTM from SharePoint",
              "Every approved FRD and every STTM workbook in the AmeriHealth document "
              "library is read once — the site is not crawled beyond the folders you name."),
             ("platform", "folder_tree", "Route each file to its Databricks home",
              "FRDs land in the FRD area and STTMs in the STTM reference area of Unity "
              "Catalog — governed, permissioned, and visible to the agent."),
             ("code", "database", "Register every document",
              "Each file is recorded with its SharePoint identity, last-modified stamp "
              "and a content fingerprint, so unchanged files are never fetched twice "
              "and a revised document is recognised as new."),
         ]),
        ("Pair FRDs to STTMs",
         "As many matches as the\ndocuments allow",
         [
             ("code", "link", "Match each FRD to the STTM that belongs to it",
              "Plain code compares every FRD with every STTM and pairs the ones that "
              "describe the same feeds. No AI model is involved; every match comes with "
              "a score and a reason a person can read."),
             ("platform", "layers", "Two lists come out",
              "Paired FRD+STTM documents become the agent's template library — the "
              "examples it draws on. FRDs with no STTM become the queue the app offers "
              "to users (next slide)."),
         ]),
        ("Stay in sync — automatically",
         "Every time SharePoint\nchanges, from then on",
         [
             ("external", "bell_ring", "A new approved FRD appears in SharePoint",
              "The agent registers it, copies it into Databricks and checks whether any "
              "STTM already maps it. If one does, they are paired; if not, the FRD joins "
              "the unmapped queue."),
             ("external", "refresh", "A new STTM appears in SharePoint",
              "Same path: registered, copied to the STTM reference area, and checked "
              "against every FRD — including STTMs a user has just finished and uploaded "
              "(next slide). The template library grows."),
         ]),
    ]
    y0 = Inches(1.66)
    row_h = Inches(1.38)
    row_gap = Inches(0.17)
    label_x, label_w = LEFT, Inches(1.3)
    panel_x = Inches(2.2)
    panel_w = W - LEFT - panel_x
    pad = Inches(0.12)
    for i, (label, sub, cards) in enumerate(rows):
        y = y0 + i * (row_h + row_gap)
        number_circle(s, label_x, y + Inches(0.12), Inches(0.34), BLUE, i + 1)
        text(s, label_x + Inches(0.44), y + Inches(0.10), label_w, Inches(0.4), label,
             size=10.5, color=INK, bold=True)
        text(s, label_x + Inches(0.44), y + Inches(0.56), Inches(1.05), Inches(0.5), sub,
             size=7.5, color=MUTED)
        right_arrow(s, panel_x - Inches(0.32), y + row_h / 2 - Inches(0.1),
                    Inches(0.2))
        card(s, panel_x, y, panel_w, row_h, fill=PANEL, line=HAIR)
        n = len(cards)
        gap = Inches(0.12)
        cw = (panel_w - 2 * pad - gap * (n - 1)) / n
        ch = row_h - 2 * pad
        for j, (kind, icon, head, body) in enumerate(cards):
            cx = panel_x + pad + j * (cw + gap)
            cy = y + pad
            card(s, cx, cy, cw, ch)
            icon_circle(s, cx + Inches(0.14), cy + Inches(0.14), Inches(0.34),
                        KIND[kind][0], icon)
            text(s, cx + Inches(0.58), cy + Inches(0.14), cw - Inches(0.7), Inches(0.34),
                 head, size=10, color=BLUE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
            text(s, cx + Inches(0.14), cy + Inches(0.56), cw - Inches(0.28),
                 ch - Inches(0.62), body, size=9, color=BODY)
    # legend + takeaway
    ly = y0 + 3 * row_h + 2 * row_gap + Inches(0.12)
    legend(s, LEFT, ly, ["external", "platform", "code"])
    text(s, LEFT, ly + Inches(0.32), CONTENT_W, Inches(0.3),
         "SharePoint stays the system of record; Databricks (Unity Catalog) holds the "
         "working copies. No AI model is called during set-up or sync — the model runs "
         "only when a person asks for a mapping (next slide).",
         size=8, color=MUTED, italic=True)
    return s


# ------------------------------------------------------------------ slide 2: using the app
def slide_app(deck):
    s = deck.slide(
        "FRD → STTM AGENT — USING THE APP",
        "AGENT 1 OF 2  ·  WHAT HAPPENS WHEN A PERSON ASKS FOR A MAPPING",
        "Pick an FRD that has no STTM yet, let the agent draft it, review it, "
        "then publish it yourself",
        notes=(
            "The picker is the corpus index the sync maintains in Unity Catalog: "
            "unmapped FRDs are offered for generation; an FRD that already has an "
            "STTM is shown with that STTM, and regenerating it is a deliberate "
            "second step (every such run is scored against the existing workbook). "
            "One billed model call per run, confirmed by the user. The app has NO "
            "publish path — the person uploads the finished workbook to the "
            "SharePoint STTM folder; the sync on slide 1 pulls it back into "
            "Databricks and pairs it with its FRD by name. A cell-level accuracy "
            "score exists only when a reference STTM for that FRD is already in the "
            "corpus (regeneration); a first draft shows its grounding checks and "
            "verdict."
        ),
    )
    steps = [
        ("human", "Select an FRD",
         "The app lists only FRDs that have no STTM in Unity Catalog. The user picks "
         "one and confirms the run."),
        ("llm", "The agent runs",
         "One AI call reads the whole FRD. Code then checks every extracted value "
         "word-for-word against the document and drafts the STTM from the closest "
         "approved STTMs."),
        ("human", "Human-in-the-loop review",
         "The user answers only what the agent could not settle — pick a candidate, "
         "none of these, or type the answer. Decisions are remembered."),
        ("code", "The STTM is presented",
         "The drafted workbook appears with its quality checks and the templates it "
         "drew on. The user downloads it — nothing leaves the app on its own."),
        ("human", "Last edits or approval",
         "The user makes any final changes in Excel, or approves it as-is. The app "
         "never uploads to SharePoint automatically."),
        ("human", "Upload to SharePoint",
         "The user uploads the finished STTM to SharePoint themselves. The agent "
         "detects it, pulls it into Databricks and pairs it with its FRD."),
    ]
    y = Inches(1.66)
    h = Inches(2.05)
    n = len(steps)
    gap = Inches(0.16)
    cw = (CONTENT_W - gap * (n - 1)) / n
    for i, (kind, head, body) in enumerate(steps):
        x = LEFT + i * (cw + gap)
        card(s, x, y, cw, h, fill=PANEL, line=HAIR)
        number_circle(s, x + Inches(0.12), y + Inches(0.14), Inches(0.34),
                      KIND[kind][0], i + 1)
        text(s, x + Inches(0.12), y + Inches(0.56), cw - Inches(0.24), Inches(0.4),
             head, size=10.5, color=BLUE, bold=True)
        text(s, x + Inches(0.12), y + Inches(0.92), cw - Inches(0.24), h - Inches(1.0),
             body, size=9, color=BODY)
        if i < n - 1:
            right_arrow(s, x + cw + Inches(0.01), y + Inches(0.9))
    ly = y + h + Inches(0.16)
    legend(s, LEFT, ly, ["human", "llm", "code"])

    # three things to know
    ty = ly + Inches(0.5)
    text(s, LEFT, ty, Inches(6), Inches(0.28), "Three things to know", size=11.5,
         color=BLUE, bold=True)
    cards = [
        ("sheet", "Only unmapped FRDs are offered",
         "If an FRD already has an STTM, the app shows that STTM instead of drafting "
         "a new one. Regenerating is a deliberate, separate choice."),
        ("upload", "Nothing is published for you",
         "Download, edit, approve — and upload to SharePoint when you are satisfied. "
         "The agent only ever writes to Databricks, never to the library."),
        ("refresh", "Every approved STTM improves the next",
         "Once your upload is pulled back in and paired, it becomes a template the "
         "agent draws on — so the library, and the drafts, get better over time."),
    ]
    cy = ty + Inches(0.36)
    ch = Inches(1.3)
    gap = Inches(0.2)
    cw = (CONTENT_W - gap * 2) / 3
    for i, (icon, head, body) in enumerate(cards):
        x = LEFT + i * (cw + gap)
        card(s, x, cy, cw, ch)
        icon_circle(s, x + Inches(0.16), cy + Inches(0.18), Inches(0.34), BLUE, icon)
        text(s, x + Inches(0.6), cy + Inches(0.18), cw - Inches(0.76), Inches(0.34),
             head, size=10.5, color=INK, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + Inches(0.16), cy + Inches(0.64), cw - Inches(0.32), ch - Inches(0.72),
             body, size=9, color=BODY)
    return s


def main(out: Path):
    deck = Deck()
    slide_setup(deck)
    slide_app(deck)
    out.parent.mkdir(parents=True, exist_ok=True)
    deck.save(out)
    print(f"wrote {out} ({deck.n} slides)")


if __name__ == "__main__":
    default = HERE.parent / "context" / "FRD_to_STTM_Agent_Architecture.pptx"
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else default)
