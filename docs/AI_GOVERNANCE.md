# AI & data governance — FRD→STTM agent

*Added 2026-08-23. This is the agent's governance record: what it is, what
data it touches, which controls exist and where they live in code, how to
reconstruct "what produced this workbook", and what remains a human
decision. It is written to be lifted straight into a governance registry
(the client runs Collibra; §7 maps onto it) — so it states facts and names
placeholders rather than guessing.*

---

## 1. AI asset record (model-card style)

| Field | Value |
|---|---|
| Asset name | FRD→STTM agent (`frd-to-sttm-agent-v2`) |
| Purpose | From an approved Functional Requirements Document (.docx), produce a Source-to-Target Mapping workbook (.xlsx) and a machine-readable feed contract for the downstream CodeGen agent. |
| Program position | First of three agents in the client's AI-in-Engineering program (FRD→STTM → CodeGen → Code Review). |
| Owner (accountable) | **UNASSIGNED** — set via `90_uc_governance` `data_owner`; also fill here. |
| Steward (day-to-day) | **UNASSIGNED** — `data_steward`. |
| Builder | Hexaware (reference implementation); the client rebuilds in its own environment from this blueprint. |
| Users | Data-engineering reviewers who open the review app, pick an FRD, review gated ambiguities, download the STTM and upload it to SharePoint. |
| Who may run it | The client's BSAs — one Entra ID group with `READ VOLUME` on the agent's volumes and `CAN USE` on the App (control #17). View = run. |
| Automation level | **Assisted, human-in-the-loop.** Nothing leaves the governed boundary without a person: every run is started by a named person, every gated ambiguity is resolved by a named person, and the hand-off (download → manual SharePoint upload) is a person's action. The repo never writes to SharePoint. |
| Proposed risk tier | Limited / internal-tooling: affects engineering work products (mapping specs), not members, claims or decisions about people. Output is reviewed before use. Re-assess if it ever writes directly to a system of record or feeds CodeGen without review. |
| Model / provider | Anthropic Claude, `claude-opus-4-8` (job parameter `model` in `resources/frd_sttm_job.yml`), structured outputs against the Pydantic schema `FrdIngestionSpec` (`src/frdsttm/models.py`, mirrored in `schema/sttm_extraction_schema.json`). Anthropic-only by program decision. **No fine-tuning** (settled; master context §7a). |
| Where the model call happens | `notebooks/02_extract.py` → `src/frdsttm/live_extraction.py`, one streamed call per document; Databricks serverless job task in production, subprocess in local dev. |
| Inputs | The FRD's full text (markdown-normalised by `01_frd_ingest`), plus retrieved few-shot exemplars from approved FRD↔STTM pairs in the corpus (`frdsttm.exemplars`). |
| Outputs | `extractions/<doc>.json` (structured spec) → `contracts/<doc>.contract.json` (validated, grounded, gated) → `rendered/<doc>.sttm.xlsx` + `reports/`. |
| Quality controls | Grounding audit (strict fields verbatim, advisory prose token-overlap — never relaxed), ambiguity gating, automatic eval against the paired approved STTM (`eval_pct` per run, exclude-own-reference cross-validation). |
| Evaluation status | Measured per run and logged (`frd_sttm_runs.eval_pct`). Program UAT gate: 80%. Real-document calibration pending (see §8). |
| Deployment | Databricks App (`app.yaml`, FastAPI + React) + three bundle jobs (`frd_sttm_pipeline`, `frd_sttm_render`, `frd_sttm_sharepoint_sync`) + one governance job (`frd_sttm_uc_governance`). |
| Status | Reference implementation; live deploy in the Hexaware workspace unproven as of 2026-08-23 (repo CLAUDE.md "Deploy blockers"). |

---

## 2. Data flow (what goes where)

```
SharePoint library (system of record; READ-ONLY for this repo)
   │  00_sharepoint_sync / 00_sharepoint_fetch — Graph, app-only creds, Sites.Selected READ
   ▼
Unity Catalog volumes  frd_raw (FRDs)   sttm_reference (approved STTMs + corpus_index.json + sync_manifest.json)
   │  01_frd_ingest → table frd_documents (full text + content_sha256)
   ▼
02_extract ──── FRD text + exemplars ────▶ Anthropic API ────▶ extractions/<doc>.json + .extraction_meta.json
   │  03_contract_build → contracts/ + table frd_contracts (grounding audit, gating)
   │  04_sttm_render    → rendered/<doc>.sttm.xlsx + reports/ + table frd_sttm_runs (APPEND)
   ▼
Review app (Databricks App)  — named reviewer resolves ambiguities → re-render → DOWNLOADS workbook
   │  audit events → volume sttm_audit/events/*.json ; run_manifest.json in the artifact set
   ▼
Reviewer uploads the workbook to SharePoint's STTM folder by hand → next sync pairs it → it becomes a template
```

Boundary crossings worth naming: (a) **client document text leaves the
workspace to the Anthropic API** at 02 — see §8 item 2; (b) a generated
workbook **leaves the app** at download — recorded as `workbook.downloaded`
with the file's sha256.

---

## 3. Data inventory & classification

The inventory, comments and tags are *code* — `notebooks/90_uc_governance.py`
(`VOLUMES`, `TABLES`, `COLUMN_TAGS`) — and are applied to Unity Catalog by
`databricks bundle run frd_sttm_uc_governance`. Summary:

| Asset | Holds | Classification (tags) |
|---|---|---|
| `frd_raw`, `demo_raw` (volumes) | Source FRDs | client_document · requirements_document · **phi_possible=true** · input |
| `sttm_reference` (volume) | Approved STTMs, corpus index, sync manifest | client_document · source_to_target_mapping · phi_possible=true · input |
| `frd_documents` (+ run-scoped copies) | Parsed FRD text, `content_sha256` | client_document · phi_possible=true; column `content` tagged contains_document_text |
| `sttm_out`, `sttm_out_app` (volumes) | Extractions, contracts, rendered workbooks, reports, `run_manifest.json` | **llm_generated** · source_to_target_mapping · human_review_required=true · output |
| `frd_contracts` (+ copies) | Validated feed contracts | llm_generated · human_review_required=true |
| `frd_sttm_runs` (+ copies) | Append-only render log | audit_log · audit_trail=true · append_only=true |
| `sttm_audit` (volume) | Append-only app audit events | audit_log · audit_trail=true · append_only=true · phi_possible=false |

Why `phi_possible=true` on documents: FRDs/STTMs are requirement documents
for a health-plan client; they *name* PHI-bearing fields (the schema has
`phi_pii_notes`; 04 renders a PHI-flag column) and may carry sample values.
They are not expected to contain member records — but "confidential, treat
as possibly PHI" is the floor until the data-handling review in §8 says
otherwise. `sensitivity=confidential` on everything; owner/steward/retention
are placeholders that *read* as placeholders until set.

---

## 4. Controls map

| # | Control | Where | How to verify |
|---|---|---|---|
| 1 | **Every governed action has a named actor.** Databricks Apps forwards `X-Forwarded-Email / -Preferred-Username / -User`; the backend reads them, never a token. In databricks mode a request without them is **refused (401)**. Local mode records the OS user and says `source: local`. | `review_app_react/backend/identity.py`; used by every state-changing route in `demo.py` / `corpus_routes.py` | `tests/test_governance.py::test_databricks_mode_*`; `GET /api/demo/audit` shows `actor` on every event |
| 2 | **Append-only audit trail.** One JSON file per event: `run.started/finished`, `resolution.recorded`, `rerender.started/finished`, `workbook.downloaded`, `corpus.sync.started`, `upload.received`. Written **before** the action in databricks mode; if the volume write fails the action is refused (502). Never document content; ids, hashes, counts. | `backend/audit.py`; volume `sttm_audit/events/` + local mirror; `GET /api/demo/audit` | `SELECT * FROM read_files('/Volumes/<cat>/<sch>/sttm_audit/events/', format => 'json')` |
| 3 | **The hand-off is recorded.** Download = the moment a generated STTM leaves the app; event carries sha256 + size of the bytes served. | `demo.py::demo_artifact_workbook` | match a SharePoint-uploaded workbook's sha256 to `workbook.downloaded.sha256` |
| 4 | **Human resolutions are attributed.** `HumanResolution.resolved_by` = the actor (was `None`). | `demo.py::demo_submit_resolution`, `models.HumanResolution` | `_provenance.human_resolutions[].resolved_by` in the contract |
| 5 | **Run provenance travels with the artifacts.** `run_manifest.json` per artifact set (who, FRD file + sha256, mode, job run id/url, outcome, timestamps); in databricks mode also in the UC out dir. | `demo.py::run_manifest/_write_run_manifest`, `jobs_runner.upload_run_manifest` | open `<set>/run_manifest.json` |
| 6 | **Extraction provenance.** `extractions/<doc>.extraction_meta.json`: provider, model, `system_prompt_sha256`, `schema_sha256`, `content_sha256` of the input, stop_reason, token usage (incl. cache), exemplars used, SDK version, job run id. Written for mock runs too (`provider: mock`). | `notebooks/02_extract.py` | the sidecar next to each extraction |
| 7 | **Runs table is a log, not a snapshot.** `frd_sttm_runs` is APPENDed with `mergeSchema`; new columns: `run_label`, `triggered_by`, `job_run_id`, `provider`, `model`, `system_prompt_sha256`, `input/output_tokens`, `frd_sha256`, `rendered_sha256`. A re-render is a new row (`run_label` ends `:rerender`). | `notebooks/04_sttm_render.py`; `frdsttm.local_tables.append_table` | `SELECT * FROM frd_sttm_runs ORDER BY run_at DESC` |
| 8 | **Actor + run label reach the notebooks in both modes.** Job parameters `triggered_by` / `run_label` (declared in both job ymls; `{{job.run_id}}` → `job_run_id`); env `TRIGGERED_BY` / `RUN_LABEL` in local subprocess mode. A hand `bundle run` records `triggered_by=manual`. | `resources/frd_sttm_job.yml`, `frd_sttm_render_job.yml`, `jobs_runner.job_parameters`, `demo._subprocess_env` | `tests/test_governance.py::test_job_parameter_sets_*` |
| 9 | **Source fingerprint at ingest.** `frd_documents.content_sha256` = sha256 of the source bytes; the same hash the corpus index and run manifest carry, so rows/index/runs join on *which bytes*, not file names. | `notebooks/01_frd_ingest.py`, `frdsttm.sync` | compare the three |
| 10 | **Human gate before anything leaves.** Publish was removed (2026-08-22): the repo is read-only against SharePoint by construction (`SharePointClient` has no upload). Re-render ≠ re-publish; the reviewer uploads by hand. | `src/frdsttm/sharepoint.py`, `tests/test_sharepoint_routes.py` | the router exposes only the config probe |
| 11 | **Quality gate is not relaxable.** Grounding audit + ambiguity gating; auto-eval vs the approved pair on every regeneration. | `frdsttm.contract_build`, `04_sttm_render` | `frd_sttm_runs.eval_pct`, `status` |
| 12 | **No silent mock in production.** In the App / a workspace task a mock request raises. | `02_extract` (`MOCK_AVAILABLE`), `demo._subprocess_env` | repo CLAUDE.md "Config doctrine" |
| 13 | **Run insulation.** App runs write only to `demo_<ts>`-suffixed tables/volumes; curated paths are unreachable. | `demo._new_suffix`, `jobs_runner.job_parameters` | `tests/test_demo_backend.py`, `test_demo_jobs_runner.py` |
| 14 | **Secrets never in repo/logs.** Secret scopes; `SharePointConfig.__repr__` excludes the secret; API key presence reported as a boolean only. | everywhere keys are read | grep |
| 15 | **Classification lives in Unity Catalog.** Comments + tags on every asset; separate APPLY-TAG-holding step, not a pipeline side-effect. | `notebooks/90_uc_governance.py`, `resources/frd_sttm_governance_job.yml` | Catalog Explorer / Collibra harvest |
| 16 | **No client documents in the repo.** Working tree clean since 2026-08-22; history purged 2026-08-23. | repo CLAUDE.md "Fixtures & data rules" | `git log --all -- local_dev_fixtures/` is empty |
| 17 | **Access model: may run == may read.** One group — the client's BSAs (an Entra ID group synced into Databricks) — gets `USE CATALOG/SCHEMA` + `READ VOLUME` on `frd_raw`, `sttm_reference`, `sttm_out_app`, `sttm_audit`, and `CAN USE` on the App. Nothing else: no `WRITE VOLUME`/`MANAGE` (owner + steward only), no secret scopes, no jobs — the app's service principal does the work. View = run; no second tier. Leaving the group removes app and data access in one step; past actions stay attributed. | `notebooks/90_uc_governance.py` (`reviewer_group`, `app_name`; `access_statements`, `grant_app_can_use`), `resources/frd_sttm_governance_job.yml` | `SHOW GRANTS ON VOLUME …frd_raw`; `databricks apps get-permissions <app>` |

---

## 5. Provenance chain — answering "what produced this workbook?"

Given a workbook someone uploaded to SharePoint:

1. `sha256` the file → find the `workbook.downloaded` event with that `sha256`
   (`set_id`, `doc_id`, `actor`, `ts`) **and** the `frd_sttm_runs` row with
   `rendered_sha256` = that hash (`run_label`, `triggered_by`, `job_run_id`,
   `model`, `system_prompt_sha256`, token usage, `frd_sha256`, `eval_pct`).
2. `set_id` → `run_manifest.json` (who started the run, which FRD file + sha,
   job run id/url, outcome) and `run.started/finished` events.
3. `frd_sha256` → `frd_documents.content_sha256` (the parsed text) and the
   corpus index entry (SharePoint item id / eTag / modified via
   `sync_manifest.json`) — i.e. *which version* of the FRD.
4. `extractions/<doc>.extraction_meta.json` → model, prompt + schema
   fingerprints, exemplars used, usage.
5. `contracts/<doc>.contract.json._provenance` → grounding audit,
   ambiguities, `human_resolutions[].resolved_by/resolved_at`,
   `resolution_audit`, `template_fill`, `rule_placement`.
6. `resolution.recorded` / `rerender.*` events → who changed what, when,
   between the first render and the downloaded one.

Every link is a hash or an id, not a file name; a revised FRD under the same
name is a different `frd_sha256`.

---

## 6. Querying the audit trail

```sql
-- everything, newest first
SELECT * FROM read_files('/Volumes/<catalog>/<schema>/sttm_audit/events/', format => 'json')
ORDER BY ts DESC;

-- who took which workbooks out, and when
SELECT ts, actor, set_id, doc_id, sha256, size_bytes
FROM read_files('/Volumes/<catalog>/<schema>/sttm_audit/events/', format => 'json')
WHERE kind = 'workbook.downloaded' ORDER BY ts DESC;

-- renders with their cost and lineage
SELECT run_at, run_label, triggered_by, job_run_id, doc_id, status, eval_pct,
       model, input_tokens, output_tokens, frd_sha256, rendered_sha256
FROM <catalog>.<schema>.frd_sttm_runs ORDER BY run_at DESC;
```

The app's `GET /api/demo/audit?limit=&kind=` lists the same events (mirrors
the volume down first in databricks mode; identity-gated like every route).

