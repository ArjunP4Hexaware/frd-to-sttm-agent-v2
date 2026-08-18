---
name: frd-to-sttm-agent
description: Load this whenever you are designing any agent elsewhere that has an LLM extract structured records (mappings, fields, entities) from a document, verifies them against the source text, and routes ambiguity to a human before a downstream consumer uses the result. It captures the exact division of labor (LLM proposes, deterministic code audits, human resolves), the three-status gating vocabulary, the "schema-in-prompt / no repair loop / truncation-is-failure" transport pattern, the confirm-and-clear-on-zero-removals bug, and the choreography needed when the same document can produce different gate shapes across runs. Read this before writing extraction prompts, grounding checks, ambiguity taxonomies, or HITL flows for a similar agent. ALSO load it when working in or asking about the frd-to-sttm-agent repo itself (FRD ingest, extraction schema, `03_contract_build` grounding audit or gating, `04_sttm_render`, the shared `contracts/frd_label_contract.json`, the review app, the Databricks-native rebuild in `docs/NATIVE_REBUILD_SPEC.md`, or the client demo runbook).
---

This skill has two parts. **Part A** is a map of the concrete FRD→STTM agent in this repo — enough that a new contributor can navigate the code, and enough that another engineer can decide whether the shape here matches their problem. **Part B** is the abstracted pattern, written so it can be applied to an unrelated "LLM extracts, code verifies, human resolves" agent.

**This file is fully self-contained.** Only this SKILL.md gets imported into the AmeriHealth Databricks workspace when this agent is built there via Genie Code — no other file from this repo (`docs/`, `README.md`, `CLAUDE.md`, `contracts/`) travels with it. Everything needed to rebuild the agent from scratch — target architecture, stage-by-stage design, the grounding/HITL contracts, hard-won lessons, and acceptance criteria — is inlined in Part A below. A companion deep-reference, `docs/NATIVE_REBUILD_SPEC.md`, still exists in this repo for local Claude Code sessions and is kept in sync with the content below; treat it as a local convenience copy, never as something Genie Code can reach.

---

## Part A — This agent, concretely

### Databricks Unit (DBU) budget — read before generating anything

Arjun and Soham share a **450-DBU/month** pool (recurring, not one-time)
across both engineers and all five agents in this program. Genie Code's
own build/iterate loop and the resulting pipeline's runtime compute both
draw against it, so both need to be efficient — not just the finished
architecture.

| Agent | Monthly DBU guardrail | Real Databricks footprint |
|---|---|---|
| **FRD → STTM (this agent)** | **~150** | **4 chained serverless notebooks + UC volumes — cost center** |
| CodeGen | ~120 | Generated Spark tests need a JVM cluster — **cost center** |
| SQL Optimization | ~130 | Warehouse EXPLAIN/DESCRIBE/telemetry queries — **cost center** |
| BRD → FRD | ~30 | Databricks App hosting only — light |
| Code Review | ~10 | Runs off Databricks entirely; audit-sink stub only — near-zero |
| *(10 DBU/month held as shared pod buffer)* | | |

This agent carries the **single largest allocation** in the program: its
steady-state design permanently requires four chained serverless
notebooks per real document, plus UC volume I/O and the review app's own
Databricks App hosting. These are planning guardrails, not automatic
limits — check the workspace usage/cost dashboard against this table
monthly; if a wave is trending over its guardrail before it's done, stop
and re-scope rather than keep spending.

**Minimizing Genie Code build cost (the biggest lever):**

The largest controllable cost is how many separate generation passes it
takes Genie Code to go from "empty folder + spec" to a working agent —
not the runtime footprint above. Attack it directly:

- **This SKILL.md is now the primary and only generation input — nothing
  else from this repo is reachable during the actual build.** The
  complete target architecture, stage-by-stage design, and acceptance
  criteria are inlined below, starting at "Product Context" through
  "Anthropic / Claude-Specific Adaptation Notes." Every decision those
  sections already make is a generation turn Genie Code doesn't have to
  spend exploring.
