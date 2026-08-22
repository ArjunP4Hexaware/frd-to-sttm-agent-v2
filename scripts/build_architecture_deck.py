"""Build the FRD-to-STTM ARCHITECTURE deck (PPTX) — how the agent is
intended to work, in the program's ACFC visual system.

    .venv/bin/pip install python-pptx     # not a runtime dep; deck-building only
    .venv/bin/python scripts/build_architecture_deck.py [output.pptx]

Default output: context/FRD_to_STTM_Agent_Architecture.pptx

Self-contained per this repo's deck convention: the shared deck_lib helpers
(identical visual system to scripts/build_acfc_deck.py — navy/teal, Calibri,
absolute 16:9 layout, LLM/code/human/data step colors) are copied in, then
this deck's content follows. Layout is absolute-positioned; text length
drives fit — shorten copy rather than growing boxes.

Facts sourced 2026-08-22 from frd-to-sttm-master-context-document.md,
CLAUDE.md, docs/TEMPLATE_ARCHITECTURE.md, docs/LIVE_E2E_2026-08-07.md.
"""
# ---------------------------------------------------------------------------
# deck_lib — shared slide-building helpers (python-pptx, absolute layout, 16:9)
# Every deck in the AI-in-Engineering program uses this same visual system so
# the five agent decks read as one family. Copy is short by design: layout is
# absolute-positioned, so text length drives fit. Shorten copy, don't grow boxes.
# ---------------------------------------------------------------------------
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

# ------------------------------------------------------------------ palette
NAVY = RGBColor(0x0E, 0x22, 0x33)
NAVY_L = RGBColor(0x1B, 0x3A, 0x52)
INK = RGBColor(0x1A, 0x24, 0x33)
BODY = RGBColor(0x3A, 0x4A, 0x5C)
MUTED = RGBColor(0x6B, 0x7A, 0x8C)
TEAL = RGBColor(0x00, 0xA1, 0x9A)
TEAL_D = RGBColor(0x00, 0x7C, 0x76)
BLUE = RGBColor(0x2E, 0x6E, 0xD1)
AMBER = RGBColor(0xD9, 0x8A, 0x1F)
RED = RGBColor(0xC2, 0x3B, 0x3B)
GREEN = RGBColor(0x1E, 0x7A, 0x46)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PAPER = RGBColor(0xF4, 0xF7, 0xF9)
LINE = RGBColor(0xD8, 0xE0, 0xE7)
PALE = RGBColor(0xC3, 0xD2, 0xDE)
TEAL_BG = RGBColor(0xE3, 0xF5, 0xF4)
BLUE_BG = RGBColor(0xE6, 0xEF, 0xFB)
AMBER_BG = RGBColor(0xFB, 0xF1, 0xE1)
GREY_BG = RGBColor(0xEE, 0xF1, 0xF4)

KIND_COLOR = {  # pipeline step kinds
    "llm": (BLUE, BLUE_BG, "LLM step (Claude)"),
    "code": (TEAL_D, TEAL_BG, "Deterministic code"),
    "human": (AMBER, AMBER_BG, "Human decision"),
    "data": (MUTED, GREY_BG, "Artifact / data"),
}

FONT = "Calibri"
FONT_H = "Calibri Light"

W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.7)
CONTENT_W = W - 2 * MARGIN
TOP = Inches(1.45)  # content starts below title band
BOTTOM = Inches(6.75)  # content must stay above footer band


