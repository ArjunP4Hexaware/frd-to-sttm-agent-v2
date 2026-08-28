# Presentation script — slides 5–7 and the SD demo

Timing: slides ~6 min · document walkthrough ~6 min · live run ~6 min (2–3 of it waiting) · comparison ~3 min.

**One thing to hold in your head the whole time:** slides 5–7 describe the *target* architecture
(SharePoint through Graph, MCP connectors, AI Gateway, audit trail, Collibra). What runs today in
the Hexaware workspace is the *core* of it — the agent itself — with the documents placed in Unity
Catalog volumes directly. Say that once, early, and nobody can catch you out.

---

## Slide 5 — The program at a glance

> "This slide is the relay. An FRD is written and approved by your team. Agent 1 turns it into an
> STTM workbook. A named person approves that workbook. Only then does Agent 2 turn the workbook
> into pipeline code — transforms, DDL, tests, job config. Nothing moves downstream without a
> person's approval at each gate.
>
> I own Agent 1, and I want to add one input the slide doesn't show, because the last week taught
> us it's essential: the **vendor data dictionary**. An FRD tells you *what* to ingest — file names,
> where they land, target tables, the rules. It does not list the columns; one of your FRDs names
> two columns and its STTM has four hundred and eleven rows. Those rows come from the vendor's
> dictionary. So Agent 1 has exactly two inputs per feed: the FRD and the vendor data dictionary.
>
> Three principles on the right, and they are not slogans — you'll see each one in the demo:
> **the AI only drafts** — the model reads the documents; ordinary code checks its answer and
> builds the workbook. **Every claim cites its source** — every file name, table and schema the
> model returns must appear word-for-word in the FRD, or it becomes a question rather than a
> fact. And **a named person signs off** — the agent stops and asks; it does not guess."

Bottom row, brief: "It runs on Databricks — Unity Catalog owns every document and every output,
the model is reached through the workspace's model serving, and the review surface is a
Databricks App. In the Hexaware environment today the documents are placed in the catalog
directly; the SharePoint connection is the production step."

*Don't say:* "Lakeflow Declarative Pipelines" as a thing that exists today. Today it is one
Databricks job. If asked: "the job is the Lakeflow step in the production build."

## Slide 6 — Solution architecture

Walk left to right.

> "Left: the source systems. In production the FRDs, the vendor dictionaries and the approved STTM
> library live in your SharePoint, and the agent reads them through one connector pattern with a
> read-only, scoped credential — the same pattern extends to Jira or Confluence later. Today, for
> this demo, those documents sit in Unity Catalog volumes; the agent's behaviour is identical.
>
> Middle: the agent. Four stages — ingest, extract, audit-and-gate, render — and **one model call**:
> Claude reads the FRD's prose once and returns the source files, target tables and rules as
> structured data. The vendor dictionary is a spreadsheet, so it's read exactly, column by column,
> by code. If a vendor's workbook strays from our template, Claude normalises it into the template
> shape, and every column name it returns is checked against the workbook — it can't add one.
>
> Bottom: the governed runtime. The model call goes through Databricks' model serving — you pay per
> token, there is no key on a laptop, and in production Unity AI Gateway sits in front of it for
> rate limits and cost. Unity Catalog holds the corpus and every run.
>
> Right: the output is a **draft** — the STTM in your own workbook layout, with open questions
> flagged. A reviewer answers them in the app, then approves and uploads. The approved workbook
> becomes a layout reference for future feeds — never for its own feed; the agent never opens a
> feed's own STTM."

*If asked "which parts are live today":* the four stages, the one model call, Unity Catalog
volumes, the review app, the human questions. Not yet: SharePoint/Graph, MCP connectors,
Jira/Confluence, AI Gateway. Say it plainly.

## Slide 7 — Data governance

> "This is the same journey read as a governance story. **Enters:** documents come from your
> SharePoint, read-only, and every table and folder in Databricks carries an owner, a steward,
> sensitivity and retention. **Opened:** only your BSA group — the people already allowed to read the
> data — can open the app; each signs in with the company login. **Generated:** every output is
> traceable — the workbook is tied by fingerprint to the exact FRD version, the exact model call and
> the person who asked. **Decided:** the AI proposes; code checks every mapping against the FRD word
> for word; a named reviewer settles what's unclear.
>
> Client data crosses the platform boundary in exactly two places — the FRD text going to the
> model, and the workbook going to a named person — and both are recorded. And because Collibra
> harvests Unity Catalog, everything set in Databricks appears in your governance register
> automatically. The agent is registered as an AI asset: what it is, who owns it, what it reads and
> writes."

*Honesty note for questions:* the audit-trail volume and the Collibra registration are the
production design. Today's build records each run's who / when / model / prompt fingerprint /
standards version in the run record; the separate append-only audit trail was removed from the
demo build to keep it small. If asked, say exactly that.

---

## Demo — the Socially Determined workflow

### 1. The SD FRD (open the .docx)

> "This is a real FRD, exactly as your BSA wrote it. Notice the sections — Introduction, In Scope,
> Assumptions, then Requirements and Data Ingestion Requirements. The agent reads all of it, but the
> heart is this table — **Structural Metadata**."

Scroll to the Structural Metadata table and point:

* **Target Schema** — `stg_sdoh/sdoh` for the two community files, `stg_cm/cm` for the individual
  file. *"Two schemas, three files — the agent has to attach the right schema to each file."*