- **Request full-scope generation per stage in one pass** (e.g.,
  "generate the ingest, extract, contract-build, and render stages as
  LDP pipeline steps now"), not file-by-file back-and-forth.
- **Treat the target architecture below as fixed scope.** Don't ask
  Genie Code to propose alternatives to the four-stage LDP design —
  that exploration is billed iteration this file already resolved.
- **Review generated code yourself, outside Genie Code**, rather than
  prompting it to re-explain or re-justify what it wrote.
- **Batch fixes** into one follow-up prompt instead of correcting issues
  one at a time across many small turns.
- **Cap generation passes per agent** (e.g., 5–8) and stop to reassess if
  you hit it — that's a signal this file is underspecified somewhere,
  not a signal to keep prompting.

**General doctrine — applies everywhere in this repo:**

- **Serverless first.** Use serverless notebooks/jobs/SQL warehouses
  wherever the workspace offers them — they bill only for execution
  seconds and scale to zero between Genie Code turns. If a classic
  cluster is unavoidable, use the smallest single-node instance type and
  set auto-termination to 10–15 minutes; never leave the default.
- **Local/mock/replay first.** Iterate against this repo's existing
  offline paths (local dev, mock providers, replay fixtures, `--dry-run`)
  for as long as possible. Reserve real Databricks compute for a small
  number of deliberate validation checkpoints, not every change.
- **Capture every successful live run once.** This repo's record/replay
  seam exists exactly so a working path never needs to be re-run, and
  re-spent, to prove it still works.
- **No scheduled/cron jobs during the build phase.** Trigger runs
  manually, only when there's something new to validate.

**Specific to this agent:**

- Build and validate against **local mode + `STTM_MOCK_EXTRACTION=1`**
  almost exclusively — it makes zero LLM calls and touches no workspace
  resource, and covers the entire 41-test suite plus most of the
  four-notebook logic.
- Reserve real notebook runs for: one ingest pass against a real UC
  volume, one or two live (non-mock) `02_extract` runs to prove the real
  Anthropic/Model-Serving path, and one full bundle-job run per wave
  milestone — not per code change.
- The bundle job is deliberately **on-demand only, no cron schedule**
  (documents arrive irregularly, no review gate exists for a scheduled
  run) — this is already a DBU-saving property; don't add a schedule to
  "make the demo more automatic."
- All four notebook tasks already run on serverless compute with
  `max_retries: 0` by design — don't add cluster provisioning or
  retries; a failed deterministic stage should fail loud, not burn DBUs
  retrying.

### Product Context

The agent is the **second link in a five-agent AI-in-Engineering program**: BRD → FRD → **FRD → STTM** → CodeGen → Code Review. Its inputs and outputs are content contracts with the neighboring agents and must remain byte-stable across both sides:

- **Upstream** — reads a Functional Requirements Document (FRD) produced by the BRD-to-FRD agent, and shares a **label contract** JSON that names section headings, project-ID conventions, requirement-ID families, and placeholder strings. That file is committed byte-identically to both repos; any change bumps its `version` and must land in both repos in the same change set (see "The shared label contract" below).
- **Downstream** — must ultimately produce a **feed-level STTM mapping contract JSON** that a CodeGen agent can consume. Today the agent renders a client-dialect **STTM workbook** (.xlsx) and emits a per-run contract JSON with human-review resolutions folded in; the workbook-→CodeGen-contract adapter is the **biggest known program-wide gap** and is out of scope for the current agent.

Design philosophy carried over from the existing implementation:

> The LLM reads prose and scattered requirement tables; deterministic code owns validation, regex-able facts, grounding checks, attribution, and rendering. Every extracted string is audited against the source document; genuine ambiguities are gated for human review, never guessed.

The rebuild must preserve that division of labor. The LLM should never be the arbiter of "did we get this right"; audits, gates, and dictionary cross-checks are.

**Current implementation, for orientation.** Today this runs as four Databricks notebooks; it also runs locally end-to-end via env-var fallbacks and a `deltalake`-backed warehouse under `local_dev_fixtures/`. The target architecture on Genie Code (below) reshapes the runtime but preserves the pipeline shape, vocabularies, audit design, and HITL model unchanged.

### The shared label contract

`contracts/frd_label_contract.json` (v1.0.0). Names the section headings the upstream BRD→FRD renderer emits ("In Scope", "Assumptions, Constraints & Dependencies", …), the Project-ID line pattern, the requirement-ID families (`BR|REQ|FR|SRQ|SIR|NFR|MDST`), and the `TBD — pending client input…` placeholder that routes to `open_items`. Loaded via `frdsttm.label_contract`; **fails loudly** if missing or unversioned — no hardcoded fallback.

The **same file** is committed byte-identically to the `brd-to-frd-agent` repo. Any change bumps `version` and must land as identical copies on both sides in the same change set. Do not edit one repo alone.

### End-to-End Pipeline

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

#### Stage 1 — FRD Ingest

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

#### Stage 2 — LLM Extraction

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

Every string is expected to appear **verbatim** in the source FRD or (for prose fields) to substantially reuse its tokens. See "Grounding Audit — Design Contract" below for the audit contract that enforces this.

**Behavior worth preserving.**

- **Request shape (design constraints spelled out in "Design Constraints a Rebuild Must Respect" below):** system prompt frames the extraction task tersely and forbids invention of identifiers, table names, schedules, or rules; user prompt is `instruction + JSON-schema-of-the-output-container + full FRD markdown`. The response is one JSON object. No tool use, no filesystem, no multi-turn reasoning, no subagents.
- **Client-side validation is authoritative.** The JSON returned is parsed and validated against the container model with `extra="forbid"` semantics — unknown keys are an error, not a warning. There is **deliberately no re-ask / repair loop**: a validation failure raises immediately, naming the offending field. Silently retrying would blur the model-quality signal the first failure carries.
- **Truncation is failure.** A `max_tokens` stop reason is treated as truncated JSON and raises; a refusal is likewise a hard failure.
- **Provider gate.** Two provider paths exist: a **mock** path (fixture-based, local-only, zero cost) and a **live** path. The gate raises on unrecognized values rather than silently degrading. A mock+live cross-configuration is an error.

**Conceptual output artifact.** One JSON file per document (`extractions/<doc_id>.json`) written to a UC Volume, keyed to the `doc_id` from Stage 1.

**On Databricks Model Serving.** This is the module that changes most in the port; see "Target Architecture on Databricks Genie Code" and "Anthropic / Claude-Specific Adaptation Notes" below.

#### Stage 3 — Contract Build (Validate, Enrich, Audit, Gate)

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

#### Stage 4 — Render + Eval (STTM Workbook)

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

### Grounding Audit — Design Contract

Because the entire quality claim of the agent rests on this audit, it gets its own section.

#### Rule categories

- **Strict fields** (identifiers, paths, patterns, table names, IDs): must appear **verbatim** as a substring of the (unicode-normalized) FRD content. Any failure elevates the whole contract to `FAIL`.
- **Advisory fields** (prose rules, retention notes, PHI notes, scope bullets): must overlap the FRD prose at ≥ 0.75 token-overlap (tokens ≥ 4 chars, normalized). Failure elevates the contract to `PASS_WITH_FLAGS` and captures **structural write-back context** (feed index, field path, original value) so a reviewer can edit the offending prose in-place.

#### Design constraint — calibrate against real model prose, not synthetic prose

Grounding thresholds and expectations should be measured against **outputs the current model actually produces on the current FRD**, not against hand-crafted synthetic mocks. Mock extractions in this repo copy real facts back from the FRD and therefore always ground; they prove plumbing works, not that the model is accurate. Historical grounding numbers from earlier schema/pipeline versions are not comparable and should not be treated as regression baselines. Any rebuild must re-baseline grounding on a live end-to-end run against the current schema before setting thresholds or writing test assertions.

#### Design constraint — never relax the audit to make a run pass

If a run flags advisory grounding, the fix is either (a) a better extraction, (b) a better source document, or (c) a reviewer's `free_text` correction. It is **not** to lower the threshold, exclude the field, or accept invented prose.

### HITL (Human-in-the-Loop) Review Model

#### What the reviewer sees

Per ambiguity:

- **Identity**: stable id, kind, and (for attribution) the extracted rule text.
- **Choices**: the candidate list (attribution / disagreement) *or* an editable free-text field (advisory grounding).
- **Prior state**: any existing resolution, so the reviewer can see who resolved it, when, and what they picked.
- **Provenance**: the feed(s) the ambiguity touches, and (for advisory grounding) the field path being edited.

#### What the reviewer can do

- **Pick a candidate** (attribution and disagreement only).
- **Reject all candidates** with `none_of_these`, which leaves the ambiguity gated so downstream automatic behavior (dictionary cross-check for attribution) can still take effect.
- **Edit prose** with `free_text` (advisory grounding only), supplying a rationale.

#### State transitions

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

#### Non-negotiables

- **Structural-pick-required policy** enforced on both client and server. The server must reject a malformed submission (e.g. `free_text` on a candidate-having ambiguity) with 4xx, not silently accept-and-drop.
- **Strict `doc_id` matching** on all detail/workbook endpoints — no fuzzy fallback to a similarly-named document.
- **Composite key `<doc_id>::<ambiguity_id>`** in any client-side state so state does not leak between documents open in the same UI session.
- **Empty vs unreadable are different.** A successful listing with no rows returns 200 + `[]`; an unreadable source returns 5xx. The client uses this to auto-switch tabs on first load without hiding real errors.

### Design Constraints a Rebuild Must Respect (Hard-Won Lessons)

These are the calibration decisions the current implementation paid for. A rebuild that ignores them will re-hit the same failure modes.

#### Structured-output "grammar too large" — the D1 constraint

Provider-native structured output (schema-constrained decoding) failed deterministically on this schema shape: many optional properties, deeply nested nullable unions, and `additionalProperties=false` everywhere. Server-side grammar compilers rejected it as too complex. The fix — and the constraint the rebuild must satisfy — is:

> **Send the JSON schema as text in the prompt; validate on the client side with `extra="forbid"` semantics.**

The rebuild target (Databricks Model Serving / AI Gateway with a Claude backing model, or a Foundation Model endpoint) may or may not have the same limit. Re-probe by attempting server-side structured output first; if it fails on the same shape, fall back to schema-in-prompt without hesitation. Do **not** simplify the schema shape to satisfy a decoder — the schema is the extraction contract with downstream consumers.

#### Request shape — schema-in-prompt + streaming, no repair loop

The current pattern is: **plain messages call, one shot, streaming for large output ceilings, client-side Pydantic-style validation, and no retry on validation errors**. This is deliberate:

- **Streaming** avoids HTTP timeouts at large output-token ceilings; the request is otherwise identical to a non-streaming call.
- **No repair loop**: a schema-invalid response is a model-output-shape signal that must be visible. Silent re-asking would mask the quality regression on the very first failure.
- **`max_tokens` truncation is failure, not partial success.** Do not attempt to parse truncated JSON.
- **No `thinking` / extended-thinking / betas / tool-use / subagents** are used today. The design commitment is "one prompt in, one JSON object out." Adaptive-thinking modes may be worth re-probing on the new platform, but must be introduced only if they measurably improve grounding on live prose — not because they are available.

#### Output-token ceiling sizing

Measured budget on a demo FRD is ~700–800 output tokens per feed. A safe ceiling gives ~75–90 feeds of headroom (currently 64 000). Set the ceiling high enough that a single-run FRD cannot truncate under normal conditions. Truncation is fail-loud, so the cost of an over-generous ceiling is zero.

#### Ambiguity id stability

The `ambiguity_id` scheme is the join key for saved reviewer decisions. It has already been migrated once; migrating again requires updating every persisted decision in every review-app data store. Freeze the algorithm (`kind + normalized-text + context → sha1 → first 12 chars`) unless there is a compelling reason to break resolution history.

#### Confirm-and-clear on zero removals (the "D2" fix)

A **better** extraction can produce **fewer** dictionary removals — which historically left the attribution ambiguity gated forever. The rule: when the ambiguity's candidate feeds equal the dictionary-confirmed feed set (even with zero removals from the LLM's picks), clear the flag. Skipping this check causes the demo pathological outcome "correct extraction → worse final status."