---

## 7. Registering in Collibra (the client's catalog)

What to register, and where each fact comes from:

| Collibra asset | Source of truth here |
|---|---|
| **AI model / AI use case**: name, purpose, owner, steward, users, risk tier, automation level, model + provider, human oversight, evaluation metric & status | §1 of this doc (keep it current); `frd_sttm_runs.eval_pct` for the metric |
| **Data sets (technical assets)**: the UC tables + volumes in §3 with their tags/comments | Collibra's Unity Catalog integration harvests UC directly — run `frd_sttm_uc_governance` first so the harvest carries classification; do not retype tags into Collibra by hand |
| **Source system**: the SharePoint library (FRD folder, STTM folder), read-only | `app.yaml` SharePoint block; repo CLAUDE.md "SharePoint" |
| **Lineage**: SharePoint → frd_raw/sttm_reference → frd_documents → (Anthropic API) → extractions → frd_contracts → rendered → reviewer → SharePoint | §2; per-instance lineage is reconstructible from §5 |
| **Policies / controls**: the §4 rows (identity, audit trail, human gate, no-write-to-SharePoint, classification) | §4, each with its code location and verification |
| **Third-party processor**: Anthropic API as the model provider; data sent = FRD text + exemplars; retention/BAA status | §8 item 2 — record the *decision*, whatever it is |
| **Open items** | §8 |

