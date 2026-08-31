# Architecture

## The rule the whole agent follows

**Every cell in the STTM comes from one of three places, and the agent says which:**

| cell | source |
|---|---|
| source column name, type, length, required, PHI, description, allowed values, sample, segment | the vendor data dictionary |
| file pattern, format, delimiter, frequency, landing path, target tables, validation rules, recycle rule | the FRD |
| catalog and schema per layer (when the FRD is silent), stage default type, standard type promoted from the vendor type, audit columns | `standards/*.json` — the transcription of ACFC's two standards documents |
| target column name | the source column name, **as-is** |

Anything else is a **question** for the reviewer or a **blocker**. The agent never fills a cell it cannot source.

## Inputs

Two per feed. Either **paired by name** in the volumes — `FRD_<x>.docx` ↔ `VDD_<x>.xlsx` (`DICT_` is accepted) — or **declared by a reviewer**, who uploads the two files together (`POST /api/runs/upload`); those land in `output_sttms/<run_id>/inputs/`, are used as given whatever they are called, are recorded on the run as `inputs`, and never enter the corpus. Pairing is still never *guessed*: it is a name match or a person's declaration, never similarity. An approved `STTM_<x>.xlsx` in `reference_sttms` marks the FRD as mapped and is used by any run — never the feed's own — as a **layout** (sheets, band labels, headers, styles). No content is read from it.

The FRD template and VDD template that BSAs and vendors fill in are in `templates/`.

## A run

`output_sttms/<run_id>/run.json` is the whole state of a run.

1. **extract** — `frd_parsing` turns the `.docx` into markdown; one Claude call (`extract.py`, schema-in-prompt, validated back into `FrdIngestionSpec`) returns the feed-level facts. The VDD is parsed by `dictionary.py`.
2. **assess** (`completeness.py`) —
   * grounding: every identifier the model returned (file patterns, tables, schemas, ids) must appear verbatim in the FRD, or it becomes an `unverified` question (keep / remove / correct); prose is token-overlap checked → `weak_match`. A miss on a field the workbook never carries (`requirement_ids`, `sttm_reference`, `history_backfill`, …) is a **note**, not a question;
   * a rule attached to several sources → `attribution` question **only if** it names a column those sources share and the FRD does not say which file: a rule that names every file is settled by the FRD, one that names no column stays on all sources, one whose column exactly one source has is attributed by the dictionary (each is a note);
   * the rule: **a question is asked only when different answers would write a different workbook and the documents do not settle it** (2026-08-28);
   * each source is paired to a VDD file by name tokens; an undecidable one → `file_pairing` question;
   * targets per layer: the FRD wins, the standards fill (`schema_for`, `catalog_for`), the rest → `target_gap` question;
   * blockers: no dictionary, an empty dictionary, no sources, nothing pairable.
   * status: `ready` (render immediately) · `needs_input` · `cannot_generate`.
3. **answers** — recorded on `run.json`; `apply_answers` folds them into the spec and targets at render time.
4. **render** (`render.py`) — rows from the VDD (+ the standards' audit columns per segment), targets from step 2, FRD rules placed on the row whose column they name, written into a chosen approved workbook's layout or the built-in one.

## Where it runs

* **Databricks App** (`app.yaml`, `STTM_APP_MODE=databricks`): the backend reads the volumes through the Files API and triggers the bundle job `frd_sttm_pipeline` (`notebooks/pipeline.py`, `task=extract|render|reindex`). The job runs `frdsttm.pipeline` over `/Volumes/<catalog>/sttm_agent/...`; `reindex` also writes the `frd_pairing` table.
* **Locally** (`STTM_APP_MODE=local`): the same backend runs `frdsttm.pipeline` in a thread over four folders. Claude is reached through the workspace's Foundation Model APIs (`STTM_PROVIDER=databricks`, `~/.databrickscfg`) or Anthropic directly.

## What was deliberately left out (2026-08-27 rebuild)

Audit trail, identity, SharePoint sync, run-insulation clones, template scoring, term catalog, exemplar retrieval, standards "fill" gating, the CodeGen contract JSON, evals against approved workbooks. Git history has all of it.
