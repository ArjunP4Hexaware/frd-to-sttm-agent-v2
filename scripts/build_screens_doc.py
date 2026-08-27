"""Build the review app's SCREEN CATALOGUE — every page a user can reach.

    ~/.virtualenvs/frdsttm/bin/python scripts/build_screens_doc.py <shots_dir> [out.html]

Default output: context/FRD_to_STTM_Agent_Screens.html

Replaces the 2026-08-22 walkthrough of the same name, which predates both the
ACFC restyle and the vendor-dictionary work and so no longer shows the app
that exists.

WHAT THIS IS. One self-contained HTML file — every screenshot inlined as a
data URI, no network, no assets directory — catalogueing each screen of the
review app, the state that produces it, what a person can do there, and which
backend call sits behind it. It is written to be opened by someone who has
never run the app: an ACFC reviewer being trained, or the team rebuilding this
inside the ACFC environment, for whom the frontend is the contract (Arjun,
2026-08-27: "as long as the UI is the same between the Hexaware and ACFC
environment, that's half the battle").

HOW THE SHOTS WERE MADE — and why that matters. Every image is a REAL capture
of the running app against the synthetic corpus, driven through the browser,
not a mock-up. States that could not be reached without a billed model call or
a wired document source are listed in their own section with the exact trigger
and what appears — labelled as not captured rather than illustrated with a
fake. A screen catalogue that quietly includes invented screens is worse than
one with gaps in it, because the gaps are the honest part.

The page uses the ACFC house style (scripts/acfc_theme.py's palette, restated
here as CSS since this is a web page, not a deck) and carries the client's
mark, cleared for use 2026-08-27.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DEFAULT = REPO / "context" / "FRD_to_STTM_Agent_Screens.html"
LOGO = REPO / "scripts" / "deck_assets" / "logos" / "acfc.png"

# --- ACFC palette, the same values scripts/acfc_theme.py carries -----------
BLUE, NAVY, SKY, RED = "#003da5", "#0a2458", "#69b3e7", "#d2202f"
INK, SLATE, MUTED = "#2d2d2d", "#333f48", "#455560"
BAND, HAIR, WHITE = "#f4f5f7", "#dde1e5", "#ffffff"

#: (shot index, anchor, section, title, state, what you can do, backend)
SCREENS = [
    (14, "empty", "1 · Select FRD",
     "The picker — nothing synced yet",
     "The corpus index does not exist. This is a first-boot state, and the "
     "only one where the app has nothing to offer.",
     ["“Sync now” pulls every FRD, approved STTM and vendor dictionary from the document "
      "source into the volumes, pairs them, and lists them here.",
      "“Rebuild index” re-pairs from whatever the volumes already hold, without touching "
      "the source. Neither makes a model call.",
      "Both are two-step: they rewrite the volumes and the index, so the button asks first."],
     "GET /api/demo/corpus → built:false"),
    (15, "picker", "1 · Select FRD",
     "The picker — grouped by what each FRD is waiting on",
     "The normal state, shown here against the two real client feeds. THE RULE "
     "(2026-08-27): an FRD may be generated only when it has a matching vendor data "
     "dictionary AND does not already have an approved STTM. Everything else is still "
     "LISTED, with what it is waiting on — hiding a document would leave a reviewer "
     "wondering where it went.",
     ["Six tiles. The last one, “Ready to map”, is the number that decides what a reviewer "
      "can actually do on this screen.",
      "READY TO MAP — has a dictionary, has no STTM. The only group with a live button. "
      "Its chip carries the dictionary's column count and any gaps the parser found.",
      "WAITING ON A VENDOR DATA DICTIONARY — no STTM, nothing to ground the source columns. "
      "Listed, not selectable, and told exactly what to ask the vendor for.",
      "ALREADY MAPPED — the approved STTM is the system of record. Presented as-is; the "
      "“regenerate anyway” control was removed with this rule.",
      "The rule is enforced SERVER-SIDE in the run endpoint, not just by hiding a button: a "
      "caller with the URL gets a 400 naming the reason."],
     "GET /api/demo/corpus/frds → eligibility_status, generatable"),
    (16, "picker-full", "1 · Select FRD",
     "The picker — full list and the sync controls",
     "The same screen scrolled. Both groups, and the controls that refresh the corpus.",
     ["The mapped row shows its pairing (exact name match here) AND a red “no vendor "
      "dictionary” — both facts are true of that feed and both are shown.",
      "“Sync from SharePoint now” is disabled when no document source is configured on the "
      "backend — the button says why on hover rather than failing on click.",
      "The index timestamp is always visible, so nobody wonders how fresh the list is."],
     "GET /api/demo/corpus/frds · POST /api/demo/corpus/sync"),
    (13, "reindex", "1 · Select FRD",
     "Rebuilding the index — the confirmation step",
     "“Rebuild index” clicked once. The control does not act on the first click.",
     ["Two-step because it rewrites the corpus index. The copy states plainly what it does "
      "and does not do: it re-pairs from the volumes, does not re-read the source, and "
      "makes no AI calls.",
      "“Sync now” behaves the same way, with copy naming the source path it will read."],
     "POST /api/demo/corpus/sync {mode:\"reindex\"} → 202, then polled"),
    (9, "gate", "2 · The billed-run gate",
     "Start a live, billed run?",
     "Reached only by “Generate STTM” on an FRD in the READY TO MAP group. It is the one "
     "place in the app that spends money, and it is deliberately a full screen rather than "
     "a dialog.",
     ["States the expected scale before anything is spent: number of billed API calls, a "
      "cost estimate, and how long it will take end to end.",
      "Says where the output goes — a run-scoped location — and that nothing is written "
      "back to the document library.",
      "“Cancel” returns to the picker having done nothing."],
     "POST /api/demo/runs (only after Run it)"),
    (10, "mapped", "3 · An FRD that already has an STTM",
     "The approved mapping, presented as-is",
     "Reached by “View STTM”. Nothing is regenerated on selection — the approved workbook "
     "is presented first, because for a mapped feed that is usually all anyone wants.",
     ["Shows how the pair was matched (exact name / similarity + score) and the workbook's "
      "size and modified date. There is no regenerate control: a mapped FRD is not "
      "generatable, and the run endpoint would refuse it.",
      "The VENDOR DICTIONARY notice sits above the buttons on purpose: this is the screen "
      "where someone decides whether to spend a regeneration, and they need to know whether "
      "the source side is grounded BEFORE they spend it.",
      "Both documents download from here — the approved STTM and the dictionary it was "
      "built from."],
     "GET /api/demo/corpus/references/{name} · /dictionaries/{name}"),

    (1, "results-top", "4 · Results",
     "Results — extraction",
     "Where a finished run lands, and — since 2026-08-27 — reachable again later at "
     "?set=&doc=, so a completed run can be bookmarked, shared or reopened tomorrow.",
     ["EXTRACTION: what the model found — feeds, target tables, rules — and each feed's "
      "stage and standard targets with the requirement ids it came from."],
     "GET /api/demo/artifacts/{set}/results"),
    (3, "results-gate", "4 · Results",
     "Results — the gate, the verdict and the eval",
     "The middle of the same page. This is the section that answers “can I trust it?”.",
     ["AMBIGUITY GATE: how many ambiguities were detected, how many the dictionary "
      "cross-check cleared, and how many are waiting for a person — that last number turns "
      "red when it is not zero.",
      "Underneath it, the grounding audit's own numbers: strict checks run, strict checks "
      "failed, advisory checks run.",
      "PIPELINE VERDICT and ACCURACY VS GOLDEN STTM: the stage-04 status, and the score "
      "against a hand-built reference where one exists — the panel says so plainly when "
      "none does, rather than showing a number with nothing behind it."],
     "same payload"),
    (5, "results-mappings", "4 · Results",
     "Results — the rendered mapping and the hand-off",
     "The end of the page, and the end of the agent's part.",
     ["Every column mapping the workbook contains: source column, type, stage target, "
      "standard target. The audit rows (source “NA”) are visible too.",
      "“Next step — yours, not the agent's” states the hand-off in words: the draft is a "
      "draft until a person says otherwise; download it, edit in Excel, upload it to the "
      "STTM folder yourself. The app publishes nothing.",
      "Downloading the workbook is a GOVERNED action — it is the moment a generated STTM "
      "leaves the platform, so it is recorded with the file's fingerprint and the "
      "downloader's name."],
     "GET /api/demo/artifacts/{set}/workbook → audit event"),
]

#: States that exist in the code but could not be reached without a billed
#: call or a wired tenant. Named, not illustrated.
NOT_CAPTURED = [
    ("Waiting on a vendor data dictionary",
     "The middle group of the picker: an FRD with no STTM and no VDD, listed with a red "
     "left edge, a “no vendor dictionary” chip, “Cannot be generated” in place of a button, "
     "and a line telling the reviewer to ask the vendor for VDD_<feed>.xlsx.",
     "Both real feeds on hand fall in the other two groups — one is ready, one is already "
     "mapped — so the group is empty in this capture. Covered by tests rather than faked."),
    ("Run in progress",
     "Between the billed-run gate and the results. A live stage-by-stage progress view — "
     "01 ingest, 02 extract, 03 audit · gate, 04 render — streaming the running job's log "
     "lines, with the Databricks job-run link in databricks mode.",
     "Requires an actual billed run. Not faked here."),
    ("Human-in-the-loop review panel",
     "Appears inside the results page when the run gated anything: every gated question as "
     "a card (pick a candidate / none of these / type an answer), a resolved count, and one "
     "“Apply resolutions & re-render” that re-runs stage 04 only — no model call, nothing "
     "billed.",
     "The synthetic corpus gates zero items, so the panel correctly renders nothing. It "
     "needs a document the agent cannot fully ground."),
    ("Sync running / sync failed",
     "A progress notice while a sync is in flight (with the job-run link in databricks "
     "mode), a red notice naming the failure, and a “some files were skipped” notice "
     "listing each file and why.",
     "Requires a wired document source. The failure copy distinguishes whose problem it "
     "is: not configured, source refused, already running."),
    ("Runs unavailable",
     "A red one-line notice under the masthead when no model credential is configured. The "
     "picker still lists everything and existing STTMs still open; only generation is off.",
     "Captured earlier in the session; reproducible by starting the backend with no key."),
]


def data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def find_shots(shots_dir: Path) -> dict[int, Path]:
    """{trailing index: path} for screenshot-<ts>-<n>.jpg."""
    out: dict[int, Path] = {}
    for p in shots_dir.iterdir():
        m = re.search(r"-(\d+)\.(jpg|jpeg|png)$", p.name)
        if m:
            out[int(m.group(1))] = p
    return out


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CSS = f"""
*{{box-sizing:border-box}}
body{{margin:0;background:{WHITE};color:{INK};
 font:15px/1.55 "Segoe UI",-apple-system,"Helvetica Neue",Arial,sans-serif}}
