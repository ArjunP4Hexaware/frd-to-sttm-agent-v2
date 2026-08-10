# FRD → STTM Agent — Native Rebuild Specification

> **Scope of this document.** A functional and architectural specification, written to be sufficient to rebuild the agent from scratch on **Databricks Genie Code** using **Lakeflow Declarative Pipelines (LDP)**, **Unity Catalog (UC)**, and **Databricks Model Serving / AI Gateway** — instead of the current Python-notebooks + Anthropic-SDK + FastAPI-review-app stack. It describes *what* each component does and *why*, in prose and pseudocode. It deliberately avoids pasting source, exact function bodies, or literal Pydantic classes; those live in the existing repo and should not be transcribed here.

---

## 1. Product Context

The agent is the **second link in a five-agent AI-in-Engineering program**: BRD → FRD → **FRD → STTM** → CodeGen → Code Review. Its inputs and outputs are content contracts with the neighboring agents and must remain byte-stable across both sides:

- **Upstream** — reads a Functional Requirements Document (FRD) produced by the BRD-to-FRD agent, and shares a **label contract** JSON that names section headings, project-ID conventions, requirement-ID families, and placeholder strings. That file is committed byte-identically to both repos; any change bumps its `version` and must land in both repos in the same change set.
- **Downstream** — must ultimately produce a **feed-level STTM mapping contract JSON** that a CodeGen agent can consume. Today the agent renders a client-dialect **STTM workbook** (.xlsx) and emits a per-run contract JSON with human-review resolutions folded in; the workbook-→CodeGen-contract adapter is the **biggest known program-wide gap** and is out of scope for the current agent.

Design philosophy carried over from the existing implementation:

> The LLM reads prose and scattered requirement tables; deterministic code owns validation, regex-able facts, grounding checks, attribution, and rendering. Every extracted string is audited against the source document; genuine ambiguities are gated for human review, never guessed.

The rebuild must preserve that division of labor. The LLM should never be the arbiter of "did we get this right"; audits, gates, and dictionary cross-checks are.

---

## 2. End-to-End Pipeline

Four deterministic stages, executed in order. Each stage's output is durable in UC (Delta table for structured summary + UC Volume for JSON artifacts) so any downstream stage can be re-run without re-executing predecessors.

```
   ┌───────────┐    ┌──────────┐    ┌──────────────────┐    ┌────────────────┐
   │  Ingest   │ →  │ Extract  │ →  │ Contract Build   │ →  │  Render + Eval │
   │  (FRD)    │    │  (LLM)   │    │ (validate/gate)  │    │  (STTM.xlsx)   │
   └───────────┘    └──────────┘    └──────────────────┘    └────────────────┘
        ↓                ↓                    ↓                       ↓
   frd_documents    extractions/       frd_contracts +           frd_sttm_runs +
     (Delta)        <doc>.json           contracts/               rendered/
                    (UC Volume)         <doc>.contract.v2.json    <doc>.sttm.xlsx
                                        (UC Volume)               (UC Volume)
                                                                  reports/
                                                                  <doc>.phase5.md
```

**Human review** attaches between Contract Build and Render: gated ambiguities land in the contract JSON; a reviewer resolves them via the review app; Render is re-run and folds the resolutions in.

### 2.1 Stage 1 — FRD Ingest

**Purpose.** Turn a source-format FRD (docx / pdf / markdown / text) into a single, faithful **markdown snapshot** captured in a Delta table, so extraction always sees the same normalized text and later grounding audits can be run against a stable string.

**Conceptual input.** Files in a UC Volume (`frd_raw`). One file = one document.

**Conceptual output row (`frd_documents` Delta table).** One row per successfully ingested FRD:

| Field           | Type       | Purpose |
|-----------------|------------|---------|
| `doc_id`        | string     | Stable per-document id (source filename stem) |
| `project_id`    | string     | Pulled from the FRD's declared *Project ID* line if present |
| `source_file`   | string     | UC-volume path of the raw file |
| `file_type`     | string     | `docx` / `pdf` / `md` / `txt` |
| `char_count`    | int        | Sanity check |
| `heading_count` | int        | Sanity check (docx must have > 0) |
| `table_count`   | int        | Diagnostic |
| `content`       | string     | Normalized markdown |
| `parsed_at`     | timestamp  | Ingest run time |

**Behavior worth preserving.**