class Deck:
    def __init__(self, agent_label, program_label="Hexaware × ACFC · AI-in-Engineering"):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = W, H
        self.blank = self.prs.slide_layouts[6]
        self.agent_label = agent_label
        self.program_label = program_label
        self.n = 0

    # ------------------------------------------------------------ primitives
    def slide(self, dark=False):
        s = self.prs.slides.add_slide(self.blank)
        self.n += 1
        if dark:
            rect(s, 0, 0, W, H, fill=NAVY)
        return s

    def save(self, path):
        self.prs.save(path)

    def footer(self, s, dark=False):
        col = PALE if dark else MUTED
        line_col = NAVY_L if dark else LINE
        rect(s, MARGIN, Inches(6.98), CONTENT_W, Pt(0.75), fill=line_col)
        text(s, MARGIN, Inches(7.02), Inches(8), Inches(0.35),
             f"{self.program_label}  ·  {self.agent_label}", size=10, color=col)
        text(s, W - MARGIN - Inches(1.5), Inches(7.02), Inches(1.5), Inches(0.35),
             str(self.n), size=10, color=col, align=PP_ALIGN.RIGHT)

    def title(self, s, title_txt, kicker=None, dark=False):
        col = WHITE if dark else INK
        kcol = TEAL if dark else TEAL_D
        y = Inches(0.42)
        if kicker:
            text(s, MARGIN, y, CONTENT_W, Inches(0.3), kicker.upper(), size=11,
                 color=kcol, bold=True, font=FONT, spacing=True)
            y = Inches(0.68)
        text(s, MARGIN, y, CONTENT_W, Inches(0.7), title_txt, size=28,
             color=col, bold=True, font=FONT_H)
        rect(s, MARGIN, Inches(1.32), Inches(1.1), Pt(3), fill=TEAL)

    def notes(self, s, txt):
        s.notes_slide.notes_text_frame.text = txt

    # ------------------------------------------------------------ slide types
    def cover(self, title_txt, subtitle, meta_lines, notes=None):
        s = self.slide(dark=True)
        rect(s, 0, 0, Inches(0.35), H, fill=TEAL)
        text(s, Inches(1.0), Inches(0.9), Inches(10), Inches(0.4),
             self.program_label.upper(), size=12, color=TEAL, bold=True, spacing=True)
        text(s, Inches(1.0), Inches(2.0), Inches(11.3), Inches(1.9), title_txt, size=44,
             color=WHITE, bold=True, font=FONT_H)
        text(s, Inches(1.0), Inches(4.0), Inches(11), Inches(1.0), subtitle, size=20,
             color=PALE, font=FONT_H)
        y = Inches(5.5)
        for ln in meta_lines:
            text(s, Inches(1.0), y, Inches(11), Inches(0.35), ln, size=13, color=PALE)
            y += Inches(0.36)
        if notes:
            self.notes(s, notes)
        return s

    def statement(self, kicker, big, small=None, notes=None):
        """One big idea per slide."""
        s = self.slide()
        self.title(s, kicker)
        text(s, MARGIN, Inches(1.7), CONTENT_W, Inches(2.8), big, size=26, color=NAVY,
             bold=True, font=FONT_H, anchor=MSO_ANCHOR.MIDDLE)
        if small:
            text(s, MARGIN, Inches(4.75), CONTENT_W, Inches(1.9), small, size=15, color=BODY)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def bullets_slide(self, title_txt, items, kicker=None, notes=None, takeaway=None,
                      size=17):
        s = self.slide()
        self.title(s, title_txt, kicker)
        bottom = BOTTOM - (Inches(0.85) if takeaway else 0)
        bullets(s, MARGIN, TOP, CONTENT_W, bottom - TOP, items, size=size)
        if takeaway:
            takeaway_bar(s, takeaway)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def two_col(self, title_txt, left_head, left_items, right_head, right_items,
                kicker=None, notes=None, takeaway=None, left_tone="grey",
                right_tone="teal", size=15):
        s = self.slide()
        self.title(s, title_txt, kicker)
        bottom = BOTTOM - (Inches(0.85) if takeaway else 0)
        gap = Inches(0.35)
        cw = (CONTENT_W - gap) / 2
        for i, (head, items, tone) in enumerate(
                [(left_head, left_items, left_tone), (right_head, right_items, right_tone)]):
            x = MARGIN + i * (cw + gap)
            panel(s, x, TOP, cw, bottom - TOP, head, items, tone=tone, size=size)
        if takeaway:
            takeaway_bar(s, takeaway)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def cards(self, title_txt, items, kicker=None, cols=3, notes=None, takeaway=None,
              body_size=13):
        """items: list of (heading, body[, tone])."""
        s = self.slide()
        self.title(s, title_txt, kicker)
        bottom = BOTTOM - (Inches(0.85) if takeaway else 0)
        rows = (len(items) + cols - 1) // cols
        gap = Inches(0.28)
        cw = (CONTENT_W - gap * (cols - 1)) / cols
        ch = (bottom - TOP - gap * (rows - 1)) / rows
        for i, it in enumerate(items):
            head, body = it[0], it[1]
            tone = it[2] if len(it) > 2 else "grey"
            r, c = divmod(i, cols)
            x = MARGIN + c * (cw + gap)
            y = TOP + r * (ch + gap)
            card(s, x, y, cw, ch, head, body, tone=tone, body_size=body_size)
        if takeaway:
            takeaway_bar(s, takeaway)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def kpis(self, title_txt, tiles, kicker=None, notes=None, source=None, below=None,
             cols=None, takeaway=None):
        """tiles: list of (big, label, sub). Optional `below` bullets under tiles."""
        s = self.slide()
        self.title(s, title_txt, kicker)
        cols = cols or min(len(tiles), 4)
        rows = (len(tiles) + cols - 1) // cols
        gap = Inches(0.25)
        cw = (CONTENT_W - gap * (cols - 1)) / cols
        th = Inches(1.9)
        bottom = BOTTOM - (Inches(0.85) if takeaway else 0)
        for i, (big, label, sub) in enumerate(tiles):
            r, c = divmod(i, cols)
            x = MARGIN + c * (cw + gap)
            y = TOP + r * (th + gap)
            kpi_tile(s, x, y, cw, th, big, label, sub)
        y_after = TOP + rows * (th + gap)
        if below:
            bullets(s, MARGIN, y_after + Inches(0.1), CONTENT_W,
                    bottom - y_after - Inches(0.5), below, size=15)
        if source:
            text(s, MARGIN, bottom - Inches(0.4), CONTENT_W, Inches(0.4),
                 "Source: " + source, size=10, color=MUTED, italic=True)
        if takeaway:
            takeaway_bar(s, takeaway)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def flow_slide(self, title_txt, steps, kicker=None, notes=None, below=None,
                   takeaway=None, legend=True, y=None, step_h=None):
        """steps: list of (label, sub, kind). Rendered as a left-to-right flow."""
        s = self.slide()
        self.title(s, title_txt, kicker)
        y = y or (TOP + Inches(0.25))
        step_h = step_h or (Inches(1.8) if len(steps) <= 5 else Inches(2.2))
        flow(s, MARGIN, y, CONTENT_W, step_h, steps)
        yy = y + step_h + Inches(0.15)
        if legend:
            legend_row(s, MARGIN, yy, [k for k in ("llm", "code", "human", "data")
                                       if any(st[2] == k for st in steps)])
            yy += Inches(0.45)
        bottom = BOTTOM - (Inches(0.85) if takeaway else 0)
        if below:
            bullets(s, MARGIN, yy + Inches(0.1), CONTENT_W, bottom - yy - Inches(0.1),
                    below, size=14)
        if takeaway:
            takeaway_bar(s, takeaway)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def table_slide(self, title_txt, headers, rows, kicker=None, notes=None,
                    col_widths=None, takeaway=None, source=None, size=12,
                    highlight_last=False):
        s = self.slide()
        self.title(s, title_txt, kicker)
        bottom = BOTTOM - (Inches(0.85) if takeaway else 0)
        if source:
            bottom -= Inches(0.6)
        table(s, MARGIN, TOP, CONTENT_W, bottom - TOP, headers, rows,
              col_widths=col_widths, size=size, highlight_last=highlight_last)
        if source:
            text(s, MARGIN, bottom + Inches(0.05), CONTENT_W, Inches(0.55),
                 "Source / assumptions: " + source, size=9.5, color=MUTED, italic=True)
        if takeaway:
            takeaway_bar(s, takeaway)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def program_map(self, title_txt, highlight, kicker=None, notes=None, below=None,
                    takeaway=None):
        """The five-agent program map with one agent highlighted."""
        s = self.slide()
        self.title(s, title_txt, kicker)
        agents = [
            ("BRD → FRD", "Business requirements →\nfunctional requirements", "brd"),
            ("FRD → STTM", "Functional requirements →\nsource-to-target mapping", "sttm"),
            ("CodeGen", "Mapping contract →\npipeline code + tests", "codegen"),
            ("Code Review", "PR / diff →\nreview findings + gate", "review"),
        ]
        y = TOP + Inches(0.3)
        bh = Inches(1.5)
        gap = Inches(0.55)
        bw = (CONTENT_W - gap * 3) / 4
        for i, (name, sub, key) in enumerate(agents):
            x = MARGIN + i * (bw + gap)
            hi = key == highlight
            box = rect(s, x, y, bw, bh, fill=(TEAL if hi else WHITE),
                       line=(TEAL if hi else PALE), lw=1.5,
                       shape=MSO_SHAPE.ROUNDED_RECTANGLE, adj=0.12)
            text(s, x, y + Inches(0.15), bw, Inches(0.45), name, size=16, bold=True,
                 color=(WHITE if hi else NAVY), align=PP_ALIGN.CENTER)
            text(s, x + Inches(0.1), y + Inches(0.6), bw - Inches(0.2), Inches(0.85), sub,
                 size=11, color=(WHITE if hi else BODY), align=PP_ALIGN.CENTER)
            if i < 3:
                arrow(s, x + bw + Inches(0.08), y + bh / 2, gap - Inches(0.16))
        # sidecar
        sy = y + bh + Inches(0.4)
        hi = highlight == "sql"
        sw = Inches(5.2)
        sx = MARGIN + (CONTENT_W - sw) / 2
        rect(s, sx, sy, sw, Inches(0.95), fill=(TEAL if hi else WHITE),
             line=(TEAL if hi else PALE), lw=1.5, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
             adj=0.2, dash=(not hi))
        text(s, sx, sy + Inches(0.08), sw, Inches(0.4), "SQL Optimization (standalone)",
             size=15, bold=True, color=(WHITE if hi else NAVY), align=PP_ALIGN.CENTER)
        text(s, sx, sy + Inches(0.48), sw, Inches(0.4),
             "Reads warehouse query telemetry, returns a ranked optimization backlog",
             size=11, color=(WHITE if hi else BODY), align=PP_ALIGN.CENTER)
        text(s, MARGIN, sy + Inches(1.05), CONTENT_W, Inches(0.35),
             "Human approval gate between every stage · shared hub + append-only ledger "
             "(agent-pipeline-orchestrator)", size=11, color=MUTED, align=PP_ALIGN.CENTER,
             italic=True)
        yy = sy + Inches(1.5)
        bottom = BOTTOM - (Inches(0.85) if takeaway else 0)
        if below:
            bullets(s, MARGIN, yy, CONTENT_W, bottom - yy, below, size=14)
        if takeaway:
            takeaway_bar(s, takeaway)
        self.footer(s)
        if notes:
            self.notes(s, notes)
        return s

    def closing(self, title_txt, lines, notes=None):
        s = self.slide(dark=True)
        rect(s, 0, 0, Inches(0.35), H, fill=TEAL)
        text(s, Inches(1.0), Inches(1.2), Inches(11), Inches(1.0), title_txt, size=36,
             color=WHITE, bold=True, font=FONT_H)
        bullets(s, Inches(1.0), Inches(2.5), Inches(11.3), Inches(4.0), lines, size=18,
                color=PALE, bullet_color=TEAL)
        self.footer(s, dark=True)
        if notes:
            self.notes(s, notes)
        return s


