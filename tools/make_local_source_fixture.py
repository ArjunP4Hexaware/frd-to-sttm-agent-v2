"""Generate a fully SYNTHETIC *source folder* — the SharePoint stand-in.

Companion to make_synthetic_smoke_fixture.py, which fabricates the two
VOLUMES directly. This one fabricates the thing UPSTREAM of them: the folder
``STTM_LOCAL_SOURCE_DIR`` points at (decided 2026-08-24 — see CLAUDE.md's
SharePoint section), so the local-folder sync path can be exercised end to
end on a machine that has no client documents and no Graph access.

Same synthetic content as the smoke fixture — it imports it rather than
inventing a second corpus — but laid out the way the library is, because
that layout is load-bearing:

    FRD_<name>.txt   +   STTM_<name>.xlsx     (<name> identical across a pair)

The prefixes are what split the two kinds; the matching name is what pairs
them. Real client documents must NEVER be used here (CLAIM: CLAUDE.md's
"Fixtures & data rules") — this tool exists so nobody needs to.

    python tools/make_local_source_fixture.py [DEST]

DEST defaults to $STTM_LOCAL_SOURCE_DIR, else ./local_dev_fixtures/source.
Refuses to write into a folder that already holds documents unless --force,
so it can never quietly overwrite a real corpus someone staged by hand.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from make_synthetic_smoke_fixture import DOCS, frd_text, workbook_for  # noqa: E402

FRD_PREFIX = "FRD_"
STTM_PREFIX = "STTM_"


def resolve_dest(argv: list[str]) -> Path:
    positional = [a for a in argv if not a.startswith("-")]
    if positional:
        return Path(positional[0]).expanduser()
    env = (os.environ.get("STTM_LOCAL_SOURCE_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    return REPO / "local_dev_fixtures" / "source"


def existing_documents(dest: Path) -> list[str]:
    if not dest.is_dir():
        return []
    return sorted(p.name for p in dest.iterdir()
                  if p.is_file() and not p.name.startswith("."))


def main() -> None:
    argv = sys.argv[1:]
    force = "--force" in argv
    dest = resolve_dest(argv)

    present = existing_documents(dest)
    if present and not force:
        print(f"{dest} already holds {len(present)} file(s): {', '.join(present[:6])}"
              f"{' …' if len(present) > 6 else ''}")
        print("Refusing to overwrite. Re-run with --force if that is what you want.")
        raise SystemExit(1)

    dest.mkdir(parents=True, exist_ok=True)
    for doc_id, d in DOCS.items():
        (dest / f"{FRD_PREFIX}{doc_id}.txt").write_text(
            frd_text(doc_id, d), encoding="utf-8")
        workbook_for(dest / f"{STTM_PREFIX}{doc_id}.xlsx", d)
        print(f"wrote {FRD_PREFIX}{doc_id}.txt + {STTM_PREFIX}{doc_id}.xlsx")

    print(f"\nsource folder ready: {dest}")
    print(f"point the app at it:  STTM_LOCAL_SOURCE_DIR={dest}")
    print("then start the app; the start-up sync loads both pairs.")


if __name__ == "__main__":
    main()