- **Fidelity, not summarization.** Headings are mapped from any style whose name resembles "Heading N" (including client-custom `Document Heading N`). Outline-level fallback is used when the style is unnamed. Tables are rendered as pipe-format markdown, with merged cells expanded and nested tables flattened cell-by-cell. Word `<w:sdt>` content controls (structured document tags) are recursively unwrapped — many client templates hide required-fields inside them, and skipping SDTs silently loses content.
- **Requirement identifiers preserved verbatim.** Lines of the form `REQ-###`, `SRQ-###`, `FR-###`, etc., are captured as bold markers so extraction can attribute rules back to them. The list of allowed families is *defined by the shared label contract*, not hardcoded here.
- **TOC noise is dropped but "Header"-styled body content is kept.** In practice the first project-ID line is a Header, not a body paragraph; discarding all headers loses it.
- **Sanity gates.** Reject the document if the normalized content is < 500 characters, or (for docx) if no headings were detected. Fail loudly rather than passing an empty parse downstream.

**On LDP.** A materialized view / streaming table sourced from the `frd_raw` UC Volume, with a Python UDF or notebook step doing the docx/pdf normalization. The Volume-file trigger pattern (Auto Loader with `binaryFile`) is a good fit; parsing is best done inside a `@dlt.table` step to keep the artifact and its provenance in UC.

### 2.2 Stage 2 — LLM Extraction

**Purpose.** Convert normalized FRD prose into a structured **feed-level specification** (`FrdIngestionSpec`) that downstream stages can validate, audit, and render.

**Conceptual output shape** (top-level container; every field is optional, meaning "not stated in the source"):

- `project`: `{ project_id, project_name, business_context_summary }`
- `in_scope[]`, `out_of_scope[]`: string bullets
- `assumptions_constraints_dependencies[]`: `{ name, description, acd_type: Assumption|Constraint|Dependency }`
- `system_interfaces[]`, `open_items[]`: string bullets
- `feeds[]` — the load-bearing entity, ~20 fields per feed grouped as:
  - **Identity**: `feed_name`, `source_system`
  - **Source file**: `file_name_patterns[]`, `file_format`, `delimiter`, `record_segments[]`
  - **Scheduling**: `frequency`, `load_windows_sla[]`
  - **Business scope**: `lobs[]`, `domain`, `sub_domain`
  - **Storage location**: `landing_location`, `stage_target`, `standard_target` (each: `{ catalog, schema, tables[], load_strategy }`)
  - **Rules**: `validation_rules[]`, `recycle_rule`
  - **Lifecycle**: `history_backfill`, `archive_retention`
  - **Sensitivity**: `phi_pii_notes`
  - **Cross-reference**: `sttm_reference`
  - **Provenance**: `requirement_ids[]`

Every string is expected to appear **verbatim** in the source FRD or (for prose fields) to substantially reuse its tokens. See §3 for the audit contract that enforces this.

**Behavior worth preserving.**

- **Request shape (see §7 for design constraints):** system prompt frames the extraction task tersely and forbids invention of identifiers, table names, schedules, or rules; user prompt is `instruction + JSON-schema-of-the-output-container + full FRD markdown`. The response is one JSON object. No tool use, no filesystem, no multi-turn reasoning, no subagents.
- **Client-side validation is authoritative.** The JSON returned is parsed and validated against the container model with `extra="forbid"` semantics — unknown keys are an error, not a warning. There is **deliberately no re-ask / repair loop**: a validation failure raises immediately, naming the offending field. Silently retrying would blur the model-quality signal the first failure carries.
- **Truncation is failure.** A `max_tokens` stop reason is treated as truncated JSON and raises; a refusal is likewise a hard failure.
- **Provider gate.** Two provider paths exist: a **mock** path (fixture-based, local-only, zero cost) and a **live** path. The gate raises on unrecognized values rather than silently degrading. A mock+live cross-configuration is an error.

**Conceptual output artifact.** One JSON file per document (`extractions/<doc_id>.json`) written to a UC Volume, keyed to the `doc_id` from Stage 1.

**On Databricks Model Serving.** This is the module that changes most in the port; see §7 and §8.

### 2.3 Stage 3 — Contract Build (Validate, Enrich, Audit, Gate)

**Purpose.** Turn the raw extraction JSON into a **contract** — validated, ground-truthed against the FRD, with regex-derivable facts filled in deterministically, ambiguities gated for a human, and a global status stamped on the artifact.