code,.mono{{font-family:Consolas,"SF Mono",Menlo,monospace;font-size:.92em}}
.topline{{height:4px;background:{BLUE}}}
header.mast{{border-bottom:1px solid {HAIR};background:{WHITE}}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 28px}}
header.mast .wrap{{display:flex;align-items:center;gap:18px;padding-block:18px}}
header.mast img{{height:56px;width:auto}}
.rule{{width:1px;align-self:stretch;margin-block:4px;background:{HAIR}}}
h1{{margin:0;font-size:27px;font-weight:700;letter-spacing:-.02em;color:{BLUE}}}
.sub{{margin:3px 0 0;font-size:13px;color:{MUTED}}}
.chip{{display:inline-block;border:1px solid {BLUE};color:{BLUE};padding:3px 9px;
 font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin-right:8px}}
.intro{{background:{BAND};border-block:1px solid {HAIR}}}
.intro .wrap{{padding-block:24px}}
h2.sec{{font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
 color:{BLUE};margin:44px 0 14px;padding-bottom:8px;border-bottom:2px solid {NAVY}}}
.screen{{margin:0 0 34px;border:1px solid {HAIR};border-left:3px solid {BLUE};background:{WHITE}}}
.screen>.body{{padding:18px 22px}}
.screen h3{{margin:0 0 4px;font-size:19px;font-weight:700;letter-spacing:-.01em;color:{NAVY}}}
.state{{margin:0 0 14px;color:{SLATE};max-width:78ch}}
.screen img{{display:block;width:100%;height:auto;border-top:1px solid {HAIR};
 border-bottom:1px solid {HAIR}}}