#### Grounding calibrated against real prose

See "Grounding Audit — Design Contract" above. Do not use mock extractions to set grounding thresholds; mocks are plumbing tests. Baseline against a live run on a real FRD.

#### Mock mode is plumbing proof, not a benchmark

The mock provider path exists to run the pipeline end-to-end without an LLM. It must remain local-only (guarded off in the workspace runtime) and must never be used to declare grounding quality. Configuration ambiguity (mock + live simultaneously) is a hard failure, not a preference.

#### Determinism, or the lack of it, is a demo concern

Three live runs on the same FRD produced three different gate shapes at identical extraction quality and identical eval scores. A rebuild's demo choreography must not depend on a specific gate count; the replay-fixture escape hatch is the reliable path for demos.

#### Fail loud on ambiguous config

Two providers configured at once, an unversioned label contract, a missing secret — all raise on startup rather than proceeding with an implicit default. The rebuild must adopt the same discipline; silent fallbacks in a compliance-sensitive pipeline are worse than a red startup.

### Target Architecture on Databricks Genie Code

#### Runtime shape

- **Ingest**, **Extract**, **Contract Build**, **Render** as four LDP steps (materialized views / streaming tables) inside a single **Lakeflow Declarative Pipeline** owned by the FRD-STTM project.
- **Unity Catalog** owns the three Delta tables (`frd_documents`, `frd_contracts`, `frd_sttm_runs`) and the artifact Volumes (`frd_raw`, `sttm_out`, `sttm_reference`).
- **Databricks Model Serving** (Foundation Model API or external-model endpoint via AI Gateway) hosts the extraction call. The current Anthropic SDK client is replaced by a Model Serving HTTP call (or the `databricks-sdk` Serving client); the request/response shape stays the same in spirit (one prompt in, one JSON object out).
- **Review app** stays as a Databricks App (FastAPI backend + React frontend). The existing `STTM_APP_MODE=databricks` code path is designed for exactly this deployment; the rebuild inherits it.
- **Secrets** for external-model endpoints (only if going via AI Gateway to an external provider) live in a Databricks secret scope; the Foundation Model path removes the secret dependency entirely because workspace identity handles auth.
- **Genie Code** is the authoring surface — pipeline steps are Genie Code notebooks or Python files under the LDP.