**Conceptual pipeline** (all pure Python; the LDP step is a plain transformation with no model calls):

```
raw_extraction_json
    │
    ├── validate()        # structural: pydantic-style extra="forbid", nested + root
    │       ↳ FAIL → emit no contract; write frd_contracts row with status=FAIL
    │
    ├── enrich()          # deterministic seams the LLM should not own
    │       ├── project_id: regex-lift from FRD; if agent said null, fill;
    │       │              if agent disagrees, KEEP agent value and gate a
    │       │              "disagreement" ambiguity carrying both candidates
    │       └── region/LOB: regex-lift REG#/lob pairs; only fill feeds whose
    │                      lobs list is empty — never clobber non-empty
    │
    ├── grounding_audit() # every extracted string vs FRD content
    │       ├── STRICT   (verbatim substring on unicode-normalized text)
    │       │            project_id, project_name, file_name_patterns,
    │       │            record_segments, landing_location, lobs,
    │       │            requirement_ids, sttm_reference,
    │       │            stage_target.{catalog,schema,tables},
    │       │            standard_target.{catalog,schema,tables}
    │       │            → any failure ⇒ status FAIL
    │       │
    │       └── ADVISORY (token-overlap ≥ 0.75, tokens ≥ 4 chars)
    │                    validation_rules, recycle_rule, load_windows_sla,
    │                    history_backfill, archive_retention, phi_pii_notes,
    │                    in_scope, out_of_scope, system_interfaces,
    │                    open_items, ACD.description
    │                    → any flag ⇒ status PASS_WITH_FLAGS, gated
    │                                   with feed-scoped write-back context
    │
    └── attribution_check()  # cross-feed rule duplication
            # if the same rule text (normalized) shows up on ≥ 2 feeds,
            # emit an "attribution" ambiguity with candidates = feed names
```

**Verdict / status vocabulary.** Exactly three statuses, in strict priority order:

| Status              | Triggered by |
|---------------------|--------------|
| `FAIL`              | Structural validation failure **or** any strict-grounding failure. No contract emitted. |
| `PASS_WITH_FLAGS`   | Any attribution or disagreement ambiguity, **or** any advisory-grounding flag. Contract emitted; review app receives gated items. |
| `PASS`              | Clean — no strict failures, no advisory flags, no ambiguities. Contract emitted with an empty gate list. |

**Ambiguity vocabulary.** Exactly three `kind` values, and they must be enforced by the schema itself (an "other" kind is prohibited — the taxonomy is a designed constraint, not a soft convention):

| Kind                  | What it represents |
|-----------------------|--------------------|
| `attribution`         | The same rule text appears on ≥ 2 feeds; a human must pick which feeds it applies to. Candidates = feed names. |
| `disagreement`        | The regex-derived value differs from the model-extracted value for a strict field (today: `project_id`). Candidates = both values. |
| `advisory_grounding`  | A prose field passed advisory grounding weakly (or was invented). Free-text edit required. No candidates. |

**Human resolution vocabulary.** Exactly three `resolution_type` values, and the reviewer's UI selects the right one **structurally**, not by kind name:

| Resolution type  | Applies when          | What it stores |
|------------------|-----------------------|-----------------|
| `candidate_pick` | `has_candidates=true` | `chosen_candidate` (must be in the candidates list) |
| `none_of_these`  | `has_candidates=true` | Explicit rejection; falls back to automatic behavior downstream |
| `free_text`      | `has_candidates=false`| `rationale` (advisory-grounding edits) |

A `free_text` submission on a candidate-having ambiguity is **malformed** and must be rejected by both client and server. This structural-pick-required policy must be enforced at both layers.

**Ambiguity id.** A stable hash of `kind + text + context`, truncated (12 chars) and prefixed with the kind (e.g. `attribution-a1b2c3d4e5f6`). This is the **join key** for saved human resolutions across re-runs; changing the id scheme is a breaking change and requires migrating the review app's stored decisions.

**Conceptual output artifact** (`contracts/<doc_id>.contract.v2.json`, plus a summary row into `frd_contracts` Delta):

```
{
  contract_name, generated_from_frd, generated_date, generator, status,
  <flattened FrdIngestionSpec here>,
  _provenance: {
     enrichments: [...],
     ambiguities: [ { id, kind, text, has_candidates, candidates[], context } ],
     grounding: { strict_checked, strict_failed, advisory_checked, advisory_flagged }
  }
}
```