# ------------------------------------------------------------------ helpers
def rect(s, x, y, w, h, fill=None, line=None, lw=1.0, shape=MSO_SHAPE.RECTANGLE,
         adj=None, dash=False):
    sh = s.shapes.add_shape(shape, int(x), int(y), int(w), int(h))
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
        if dash:
            from pptx.enum.dml import MSO_LINE
            sh.line.dash_style = MSO_LINE.DASH
    sh.shadow.inherit = False
    return sh


def text(s, x, y, w, h, txt, size=14, color=INK, bold=False, italic=False, font=FONT,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=False, wrap=True):
    tb = s.shapes.add_textbox(int(x), int(y), int(w), int(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.margin_left = tf.margin_right = Emu(0)
    tf.margin_top = tf.margin_bottom = Emu(0)
    tf.vertical_anchor = anchor
    lines = txt.split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        r = p.add_run()
        r.text = ln
        f = r.font
        f.size = Pt(size)
        f.bold = bold
        f.italic = italic
        f.name = font
        f.color.rgb = color
        if spacing:
            rPr = r._r.get_or_add_rPr()
            rPr.set("spc", "150")
    return tb


def bullets(s, x, y, w, h, items, size=15, color=INK, bullet_color=TEAL, sub_color=BODY):
    """items: str | (str, level) | (str, level, {'bold':..,'color':..}). level 0/1."""
    tb = s.shapes.add_textbox(int(x), int(y), int(w), int(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Emu(0)
    tf.margin_top = tf.margin_bottom = Emu(0)
    first = True
    for it in items:
        if isinstance(it, str):
            it = (it, 0)
        txt, level = it[0], it[1]
        opts = it[2] if len(it) > 2 else {}
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(8 if level == 0 else 4)
        p.level = 0
        indent = "        " if level else ""
        r0 = p.add_run()
        r0.text = indent + ("▪  " if level == 0 else "–  ")
        r0.font.size = Pt(size if level == 0 else size - 2)
        r0.font.color.rgb = bullet_color if level == 0 else MUTED
        r0.font.name = FONT
        r0.font.bold = True
        r = p.add_run()
        r.text = txt
        r.font.size = Pt(opts.get("size", size if level == 0 else size - 2))
        r.font.bold = opts.get("bold", False)
        r.font.italic = opts.get("italic", False)
        r.font.name = FONT
        r.font.color.rgb = opts.get("color", color if level == 0 else sub_color)
    return tb


TONES = {
    "grey": (GREY_BG, MUTED, NAVY),
    "teal": (TEAL_BG, TEAL, TEAL_D),
    "blue": (BLUE_BG, BLUE, BLUE),
    "amber": (AMBER_BG, AMBER, AMBER),
    "navy": (NAVY, TEAL, WHITE),
    "white": (WHITE, LINE, NAVY),
}


def card(s, x, y, w, h, head, body, tone="grey", body_size=13):
    bg, accent, headcol = TONES[tone]
    rect(s, x, y, w, h, fill=bg, shape=MSO_SHAPE.ROUNDED_RECTANGLE, adj=0.06)
    rect(s, x, y, Pt(4), h, fill=accent)
    pad = Inches(0.22)
    text(s, x + pad, y + Inches(0.15), w - 2 * pad, Inches(0.55), head, size=15,
         bold=True, color=headcol)
    body_col = PALE if tone == "navy" else BODY
    if isinstance(body, (list, tuple)):
        bullets(s, x + pad, y + Inches(0.7), w - 2 * pad, h - Inches(0.8), body,
                size=body_size, color=body_col, sub_color=body_col,
                bullet_color=accent)
    else:
        text(s, x + pad, y + Inches(0.7), w - 2 * pad, h - Inches(0.8), body,
             size=body_size, color=body_col)


def panel(s, x, y, w, h, head, items, tone="grey", size=15):
    bg, accent, headcol = TONES[tone]
    rect(s, x, y, w, h, fill=bg, shape=MSO_SHAPE.ROUNDED_RECTANGLE, adj=0.04)
    rect(s, x, y, w, Inches(0.6), fill=accent, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
         adj=0.25)
    rect(s, x, y + Inches(0.3), w, Inches(0.3), fill=accent)  # square off bottom
    text(s, x + Inches(0.25), y + Inches(0.12), w - Inches(0.5), Inches(0.4), head,
         size=15, bold=True, color=WHITE)
    bullets(s, x + Inches(0.25), y + Inches(0.8), w - Inches(0.5), h - Inches(0.95),
            items, size=size, bullet_color=accent)


def kpi_tile(s, x, y, w, h, big, label, sub):
    rect(s, x, y, w, h, fill=WHITE, line=LINE, lw=1, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
         adj=0.08)
    rect(s, x, y + h - Pt(4), w, Pt(4), fill=TEAL)
    big_size = 32 if len(big) <= 8 else (26 if len(big) <= 11 else 21)
    text(s, x + Inches(0.2), y + Inches(0.15), w - Inches(0.4), Inches(0.75), big, size=big_size,
         bold=True, color=NAVY, font=FONT_H, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + Inches(0.2), y + Inches(0.9), w - Inches(0.4), Inches(0.35), label, size=13,
         bold=True, color=TEAL_D)
    text(s, x + Inches(0.2), y + Inches(1.22), w - Inches(0.4), Inches(0.5), sub, size=10.5,
         color=MUTED)


def arrow(s, x, y_center, length):
    a = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, int(x), int(y_center - Inches(0.11)),
                           int(length), int(Inches(0.22)))
    a.fill.solid()
    a.fill.fore_color.rgb = PALE
    a.line.fill.background()
    a.shadow.inherit = False
    return a


def flow(s, x, y, w, h, steps):
    n = len(steps)
    gap = Inches(0.32) if n <= 5 else Inches(0.26)
    bw = (w - gap * (n - 1)) / n
    sub_size = 10.5 if n <= 5 else 10
    for i, (label, sub, kind) in enumerate(steps):
        accent, bg, _ = KIND_COLOR[kind]
        bx = x + i * (bw + gap)
        rect(s, bx, y, bw, h, fill=bg, shape=MSO_SHAPE.ROUNDED_RECTANGLE, adj=0.1)
        rect(s, bx, y, bw, Pt(5), fill=accent)
        text(s, bx, y + Inches(0.15), bw, Inches(0.3), f"{i + 1}", size=11, bold=True,
             color=accent, align=PP_ALIGN.CENTER)
        text(s, bx + Inches(0.08), y + Inches(0.42), bw - Inches(0.16), Inches(0.5), label,
             size=13, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
        text(s, bx + Inches(0.1), y + Inches(0.9), bw - Inches(0.2), h - Inches(0.95), sub,
             size=sub_size, color=BODY, align=PP_ALIGN.CENTER)
        if i < n - 1:
            arrow(s, bx + bw + Inches(0.04), y + h / 2, gap - Inches(0.08))


def legend_row(s, x, y, kinds):
    cx = x
    for k in kinds:
        accent, bg, label = KIND_COLOR[k]
        rect(s, cx, y + Inches(0.08), Inches(0.22), Inches(0.22), fill=accent,
             shape=MSO_SHAPE.ROUNDED_RECTANGLE, adj=0.3)
        text(s, cx + Inches(0.3), y + Inches(0.03), Inches(2.4), Inches(0.35), label,
             size=11, color=BODY)
        cx += Inches(2.6)


def takeaway_bar(s, txt):
    y = BOTTOM - Inches(0.7)
    rect(s, MARGIN, y, CONTENT_W, Inches(0.7), fill=NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
         adj=0.2)
    rect(s, MARGIN + Inches(0.25), y + Inches(0.2), Pt(4), Inches(0.3), fill=TEAL)
    text(s, MARGIN + Inches(0.45), y, CONTENT_W - Inches(0.7), Inches(0.7), txt, size=14,
         color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)


def table(s, x, y, w, h, headers, rows, col_widths=None, size=12, highlight_last=False):
    nrows, ncols = len(rows) + 1, len(headers)
    shp = s.shapes.add_table(nrows, ncols, int(x), int(y), int(w),
                             int(min(h, Inches(0.62) * nrows)))
    tbl = shp.table
    if col_widths:
        tot = sum(col_widths)
        for i, cw in enumerate(col_widths):
            tbl.columns[i].width = int(w * cw / tot)
    row_h = int(min(h / nrows, Inches(0.62)))
    for r in range(nrows):
        tbl.rows[r].height = row_h

    def _cell(c, txt, bold=False, color=INK, fill=None, sz=size, align=PP_ALIGN.LEFT):
        c.text = ""
        tf = c.text_frame
        tf.word_wrap = True
        c.margin_left = c.margin_right = Inches(0.08)
        c.margin_top = c.margin_bottom = Inches(0.04)
        c.vertical_anchor = MSO_ANCHOR.MIDDLE
        lines = str(txt).split("\n")
        for i, ln in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            rr = p.add_run()
            rr.text = ln
            rr.font.size = Pt(sz)
            rr.font.bold = bold
            rr.font.name = FONT
            rr.font.color.rgb = color
        if fill is not None:
            c.fill.solid()
            c.fill.fore_color.rgb = fill

    for ci, hd in enumerate(headers):
        _cell(tbl.cell(0, ci), hd, bold=True, color=WHITE, fill=NAVY, sz=size)
    for ri, row in enumerate(rows, start=1):
        last = highlight_last and ri == nrows - 1
        for ci, val in enumerate(row):
            fill = TEAL_BG if last else (WHITE if ri % 2 else PAPER)
            _cell(tbl.cell(ri, ci), val, bold=(ci == 0 or last),
                  color=(TEAL_D if last else INK), fill=fill)
    # kill the default table style banding look
    tblPr = shp._element.graphic.graphicData.tbl.tblPr
    tblPr.set("bandRow", "0")
    tblPr.set("firstRow", "0")
    return shp



# ---------------------------------------------------------------------------
# Content: FRD → STTM Agent — architecture deck
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

OUT = (Path(sys.argv[1]) if len(sys.argv) > 1
       else Path(__file__).resolve().parent.parent / "context"
       / "FRD_to_STTM_Agent_Architecture.pptx")

d = Deck("FRD → STTM Agent · Architecture")

# 1 ── cover
d.cover(
    "FRD → STTM Agent",
    "How a Functional Requirements Document becomes a governed, "
    "evaluated Source-to-Target Mapping",
    ["Architecture overview · repo state 2026-08-22",
     "AmeriHealth Caritas (ACFC) · agent 1 of 3: FRD→STTM → CodeGen → Code Review",
     "Hexaware builds the reference implementation; ACFC rebuilds it in its own environment"],
    notes="Frame: this deck explains how the agent is INTENDED to work — the design, "
          "not a status report. One slide near the end separates proven from unproven.")

# 2 ── doctrine
d.statement(
    "The design doctrine",
    "The LLM proposes.\nDeterministic code audits and decides.\nA human resolves.",
    small="One model call per document — everything else is auditable code: grounding "
          "checks, gates, verdicts, similarity scores, template choices. The model is "
          "never the arbiter of “did we get this right”.",
    notes="This line is the whole architecture. Every later slide is an instance of it.")

# 3 ── where it sits (three-agent relay, drawn with primitives — program map
#      helper predates the 2026-08-21 rescope to three agents)
s = d.slide()
d.title(s, "Where it sits: first agent in a three-agent relay", kicker="Program context")
agents = [
    ("FRD → STTM", "Approved FRD (.docx) →\nSTTM workbook + mapping contract", True),
    ("CodeGen", "Feed + mapping contracts →\nPySpark/Delta pipelines + tests", False),
    ("Code Review", "PR / diff → ranked, grounded\nreview report (read-only)", False),
]
ry = TOP + Inches(0.45)
bh = Inches(1.6)
rgap = Inches(0.7)
bw = (CONTENT_W - rgap * 2) / 3
for i, (name, sub, hi) in enumerate(agents):
    x = MARGIN + i * (bw + rgap)
    rect(s, x, ry, bw, bh, fill=(TEAL if hi else WHITE), line=(TEAL if hi else PALE),
         lw=1.5, shape=MSO_SHAPE.ROUNDED_RECTANGLE, adj=0.10)
    text(s, x, ry + Inches(0.18), bw, Inches(0.45), name, size=17, bold=True,
         color=(WHITE if hi else NAVY), align=PP_ALIGN.CENTER)
    text(s, x + Inches(0.15), ry + Inches(0.68), bw - Inches(0.3), Inches(0.85), sub,
         size=11.5, color=(WHITE if hi else BODY), align=PP_ALIGN.CENTER)
    if i < 2:
        arrow(s, x + bw + Inches(0.1), ry + bh / 2, rgap - Inches(0.2))
bullets(s, MARGIN, ry + bh + Inches(0.5), CONTENT_W, Inches(2.2), [
    ("Two hand-off contracts", 0, {"bold": True}),
         ("downstream, CodeGen consumes the per-document feed "
     "contract JSON directly; the workbook→mapping-contract round trip exists but is unvalidated", 1),
    ("SharePoint is the system of record", 0, {"bold": True}),
         ("FRDs in, published STTM workbooks out; "
     "Unity Catalog is the working store in between", 1),
    ("Hexaware builds, ACFC rebuilds", 0, {"bold": True}),
         ("every non-obvious decision is written down with its "
     "reason — what cannot be re-derived from docs, contracts and config does not survive the hand-off", 1),
], size=14)
d.footer(s)
d.notes(s, "BRD→FRD and SQL Optimization were cut from the program 2026-08-21.")

# 4 ── end-to-end pipeline
d.flow_slide(
    "The pipeline: six stages, one model call",
    [("00 Fetch", "SharePoint library\n→ frd_raw volume", "data"),
     ("01 Ingest", "docx/pdf → faithful\nmarkdown snapshot", "code"),
     ("02 Extract", "ONE Claude call:\nschema + exemplars", "llm"),
     ("03 Gate", "validate · ground\n· gate ambiguities", "code"),
     ("Review", "analyst resolves\ngated items", "human"),
     ("04 Render", "template decision →\nSTTM .xlsx + eval", "code"),
     ("05 Publish", "confirm-gated,\none document", "human")],
    kicker="End to end",
    below=[
        ("Network lives at the edges", 0, {"bold": True}),
         ("stages 01–04 are offline and credential-free; "
         "the whole test suite runs with zero network", 1),
        ("The job deliberately ends at render", 0, {"bold": True}),
         ("a re-render after review is not a re-publish — "
         "publishing is a human decision, per document, behind an explicit confirm", 1),
        ("Every stage's output is durable", 0, {"bold": True}),
         ("Delta tables + UC volume artifacts; any stage "
         "re-runs without re-executing its predecessors", 1),
    ],
    notes="Dual-mode: the same six files run as plain local scripts and as Databricks "
          "notebook tasks (widgets ↔ env vars, Spark ↔ deltalake).")

# 5 ── extraction transport
d.two_col(
    "Stage 02 — the one model call",
    "What the model receives", [
        ("Terse system prompt", 0, {"bold": True}),
         ("extract only stated facts; never invent names, "
         "schedules, tables or rules; preserve identifiers verbatim", 1),
        ("The full JSON schema in the prompt", 0, {"bold": True}),
         ("field-level guidance rides on the schema; "
         "server-side structured output rejects this schema (“grammar too large”) — "
         "measured, not assumed", 1),
        ("Retrieved exemplars (when a corpus exists)", 0, {"bold": True}),
         ("mapping digests from the most "
         "similar APPROVED pairs — conventions only, with a byte-stable prompt prefix "
         "for caching", 1),
        ("The parsed FRD markdown", 0, {"bold": True}),
         ("the exact text stage 01 captured", 1),
    ],
    "What is structurally impossible", [
        ("Silent repair", 0, {"bold": True}),
         ("client-side validation with extra=“forbid”; a schema "
         "error raises immediately — deliberately NO re-ask loop", 1),
        ("Truncation passing as success", 0, {"bold": True}),
         ("a max-tokens stop is a hard failure", 1),
        ("Exemplar fact leakage", 0, {"bold": True}),
         ("stage 03 demands every strict field verbatim in the "
         "TARGET document — a copied table name fails like an invented one", 1),
        ("Silent mock in a workspace", 0, {"bold": True}),
         ("mock is laptop-only; in Databricks or the "
         "deployed App a mock request raises, never no-ops", 1),
    ],
    kicker="LLM boundary", left_tone="blue", right_tone="grey", size=13,
    takeaway="One streaming call per document — measured at ≈$0.15 and ~31 s on the live E2E.",
    notes="The removed-Gemini posture: schema-in-prompt + client validation is the committed transport.")

# 6 ── audit & gate
d.cards(
    "Stage 03 — every extracted string is audited",
    [("Strict grounding", "Identifiers, paths, patterns, table names must appear "
      "VERBATIM in the FRD. Any miss ⇒ the whole contract FAILS.", "teal"),
     ("Advisory grounding", "Prose (rules, retention, PHI notes) needs ≥0.75 token "
      "overlap. A miss flags the run and gates the field for review.", "teal"),
     ("Deterministic enrichment", "Regex-able facts (project id, LOB pairs) are code's "
      "job. If code and model disagree, BOTH values gate as a disagreement.", "grey"),
     ("Attribution check", "The same rule text on ≥2 feeds gates an ambiguity — "
      "a human picks the owning feeds, the agent never guesses.", "grey"),
     ("Three verdicts, computed in code", "PASS · PASS_WITH_FLAGS · FAIL — never "
      "model-declared. Exactly three ambiguity kinds; “other” is prohibited.", "amber"),
     ("Stable ambiguity ids", "Hash of kind+text+context — the join key that lets a "
      "reviewer's saved decisions survive re-runs.", "amber")],
    kicker="Quality gate", cols=3, body_size=12,
    takeaway="Never relax a gate to make a run pass — fix the extraction, the document, or review it.",
    notes="This audit is the agent's entire quality claim; it is also what makes exemplars safe.")

# 7 ── human review
d.table_slide(
    "Human review — structurally enforced, never optional",
    ["Gated ambiguity", "What it means", "Reviewer's move"],
    [["attribution", "One rule text appears on several feeds", "Pick the owning feed(s), or none-of-these"],
     ["disagreement", "Model and regex extracted different values", "Pick a candidate, or none-of-these"],
     ["advisory_grounding", "Prose drifted from the source text", "Edit the prose (free text + rationale)"]],
    kicker="HITL", col_widths=[2.6, 5.2, 4.1], size=13,
    takeaway="A human resolution, once applicable, is authoritative — the automatic cross-check never "
             "re-decides it, and every non-application is recorded with a reason.",
    notes="free_text on a candidate-having ambiguity is rejected by client AND server — "
          "the pick is structural, not a convention.")

# 8 ── template architecture: the idea
d.statement(
    "The template architecture",
    "Every approved FRD→STTM pair\nis a template.",
    small="On first run in an environment, the app ingests every FRD and STTM it can reach "
          "in SharePoint and pairs them deterministically — zero model calls. Generating a "
          "new STTM retrieves the most similar approved pairs: their conventions feed the "
          "extraction prompt, and the best-matching workbook drives the rendered layout and "
          "dictionary. The manager's “8–10 templates” are the curated special case; "
          "the corpus generalizes it.",
    notes="Decided + built 2026-08-22. Full rationale in docs/TEMPLATE_ARCHITECTURE.md.")

# 9 ── template machinery
d.flow_slide(
    "Template machinery — retrieval is code, not a model",
    [("Bootstrap", "crawl SharePoint\n→ volumes (0 LLM calls)", "data"),
     ("Pair + index", "similarity scoring,\ncorpus_index.json", "code"),
     ("Decide mode", "single · amalgam\n· freeform", "code"),
     ("Exemplars", "top-k pairs into the\nextraction prompt", "llm"),
     ("Render", "template layout +\ndictionary", "code"),
     ("Eval", "vs the doc's OWN STTM\n(excluded as template)", "code")],
    kicker="Corpus-driven generation",
    below=[
        ("Deterministic similarity, deliberately", 0, {"bold": True}),
         ("no embeddings vendor (the program is "
         "Anthropic-only and the Claude API has none) — identifier overlap + token cosine is free, "
         "offline-testable, and explainable: “84% of this workbook's columns appear in the FRD”", 1),
        ("Exclude-own is the honesty rule", 0, {"bold": True}),
         ("the ground-truth workbook never feeds its own render, "
         "so every regeneration is a real, automatic golden-pair eval — numbers fit for executives", 1),
        ("Every threshold lives in config", 0, {"bold": True}),
         ("seeded on synthetic fixtures; calibrated on real pairs "
         "before any demo", 1),
    ],
    notes="Ingest-all is NOT extract-all: the billed call happens only when a generation is requested.")

# 10 ── the three modes
d.cards(
    "Exactly three template modes — computed, recorded, shown",
    [("Single", "Top match clears the single threshold. That workbook is the template: "
      "its dialect, its dictionary, its conventions.", "teal"),
     ("Amalgam", "No single winner, several partial matches: top-k workbooks merge, "
      "first-wins per table key, the best match's dialect leads. Per-sheet provenance kept.", "blue"),
     ("Freeform", "Nothing matched. Best-effort render from the contract alone — feed "
      "metadata and rules, no column dictionary — and the run is FLAGGED. Never a silent guess.", "amber")],
    kicker="Mode decision", cols=3, body_size=13,
    takeaway="The decision + scored evidence land in the contract's provenance, the run report, "
             "and the app's results panel.",
    notes="A scored template that fails the structural feed match demotes to freeform and says so.")

# 11 ── runtime modes
d.two_col(
    "One app, two execution modes",
    "Local mode (a laptop)", [
        ("Subprocess pipeline", 0, {"bold": True}),
         ("01→04 spawned with run-scoped env insulation "
         "(suffixed schema + volumes)", 1),
        ("Provider pinned live", 0, {"bold": True}),
         ("mock stripped from the run env; key from env or .env", 1),
        ("Artifacts on disk", 0, {"bold": True}),
         ("gitignored local_dev_fixtures/ mirrors the volume layout", 1),
        ("What it is for", 0, {"bold": True}),
         ("development, offline smoke, and cheap live validation", 1),
    ],
    "Databricks App mode (deployed)", [
        ("The container never runs notebooks", 0, {"bold": True}),
         ("live runs trigger the bundle job via the "
         "Jobs API with the same insulation as job parameters", 1),
        ("Artifacts native to Unity Catalog", 0, {"bold": True}),
         ("runs survive App restarts; the container "
         "copy is a rehydratable cache", 1),
        ("Key from the secret scope", 0, {"bold": True}),
         ("no key in the container; a missing secret fails "
         "loudly inside the extract task", 1),
        ("Same UI, same gates", 0, {"bold": True}),
         ("billed-run confirm, manual publish, corpus panel — unchanged", 1),
    ],
    kicker="Runtime", left_tone="grey", right_tone="teal", size=13,
    takeaway="STTM_APP_MODE switches the run path; the pipeline code, gates and artifacts are identical.",
    notes="SharePoint attaches identically in both modes; the app also uploads corpus artifacts "
          "to the UC reference volume in databricks mode so the job can read them.")

# 12 ── storage map
d.table_slide(
    "Where everything lives",
    ["Store", "Contents"],
    [["frd_raw volume", "Source FRDs (corpus bootstrap + stage 00 land here)"],
     ["sttm_reference volume", "Reference STTM workbooks + corpus_index.json — the template library"],
     ["sttm_out volume (app runs: sttm_out_app/<suffix>)", "extractions/ · contracts/ · rendered/ · reports/ per document"],
     ["Delta: frd_documents · frd_contracts · frd_sttm_runs", "Stage summaries; 04 reads contract JSONs, not the table — the JSON carries human resolutions"],
     ["SharePoint output folder", "Published workbooks only — the ONLY place the agent ever writes in the library"],
     ["The git repo", "Code, contracts, config, docs. NO client documents, raw or derived — synthetic fixtures only"]],
    kicker="Storage", col_widths=[4.6, 7.3], size=12.5,
    takeaway="SharePoint is the system of record; Unity Catalog is the working store; the repo holds only code.",
    notes="The no-documents rule is 2026-08-22 policy: the anonymized demo pair and replay set were removed too.")

# 13 ── proven numbers
d.kpis(
    "Measured, not promised — and what is still unproven",
    [("≈$0.15", "per document", "one billed call, live E2E 2026-08-07"),
     ("~34 s", "pipeline wall-clock", "ingest → rendered workbook, local"),
     ("94.1%", "cell-level eval", "3,094 / 3,288 cells vs the golden pair"),
     ("158", "offline tests", "zero network, zero credentials")],
    kicker="Evidence",
    below=[
        ("Also measured", 0, {"bold": True}),
         ("strict grounding 45/45 · hallucination sweep 0/50 ungrounded · "
         "template modes verified end-to-end on synthetic fixtures", 1),
        ("Still unproven — treat as false until seen working", 0, {"bold": True}),
         ("SharePoint against a real tenant "
         "(Entra ID consent is the long pole) · any Databricks execution from this checkout "
         "(bundle run, Jobs-API path, Apps deploy) · similarity thresholds on real documents", 1),
    ],
    source="docs/LIVE_E2E_2026-08-07.md · frd-to-sttm-master-context-document.md §11 (2026-08-22)",
    notes="Be exact with executives: the left column is measured; the right list is scheduled "
          "verification, not doubt about the design.")

# 14 ── closing: intended operation
d.closing(
    "Intended operation, end to end",
    ["Connect once: SharePoint site + folders, Entra ID app registration, workspace volumes and secret scope",
     "Bootstrap the corpus — every FRD and STTM in the library, paired and indexed, zero model calls",
     "Pick any unmapped FRD (or deliberately regenerate a mapped one) — one confirm-gated billed run",
     "Review the gated ambiguities; re-render folds resolutions in; the eval scores it against the real STTM",
     "Publish the approved workbook back to SharePoint — explicit, per document, replacing the old one",
     "Every new approved pair makes the corpus — and the next mapping — better"],
    notes="The last line is the flywheel: approval feeds the template library, which feeds quality.")

d.save(OUT)
print(f"wrote {OUT} ({d.n} slides)")
