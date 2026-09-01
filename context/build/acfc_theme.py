"""ACFC house style — the shared theme + primitives for every deck in context/.

Sampled live from amerihealthcaritas.com on 2026-08-27 rather than guessed, so
a Hexaware deck reads as native material in an ACFC room:

  palette   #003DA5 primary blue (h1, primary controls, links — 75 uses on the
                    home page), #0A2458 deep navy (h2, dark bands, footer),
                    #2D2D2D body ink, #333F48 / #455560 slate secondary,
                    #69B3E7 light-blue accent, #F0F0F0 band, #EEEEEE hairline.
            #D2202F flag red is sampled from the logo mark itself and is used
            ONLY as the attention colour — "the agent will not guess this".
  type      the site sets Area Normal (a licensed webfont). Segoe UI is the
            nearest geometric-humanist face shipping with Windows Office, so a
            deck renders true on an ACFC or Hexaware laptop with nothing to
            install. Headings bold, slightly negative tracking (the site sets
            -1.4% on its h1).
  geometry  SQUARE CORNERS EVERYWHERE, no shadows. The site's buttons are 0px
            radius, outlined and unfilled — the loudest signal in the system.
  layout    flat alternating white / #F0F0F0 bands, wide gutters, no card
            chrome; columns separated by whitespace, not by boxes.

THE ACFC LOGO: cleared for use by Arjun on 2026-08-27 ("use the ACFC logo
wherever you feel is necessary"), so `acfc_logo()` places the real mark and it
is the masthead device on every deck. Before that clearance the decks used
`flag_bars`, which evoked the flag in the mark without reproducing it; that
helper is KEPT for surfaces where the full mark is too heavy or too small to
read (a 0.6in slot, a repeated footer), not as a stand-in for a logo we are
now allowed to use. Two variants ship: `acfc.png` (full colour, for white and
tinted grounds) and `acfc_white.png` (knockout, for the navy bands) — a
colour logo on navy is the one placement that always looks wrong.

This module replaced the "Signal" theme (scripts/deck_assets/ONEPAGER_DESIGN.md)
as the house style for context/ on 2026-08-27, at Arjun's direction. That
document is kept as the record of the previous system.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ASSETS = Path(__file__).resolve().parent / "deck_assets"
LOGOS = ASSETS / "logos"
ICONS = ASSETS / "icons_light"

# --- ACFC palette (sampled from amerihealthcaritas.com, 2026-08-27) ---------
BLUE, NAVY, SKY = "003da5", "0a2458", "69b3e7"
RED = "d2202f"
INK, SLATE, MUTED = "2d2d2d", "333f48", "455560"
BAND, HAIR, HAIR2, WHITE = "f0f0f0", "e2e5e9", "cdd2d8", "ffffff"
TINT_BLUE, TINT_SKY, TINT_RED = "eaf0fa", "eef6fd", "fcecee"

SANS, MONO = "Segoe UI", "Consolas"
W_IN, H_IN = 13.333, 7.5
L = 0.75
R = W_IN - 0.75
CW = R - L


# --------------------------------------------------------------------------- #
# primitives — square corners, no shadows, ever
# --------------------------------------------------------------------------- #
def rgb(h):
    return RGBColor.from_string(h.upper())


def _flat(shape):
    spPr = shape._element.spPr
    spPr.append(spPr.makeelement(qn("a:effectLst"), {}))


def rect(s, x, y, w, h, *, fill=BAND, line=None, line_w=1.0):
    sh = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = rgb(fill)
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = rgb(line)
        sh.line.width = Pt(line_w)
    _flat(sh)
    sh.text_frame.text = ""
    return sh


def line(s, x1, y1, x2, y2, *, color=HAIR, width=1.0):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    return c


def text(s, x, y, w, h, runs, *, size=11, color=SLATE, font=SANS, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=None, line_spacing=None):
    """runs: str | [(txt, opts), ...] | [[(txt, opts), ...], ...]  (paragraphs)"""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs
    if isinstance(runs, str):
        paras = [[(runs, {})]]
    elif runs and not isinstance(runs[0], list):
        paras = [runs]
    for pi, para in enumerate(paras):
        p = tf.paragraphs[0] if pi == 0 else tf.add_paragraph()
        p.alignment = align
        if line_spacing:
            p.line_spacing = line_spacing
        for t, ov in para:
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = ov.get("font", font)
            f.size = Pt(ov.get("size", size))
            f.bold = ov.get("bold", bold)
            f.color.rgb = rgb(ov.get("color", color))
            sp = ov.get("spacing", spacing)
            if sp is not None:
                r._r.get_or_add_rPr().set("spc", str(int(sp * 100)))
    return tb


def square(s, x, y, d, color):
    rect(s, x, y, d, d, fill=color)


def outline_chip(s, x, y, label, *, color=BLUE, size=9):
    """The site's signature control: square, outlined, unfilled."""
    w = 0.093 * len(label) + 0.46
    rect(s, x, y, w, 0.32, fill=None, line=color, line_w=1.0)
    text(s, x, y + 0.075, w, 0.2, [(label.upper(), {})], size=size, color=color,
         font=SANS, bold=True, spacing=0.8, align=PP_ALIGN.CENTER)
    return x + w + 0.14