`frd_contracts` Delta columns: `doc_id, status, n_feeds, n_ambiguities, n_strict_failed, contract, audited_at`.

### 2.4 Stage 4 — Render + Eval (STTM Workbook)

**Purpose.** Produce a client-dialect STTM `.xlsx` and an eval score against a golden reference workbook, after folding in any human resolutions.

**Conceptual pipeline** (all pure Python; no model calls):

```
contract JSON  ─┐
                ├── read reference workbook → build source dictionary
reference xlsx ─┘
                ├── apply_human_resolutions()   # AUTHORITATIVE; runs first
                │       → resolution_audit entries: applied / not_applied / stale
                │
                ├── resolve_attribution()       # dictionary cross-check
                │       → remove rules from feeds whose dictionary
                │         columns don't reference them; CONFIRM-AND-CLEAR
                │         when candidate feeds match dictionary-confirmed set
                │         even at zero removals (the "D2" fix)
                │
                ├── derive_field_mappings()     # explicit contestable defaults
                │       → 1:1 column mapping, datatype=String on both layers,
                │         stage table by segment suffix (HDR/DTL/TRL),
                │         standard catalog/schema from contract if stated
                │
                ├── render_workbook()           # dialect-aware
                │       → sheet_per_table (per-feed MAPPING-<TABLE> sheets)
                │       → single_sheet     (one wide sheet, CAQH-style)
                │
                └── evaluate_against_reference()
                        → cell-level match rate across
                          (schema, table, column, datatype) × 2 layers
```

**Two supported dialects** (both must survive the port; header-alias tables identify columns in either layout):

| Dialect             | Layout |
|---------------------|--------|
| `sheet_per_table`   | `FILE_DETAILS` + `VERSION_HISTORY` + one `MAPPING-<TABLE>` sheet per feed with three column blocks (Source Layout / Stage Layer / Standard Layer). |
| `single_sheet`      | One wide sheet: metadata + one Source/Stage/Standard block. |

**Precedence rule (critical).** Once a human resolution is structurally applicable, it is authoritative and is never re-decided by the automatic dictionary cross-check. Every non-application must record a `reason_not_applied` (e.g. `stale` = candidate no longer in the current candidate set).

**Conceptual output row (`frd_sttm_runs` Delta table).** One row per render:

`doc_id, status, dialect, reference, n_resolutions, n_human_resolutions, n_human_resolutions_applied, eval_pct, eval_cells, rendered_path, run_at`.

**Artifacts also written to UC Volume:** `rendered/<doc_id>.sttm.xlsx`, `reports/<doc_id>.phase5.md` (a human-readable run report).

**Read the contract JSON, not the `frd_contracts` Delta.** The Delta snapshot is pre-human-resolutions; the JSON is post-resolutions. This is a deliberate design choice — the port must preserve it.

---

## 3. Grounding Audit — Design Contract

Because the entire quality claim of the agent rests on this audit, it is called out as its own section.

### 3.1 Rule categories

- **Strict fields** (identifiers, paths, patterns, table names, IDs): must appear **verbatim** as a substring of the (unicode-normalized) FRD content. Any failure elevates the whole contract to `FAIL`.
- **Advisory fields** (prose rules, retention notes, PHI notes, scope bullets): must overlap the FRD prose at ≥ 0.75 token-overlap (tokens ≥ 4 chars, normalized). Failure elevates the contract to `PASS_WITH_FLAGS` and captures **structural write-back context** (feed index, field path, original value) so a reviewer can edit the offending prose in-place.

### 3.2 Design constraint — calibrate against real model prose, not synthetic prose

Grounding thresholds and expectations should be measured against **outputs the current model actually produces on the current FRD**, not against hand-crafted synthetic mocks. Mock extractions in this repo copy real facts back from the FRD and therefore always ground; they prove plumbing works, not that the model is accurate. Historical grounding numbers from earlier schema/pipeline versions are not comparable and should not be treated as regression baselines. Any rebuild must re-baseline grounding on a live end-to-end run against the current schema before setting thresholds or writing test assertions.

### 3.3 Design constraint — never relax the audit to make a run pass

If a run flags advisory grounding, the fix is either (a) a better extraction, (b) a better source document, or (c) a reviewer's `free_text` correction. It is **not** to lower the threshold, exclude the field, or accept invented prose.