Practical note: Collibra registration is a *description* of the controls
above, not a control itself. If §4 rows 1–9 are not live in the deployed
app, registering the asset describes something that does not exist yet.

---

## 8. Open decisions (human calls, not code)

1. **Owner and steward.** Names/groups for `data_owner` / `data_steward`;
   pass them to `frd_sttm_uc_governance` and fill §1.
2. **Data path to the model provider.** Live extraction sends full FRD text
   (client requirements for a health-plan, possibly naming PHI fields) to
   the Anthropic API. Confirm this path is within the client's approved
   processors / BAA posture *before* real documents are run — the same
   review the repo already gates corpus harvesting on. Options if not:
   Claude via an approved enterprise channel, or redaction of sample values
   before the call. Record the decision here.
3. **Retention.** Nothing deletes: per-run artifact sets (`sttm_out_app/<suffix>`,
   `demo_raw/<suffix>`), suffixed tables, audit events and local mirrors
   accumulate. Decide retention for (a) run artifacts, (b) the audit trail
   (usually longer), (c) the container's local mirror; then implement — set
   `retention_policy` in the tags when decided.
4. **Access model — DECIDED 2026-08-23 (Arjun): may run == may read.**
   Intended runners are the client's BSAs; anyone who may view past runs
   may start one. Implemented as control #17: one Entra-synced group gets
   READ on the read volumes + `CAN USE` on the App via
   `frd_sttm_uc_governance --params reviewer_group=…,app_name=…`. Not
   `MANAGE` (admin-level; would make every BSA an owner of PHI-tagged
   volumes). The app's SP still holds the real volume privileges — the
   group grant is what makes "can open the app" and "can see the data"
   coincide (a convention enforced by group discipline, not per-request
   checks; on-behalf-of enforcement via `X-Forwarded-Access-Token` remains
   available if that ever proves insufficient). What remains: the client
   names the group and the deployed app name.
5. **Git history — DONE 2026-08-23.** `demo_frd.docx`, `demo_sttm.xlsx`
   and `local_dev_fixtures/` were removed from every commit
   (`git filter-repo`), `main` + `staging` force-pushed. Remaining: ask
   GitHub Support to purge the now-dangling old commits (still fetchable by
   raw SHA until then); every pre-purge clone (incl. the other contributor's)
   must be re-cloned. `mock_extractions.py` still carries hand-copied facts
   from two source FRDs (flagged in repo CLAUDE.md).
6. **Stale-pair warning.** A revised FRD whose paired STTM predates it is
   detectable (`content_sha256`, eTag) but not yet *shown* to the reviewer.
7. **Eval calibration on real documents.** Template thresholds are seeded
   on the synthetic fixture; the 80% UAT gate needs real pairs.
8. **Notification address.** `notification_email` in `databricks.yml` is a
   placeholder; job failures page nobody until it is set per target.
