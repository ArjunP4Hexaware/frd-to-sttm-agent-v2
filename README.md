# FRD to STTM Agent

Turns two documents into a Source-to-Target Mapping workbook:

* the **FRD** (Functional Requirements Document, `.docx`) — what to ingest, where it lands, the target tables and the rules;
* the **VDD** (vendor data dictionary, `.xlsx`) — every source column: name, type, length, required, PHI, description.

The agent extracts what it can from both, tells the reviewer what is missing, asks what it will not guess, and builds the STTM. ACFC's naming and engineering standards ship with the agent as config (`standards/`). Column names are carried **as-is** from the dictionary — neither standard states a column naming rule, so nothing is invented.

## How it works

```
frds/FRD_<x>.docx  +  vdds/VDD_<x>.xlsx
        │
        ▼  extract        one Claude call over the FRD text → structured spec
        ▼  assess         grounding audit · dictionary pairing · target side from FRD + standards
        │                 → ready | needs_input (questions) | cannot_generate (blockers)
        ▼  answers        the reviewer answers the questions in the app (no second model call)
        ▼  render         rows from the VDD, targets from the FRD/standards, layout from an approved STTM
        │
output_sttms/<run_id>/run.json · <x>.xlsx · report.md
```

Four Unity Catalog volumes in `<catalog>.sttm_agent`: `frds`, `vdds`, `reference_sttms` (approved STTMs, used for **layout only** — a run never reads one for content), `output_sttms`. One table, `frd_pairing`, lists every FRD with its paired VDD and STTM and whether it can be generated.

## Layout

```
src/frdsttm/        the agent
  frd_parsing.py      .docx → markdown           dictionary.py     VDD parser
  extract.py          the Claude call            completeness.py   grounding, pairing, targets, questions
  render.py           rows + workbook            reference_layout.py  read an approved STTM's layout
  standards.py        standards/*.json           corpus.py         index + pairing table
  pipeline.py         run_extract / run_render / reindex over the four folders
notebooks/pipeline.py the Databricks job (task = extract | render | reindex) — wiring only
app/backend/          FastAPI (runs the pipeline in-process locally, as the job in the workspace)
app/frontend/         React (dist/ is committed — rebuild before committing UI changes)
standards/            the two ACFC standards documents + their JSON transcriptions
templates/            FRD_TEMPLATE.docx, VDD_TEMPLATE.xlsx (what BSAs and vendors fill in)
tools/                push_documents.py (folder → volumes → reindex), build_vdd_template.py, build_vdd_from_sttm.py
tests/                offline, synthetic documents only
```

## Run it

```bash
python3 -m venv ~/.virtualenvs/frdsttm && source ~/.virtualenvs/frdsttm/bin/activate
pip install -e ".[app,dev]"
pytest                                   # offline

# local: the four folders under ./local_data, Claude via the workspace (~/.databrickscfg)
mkdir -p local_data/{frds,vdds,reference_sttms,output_sttms}   # drop FRD_/VDD_/STTM_ files in
cp .env.example .env && set -a && . ./.env && set +a
python app/backend/app.py                # http://127.0.0.1:8000 — press "Reindex", then Generate
```

Deploy to the workspace:

```bash
databricks bundle deploy -t dev                          # the job
databricks jobs list -o json | grep -B2 frd_sttm_pipeline # → set STTM_JOB_ID in app.yaml
python tools/push_documents.py ~/Desktop/documents        # folder → volumes → reindex
cd app/frontend && npm install && npm run build && cd -
databricks sync . /Workspace/Users/<you>/frd-to-sttm-agent --exclude '.venv' --exclude 'node_modules' --exclude 'local_data'
databricks apps start frd-sttm-review-app && databricks apps deploy frd-sttm-review-app --source-code-path /Workspace/Users/<you>/frd-to-sttm-agent
databricks apps stop frd-sttm-review-app                  # when done — apps bill per hour
```