def flag_bars(s, x, y, *, w=0.86, color=RED):
    """Three tapering bars — the flag in the mark, evoked not reproduced."""
    for i, (dx, dw) in enumerate(((0.0, w), (0.05, w * 0.86), (0.12, w * 0.70))):
        rect(s, x + dx, y + i * 0.10, dw, 0.055, fill=color)


# --------------------------------------------------------------------------- #
# slide chrome
# --------------------------------------------------------------------------- #
def new_slide(prs, *, band=False):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    rect(s, 0, 0, W_IN, H_IN, fill=WHITE)
    if band:
        rect(s, 0, 0, W_IN, 1.86, fill=BAND)
    rect(s, 0, 0, 0.11, H_IN, fill=BLUE)          # the spine
    return s


BOTTOM = 6.84          # nothing may extend past this; the footer rule is at 6.90


def head(s, kicker, title, sub=None):
    """Returns the y at which slide content may begin (1.86, or 2.08 if the
    subtitle wraps to two lines). Kept tight on purpose — every slide in this
    deck is content-dense and the QA render showed 5 of 14 overflowing the
    footer with the looser block."""
    text(s, L, 0.44, CW, 0.22, [(kicker.upper(), {})],
         size=9, color=BLUE, font=SANS, bold=True, spacing=1.6)
    # CW - 1.7 so a title never runs under a mark placed top-right by the
    # caller; on slides with no mark the extra gutter is invisible.
    text(s, L, 0.70, CW - 1.7, 0.44, [(title, {})],
         size=24, color=NAVY, font=SANS, bold=True, spacing=-0.35)
    extra = 0.0
    if sub:
        text(s, L, 1.24, CW - 0.5, 0.56, [(sub, {})], size=11.5, color=MUTED,
             font=SANS, line_spacing=1.20)
        extra = 0.22 if len(sub) > 150 else 0.0
    line(s, L, 1.64 + extra, R, 1.64 + extra, color=HAIR2)
    return 1.86 + extra


def foot(s, n, note=""):
    line(s, L, H_IN - 0.60, R, H_IN - 0.60, color=HAIR2)
    if note:
        text(s, L, H_IN - 0.47, CW - 1.1, 0.24, [(note, {})], size=8.5, color=MUTED, font=SANS)
    text(s, R - 1.0, H_IN - 0.47, 1.0, 0.24, [(f"{n:02d}", {})],
         size=9, color=BLUE, font=MONO, bold=True, align=PP_ALIGN.RIGHT)


def panel(s, x, y, w, h, *, accent=BLUE, fill=BAND):
    """Flat band with a square accent edge — the deck's only container."""
    rect(s, x, y, w, h, fill=fill)
    rect(s, x, y, 0.055, h, fill=accent)


def panel_title(s, x, y, w, title, *, kicker=None, accent=BLUE):
    ty = y + 0.20
    if kicker:
        text(s, x + 0.30, ty, w - 0.5, 0.2, [(kicker.upper(), {})],
             size=8.5, color=accent, font=SANS, bold=True, spacing=1.3)
        ty += 0.26
    text(s, x + 0.30, ty, w - 0.5, 0.32, [(title, {})],
         size=14, color=NAVY, font=SANS, bold=True, spacing=-0.2)
    return ty + 0.40


