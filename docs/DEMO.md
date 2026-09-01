# Demo runbook — the Socially Determined (SD) workflow

## Before the room (10 minutes)

```bash
databricks apps start frd-sttm-review-app        # ~2 min; the App bills per hour while up
```

Open https://frd-sttm-review-app-7405616719878880.0.azure.databricksapps.com and wait for the
picker to show both FRDs (the first load mirrors the volumes — 5–10 s). If the Runs list is empty,
press **Reindex the volumes** once.

Fallback if the live model call misbehaves: a finished SD run is reachable by URL —
append `?run=run_20260828_023551` (411 rows, 5/5 answered) — and walk the same screens.
A failed run shows a **Run again** button; a second attempt normally succeeds.

## The story (one sentence)

Two documents in — the FRD and the vendor's data dictionary — the agent extracts what it can,
asks for what it will not guess, and builds the STTM in ACFC's own workbook layout.

## Click path (about 5 minutes)

1. **Picker.** Point at the four-step strip. Then the SD row: `dictionary · 399 columns · 3 files`,
   `approved STTM · 411 rows`. Say: the approved STTM is never read for content — the new draft
   comes from the FRD and the dictionary alone.
2. **Generate STTM** on the SD row. The run page opens: *Reading the documents*, the five steps,
   elapsed time, a link to the Databricks job. Extraction takes ~1–2 minutes — use it to explain
   the grounding check (every identifier the model returns must appear verbatim in the FRD).
3. **What the agent read.** `67 headings · 40 tables`, `399 columns · 3 files`, `45 / 45 verbatim`,
   `0 / 4 answered`.
4. **Sources.** Three sources, each paired to its dictionary file (86 / 267 / 46 columns). Point at
   the origin column: catalog *from the ACFC naming standards*, schema *stated in the FRD*.
5. **Questions.** Four rules the FRD attached to all three sources ("the below files…"). The agent
   will not pick an owner. Choose **All of them** on each, **Save answer**. The banner flips to
   *Ready to generate*.
6. **Generate the STTM.** ~1 minute, no model call. Then **Download** — open it in Excel: three
   MAPPING sheets in the client's own layout, 411 rows, column names as the vendor wrote them,
   audit columns from the standards.
7. **Run report** (optional) — the same facts as a markdown page.

If a run fails with a JSON/format error, press **Run again** — the model occasionally slips on
formatting; the second attempt is a fresh call.

## Questions people ask

* *Why not guess the column names?* Neither ACFC standards document states a column naming rule.
  A guess would have to be learned from approved workbooks, which is the self-referential loop we
  removed. Names are carried as-is; a confirmed rule becomes config, not code.
* *What if the dictionary is missing?* The row says "no vendor dictionary" and cannot be generated.
  A run never invents a source column.
* *Where does it run?* The App triggers one Databricks job (`frd_sttm_pipeline`); artifacts land in
  the `output_sttms` volume; the model is Claude through the workspace's Foundation Model APIs.

## After the room

```bash
databricks apps stop frd-sttm-review-app
```
