---
name: frd-to-sttm-agent
description: Load this when the task is to recreate, port, or re-implement the FRD-to-STTM agent — the pipeline that turns an approved Functional Requirements Document into a Source-to-Target Mapping workbook plus a machine-readable feed-level contract — in a new environment, on a different stack, or from scratch. It is a complete, platform-agnostic functional and architectural spec: the four-stage pipeline, the grounding-audit quality gate, the three-status/three-ambiguity/three-resolution vocabularies, the human-in-the-loop review model, the SharePoint sync design, the governance/audit model, and the hard-won design constraints (schema-in-prompt extraction, the confirm-and-clear-on-zero-removals fix, non-determinism-as-a-demo-concern). ALSO load it when working in or asking about the frd-to-sttm-agent repo itself (FRD ingest, extraction schema, contract build / grounding audit / gating, STTM render, the shared `contracts/frd_label_contract.json`, the review app, or the SharePoint sync).
---

**Purpose of this file.** A self-contained functional and architectural specification of the FRD→STTM agent, written so someone can rebuild it **as faithfully as possible in a new environment** — a different cloud, a plain Python service, a different vendor's data platform — without access to the rest of this repo. It describes *what* every component does and *why*, and calls out every place a design choice was paid for by a real failure mode. It deliberately avoids pasting source or literal class definitions; those live in the existing repo (`src/frdsttm/`, `notebooks/`, `review_app_react/`) and should be read there if you have this repo available, not transcribed here.

**Storage nomenclature used throughout.** This spec uses two generic nouns instead of naming a specific product, so the design travels to any stack:
- **table store** — an append-friendly structured store queryable by row (the reference implementation backs this with Delta tables in Databricks Unity Catalog; a Postgres table or any transactional/log-structured store works identically).
- **artifact store** — a blob/file store keyed by path (the reference implementation uses Databricks Unity Catalog Volumes; S3/GCS/Azure Blob/a local filesystem work identically).

Every time these nouns appear, assume "and here is what the reference implementation concretely used" even where not restated.

---

## 1. What this agent does, and where it sits

Consumes an approved FRD (`.docx`, also `.pdf`/`.md`/`.txt`) and produces a Source-to-Target Mapping (STTM) workbook plus a machine-readable feed-level mapping contract JSON. It is the **first agent in a three-agent AI-in-Engineering program**: **FRD→STTM** → CodeGen → Code Review. (An earlier five-agent scope included BRD→FRD upstream and a SQL-Optimization agent; both left the program — preserved here only where it explains why a contract is now a frozen input rather than a live sync.)

Two hand-off contracts matter to a faithful rebuild:

- **Upstream, now a FROZEN INPUT.** Stage 1 parses labels a specific FRD-authoring convention emits: section headings ("In Scope", "Assumptions, Constraints & Dependencies", …), a `Project ID: NNNNNNN` line, requirement-id families (`BR|REQ|FR|SRQ|SIR|NFR|MDST`), and a `TBD — pending client input…` placeholder that routes to open items. These are loaded from a **versioned label contract JSON** (see §9) rather than hardcoded — the parser fails loudly if that file is missing or unversioned. Originally this file was byte-synced with an upstream agent's repo; treat it now as a frozen description of the FRD documents this agent must parse, changed only when a real input document stops matching it.
- **Downstream.** A CodeGen consumer needs both the rendered STTM workbook's own feed-level contract (emitted at the end of stage 3, before rendering) **and** a workbook-derived mapping contract JSON extracted from the rendered `.xlsx` by a separate downstream tool. That extraction step is a downstream consumer's responsibility, not this agent's, but this agent's render output must satisfy it: (a) a `Comment` column carrying each validation rule on the row(s) whose source column it names, (b) a `Recycle Flag` column carrying the recycle rule verbatim, (c) trailing audit rows (row-level, source `NA`) derived from the target template's own column/datatype rather than invented, and (d) required metadata (project name, a load strategy the consumer's own validation accepts, a delimiter when the file is delimited text) — an FRD that states none of these will fail at the consumer's validation step, loudly, before the workbook is even read. This round trip has only been proven on synthetic documents; a template lacking a column the consumer requires yields a workbook the consumer will reject.

## 2. Design philosophy (non-negotiable)