---

## 4. HITL (Human-in-the-Loop) Review Model

### 4.1 What the reviewer sees

Per ambiguity:

- **Identity**: stable id, kind, and (for attribution) the extracted rule text.
- **Choices**: the candidate list (attribution / disagreement) *or* an editable free-text field (advisory grounding).
- **Prior state**: any existing resolution, so the reviewer can see who resolved it, when, and what they picked.
- **Provenance**: the feed(s) the ambiguity touches, and (for advisory grounding) the field path being edited.

### 4.2 What the reviewer can do

- **Pick a candidate** (attribution and disagreement only).
- **Reject all candidates** with `none_of_these`, which leaves the ambiguity gated so downstream automatic behavior (dictionary cross-check for attribution) can still take effect.
- **Edit prose** with `free_text` (advisory grounding only), supplying a rationale.

### 4.3 State transitions

```
       ┌────────────────────────────────────────────┐
       ▼                                            │
   (gated) ─pick──▶ candidate_pick ─re-render──▶ applied
       │
       ├─reject─▶ none_of_these ─re-render──▶ dictionary-fallback
       │
       └─edit──▶ free_text ─re-render──▶ prose_updated
```

Resolutions are **upserted by `ambiguity_id`** into `_provenance.human_resolutions` on the contract JSON in place. On the next render, the resolution is applied first (authoritative), then the automatic dictionary cross-check runs on whatever remains. Every application produces a `resolution_audit` entry marking `applied`, `not_applied` (with reason), or `stale` (candidate no longer valid).

### 4.4 Non-negotiables

- **Structural-pick-required policy** enforced on both client and server. The server must reject a malformed submission (e.g. `free_text` on a candidate-having ambiguity) with 4xx, not silently accept-and-drop.
- **Strict `doc_id` matching** on all detail/workbook endpoints — no fuzzy fallback to a similarly-named document.
- **Composite key `<doc_id>::<ambiguity_id>`** in any client-side state so state does not leak between documents open in the same UI session.
- **Empty vs unreadable are different.** A successful listing with no rows returns 200 + `[]`; an unreadable source returns 5xx. The client uses this to auto-switch tabs on first load without hiding real errors.

---

## 5. Design Constraints a Rebuild Must Respect (Hard-Won Lessons)

These are the calibration decisions the current implementation paid for. A rebuild that ignores them will re-hit the same failure modes.

### 5.1 Structured-output "grammar too large" — the D1 constraint

Provider-native structured output (schema-constrained decoding) failed deterministically on this schema shape: many optional properties, deeply nested nullable unions, and `additionalProperties=false` everywhere. Server-side grammar compilers rejected it as too complex. The fix — and the constraint the rebuild must satisfy — is:

> **Send the JSON schema as text in the prompt; validate on the client side with `extra="forbid"` semantics.**

The rebuild target (Databricks Model Serving / AI Gateway with a Claude backing model, or a Foundation Model endpoint) may or may not have the same limit. Re-probe by attempting server-side structured output first; if it fails on the same shape, fall back to schema-in-prompt without hesitation. Do **not** simplify the schema shape to satisfy a decoder — the schema is the extraction contract with downstream consumers.

### 5.2 Request shape — schema-in-prompt + streaming, no repair loop

The current pattern is: **plain messages call, one shot, streaming for large output ceilings, client-side Pydantic-style validation, and no retry on validation errors**. This is deliberate:

- **Streaming** avoids HTTP timeouts at large output-token ceilings; the request is otherwise identical to a non-streaming call.
- **No repair loop**: a schema-invalid response is a model-output-shape signal that must be visible. Silent re-asking would mask the quality regression on the very first failure.
- **`max_tokens` truncation is failure, not partial success.** Do not attempt to parse truncated JSON.
- **No `thinking` / extended-thinking / betas / tool-use / subagents** are used today. The design commitment is "one prompt in, one JSON object out." Adaptive-thinking modes may be worth re-probing on the new platform, but must be introduced only if they measurably improve grounding on live prose — not because they are available.

### 5.3 Output-token ceiling sizing

Measured budget on a demo FRD is ~700–800 output tokens per feed. A safe ceiling gives ~75–90 feeds of headroom (currently 64 000). Set the ceiling high enough that a single-run FRD cannot truncate under normal conditions. Truncation is fail-loud, so the cost of an over-generous ceiling is zero.

