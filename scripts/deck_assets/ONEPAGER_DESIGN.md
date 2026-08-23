# Signal — design philosophy for the one-slide architecture

> **v5 (2026-08-22, Arjun): TOP-DOWN.** The slide now reads as technology layers — System of record (Microsoft SharePoint; FRD .docx and approved STTM .xlsx as inputs; Entra ID / Graph read access) → Data platform (Databricks: Unity Catalog volumes + Delta, the Jobs pipeline 01–04 with Claude on stage 02, the Databricks App review UI on FastAPI + React) → Human review (gold; the approved STTM loops back up the left margin to SharePoint) and Outputs / downstream (indigo; STTM .xlsx, contract.json → CodeGen, Code Review). Each technology carries its official logo (see **Logos** below). The flow-field background and the Signal theme are unchanged.
>
> **v4 (2026-08-22, Arjun): the canvas is WHITE.** The philosophy below was written for the dark v3 and still governs; the theme table at the end is the light spec now in force, with the dark values kept for reference.

Applies to `context/FRD_to_STTM_Agent_System_Architecture.pptx`, built by
`scripts/build_architecture_onepager.py`. Written 2026-08-22 using the
canvas-design, algorithmic-art, brand-guidelines and theme-factory skills;
the theme below is the theme-factory "custom theme" Arjun chose over the
Tech Innovation and Midnight Galaxy presets.

## The movement: Signal

Information as signal against a quiet field. The slide is a dark instrument
panel, not a poster: a near-black ground, a faint computed field moving
across it, and five lit nodes where the system does work. Everything that
is not signal recedes — hairlines instead of boxes, one accent per meaning,
no decoration that does not encode something. The composition must read
from three metres in the first two seconds (five nodes, left to right, one
return loop) and still reward a close reading (stage chips, the one orange
node, the thumbnails of the documents going in and coming out).

Space is the primary material. Nodes sit on a strict horizontal axis with
equal gutters; nothing stacks under them except the single return loop and
a one-line footer. Type is large and few: a 30 pt title, 15 pt node titles,
11 pt body in near-white, and mono only for the small identifiers that name
stages — never for running text. Contrast is deliberately high: the lowest
text tone on the slide is a mid grey that still passes comfortably on the
black ground. Every box and label was placed, rendered, inspected and
re-placed; nothing touches, nothing wraps by accident.

Colour is a semantic code, not a palette of moods. Blueprint's dark neutrals
carry the structure (black `111418`, card `1c2127`, raised `252a31`, hairline
`383e47`, text ramp `8f99a8 → c5cbd3 → f6f7f9`). Four accents each mean one
thing and are never used decoratively: **Claude orange `d97757`** (Anthropic
brand accent) marks the *single* model call in the whole system and appears
nowhere else; **turquoise `13c9ba`** is deterministic code — sync, parse,
audit, gate, render; **gold `f0b726`** is a person; **indigo `9881f3`** is an
external system — the SharePoint library and the downstream agents. A reader
who learns the code once can read the whole slide by colour.

The field behind the panel is algorithmic, seeded and reproducible ("Field
Dynamics"): several thousand particles born on the left edge follow a
layered-noise vector field left to right — the direction the documents
travel — leaving faint trails that are densest at the two edges and calm in
the band where the nodes sit, so the art frames the content and never
competes with it. Same seed, same image, every build. It is meant to be felt
rather than noticed: the suggestion of a system in motion beneath a still,
exact reading surface.

Craft standard: this must look like the product of many hours by someone at
the top of their field — a Swiss-grid discipline applied to a dark,
contemporary data-product idiom. Refinement means removing, aligning and
sharpening what is already there, never adding another element.

## Theme spec (theme-factory custom theme) — light, in force since v4

| Role | Light (v4, current) | Dark (v3, reference) |
|---|---|---|
| Canvas | `ffffff` + pale generated field | `111418` + dark field |
| Card / raised / hairline / hairline-2 | `f6f7f9` / `edeff2` / `d3d8de` / `c5cbd3` | `1c2127` / `252a31` / `383e47` / `404854` |
| Text — primary / secondary / muted | `1c2127` / `404854` / `738091` (title `111418`) | `f6f7f9` / `c5cbd3` / `8f99a8` |
| LLM — shape / text / tint | `d97757` Claude orange / `a04d2c` / `fbeee8` | `d97757` / — / `8a4a36` |
| Deterministic code — shape / text | `00a396` / `007067` | `13c9ba` / `007067` |
| Human — shape / text | `d1980b` / `866103` | `f0b726` / `866103` |
| External system — shape / text | `7961db` / `5642a6` | `9881f3` / `634dbf` |
| Icons | `scripts/deck_assets/icons_light` (`404854`) | `icons_dark` (`c5cbd3`) |
| Headings / body / identifiers / small labels | Calibri Bold / Calibri / **Consolas Bold** (stage + node ids) / **Calibri Bold caps, tracked** (chips, header line, loop caption) | Courier New for ids + labels |

Two rules that made the light version work: (1) thin monospace (Courier New) at 8–9 pt renders nearly invisible in PowerPoint on white — PowerPoint screenshot 2026-08-22 9:03 PM — so small labels are bold Calibri caps and ids bold Consolas, and the secondary/muted text shades were darkened to `2f343c` / `5f6b7c`; (2) every accent has a *shape* shade
(dots, borders, dashed loop) and a *text* shade two steps darker, because
the bright Blueprint accents that glow on black fail contrast on white.

## Conceptual seed (kept quiet)

The particles cross left to right and thin out exactly where the human
node sits — the field is the automated flow; the calm is the review.

## Logos (v5) — `scripts/deck_assets/logos/`

Official marks, trimmed of margins, used at small size on light cards and
never recoloured. Sources (all fetched 2026-08-22):

| File | Mark | Source |
|---|---|---|
| `sharepoint.png` | Microsoft SharePoint (2019–present) | Wikimedia Commons `Microsoft_Office_SharePoint_(2019–present).svg` |
| `word.png` / `excel.png` | Microsoft Word / Excel (2019–present) | Wikimedia Commons `Microsoft_Office_Word_(2019–present).svg`, `…Excel…svg` |
| `entra.png` | Microsoft Entra ID | Wikimedia Commons `Microsoft_Entra_ID_color_icon.svg` |
| `databricks.png` | Databricks | Wikimedia Commons `Databricks_Logo.png` |
| `unitycatalog.png` | Unity Catalog | github.com/unitycatalog/unitycatalog `docs/assets/images/uc-logo.png` |
| `claude.png` / `anthropic.png` | Claude / Anthropic | Wikimedia Commons `Claude_AI_logo.svg`, `Anthropic_logo.svg` |
| `fastapi.png` / `react.png` | FastAPI / React | Wikimedia Commons `FastAPI_logo.svg`, `React-icon.svg` |
| `json.png` | JSON | Wikimedia Commons `JSON_vector_logo.svg` |

SVGs were rasterised at 512 px with sharp (`scratch icons/raster.js`) and
trimmed with Pillow. Trademarks belong to their owners; the slide is an
internal architecture document that names the technologies it uses.