ul{{margin:0;padding-left:18px}}
li{{margin:6px 0;color:{SLATE};max-width:86ch}}
li::marker{{color:{SKY}}}
.api{{margin-top:14px;padding-top:10px;border-top:1px solid {HAIR};
 font-family:Consolas,Menlo,monospace;font-size:12px;color:{BLUE}}}
.gap{{border-left-color:{RED}}}
.gap h3{{color:{RED}}}
.why{{margin-top:8px;font-size:13.5px;color:{MUTED}}}
.toc{{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:10px;margin-top:18px}}
.toc a{{display:block;border:1px solid {HAIR};padding:9px 11px;text-decoration:none;
 color:{NAVY};font-size:13.5px;background:{WHITE}}}
.toc a:hover{{border-color:{BLUE};color:{BLUE}}}
.toc a b{{display:block;font-family:Consolas,Menlo,monospace;font-size:11px;color:{BLUE};
 font-weight:700;margin-bottom:2px}}
footer{{margin-top:52px;background:{NAVY};color:#cdd6e6;font-size:13px}}
footer .wrap{{padding-block:16px;display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between}}
footer strong{{color:{WHITE}}}
"""


def build(shots_dir: Path, out: Path) -> Path:
    shots = find_shots(shots_dir)
    missing = [i for i, *_ in SCREENS if i not in shots]
    if missing:
        raise SystemExit(f"missing screenshots for indices {missing} in {shots_dir}")

    toc = "".join(
        f'<a href="#{a}"><b>{esc(sec)}</b>{esc(title)}</a>'
        for _, a, sec, title, *_ in SCREENS
    )

    blocks, current = [], None
    for idx, anchor, sec, title, state, actions, api in SCREENS:
        if sec != current:
            blocks.append(f'<h2 class="sec">{esc(sec)}</h2>')
            current = sec
        items = "".join(f"<li>{esc(a)}</li>" for a in actions)
        blocks.append(f"""
<section class="screen" id="{anchor}">
  <img src="{data_uri(shots[idx])}" alt="{esc(title)}">
  <div class="body">
    <h3>{esc(title)}</h3>
    <p class="state">{esc(state)}</p>
    <ul>{items}</ul>
    <div class="api">{esc(api)}</div>
  </div>
</section>""")

    gaps = "".join(f"""
<section class="screen gap">
  <div class="body">
    <h3>{esc(t)}</h3>
    <p class="state">{esc(d)}</p>
    <p class="why"><strong>Not captured:</strong> {esc(w)}</p>
  </div>
</section>""" for t, d, w in NOT_CAPTURED)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FRD → STTM Agent — every screen</title>
<style>{CSS}</style></head><body>
<div class="topline"></div>
<header class="mast"><div class="wrap">
  <img src="{data_uri(LOGO)}" alt="AmeriHealth Caritas">
  <span class="rule"></span>
  <div><h1>FRD → STTM Agent</h1>
  <p class="sub">Every screen a reviewer can reach, and what produces it</p></div>
</div></header>

<div class="intro"><div class="wrap">
  <span class="chip">real captures</span><span class="chip">synthetic corpus</span>
  <span class="chip">no mock-ups</span>
  <p style="max-width:82ch;margin:14px 0 0;color:{SLATE}">
    The review app is one surface with four screens and a handful of states. Every image below
    is a real capture of the running app against a synthetic corpus — none is a mock-up. States
    that cannot be reached without a billed model call or a wired document source are listed at
    the end, named rather than illustrated: a catalogue that quietly includes invented screens
    is worse than one with honest gaps in it.
  </p>
  <p style="max-width:82ch;margin:10px 0 0;color:{SLATE}">
    The frontend is intended to be <strong>the same in the Hexaware environment and in ACFC's
    own rebuild</strong>. The backend behind it will differ; nothing in the layout branches on
    environment. The one element that knows where it is running is the runtime chip in the
    masthead, which changes its text and never its position.
  </p>
  <div class="toc">{toc}</div>
</div></div>

<div class="wrap">
{''.join(blocks)}
<h2 class="sec">5 · States not captured here</h2>
{gaps}
</div>

<footer><div class="wrap">
  <span><strong>The model proposes</strong> · deterministic code audits and decides ·
  <strong>a person resolves and approves</strong></span>
  <span>Nothing is written back to the document library — the reviewer uploads the approved workbook</span>
</div></footer>
</body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip().splitlines()[2])
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DEFAULT
    print(build(Path(sys.argv[1]), target))