### 5.4 Ambiguity id stability

The `ambiguity_id` scheme is the join key for saved reviewer decisions. It has already been migrated once; migrating again requires updating every persisted decision in every review-app data store. Freeze the algorithm (`kind + normalized-text + context → sha1 → first 12 chars`) unless there is a compelling reason to break resolution history.

### 5.5 Confirm-and-clear on zero removals (the "D2" fix)

A **better** extraction can produce **fewer** dictionary removals — which historically left the attribution ambiguity gated forever. The rule: when the ambiguity's candidate feeds equal the dictionary-confirmed feed set (even with zero removals from the LLM's picks), clear the flag. Skipping this check causes the demo pathological outcome "correct extraction → worse final status."

### 5.6 Grounding calibrated against real prose

See §3.2. Do not use mock extractions to set grounding thresholds; mocks are plumbing tests. Baseline against a live run on a real FRD.

### 5.7 Mock mode is plumbing proof, not a benchmark

The mock provider path exists to run the pipeline end-to-end without an LLM. It must remain local-only (guarded off in the workspace runtime) and must never be used to declare grounding quality. Configuration ambiguity (mock + live simultaneously) is a hard failure, not a preference.

### 5.8 Determinism, or the lack of it, is a demo concern

Three live runs on the same FRD produced three different gate shapes at identical extraction quality and identical eval scores. A rebuild's demo choreography must not depend on a specific gate count; the replay-fixture escape hatch is the reliable path for demos.

### 5.9 Fail loud on ambiguous config

Two providers configured at once, an unversioned label contract, a missing secret — all raise on startup rather than proceeding with an implicit default. The rebuild must adopt the same discipline; silent fallbacks in a compliance-sensitive pipeline are worse than a red startup.

---

## 6. Target Architecture on Databricks Genie Code

### 6.1 Runtime shape

- **Ingest**, **Extract**, **Contract Build**, **Render** as four LDP steps (materialized views / streaming tables) inside a single **Lakeflow Declarative Pipeline** owned by the FRD-STTM project.
- **Unity Catalog** owns the three Delta tables (`frd_documents`, `frd_contracts`, `frd_sttm_runs`) and the artifact Volumes (`frd_raw`, `sttm_out`, `sttm_reference`).
- **Databricks Model Serving** (Foundation Model API or external-model endpoint via AI Gateway) hosts the extraction call. The current Anthropic SDK client is replaced by a Model Serving HTTP call (or the `databricks-sdk` Serving client); the request/response shape stays the same in spirit (one prompt in, one JSON object out).
- **Review app** stays as a Databricks App (FastAPI backend + React frontend). The existing `STTM_APP_MODE=databricks` code path is designed for exactly this deployment; the rebuild inherits it.
- **Secrets** for external-model endpoints (only if going via AI Gateway to an external provider) live in a Databricks secret scope; the Foundation Model path removes the secret dependency entirely because workspace identity handles auth.
- **Genie Code** is the authoring surface — pipeline steps are Genie Code notebooks or Python files under the LDP.

### 6.2 Table & volume topology

```
Unity Catalog
├── <catalog>.<schema>.frd_documents      (Delta, one row per ingested FRD)
├── <catalog>.<schema>.frd_contracts      (Delta, one row per contract build)
└── <catalog>.<schema>.frd_sttm_runs      (Delta, one row per render)

UC Volumes
├── /Volumes/<catalog>/<schema>/frd_raw           (input FRDs)
├── /Volumes/<catalog>/<schema>/sttm_reference    (golden reference workbooks)
└── /Volumes/<catalog>/<schema>/sttm_out
        ├── extractions/<doc_id>.json
        ├── contracts/<doc_id>.contract.v2.json
        ├── rendered/<doc_id>.sttm.xlsx
        └── reports/<doc_id>.phase5.md
```

### 6.3 Config surface (parameterize the pipeline; do not hardcode)

| Setting             | Purpose |
|---------------------|---------|
| `catalog`, `schema` | UC target |
| `raw_volume`, `out_volume`, `reference_volume` | UC Volumes |
| `docs_table`, `contracts_table`, `runs_table`  | Delta table names |
| `model`             | Model Serving endpoint name or Foundation Model id |
| `max_tokens`        | Output ceiling (default ~64k; see §5.3) |
| `max_retries`       | Transient-error retries only (429 / 5xx); never on validation errors |
| `llm_provider`      | `mock` | `live`; unrecognized value raises |
| `mock_extraction`   | Local-only escape hatch; hard-fails if enabled in workspace runtime |
| `app_mode`          | `local` | `databricks` (review-app data access) |