* **Target Table Name** — the three file patterns: `demographics_package_YYYY_MM.csv`,
  `analytics_package_YYYY_MM.csv`, `sd_ind_risk_…psv`.
* **Load Strategy STG / STD** — `Truncate and Load` / `Append`.
* **Source Data Dictionary** — *"File and field descriptions are mentioned in the mapping
  document."* Say: *"That row points at the STTM — the document we're trying to produce. Circular.
  It's the slot where the vendor data dictionary belongs, and it's why the dictionary is a first-
  class input."*

Then the Data Quality prose: *"If the ZIP_CODE column is NULL, then we are rejecting the record
… from the below files."* — *"'The below files.' Which files? The agent will not decide that. You
will see it ask."*

### 2. The FRD template (open `templates/FRD_TEMPLATE.docx`)

> "Same table, blank. This is what your BSAs fill in. The agent is written against these eleven
> rows, so nothing about the FRD process changes — the only ask is to fill the Source Data
> Dictionary row with the dictionary's name instead of pointing at the mapping."

### 3. The SD vendor data dictionary (open the .xlsx)

> "The second input. One row per file on the FILES sheet" — three rows: `demographics_package`,
> `analytics_package`, `sd_ind_risk…` — "and one sheet per file listing every column: position,
> name, type, length, required, description, allowed values, example, PHI. Eighty-six, two hundred
> and sixty-seven and forty-six columns — three hundred and ninety-nine in all. Every row of the
> STTM comes from here. The agent never invents a column; if it isn't in this workbook, it isn't in
> the mapping."

Be straight if asked: this dictionary was built from the approved STTM's source columns because
the vendor's own spec wasn't available — it stands in for what the vendor will supply.

### 4. The VDD template (open `templates/VDD_TEMPLATE.xlsx`)

> "This is what we hand a vendor: README, FILES, a FIELDS sheet per file, a multi-record example
> for header/detail/trailer files, code sets, a change log. If a vendor returns it in a different
> shape, Claude normalises it — and every column name is verified against what they sent."

### 5. The reference STTM (open the approved SD workbook)

> "This is the STTM your analyst built by hand for this feed — FILE_DETAILS, VERSION_HISTORY, one
> MAPPING sheet per file, four hundred and eleven rows: source column, description, sample, type,
> null check, PHI, then the stage target and the standard target. I'm showing it now so you can
> judge the agent's output against it in a few minutes. The agent will **not** look at this file.
> Not for content, not for layout. In production it wouldn't exist yet."

### 6. Run the app

Open the App (start it 10 min before; `databricks apps start frd-sttm-review-app`).

> "Four steps across the top — two documents in, extract and check, say what's missing, build the
> STTM. Below, every FRD in the volume with what's paired to it: the SD FRD has its dictionary —
> 399 columns, 3 files — and an approved STTM, which the run will not open."

Click **Generate STTM** on the SD row.

> "It's reading both documents now. One model call over the FRD; the dictionary parsed column by
> column. This runs as a Databricks job — one to three minutes. While it runs: every identifier the
> model returns is checked word-for-word against the FRD. Forty-five identifiers on this document.
> If one weren't there, it would come back to me as a question, not go into the workbook."

When it flips to **Needs your answers**:

> "Here's what it read: 67 headings, 40 tables; 399 columns, 3 files; 45 of 45 identifiers
> verbatim; and the questions. Three sources, each paired to its dictionary file — 86, 267, 46
> columns — and for each, where the target came from: the catalog from ACFC's naming standards,
> the schema and tables from the FRD."

Scroll to the questions.

> "'The below files.' The FRD attached this rule to all three sources without naming them. The
> agent asks, instead of guessing." Pick **All of them** on each, **Save answer**. (4–6 questions;
> the split varies run to run.) *"Ready to generate."*

Click **Generate the STTM**. ~1 minute, no model call.

> "STTM generated. 411 rows across three sheets. Column names exactly as the vendor wrote them —
> neither ACFC standard states a column naming rule, so we don't invent one. Stage types are
> String; standard types are promoted from the vendor's type, which is what your coding standard
> says."

**Download the STTM workbook**, open it in Excel beside the reference.

### 7. Compare to the reference (what to say, numbers from last night's run)

> "Same three sheets. 411 rows to 411 — none missing, none extra. Every stage schema, table and
> column name identical. Every standard schema, table and column name identical. Every null rule,
> PHI flag and description identical.
>
> The one place they differ: 141 standard-layer data types. The analyst typed those columns
> `Decimal(10,2)` because the sample value had a decimal point. The vendor's dictionary says they
> are `String`, and your coding standard says the standard type follows the source type — so the
> agent wrote `String`. That's not the agent being wrong; it's the agent following the written
> standard where the analyst inferred from a sample. If you want sample-driven promotion, that's
> one line in the standards config, not code."

Then the close: *"That workbook took the analyst days. The agent produced it in five minutes, said
exactly what it wasn't sure of, and never opened the answer key."*

---

## If things go wrong

* Run fails with a JSON/format error → **Run again** on the failed run. The second attempt is a
  fresh model call.
* Extraction is slow (3–4 min happens) → keep talking through the grounding check and the
  Structural Metadata table; the page refreshes itself.
* Nothing works → open `?run=run_20260828_123706` (last night's finished run, 411 rows) and walk
  the same screens.
