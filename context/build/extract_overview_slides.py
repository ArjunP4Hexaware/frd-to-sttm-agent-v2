"""Cut slides 6 and 7 of the ACFC program-overview deck into the two one-pagers.

    ~/.virtualenvs/frdsttm/bin/python context/build/extract_overview_slides.py [in.pptx]

Default input: context/ACFC_AI_in_Engineering_Program_Overview_New Draft v2.pptx
Outputs (each a single-slide deck, the overview deck's own chrome kept):
    slide 6 -> context/FRD-to-STTM-Agent-Solution-Architecture.pptx
    slide 7 -> context/FRD-to-STTM-Agent-Data-Governance-Architecture.pptx

Supersedes build_architecture_onepager.py / build_governance_onepager.py
(Arjun, 2026-08-28): the one-pagers ARE the overview deck's slides now, so
they are cut from it rather than drawn again. The input deck is never written
to. Dropping the sldId and its relationship is enough — python-pptx saves
only the parts still reachable from the package, so the other slides, their
notes and their media do not come along.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation

REPO = Path(__file__).resolve().parents[2]
IN_DEFAULT = REPO / "context" / "ACFC_AI_in_Engineering_Program_Overview_New Draft v2.pptx"
CUTS = {
    6: REPO / "context" / "FRD-to-STTM-Agent-Solution-Architecture.pptx",
    7: REPO / "context" / "FRD-to-STTM-Agent-Data-Governance-Architecture.pptx",
}


def keep_only(src: Path, slide_no: int, out: Path) -> None:
    prs = Presentation(str(src))
    sldIdLst = prs.slides._sldIdLst
    ids = list(sldIdLst)
    if not 1 <= slide_no <= len(ids):
        raise SystemExit(f"{src.name} has {len(ids)} slides; no slide {slide_no}")
    for i, sldId in enumerate(ids, 1):
        if i == slide_no:
            continue
        prs.part.drop_rel(sldId.rId)
        sldIdLst.remove(sldId)
    prs.save(str(out))
    print(f"slide {slide_no} of {src.name} -> {out.relative_to(REPO)}")


def main(argv: list[str]) -> None:
    src = Path(argv[1]) if len(argv) > 1 else IN_DEFAULT
    for slide_no, out in CUTS.items():
        keep_only(src, slide_no, out)


if __name__ == "__main__":
    main(sys.argv)