> The LLM reads prose and scattered requirement tables; deterministic code owns validation, regex-able facts, grounding checks, attribution, and rendering. Every extracted string is audited against the source document; genuine ambiguities are gated for human review, never guessed.

The LLM is never the arbiter of "did we get this right" — audits, gates, and dictionary cross-checks are. A rebuild that lets the model self-certify its own output, or that adds a "just ask it again" repair loop, has broken the design.

## 3. End-to-end pipeline

Four deterministic stages, run in order. Each stage's output is durable (one table-store row + artifact-store file(s)) so any stage can be re-run without re-running its predecessors.

```
   ┌───────────┐    ┌──────────┐    ┌──────────────────┐    ┌────────────────┐
   │  Ingest   │ →  │ Extract  │ →  │ Contract Build   │ →  │  Render + Eval │
   │  (FRD)    │    │  (LLM)   │    │ (validate/gate)  │    │  (STTM.xlsx)   │
   └───────────┘    └──────────┘    └──────────────────┘    └────────────────┘
        ↓                ↓                    ↓                       ↓
   frd_documents    extractions/       frd_contracts +           frd_sttm_runs +
  (table store)     <doc>.json      contracts/<doc>.contract     rendered/<doc>.sttm.xlsx
                  (artifact store)      .v2.json (artifact)      reports/<doc>.phase5.md
                                                                     (artifact store)
```

Human review attaches between Contract Build and Render: gated ambiguities land in the contract JSON; a reviewer resolves them; Render re-runs and folds the resolutions in.

### 3.1 Stage 1 — Ingest

**Purpose.** Turn a source-format FRD into one faithful **markdown snapshot**, stored as a table-store row, so extraction always sees normalized text and later grounding checks run against a stable string.

**Input.** Files in an artifact-store folder. One file = one document.