def bullets(s, x, y, w, items, *, size=10, color=SLATE, gap=0.36, mark=BLUE):
    for it in items:
        square(s, x, y + 0.085, 0.075, mark)
        runs = it if isinstance(it, list) else [(it, {})]
        text(s, x + 0.22, y - 0.015, w - 0.22, gap * 2, [runs], size=size,
             color=color, font=SANS, line_spacing=1.14)
        y += gap
    return y


def table(s, x, y, cols, rows, *, rh=None, bottom=None, size=9.6, zebra=True):
    """`bottom` auto-fits the row height so the table cannot run into the
    callout beneath it — the failure mode the first QA render showed on three
    of the four table slides."""
    if bottom is not None:
        rh = (bottom - y - 0.39) / len(rows)
    tot = sum(c[1] for c in cols)
    cx = x
    for label, w, *_ in cols:
        text(s, cx, y, w - 0.16, 0.22, [(label.upper(), {})],
             size=8.5, color=BLUE, font=SANS, bold=True, spacing=1.1)
        cx += w
    yy = y + 0.30
    line(s, x, yy, x + tot, yy, color=NAVY, width=1.25)
    yy += 0.09
    for i, cells in enumerate(rows):
        if zebra and i % 2 == 1:
            rect(s, x - 0.10, yy - 0.05, tot + 0.20, rh, fill=BAND)
        cx = x
        for (label, w, *rest), cell in zip(cols, cells):
            o = dict(rest[0]) if rest else {}
            runs = cell if isinstance(cell, list) else [(str(cell), {})]
            text(s, cx, yy, w - 0.16, rh,
                 [runs] if not isinstance(runs[0], list) else runs,
                 size=o.get("size", size), color=o.get("color", SLATE),
                 font=o.get("font", SANS), bold=o.get("bold", False),
                 align=o.get("align", PP_ALIGN.LEFT), line_spacing=1.1)
            cx += w
        yy += rh
    return yy


def callout(s, x, y, w, h, runs, *, accent=RED, fill=TINT_RED, size=11):
    panel(s, x, y, w, h, accent=accent, fill=fill)
    text(s, x + 0.32, y + 0.16, w - 0.62, h - 0.24,
         [runs] if not isinstance(runs[0], list) else runs,
         size=size, color=SLATE, font=SANS, line_spacing=1.20)




# --------------------------------------------------------------------------- #
# artwork
# --------------------------------------------------------------------------- #
def add_image(s, path, x, y, w, h):
    """Fit-inside placement — logos keep their aspect ratio and are centred in
    the box, so a wordmark and a glyph can share a row without distortion."""
    iw, ih = Image.open(path).size
    k = min(w / iw, h / ih)
    s.shapes.add_picture(str(path), Inches(x + (w - iw * k) / 2),
                         Inches(y + (h - ih * k) / 2), Inches(iw * k), Inches(ih * k))


def logo(s, name, x, y, w, h):
    p = LOGOS / f"{name}.png"
    if p.exists():
        add_image(s, p, x, y, w, h)


def icon(s, name, x, y, size):
    p = ICONS / f"{name}.png"
    if p.exists():
        add_image(s, p, x, y, size, size)


def arrow(s, x1, y1, x2, y2, *, color=None, width=1.5, dash=None):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color or BLUE)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    if dash:
        ln.append(ln.makeelement(qn("a:prstDash"), {"val": dash}))
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def notes(slide, text: str) -> None:
    """Attach speaker notes.

    Load-bearing in a hard-capped deck: material cut from the slides is not
    deleted, it moves here, so three slides can still answer a follow-up
    question without a fourth slide appearing.
    """
    slide.notes_slide.notes_text_frame.text = text.strip()


def acfc_logo(slide, x, y, w, *, knockout: bool = False) -> float:
    """Place the AmeriHealth Caritas mark, left-aligned, and return its height.

    Width-driven: the mark's aspect (1.516:1) is preserved from the file, so a
    caller sets the width the layout allows and gets the height back to lay out
    against. Never scale it below ~0.9in wide — "Care is the heart of our work"
    is part of the lockup and turns to mud.

    `knockout=True` selects the white variant for the navy footer bands.
    """
    name = "acfc_white" if knockout else "acfc"
    path = LOGOS / f"{name}.png"
    if not path.exists():
        return 0.0
    iw, ih = Image.open(path).size
    h = w * ih / iw
    slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
    return h