Genie Code / LDP pipeline **parameters** map cleanly to these names.

---

## 7. Acceptance Criteria — What "Done" Looks Like

The current test suite is the strongest available specification of "correct behavior." A rebuild is not done until an equivalent suite is green. Grouped by theme:

### 7.1 Grounding & enrichment

- Unicode / markdown normalization round-trips: NFKC, smart-quote collapse, code-fence stripping.
- Token-length filtering: single-character or short tokens do not distort advisory overlap.
- Strict-substring detection: a value present verbatim in the FRD passes; a hallucinated identifier fails.
- Advisory overlap at exactly the 0.75 threshold: on-boundary values pass; below-threshold values fail.
- Empty values skip the audit (nothing to ground).
- Grounding audit runs both strict and advisory paths in one call and returns structural write-back context for advisory flags.
- Enrichment: project-id fill when agent said null; disagreement gated when agent value differs; LOB fill only for empty lobs; existing non-empty lobs never clobbered.

### 7.2 Gating vocabulary

- Clean spec → status `PASS`, empty gate list.
- Schema-invalid spec → status `FAIL`, **no contract emitted**.
- Strict-ungrounded spec → status `FAIL`.
- Attribution / advisory-grounding conditions → status `PASS_WITH_FLAGS`.
- Every emitted ambiguity validates against the `GatedAmbiguity` schema; unknown `kind` values are rejected at model level.
- Provenance banner (`_provenance.grounding` counts) matches the actual audit results.
- Human-readable run report includes each ambiguity's text and kind.

### 7.3 Model contract

- Round-trip serialization of every canonical spec fixture.
- `extra="forbid"` at both root and every nested container.
- `schema` alias round-trips (the field is a reserved word in Pydantic and requires aliasing).
- `GatedAmbiguity` accepts exactly the three `kind` values; an "other" is rejected.
- `HumanResolution` accepts exactly the three `resolution_type` values; unknown types are rejected.
- Empty defaults (`feed_name is None`, empty lists) are legal — "not stated" is a first-class state.

### 7.4 Label contract

- File is present, parses, and carries a version.
- Every section / requirement-family / project-id sub-key is present.
- Project-id regex matches normalized text.
- Digits pattern matches expected filenames.
- Extraction schema still names every label the contract declares.
- Missing file or missing version raises a specific `LabelContractError` — never a silent fallback.

### 7.5 LLM extraction transport

- Prompt embeds the schema (with field descriptions) and the document body.
- Explicit-schema override path works (a caller can supply a subset schema).
- Parse succeeds on valid JSON, fenced JSON (```json … ```), and JSON with permitted extra whitespace.
- Parse fails on extra fields and on invalid JSON.
- Happy-path against a fake client verifies model id, max_tokens, system prompt passthrough.
- `max_tokens` and `refusal` stop reasons skip parsing and raise.
- Validation failure propagates naming the offending field.

### 7.6 Render attribution

- **D2 confirm-and-clear:** zero removals + candidate feeds == dictionary-confirmed feeds → flag cleared.
- Dictionary-inconclusive → flag stays gated (regression guard).
- Partial overlap → flag partially cleared (regression guard).

### 7.7 Review-app backend

- Suffix format for new runs (backend-generated `<prefix>_<timestamp>`), collision uniquifier, subprocess env insulation.
- Run lifecycle (launch / status / completion).
- 409 on a second concurrent run.
- Failed stage marks the run failed and surfaces the reason.
- Missing API key blocks a live-provider run.
- Path containment: no traversal, no touching curated paths.
- `.docx` requirement on upload, size and type validation.
- Replay discovery works; traversal is rejected.
- Results payload shape: strip internal gate metadata not intended for clients.
- Workbook download endpoint returns 404 for unknown `doc_id` and does not fuzzy-match.
- Rule-text extraction from ambiguity context: word-boundary quoting, truncation.

### 7.8 End-to-end demo scenarios (integration-level)

- Live run against the demo FRD produces `PASS` or `PASS_WITH_FLAGS`, no strict-grounding failures, no hallucinated identifiers, and a golden-pair cell-eval near the demo baseline (~94%).
- Replay run (no API key, tracked fixtures) reproduces the same rendered workbook byte-for-byte.
- Mock provider run completes without an LLM and demonstrates plumbing only — the mock is not counted as a quality signal.