#### Table & volume topology

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

#### Config surface (parameterize the pipeline; do not hardcode)

| Setting             | Purpose |
|---------------------|---------|
| `catalog`, `schema` | UC target |
| `raw_volume`, `out_volume`, `reference_volume` | UC Volumes |
| `docs_table`, `contracts_table`, `runs_table`  | Delta table names |
| `model`             | Model Serving endpoint name or Foundation Model id |
| `max_tokens`        | Output ceiling (default ~64k; see "Output-token ceiling sizing" above) |
| `max_retries`       | Transient-error retries only (429 / 5xx); never on validation errors |
| `llm_provider`      | `mock` | `live`; unrecognized value raises |
| `mock_extraction`   | Local-only escape hatch; hard-fails if enabled in workspace runtime |
| `app_mode`          | `local` | `databricks` (review-app data access) |

Genie Code / LDP pipeline **parameters** map cleanly to these names.

### Acceptance Criteria — What "Done" Looks Like

The current test suite is the strongest available specification of "correct behavior." A rebuild is not done until an equivalent suite is green. Grouped by theme:

#### Grounding & enrichment

- Unicode / markdown normalization round-trips: NFKC, smart-quote collapse, code-fence stripping.
- Token-length filtering: single-character or short tokens do not distort advisory overlap.
- Strict-substring detection: a value present verbatim in the FRD passes; a hallucinated identifier fails.
- Advisory overlap at exactly the 0.75 threshold: on-boundary values pass; below-threshold values fail.
- Empty values skip the audit (nothing to ground).
- Grounding audit runs both strict and advisory paths in one call and returns structural write-back context for advisory flags.
- Enrichment: project-id fill when agent said null; disagreement gated when agent value differs; LOB fill only for empty lobs; existing non-empty lobs never clobbered.