**Output row.** `doc_id` (stable — the source filename stem), `project_id` (pulled from the FRD's declared Project ID line if present), `source_file`, `file_type`, `char_count`, `heading_count`, `table_count`, `content` (normalized markdown), `parsed_at`.

**Behavior that must survive a rebuild:**
- **Fidelity, not summarization.** Headings map from any style resembling "Heading N" (including client-custom names), with outline-level fallback when the style is unnamed. Tables render as pipe-format markdown; merged cells expand, nested tables flatten cell-by-cell. Word structured-document-tag content controls (`<w:sdt>`) are recursively unwrapped — many templates hide required fields inside them.
- **Requirement IDs preserved verbatim** as bold markers so extraction can attribute rules back to them. The allowed ID-family list comes from the label contract, never hardcoded here.
- **TOC noise dropped, but "Header"-styled body content kept** — the project-ID line is typically a Header style, not a body paragraph.
- **Sanity gates, fail loud.** Reject the document if normalized content is under ~500 characters, or (docx) if zero headings were detected. Never pass an empty parse downstream.

### 3.2 Stage 2 — LLM extraction

**Purpose.** Convert normalized FRD prose into a structured, feed-level specification the rest of the pipeline can validate, audit, and render.

**Output shape** (top-level container; every field optional — absence means "not stated in the source"):
- `project`: `{ project_id, project_name, business_context_summary }`
- `in_scope[]`, `out_of_scope[]`: string bullets
- `assumptions_constraints_dependencies[]`: `{ name, description, acd_type: Assumption|Constraint|Dependency }`
- `system_interfaces[]`, `open_items[]`: string bullets
- `feeds[]` — the load-bearing entity, ~20 fields grouped as: **identity** (`feed_name`, `source_system`), **source file** (`file_name_patterns[]`, `file_format`, `delimiter`, `record_segments[]`), **scheduling** (`frequency`, `load_windows_sla[]`), **business scope** (`lobs[]`, `domain`, `sub_domain`), **storage location** (`landing_location`, `stage_target`, `standard_target`, each `{catalog, schema, tables[], load_strategy}`), **rules** (`validation_rules[]`, `recycle_rule`), **lifecycle** (`history_backfill`, `archive_retention`), **sensitivity** (`phi_pii_notes`), **cross-reference** (`sttm_reference`), **provenance** (`requirement_ids[]`).

See §7 ("The extraction transport") for the request-shape rules this stage must follow — they are load-bearing, not stylistic.

**Retrieval augmentation.** A **historical corpus** of previously approved FRD/STTM pairs (see §6) supplies few-shot exemplar blocks retrieved for the current FRD and included in the extraction prompt. This is a quality lever, not a correctness dependency — extraction must still work (just with fewer priors) when the corpus is empty or the current document has no close match.

**Output artifact.** One JSON file per document keyed by `doc_id`, plus an extraction-metadata sidecar recording provider, model, prompt/schema hashes, token usage, which exemplars were used, and SDK/job identifiers — written even for non-live (mock/replay) runs, because provenance must be reconstructable regardless of run mode.

### 3.3 Stage 3 — Contract build (validate, enrich, audit, gate)

**Purpose.** Turn the raw extraction JSON into a **contract**: structurally validated, ground-truthed against the FRD, with regex-derivable facts filled in deterministically, ambiguities gated for a human, and a global status stamped on the artifact.

**Pipeline** (pure, deterministic — no model calls):

```
raw_extraction_json
  ├─ validate()        structural: every object rejects unknown keys, nested + root
  │        ↳ FAIL → emit no contract; record status=FAIL and stop
  ├─ enrich()          deterministic seams the LLM should not own —
  │        │           project_id: regex-lift from FRD; fill if the model said null;
  │        │                        if the model disagrees, KEEP the model's value and
  │        │                        gate a "disagreement" ambiguity carrying both
  │        └─ region/LOB: regex-lift; only fill feeds whose lobs list is EMPTY —
  │                        never clobber a non-empty model-supplied value
  ├─ grounding_audit()  see §4 — the quality gate
  └─ attribution_check() cross-feed rule duplication: if the same (normalized) rule
           text appears on ≥2 feeds, emit an "attribution" ambiguity whose
           candidates are the feed names
```

**Status vocabulary — exactly three, strict priority order:**

| Status | Triggered by |
|---|---|
| `FAIL` | Structural validation failure, or any strict-grounding failure. No contract emitted. |
| `PASS_WITH_FLAGS` | Any attribution/disagreement ambiguity, or any advisory-grounding flag. Contract emitted; gated items go to review. |
| `PASS` | Clean. Contract emitted with an empty gate list. |

**Ambiguity vocabulary — exactly three `kind`s, enforced at the schema level (no "other" escape hatch):**

| Kind | Represents | Candidates |
|---|---|---|
| `attribution` | Same rule text on ≥2 feeds; a human picks which feed(s) it belongs to | feed names |
| `disagreement` | Regex-derived value differs from the model-extracted value on a strict field | both values |
| `advisory_grounding` | A prose field passed advisory grounding weakly, or was invented | none — free-text edit |

**Resolution vocabulary — exactly three `resolution_type`s, chosen structurally by `has_candidates`, never by `kind` name:**

| Type | Applies when | Stores |
|---|---|---|
| `candidate_pick` | `has_candidates=true` | `chosen_candidate` (must be in the candidate list) |
| `none_of_these` | `has_candidates=true` | explicit rejection; automatic behavior falls back downstream |
| `free_text` | `has_candidates=false` | `rationale` |

A `free_text` submission against a candidate-having ambiguity is malformed and must be rejected at **both** client and server.

**Ambiguity id.** A stable hash of `kind + normalized-text + context`, truncated and prefixed with the kind (e.g. `attribution-a1b2c3d4e5f6`). This is the join key for saved human resolutions across re-runs — freeze the algorithm; changing it requires migrating every persisted decision.

**Output artifact** (per document): a contract JSON carrying the flattened spec plus `_provenance: { enrichments[], ambiguities[], grounding: {strict_checked, strict_failed, advisory_checked, advisory_flagged} }`, and a table-store summary row (`doc_id, status, n_feeds, n_ambiguities, n_strict_failed, contract, audited_at`).

**Read the contract JSON, not the table-store row, downstream.** The table-store snapshot is pre-human-resolution; the artifact JSON is post-resolution. This is deliberate — later stages must read the artifact.

### 3.4 Stage 4 — Render + eval

**Purpose.** Produce a client-dialect STTM `.xlsx` and an eval score against a golden reference workbook, after folding in human resolutions.

**Pipeline:**

```
contract JSON ─┐
               ├─ read the historical corpus for a matching prior STTM →
reference xlsx ┘   build a source dictionary + (if matched) a layout template
               │
               ├─ apply_human_resolutions()   AUTHORITATIVE; runs first
               │       → resolution_audit entries: applied / not_applied(reason) / stale
               │
               ├─ resolve_attribution()       dictionary cross-check
               │       → remove rules from feeds the dictionary doesn't confirm;
               │         CONFIRM-AND-CLEAR even at zero removals when the
               │         remaining candidate set already equals the confirmed
               │         set (§5, "the D2 fix" — do not skip this)
               │
               ├─ derive_field_mappings()     explicit, contestable defaults:
               │       1:1 column mapping, datatype=String on both layers,
               │       stage table chosen by record-segment suffix, standard
               │       catalog/schema from the contract if stated
               │
               ├─ render()
               │       if a historical template matched: render INTO that
               │       workbook's own layout — sheets, band labels, headers,
               │       widths, styles kept; its data rows removed; ours
               │       written under the SAME headers via the logical roles
               │       already recovered; a template column the contract
               │       knows nothing about stays BLANK and is listed in
               │       `_provenance.template_fill.unfilled_columns` (never
               │       guessed); unused template sheets removed; extra
               │       feeds get a copy of the lead sheet
               │       else: fall back to a built-in freeform renderer
               │       (sheet-per-table or single-wide-sheet dialect)
               │
               └─ evaluate_against_reference()
                       cell-level match rate across (schema, table, column,
                       datatype) × {stage, standard} layers
```

**Precedence rule (critical).** Once a human resolution is structurally applicable, it is authoritative and is **never** re-decided by the automatic dictionary cross-check. Every non-application records a `reason_not_applied` (e.g. `stale` = the candidate no longer exists in the current candidate set).

**Output row** (per render): `doc_id, status, dialect, reference, n_resolutions, n_human_resolutions, n_human_resolutions_applied, eval_pct, eval_cells, rendered_path, run_at`. **Output artifacts:** the rendered workbook and a human-readable run report.

## 4. Grounding audit — the quality gate

Because the agent's entire quality claim rests here, it gets its own section.

- **Strict fields** (identifiers, paths, patterns, table names): must appear **verbatim** as a substring of the unicode-normalized source content. Any failure elevates the whole contract to `FAIL`. Fields: `project_id`, `project_name`, `file_name_patterns`, `record_segments`, `landing_location`, `lobs`, `requirement_ids`, `sttm_reference`, `stage_target.{catalog,schema,tables}`, `standard_target.{catalog,schema,tables}`.
- **Advisory fields** (prose): must overlap source prose at ≥0.75 token-overlap (tokens ≥4 chars, normalized). Failure elevates to `PASS_WITH_FLAGS` and captures structural write-back context (which feed, which field path, the original value) so a reviewer can edit in place. Fields: `validation_rules`, `recycle_rule`, `load_windows_sla`, `history_backfill`, `archive_retention`, `phi_pii_notes`, `in_scope`, `out_of_scope`, `system_interfaces`, `open_items`, ACD `.description`.
- Run both paths in one call; return counts (`strict_checked`, `strict_failed`, `advisory_checked`, `advisory_flagged`) as a provenance banner.
- **Calibrate the threshold against live model output on real documents, never against synthetic/mock extractions** — a mock that copies facts back from the FRD always grounds; it proves plumbing, not accuracy.
- **Never relax the audit to make a run pass.** The fix is a better extraction, a better source document, or a reviewer's `free_text` correction — never a lowered threshold or an excluded field.

## 5. Design constraints a rebuild must respect (hard-won)

- **Structured-output "grammar too large."** Provider-native schema-constrained decoding failed deterministically on this schema's shape (many optional properties, deeply nested nullable unions, every object rejecting unknown keys) — the server-side grammar compiler rejected it as too complex. Fix, and the constraint to keep: **send the JSON schema as text in the prompt; validate client-side.** Re-probe server-side structured output on whatever provider you target; if it fails on this same shape, fall back without hesitation — do **not** simplify the schema to satisfy a decoder, since the schema is the downstream contract.
- **No repair loop.** A schema-invalid response is a model-quality signal that must stay visible. Never silently re-ask.
- **Truncation is failure, not partial success.** A max-output-token stop reason (or a refusal) is a hard error; never parse truncated JSON. Size the output ceiling generously — truncation is fail-loud, so an over-generous ceiling costs nothing. (Reference measurement: ~700–800 output tokens per feed; size for 75+ feeds of headroom.)
- **Confirm-and-clear on zero removals (the "D2" fix).** A *better* extraction can produce *fewer* dictionary removals in the render-attribution step — which, if unhandled, leaves the attribution ambiguity gated forever. Rule: when the ambiguity's candidate feeds equal the dictionary-confirmed feed set, clear the flag even at zero removals. Test all three shapes: clears at zero removals when confirmed; stays gated when inconclusive; partially clears on partial overlap.
- **Ambiguity id stability.** The id scheme is the join key for every saved reviewer decision. Freeze it; a second migration means migrating every review app's stored decisions.
- **Non-determinism is inherent, not a bug.** The same document through the same model can produce different gate shapes across runs at identical extraction quality and identical eval score. Do not choreograph a demo around a specific gate count; ship a replay-fixture escape hatch (a tracked run set that reproduces a known narrative offline, no live model call) for when a live run's shape doesn't cooperate. A zero-gate run is not a failure to demo — reframe it: "the agent flags what it isn't sure of; the dictionary auto-confirms what the data already proves; zero flags means nothing needed a human this run."
- **Fail loud on ambiguous config, always.** Two providers configured at once, an unversioned label contract, a missing secret, mock-mode requested where a live run is required — all raise at startup or at the point of use. Never proceed on an implicit default in a pipeline whose output a client will act on.
- **Mock/replay mode is plumbing proof, never a quality benchmark.** Keep it local-only (hard-disabled wherever a real workspace/production run would use it — an explicit mock request there should raise, not be silently ignored) and never use its always-grounds behavior to set thresholds.

## 6. Historical corpus — quality lever, not a hard dependency

Every approved FRD/STTM pair becomes a template once harvested (see §8, sync):
1. **Golden-pair eval** on every regeneration (excluding the document's own reference when checking itself).
2. **Retrieved few-shot exemplars** in the stage-2 prompt (§3.2).
3. **The matched workbook as stage-4's dictionary + layout** (template-fill rendering, §3.4).

Pairing between an FRD and its STTM is **name-based first** (a fixed naming convention — e.g. `FRD_<name>` ↔ `STTM_<name>` by matching the stem after each role's prefix), with a **deterministic similarity score as the fallback** only when name-pairing doesn't resolve. Thresholds for "is this a good-enough template match" must be calibrated against your actual document set — seed values from a small fixture corpus are not calibrated for a real corpus; recalibrate before relying on them and record the outcome.

## 7. The extraction transport — request shape

- **One prompt in, one JSON object out.** System prompt frames the task tersely and forbids inventing identifiers, table names, schedules, or rules. User prompt = instruction + JSON-schema-of-the-output-container + the full normalized document. No tool use, no multi-turn reasoning, no subagents.
- **Client-side validation is authoritative**, with every object (root and nested) rejecting unknown keys.
- **No repair loop; truncation is failure.** (Repeated from §5 because it governs this stage specifically.)
- **Streaming only to avoid HTTP timeouts** at a large output-token ceiling — the response is consumed only after it completes; drop streaming if your transport doesn't need it at your ceiling.
- **A provider gate that raises on unrecognized or conflicting values** — e.g. mock mode and a live provider both requested is a startup error, not a preference to silently resolve.
- **Retries only on transient errors** (429/5xx-equivalent); never on a validation failure — that is a signal, not a fluke to smooth over.
- **This reference implementation calls the Anthropic Claude API directly** (a specific model id, configured, not hardcoded); the transport pattern above is what must survive a swap to any other model host.
- Reasoning/extended-thinking modes are worth trying on any given provider, but adopt one only if it measurably improves grounding on live prose — never merely because it's offered.

## 8. SharePoint sync — the system of record

A document library (SharePoint via Microsoft Graph, or an equivalent document-library API) is this program's **system of record**: FRDs and approved STTMs live there; the pipeline's artifact store is a working mirror kept in step by a sync process.

- **Read-only by construction.** Nothing in the pipeline writes back to the library. There is no upload endpoint, no output-folder config. The hand-off is a **person** uploading a reviewed workbook to the library's STTM location; the next sync pulls it in and pairs it with its FRD. This has a real permissions consequence: the library-access grant needed is read-only on the one library/site, never write.
- **The sync is both the initial load and the steady state.** It lists the FRD location and the STTM/reference location (which may be the same folder), downloads only what is new or changed (tracked by a per-item change token — id + etag/version + modified-time + size — recorded in a manifest next to the corpus index), removes the local copy of anything the library no longer has (**only** files the sync itself pulled in — a hand-staged local file is left alone), then rebuilds a **corpus index** (one entry per document, carrying a content hash) from what's now on disk. First run = bulk load; every later run = incremental, decided by comparing the manifest, not by a wall-clock "since last run" timestamp — this is more robust than a boot-time diff because it also catches a same-named file that changed in place.
- **Trigger: process start-up, plus an on-demand manual trigger. No cron schedule.** Documents arrive irregularly and there is no review gate on a scheduled pull, so a fixed schedule buys nothing and only adds an unreviewed sync window. On start-up, list everything not yet mirrored locally and pull it in; the same worker backs the manual trigger. Guard against two sync workers racing on the manifest (a concurrency limit of 1, or equivalent locking).
- **Naming-convention prefix filter.** If the library has a fixed naming convention (e.g., every FRD named `FRD_<name>`, every STTM `STTM_<name>`), listing can filter by prefix and pairing becomes exact-stem matching — similarity scoring stays only as the fallback for anything that doesn't fit the convention. Files that don't match the prefix are **counted** in the sync summary, never silently dropped.
- **Fail-loud, both directions.** Missing configuration raises naming the remedy. A short/partial download is reported, never written. A missing or empty library location is a warning (not a hard failure — a routine sync tick must not page anyone), but a refused/unauthorized listing call is a hard failure. One bad file's download or parse is recorded per-file, never fatal to the whole sync.
- **Credentials.** App-only/service credentials against the library API, secret excluded from any repr/log path. Prefer the narrowest available scope (a specific-site/specific-library read grant) over a tenant-wide read grant.
- **Picker / UI consequence.** A document already paired with an approved reference in the corpus index is a different UI state than an unpaired one — see §11 for exactly what "different state" should mean and the one thing it must never mean (silently vanishing).

## 9. The label contract

A single versioned artifact (this reference implementation: a JSON file) names every parsing convention stage 1 depends on: section headings, the project-ID line pattern, the requirement-id family list, the "pending"/TBD placeholder string. Load it explicitly and **fail loudly** if it's missing or carries no version — never fall back to a hardcoded default. If this convention is shared with an upstream document-producing process, keep the copies byte-identical and bump the version together; if there is no such upstream process (a frozen convention), treat the file as a description of the input documents this agent parses, changed only when a real document stops matching it.

## 10. Human-in-the-loop review model

**What the reviewer sees**, per ambiguity: a stable id; the `kind`; for `attribution`/`disagreement`, a candidate list; for `advisory_grounding`, an editable free-text field seeded with the current value; any prior resolution (who, when, what was picked); and provenance (which feed(s), and for advisory grounding, the exact field path).

**What the reviewer can do:** pick a candidate; reject all candidates (`none_of_these` — leaves it gated so the automatic dictionary cross-check can still resolve it); or edit prose with a rationale (`free_text`, advisory-grounding only).

**State machine:**
```
   (gated) ─pick───▶ candidate_pick ─re-render─▶ applied
       │
       ├─reject──▶ none_of_these ─re-render─▶ dictionary-fallback
       │
       └─edit────▶ free_text ─re-render─▶ prose_updated
```

Resolutions are **upserted by ambiguity id** in place on the contract artifact. On the next render, the resolution applies first (authoritative); the dictionary cross-check runs only on what remains. Every application records an audit entry: `applied` / `not_applied(reason)` / `stale`.

**Non-negotiables:**
- Structural-pick-required policy enforced on **both** client and server — reject a malformed submission (e.g. `free_text` on a candidate-having ambiguity) with a 4xx, never silently accept-and-drop.
- **Strict document-id matching** on every detail/download endpoint — no fuzzy fallback to a similarly-named document. This is the exact bug class that ships the wrong data to production.
- **Composite key `<doc_id>::<ambiguity_id>`** in any client-side state, so state never leaks between two documents open in the same session.
- **Empty vs. unreadable are different HTTP outcomes.** A successful listing with zero rows is 200+`[]`; an unreadable source is 5xx. The reviewer UI relies on this to switch views automatically on first load without ever masking a real error as "just empty."

## 11. What the picker/UI should show

- A document already paired with an **approved** reference in the corpus index presents that approved artifact (download + library link) rather than inviting a fresh (billed) generation — regeneration is available but is a deliberate two-step action, gated the same way a first-time generation is, and any such re-run is automatically eval'd against the existing approved artifact, which is never overwritten by the automatic path.
- An **unpaired** document is presented for generation.
- **A revised document whose paired reference predates the revision** (detectable via the content-hash/change-token the sync already tracks) should be surfaced distinctly, not silently presented as if the pairing were still current — this is the one gap the reference implementation had not yet closed at time of writing; a rebuild has no excuse to reproduce that gap since the fingerprint to detect it already exists in the index.
- If you introduce **any policy that keeps a paired document generatable regardless of its pairing state** (e.g., a small, explicit allow-list for a specific event), keep it: (a) explicit and narrowly scoped (by document id, not a global toggle), (b) visibly time-boxed if it's temporary, and (c) removed or expired deliberately rather than left as silent permanent behavior — do not let a one-time exception become an undocumented permanent rule. Record such a decision in your equivalent of this repo's CLAUDE.md, not only in code.

## 12. Governance and audit

- **Every governed action has a named actor and an audit event, or it does not happen.** Governed actions: start a run, record a resolution, re-render, download a workbook (the hand-off boundary — this is where a generated STTM leaves the governed system, since the pipeline never writes back to the library), start a sync, upload. Identity comes from whatever the hosting platform injects for an authenticated caller (this reference implementation: reverse-proxy-forwarded headers); in a networked/production deployment, a request missing that identity is a hard 401 (an explicit operator-only escape hatch may record actor `unknown`, never silently proceed as if authenticated); a local/dev mode may record the OS user instead.
- **Audit events are append-only, written before the action, and a failed write blocks the action.** One record per event (this reference implementation: one JSON file per event in an artifact-store "events" path, mirrored locally). Never log document content in an event — only who, what action, on which id, when.
- **Provenance is hashes and ids, never file names.** A document's content hash should match everywhere it's referenced: the ingest record, the corpus index, the run manifest, the extraction-metadata sidecar. A rendered workbook's content hash should match between the run record and the download event.
- **The run log is append + schema-evolve, never overwrite** — it is the record of every render, including who triggered it, by what path (interactive/job/manual), with what provider/model, and the input/output document hashes.
- **Classification/tagging is a separate, hand-run step — never a pipeline side effect.** Whatever you use to classify data sensitivity and register ownership (this reference implementation: Unity Catalog tags feeding a data-governance catalog) should be applied by a deliberate, privileged, idempotent job — not embedded in the four pipeline stages.
- **Access model: may-run implies may-read, one tier.** Whoever is allowed to trigger the pipeline is allowed to read its inputs/outputs; there is no separate "can view but not run" tier. Reserve write/manage-level grants for an owner/steward role distinct from the run-capable group.
- **Leave every non-technical governance decision recorded as a decision, not resolved in code**: data ownership/stewardship, the model vendor's data-handling posture, retention period, and exactly who gets the run-capable grant. A rebuild that invents defaults for these silently is doing someone else's job badly; write down what was decided and by whom.

## 13. Config surface

Parameterize, never hardcode, at minimum: the document-store/artifact-store locations (raw FRDs, reference workbooks, output), the three table-store names, the model identifier, the output-token ceiling, retry count (transient errors only), the provider selector (raising on an unrecognized value), a mock/replay toggle (hard-disabled outside local/dev), and the app's local-vs-networked mode switch (identity/audit behavior differs by mode, per §12). If your runtime supports two different configuration surfaces (e.g., a notebook-widget system and environment variables), give every knob both, with one deterministically overriding the other, and use the **same underlying name** across surfaces unless a genuine constraint forces a mismatch — and if it does, document the mapping explicitly rather than leaving two names to drift apart silently.

## 14. Data handling discipline

If the source documents carry real client content: never let real documents (raw or derived) live in the source repository — treat any anonymized fixture material as something produced through one deliberate, mandated anonymization tool path, not ad hoc redaction, and keep working/generated artifacts in the artifact store (or a gitignored local scratch path) rather than tracked in version control. A synthetic-fixture generator that produces structurally valid but content-free documents is worth building early — it lets the full pipeline run offline, in CI, and in a fresh environment with zero client material and zero network access.

## 15. Acceptance criteria — treat a test suite as the real spec

A rebuild is not done until an equivalent suite is green, grouped by theme:

- **Grounding & enrichment.** Unicode/markdown normalization round-trips (NFKC, smart-quote collapse, code-fence stripping); short tokens don't distort advisory overlap; a verbatim value passes strict grounding and a hallucinated one fails; advisory overlap right at the 0.75 boundary in both directions; empty values skip the audit; one grounding call returns both strict and advisory results plus write-back context; enrichment fills only on model-null (project id) or model-empty (lobs), never clobbers.
- **Gating vocabulary.** Clean spec → `PASS`, empty gate list. Schema-invalid → `FAIL`, no contract emitted. Strict-ungrounded → `FAIL`. Attribution/advisory conditions → `PASS_WITH_FLAGS`. Every ambiguity validates against its schema; an unknown `kind` is rejected. The provenance banner's counts match the actual audit run.
- **Model contract.** Round-trip serialization of every canonical fixture; every object rejects unknown keys at every nesting level; a `GatedAmbiguity` accepts exactly the three kinds; a `HumanResolution` accepts exactly the three resolution types; empty/absent fields are legal first-class states, not errors.
- **Label contract.** Present, parses, carries a version; every declared convention is actually used by the parser; a missing file or missing version raises a specific error, never a silent fallback.
- **Extraction transport.** Prompt embeds the schema and the document body; parse succeeds on plain and fenced JSON, fails on extra fields and invalid JSON; a fake-client happy path checks model id, output ceiling, and system-prompt passthrough are all wired correctly; a truncation or refusal stop reason skips parsing and raises; a validation failure propagates naming the offending field.
- **Render attribution.** The D2 confirm-and-clear case (zero removals, candidates == confirmed set → cleared); the inconclusive case (stays gated); the partial-overlap case (partially cleared).
- **Review-app backend.** Concurrent-run rejection (409); a failed stage marks the run failed and surfaces why; missing credentials block a live run; path containment (no traversal, no touching curated storage); upload validation (type, size); strict-id lookups reject fuzzy matches; a results payload strips internal-only gate metadata before it reaches a client.
- **End-to-end scenarios.** A live run on a real-shaped document produces `PASS` or `PASS_WITH_FLAGS` with no strict failures and no hallucinated identifiers, at an eval score near your calibrated baseline; a replay run (no live model call, tracked fixtures) reproduces byte-for-byte; a mock run completes and is understood by everyone involved to prove plumbing only.

## 16. Do-not list (condensed)

- Do not let the LLM self-certify — every claim it makes gets audited by code.
- Do not add a repair/retry loop on a validation failure — only on transient transport errors.
- Do not relax a grounding threshold, exclude a field, or accept invented prose to make a run pass.
- Do not skip the confirm-and-clear-at-zero-removals check — it is the one case where a *better* model output would otherwise produce a *worse* final status.
- Do not add a write path back to the source document library "for convenience" — the whole permission and audit model assumes read-only.
- Do not put a schedule on the sync or the pipeline job unless you also build a review gate for an unattended run — until then, start-up + manual trigger is the correct trigger set, not a placeholder for "should add a cron later."
- Do not move classification/tagging into the pipeline stages — it is a separate, hand-run, privileged step.
- Do not silently default on ambiguous configuration (two providers, missing version, missing secret) — raise, naming the remedy.
- Do not fuzzy-match a document id anywhere a specific document's data could leak into another document's view.
- Do not treat a mock/replay run's grounding success as a quality signal.
