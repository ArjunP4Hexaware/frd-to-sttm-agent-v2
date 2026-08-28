# FRD-to-STTM Agent — working notes

Rebuilt from scratch on 2026-08-27 (Arjun). The previous CLAUDE.md described
a much larger system; it is in git history, not here.

## What this is

FRD (`.docx`) + vendor data dictionary (`.xlsx`) → STTM workbook. The agent
extracts what it can from both, asks the reviewer for what it will not
guess, and renders. ACFC's standards are config in `standards/`. Target
column names are carried **as-is** from the dictionary — no guessing, ever.
Read `docs/ARCHITECTURE.md` first; `README.md` has the commands.

## Rules that hold

- **Two inputs per feed, paired by name**: `FRD_<x>` ↔ `VDD_<x>`. No similarity
  pairing — a wrongly paired dictionary puts another vendor's columns on a source.
- **A run never reads an approved STTM for content.** `reference_sttms` supplies
  layout only, and never the feed's own workbook first.
- **Nothing is invented.** A value is from the FRD, the VDD, or the standards,
  or it is a question (`completeness.py`). A missing dictionary is a blocker.
- **Four volumes, one table**: `frds`, `vdds`, `reference_sttms`, `output_sttms`;
  `frd_pairing` (written by `task=reindex`). No other tables. No audit volume.
- **All logic in `src/frdsttm/`**; `notebooks/pipeline.py` and `app/backend/` only wire it.
- **Tests are offline and synthetic** (`tests/conftest.py` builds the documents).
- `app/frontend/dist/` is committed (the workspace serves it without a build
  step): run `npm run build` before committing a UI change.
- `sample_documents/` holds real client pairs (SD, CAQH) as working fixtures;
  they are to be purged from git on Friday 2026-08-28 (`git filter-repo`). The
  standards `.docx` in `standards/` are client documents too.

## Workspace (Hexaware dev)

`arjun_workspace.sttm_agent`. Job `frd_sttm_pipeline` (bundle, dev target —
the deployed name is prefixed; `STTM_JOB_ID` in `app.yaml` pins the id).
App `frd-sttm-review-app`, deployed from the repo root. Claude through the
workspace's Foundation Model APIs (`databricks-claude-opus-5`); no Anthropic key.
Stop the App when not demoing — it bills per hour.

## Arjun's working rules (from ~/.claude/CLAUDE.md)

Concise. Real-world examples. Ask when ambiguous. Anything that costs money —
ask first. Push back hard when warranted. He writes the code by default; this
rebuild was an explicit override.