#### Gating vocabulary

- Clean spec → status `PASS`, empty gate list.
- Schema-invalid spec → status `FAIL`, **no contract emitted**.
- Strict-ungrounded spec → status `FAIL`.
- Attribution / advisory-grounding conditions → status `PASS_WITH_FLAGS`.
- Every emitted ambiguity validates against the `GatedAmbiguity` schema; unknown `kind` values are rejected at model level.
- Provenance banner (`_provenance.grounding` counts) matches the actual audit results.
- Human-readable run report includes each ambiguity's text and kind.

#### Model contract

- Round-trip serialization of every canonical spec fixture.
- `extra="forbid"` at both root and every nested container.
- `schema` alias round-trips (the field is a reserved word in Pydantic and requires aliasing).
- `GatedAmbiguity` accepts exactly the three `kind` values; an "other" is rejected.
- `HumanResolution` accepts exactly the three `resolution_type` values; unknown types are rejected.
- Empty defaults (`feed_name is None`, empty lists) are legal — "not stated" is a first-class state.

#### Label contract

- File is present, parses, and carries a version.
- Every section / requirement-family / project-id sub-key is present.
- Project-id regex matches normalized text.
- Digits pattern matches expected filenames.
- Extraction schema still names every label the contract declares.
- Missing file or missing version raises a specific `LabelContractError` — never a silent fallback.

#### LLM extraction transport

- Prompt embeds the schema (with field descriptions) and the document body.
- Explicit-schema override path works (a caller can supply a subset schema).
- Parse succeeds on valid JSON, fenced JSON (```json … ```), and JSON with permitted extra whitespace.
- Parse fails on extra fields and on invalid JSON.
- Happy-path against a fake client verifies model id, max_tokens, system prompt passthrough.
- `max_tokens` and `refusal` stop reasons skip parsing and raise.
- Validation failure propagates naming the offending field.

#### Render attribution

- **D2 confirm-and-clear:** zero removals + candidate feeds == dictionary-confirmed feeds → flag cleared.
- Dictionary-inconclusive → flag stays gated (regression guard).
- Partial overlap → flag partially cleared (regression guard).

#### Review-app backend

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

#### End-to-end demo scenarios (integration-level)

- Live run against the demo FRD produces `PASS` or `PASS_WITH_FLAGS`, no strict-grounding failures, no hallucinated identifiers, and a golden-pair cell-eval near the demo baseline (~94%).
- Replay run (no API key, tracked fixtures) reproduces the same rendered workbook byte-for-byte.
- Mock provider run completes without an LLM and demonstrates plumbing only — the mock is not counted as a quality signal.

### Anthropic / Claude-Specific Adaptation Notes

Everything in this section is a rework item for the port to Databricks Model Serving / AI Gateway. Nothing else in the pipeline is provider-specific.

1. **Model client.** The current `anthropic.Anthropic` client (streamed `messages` call with `model`, `max_tokens`, `system`, `messages` parameters) must be replaced by a **Databricks Model Serving** invocation — either the Foundation Model API for a Claude-family endpoint, an external-model endpoint via AI Gateway, or the `databricks-sdk` serving client. The request shape (system prompt + user prompt containing the schema and the FRD content) is preserved; only the transport changes.

2. **Model identifier.** The default `claude-opus-4-8` referenced in the extraction notebook and job resource file is an **Anthropic-side model id**. On the Databricks target it becomes a **Serving endpoint name** (or Foundation Model id, depending on route). Update every configuration touchpoint that today reads this value — extraction notebook, job resource YAML, demo runbook, and any documentation. Treat "endpoint name" as configuration, not code.