---

## 8. Anthropic / Claude-Specific Adaptation Notes

Everything in this section is a rework item for the port to Databricks Model Serving / AI Gateway. Nothing else in the pipeline is provider-specific.

1. **Model client.** The current `anthropic.Anthropic` client (streamed `messages` call with `model`, `max_tokens`, `system`, `messages` parameters) must be replaced by a **Databricks Model Serving** invocation — either the Foundation Model API for a Claude-family endpoint, an external-model endpoint via AI Gateway, or the `databricks-sdk` serving client. The request shape (system prompt + user prompt containing the schema and the FRD content) is preserved; only the transport changes.

2. **Model identifier.** The default `claude-opus-4-8` referenced in the extraction notebook and job resource file is an **Anthropic-side model id**. On the Databricks target it becomes a **Serving endpoint name** (or Foundation Model id, depending on route). Update every configuration touchpoint that today reads this value — extraction notebook, job resource YAML, demo runbook, and any documentation. Treat "endpoint name" as configuration, not code.

3. **Auth / secret scope.** Today the API key is loaded from a Databricks secret scope for workspace runs, and from an env var for local runs. On Foundation Model endpoints, **workspace identity handles auth** and the secret scope drops out entirely. If routing through AI Gateway to an external Anthropic key, the secret scope is retained but its consumer is the Gateway, not the pipeline code.

4. **SDK error taxonomy.** Today the code catches Anthropic-specific error classes on the extract path. Replace those catches with the equivalent Model Serving client error classes (or plain HTTP-status-based branching if calling REST directly). Preserve the semantic: transient (429/5xx) retries only, validation failures never retry.

5. **Retry policy.** The current `max_retries=2` runs inside the Anthropic SDK. When the SDK is removed, an equivalent transient-only retry wrapper must be re-implemented at the transport layer — do not accidentally introduce retries on parse / validation failures.

6. **Streaming.** Streaming is used today only to avoid HTTP timeouts at large `max_tokens` ceilings; the response is consumed only after completion. If the Model Serving endpoint supports non-streaming calls at this output size without timeouts, streaming can be dropped without behavior change. If not, use the endpoint's streaming variant.

7. **Structured outputs.** Today the code sends the JSON schema **as text inside the prompt** because Anthropic's server-side `messages.parse(output_format=...)` failed on this schema shape with a "grammar too large" error. When the transport changes, **re-probe server-side structured output on the target endpoint** with the current schema (which cannot be simplified — see §5.1). If it works, adopt it and simplify the prompt; if it fails, keep the schema-in-prompt pattern intact.

8. **Adaptive-thinking / extended-thinking.** The current implementation does **not** use `thinking`, `extended_thinking`, `betas`, message caching, computer use, or the agent SDK. If the Databricks target exposes an "adaptive thinking" or reasoning mode via AI Gateway, treat adopting it as a **measured experiment**, not a default. It must demonstrably improve grounding on live prose (§5.6) before it enters the production request shape.

9. **Provider gate values.** The `STTM_LLM_PROVIDER` env var currently accepts `"anthropic"` / `"mock"` / empty. Under the port, `"anthropic"` becomes `"databricks"` (or the appropriate endpoint tag). The unrecognized-value-raises semantic is preserved.

10. **Review-app subprocess env injection.** The review app's demo runner injects `ANTHROPIC_API_KEY` and `STTM_LLM_PROVIDER=anthropic` into subprocess environments. Under the port these become Databricks workspace credentials (if any) and the new provider tag. The env-insulation test that guards against touching curated paths must be updated but preserved in spirit.

11. **`pyproject.toml`.** Remove `anthropic>=0.60` from core dependencies; add the `databricks-sdk` (already present in the `[ui]` extra — promote to core for the port) and any Model Serving client packages.

12. **Documentation references.** The demo runbook, live E2E report, and defect catalogue all cite Anthropic-specific behaviors (D1 grammar-too-large, SSE-through-Databricks-Apps proxy, cost estimates per invocation). Retain them as historical context in an "archive" section of the docs, but the runbook that ships with the rebuild must reflect the Databricks-native cost model and observability surface (Model Serving usage tables, endpoint metrics) — not the Anthropic dashboard.

---

*End of specification.*