3. **Auth / secret scope.** Today the API key is loaded from a Databricks secret scope for workspace runs, and from an env var for local runs. On Foundation Model endpoints, **workspace identity handles auth** and the secret scope drops out entirely. If routing through AI Gateway to an external Anthropic key, the secret scope is retained, but its consumer is the Gateway, not the pipeline code.

4. **SDK error taxonomy.** Today the code catches Anthropic-specific error classes on the extract path. Replace those catches with the equivalent Model Serving client error classes (or plain HTTP-status-based branching if calling REST directly). Preserve the semantic: transient (429/5xx) retries only, validation failures never retry.

5. **Retry policy.** The current `max_retries=2` runs inside the Anthropic SDK. When the SDK is removed, an equivalent transient-only retry wrapper must be re-implemented at the transport layer — do not accidentally introduce retries on parse / validation failures.

6. **Streaming.** Streaming is used today only to avoid HTTP timeouts at large `max_tokens` ceilings; the response is consumed only after completion. If the Model Serving endpoint supports non-streaming calls at this output size without timeouts, streaming can be dropped without behavior change. If not, use the endpoint's streaming variant.

7. **Structured outputs.** Today the code sends the JSON schema **as text inside the prompt** because Anthropic's server-side `messages.parse(output_format=...)` failed on this schema shape with a "grammar too large" error. When the transport changes, **re-probe server-side structured output on the target endpoint** with the current schema (which cannot be simplified — see "Structured-output 'grammar too large' — the D1 constraint" above). If it works, adopt it and simplify the prompt; if it fails, keep the schema-in-prompt pattern intact.

8. **Adaptive-thinking / extended-thinking.** The current implementation does **not** use `thinking`, `extended_thinking`, `betas`, message caching, computer use, or the agent SDK. If the Databricks target exposes an "adaptive thinking" or reasoning mode via AI Gateway, treat adopting it as a **measured experiment**, not a default. It must demonstrably improve grounding on live prose (see "Grounding calibrated against real prose" above) before it enters the production request shape.

9. **Provider gate values.** The `STTM_LLM_PROVIDER` env var currently accepts `"anthropic"` / `"mock"` / empty. Under the port, `"anthropic"` becomes `"databricks"` (or the appropriate endpoint tag). The unrecognized-value-raises semantic is preserved.

10. **Review-app subprocess env injection.** The review app's demo runner injects `ANTHROPIC_API_KEY` and `STTM_LLM_PROVIDER=anthropic` into subprocess environments. Under the port these become Databricks workspace credentials (if any) and the new provider tag. The env-insulation test that guards against touching curated paths must be updated but preserved in spirit.

11. **`pyproject.toml`.** Remove `anthropic>=0.60` from core dependencies; add the `databricks-sdk` (already present in the `[ui]` extra — promote to core for the port) and any Model Serving client packages.

12. **Documentation references.** The demo runbook, live E2E report, and defect catalogue all cite Anthropic-specific behaviors (D1 grammar-too-large, SSE-through-Databricks-Apps proxy, cost estimates per invocation). Retain them as historical context in an "archive" section of the docs, but the runbook that ships with the rebuild must reflect the Databricks-native cost model and observability surface (Model Serving usage tables, endpoint metrics) — not the Anthropic dashboard.

### Where to read next

**Local repo / Claude Code development only — none of these are reachable when only this SKILL.md is imported into the AmeriHealth Databricks workspace.** Everything needed for the actual build is inlined above; these are for engineers working in this checkout:

- `README.md` — pipeline overview, run instructions (Databricks bundle + local), review-app setup, branching model. Start here if you have never run the agent.
- `CLAUDE.md` — the working-notes file loaded into every session in this repo. Config doctrine, the widget-name mismatch between notebooks, provider-seam rules, fixture rules, known gaps. Read before making code changes.
- `docs/DEMO_RUNBOOK.md` — how to run the client-facing demo in `review_app_react/` (choreography, non-determinism framing, contingency to replay, hard rules on real client documents).
- `docs/NATIVE_REBUILD_SPEC.md` — the source this SKILL.md's Part A was merged from; kept as a standalone local copy, same content as above.
- `docs/LIVE_E2E_2026-08-07.md` — the D2 incident and the numbers the demo runbook cites.
- `contracts/frd_label_contract.json` — the shared upstream contract; treat as read-only unless you are coordinating a bump with the BRD→FRD repo.

---

## Part B — The reusable pattern

Everything below is the same design abstracted away from FRD/STTM and AmeriHealth Caritas. It is written so an engineer building an unrelated agent — extracting field-level clauses from insurance policies, extracting API contracts from RFCs, extracting billing codes from clinical notes, whatever — can apply the pattern directly.

**Applicability check.** This pattern fits when *all* of these hold:

- The **source is a document** (or a small set of documents), not a stream or a database.
- The output is a **structured record** with a designed schema — a mapping, a spec, a set of typed fields — not free-form text.
- The document contains prose *and* scattered facts (tables, IDs, patterns) that a regex or a lookup can verify.
- Getting a field **wrong silently** is worse than **blocking on a question** — because a downstream consumer (another agent, a code generator, a compliance workflow, an ETL job) will act on it.
- There exists **some form of ground truth** you can cross-check against: the source document itself for verbatim substrings, a dictionary / reference table for entity existence, a regex for identifiers.

If the fit is weak on any of those, the pattern will over-engineer the problem. If all five hold, the pattern is worth stealing wholesale.

### The five design commitments

Every item in this section is essential — skip any and the pattern breaks.

**1. Division of labor: LLM proposes, code verifies, human resolves. [essential]**

The LLM only reads prose and scattered tables and returns a structured record. Every downstream check is deterministic Python. The LLM is never the arbiter of "did we get this right" — audits and cross-checks are. Everything you would normally ask the model to "double-check" becomes a code path with a test.

**2. Grounding audit as the quality gate — two categories, one call. [essential]**

Every extracted string is audited against the source text before the record is trusted. Split fields into two rule categories:

- **Strict fields** — identifiers, paths, table names, well-formed patterns. Must appear **verbatim** in the (unicode-normalized) source. Any failure → the whole record is `FAIL`, nothing downstream sees it.
- **Advisory fields** — prose rules, notes, descriptions. Must overlap the source at a fixed token-overlap threshold (start at 0.75 with tokens ≥ 4 chars; **calibrate against live model output on real documents**, never against synthetic mocks). Any flag → status `PASS_WITH_FLAGS`, with structural write-back context (which record, which field, original value) so a reviewer can edit in place.

Run both paths in one function call; return counts (`strict_checked`, `strict_failed`, `advisory_checked`, `advisory_flagged`) alongside the flags. Emit them as a provenance banner on the record. Never relax a threshold to make a run pass — the fix is a better extraction, a better document, or a reviewer edit.

**3. Three statuses, three ambiguity kinds, three resolution types. Enforce the vocabularies at the schema level. [essential]**

Statuses (strict priority):

| Status              | Triggered by |
|---------------------|--------------|
| `FAIL`              | Structural validation failure or any strict-grounding failure. No record emitted downstream. |
| `PASS_WITH_FLAGS`   | Any ambiguity or advisory-grounding flag. Record emitted; review UI receives gated items. |
| `PASS`              | Clean. Record emitted, empty gate list. |

Ambiguity kinds — pick a small closed set that covers your problem, and prohibit an "other" escape. In the FRD case: `attribution` (same value applies to multiple entities), `disagreement` (regex-derived vs model-derived differ on a strict field), `advisory_grounding` (prose failed advisory audit). Yours may be different — but the *closed-set* discipline is not.

Resolution types — pick the resolution **structurally** from `has_candidates`, not from `kind`. Three suffice: `candidate_pick`, `none_of_these`, `free_text`. Reject the malformed combinations at both client and server (a `free_text` on a candidate-having ambiguity is a 4xx). This is what stops the review UI from silently accepting resolutions that render nothing downstream.

**4. Deterministic seams the LLM should not own. [essential]**

Any fact a regex, a lookup, or a dictionary cross-check can compute more reliably than the LLM should be owned by code, not the model — but the model still gets to see and disagree:

- If a strict identifier can be regex-lifted from the source, do it. If the model returned `null`, fill from the regex. If the model returned a different value, **keep the model's value and gate a `disagreement`** carrying both candidates. Do not silently overwrite.
- If a per-entity attribute can be derived from a reference dictionary (an authoritative list of columns/entities the record must reference), the derivation is authoritative when it can prove itself, and gated when it cannot.
- Reserve enrichment for the seams. Never clobber a non-empty model-supplied field with a heuristic.

The **precedence rule** at the resolution step is: human resolution first (authoritative, never re-decided), then the deterministic cross-check on whatever remains. Every non-application of a human resolution produces an audit entry (`applied` / `not_applied` with reason / `stale` if the candidate is no longer valid).

**5. The confirm-and-clear-on-zero-removals rule (the D2 fix). [essential]**

If your dictionary cross-check "resolves" an ambiguity by *removing* misattributed items, a **better** extraction produces **fewer removals** — and historically leaves the flag gated forever. The rule: when the ambiguity's candidate set equals the dictionary-confirmed set, clear the flag even at zero removals. Skip this and you get "correct extraction → worse final status" the first time the model gets sharper. Regression-test the three shapes: clear at zero removals when confirmed, stay gated when inconclusive, partially clear on partial overlap.

### The extraction transport — request shape **[essential]**

- **One prompt in, one JSON object out.** System prompt frames the task tersely and forbids invention of identifiers, table names, schedules, or rules. User prompt = `instruction + JSON-schema-of-the-output + full source content`. No tool use, no multi-turn, no subagents. Extended-thinking / reasoning modes may be worth trying, but must be introduced only if they measurably improve grounding on live prose — not because they are available.
- **Client-side validation is authoritative, with `extra="forbid"` semantics everywhere (root and every nested container).** Unknown keys are an error, not a warning.
- **No repair loop.** A schema-invalid response raises immediately, naming the offending field. Silently re-asking would mask the exact model-quality regression you need to see.
- **Truncation is failure.** A `max_tokens` stop reason (or a refusal) is a hard error. Do not attempt to parse truncated JSON. Set the output-token ceiling generously.
- **Streaming only to avoid HTTP timeouts** at large output-token ceilings; the response is consumed after completion. If your platform doesn't need streaming at your ceiling, drop it.
- **Provider gate raises on unrecognized values.** Providing two providers at once (e.g. `mock` + `live`) is a startup error, not a preference.

### If server-side structured output ("grammar too large") fails **[essential]**

Provider-native structured output can fail deterministically on schemas with many optional properties, deeply nested nullable unions, and `additionalProperties=false` throughout. That was our D1 constraint on Anthropic's `messages.parse`. Two rules:

1. Try it first. If it works on your schema, use it and drop the schema-in-prompt scaffolding.
2. If it fails, **fall back to schema-in-prompt without hesitation. Do not simplify the schema shape to satisfy a decoder** — the schema is your contract with downstream consumers.

### Mock mode discipline **[essential]**

Build a mock provider path — a hand-authored fixture record per source document, keyed by the same `doc_id` the live path would use. Use it to exercise everything downstream of extraction (audits, gating, review UI, rendering) without an LLM. Two rules:

1. **Local-only.** Guarded off in the workspace runtime; a real production run can never silently skip the model call.
2. **Not a benchmark.** Mocks that copy facts back from the source always ground. They prove plumbing; they say nothing about extraction quality. Baseline any grounding threshold or eval target on a live run against real documents.

### Non-determinism is a demo-choreography concern **[essential]**

Same document, same model, same threshold → different gate shapes across runs, at identical extraction quality and identical downstream eval scores. This is inherent to the pattern (LLM sampling meets a strict audit), not a bug. Consequences for anyone shipping this to a client:

- **Do not choreograph a demo on a specific gate count.** Any live run must render its actual state honestly.
- **Ship replay fixtures.** A tracked run set that reproduces the flag-then-confirm narrative in one click, offline, with no API key. Zero-cost fallback if the live run's shape doesn't cooperate on demo day.
- **A zero-gate run is not a boring run.** Reframe: "the agent flags what it isn't sure of; the dictionary auto-confirms what the data proves; anything else waits for a human. Zero means nothing needed a human this time — which is itself the point."

### Review-app non-negotiables **[essential]**

- Composite key `<doc_id>::<ambiguity_id>` in client-side state so nothing leaks between documents open in the same session.
- Strict `doc_id` matching on all detail/workbook endpoints. **No fuzzy fallback to a similarly-named document** — this is the class of bug that ships wrong data to production.
- Empty vs unreadable are different at the API layer: a successful listing with no rows is `200 + []`; an unreadable source is `5xx`. The client uses this to auto-switch tabs on first load without hiding real errors.
- The `ambiguity_id` scheme (a stable hash of `kind + normalized-text + context`) is the join key for saved reviewer decisions. Migrating the algorithm requires migrating every persisted decision — freeze it early.

### Acceptance criteria — treat the test suite as your spec **[essential]**

The strongest specification of "correct behavior" for this pattern is the test suite. This repo's own "Acceptance Criteria" section above groups the tests by theme (grounding & enrichment, gating vocabulary, model contract, label contract, LLM extraction transport, render/attribution, review-app backend, end-to-end demo scenarios). A rebuild is not done until an equivalent suite is green.

### Porting to a new platform — this pattern is being re-targeted right now *(incidental — swap freely)*

This exact pattern is currently being ported from Python-notebooks + Anthropic-SDK + FastAPI-review-app to **Databricks-native** (Genie Code + Lakeflow Declarative Pipelines + Unity Catalog + Model Serving). This repo's own "Target Architecture on Databricks Genie Code" section above is the target-architecture description for that port; "Anthropic / Claude-Specific Adaptation Notes" is the list of Anthropic-specific rework items (client, model id, secret scope, error taxonomy, retry policy, streaming, structured-outputs re-probe on the new endpoint, thinking modes, provider-gate values, subprocess env injection, `pyproject.toml`, docs). It is a working example of what "port this pattern to a new platform" actually costs — go read it before you assume swapping providers is a one-liner. What survives the port is the pipeline shape, the vocabularies, the audit design, and the HITL model. What changes is transport and identity.
