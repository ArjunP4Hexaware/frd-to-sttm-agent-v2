---
name: frd-to-sttm-agent
description: Load this whenever you are designing any agent elsewhere that has an LLM extract structured records (mappings, fields, entities) from a document, verifies them against the source text, and routes ambiguity to a human before a downstream consumer uses the result. It captures the exact division of labor (LLM proposes, deterministic code audits, human resolves), the three-status gating vocabulary, the "schema-in-prompt / no repair loop / truncation-is-failure" transport pattern, the confirm-and-clear-on-zero-removals bug, and the choreography needed when the same document can produce different gate shapes across runs. Read this before writing extraction prompts, grounding checks, ambiguity taxonomies, or HITL flows for a similar agent. ALSO load it when working in or asking about the frd-to-sttm-agent repo itself (FRD ingest, extraction schema, `03_contract_build` grounding audit or gating, `04_sttm_render`, the shared `contracts/frd_label_contract.json`, the review app, the Databricks-native rebuild in `docs/NATIVE_REBUILD_SPEC.md`, or the client demo runbook).
---

This skill has two parts. **Part A** is a map of the concrete FRD→STTM agent in this repo — enough that a new contributor can navigate the code, and enough that another engineer can decide whether the shape here matches their problem. **Part B** is the abstracted pattern, written so it can be applied to an unrelated "LLM extracts, code verifies, human resolves" agent.

The authoritative long-form reference is `docs/NATIVE_REBUILD_SPEC.md` in this repo. It is deliberately written as a from-scratch architecture spec (not a code walkthrough) and everything below points to it for depth rather than restating it.

---

## Part A — This agent, concretely

### What it is

Consumes an approved Functional Requirements Document (.docx) and produces a Source-to-Target Mapping (STTM) workbook plus a machine-readable feed-level mapping contract JSON. Second agent in a five-agent program (BRD→FRD → **FRD→STTM** → CodeGen → Code Review; SQL Optimization is standalone). Runs as four Databricks notebooks; also runs locally end-to-end via env-var fallbacks and a `deltalake`-backed warehouse under `local_dev_fixtures/`.

### The four deterministic stages

```
FRD .docx  →  01_frd_ingest  →  02_extract  →  03_contract_build  →  04_sttm_render
              docx → md          Anthropic     validate + enrich    dictionary check
              Delta row          structured    + grounding audit    + derive mappings
                                 outputs       + ambiguity gating   + render .xlsx
                                                                     + eval vs golden
```

Each stage's output is durable (Delta table + UC Volume artifact) so any later stage can be re-run without re-executing predecessors. The LLM appears in exactly one stage (`02_extract`); everything else — validation, regex-derivable enrichments, grounding checks, attribution resolution, mapping derivation, rendering, eval — is deterministic Python.

Stage-by-stage detail (input row shape, sanity gates, output artifact paths, dialect handling for the workbook) lives in `docs/NATIVE_REBUILD_SPEC.md` §2.

### The grounding audit — the quality gate

Every extracted string is checked against the FRD content the extractor saw. Two rule categories:

- **Strict fields** (identifiers, paths, file patterns, table names, requirement IDs, `stage_target` / `standard_target` catalog/schema/tables): must appear **verbatim** as a substring of the unicode-normalized FRD. Any failure → contract status `FAIL`, no contract emitted.
- **Advisory fields** (prose rules, retention notes, PHI/PII notes, scope bullets, ACD descriptions): must overlap the FRD prose at ≥ 0.75 token-overlap (tokens ≥ 4 chars, normalized). Any flag → contract status `PASS_WITH_FLAGS`, with structural write-back context (feed index, field path, original value) recorded so a reviewer can edit the offending prose in place.

The audit is the entire quality claim of the agent. §3 of the rebuild spec lists the two rules that must never be broken: (a) calibrate thresholds against real model prose on real FRDs, not against mock extractions that copy facts back and always ground; (b) if a run flags, the fix is a better extraction / better document / reviewer's `free_text` — never a lower threshold or an excluded field.

### The HITL model (three kinds × three resolutions)

Ambiguities are gated, never guessed. The taxonomy is a **designed constraint**, enforced at the schema level (an "other" kind is prohibited):

| `kind`                | Meaning                                                              | Has candidates? |
|-----------------------|----------------------------------------------------------------------|-----------------|
| `attribution`         | Same rule text on ≥ 2 feeds; a human picks which feeds it applies to | Yes (feed names) |
| `disagreement`        | Regex-derived value differs from model-extracted value (today: `project_id`) | Yes (both values) |
| `advisory_grounding`  | Prose field passed advisory weakly or was invented                   | No               |

Resolutions are picked **structurally** by whether the ambiguity has candidates, not by kind name:

| `resolution_type` | Applies when             | Stores                                          |
|-------------------|--------------------------|-------------------------------------------------|
| `candidate_pick`  | `has_candidates=true`    | `chosen_candidate` (must be in candidates list) |
| `none_of_these`   | `has_candidates=true`    | Explicit rejection; falls back to auto behavior |
| `free_text`       | `has_candidates=false`   | `rationale` (advisory-grounding edits)          |

A `free_text` on a candidate-having ambiguity is malformed and must be rejected by both client and server (4xx, not silent accept-and-drop). See rebuild spec §4 for the full state-transition diagram and non-negotiables (composite `<doc_id>::<ambiguity_id>` keys, strict `doc_id` matching, 200+`[]` vs 5xx distinction).

The ambiguity id is a stable 12-char hash of `kind + normalized-text + context`; it is the join key for saved reviewer decisions across re-runs. Freeze the algorithm — the migration cost is every persisted decision in every review-app data store.

### The shared label contract

`contracts/frd_label_contract.json` (v1.0.0). Names the section headings the upstream BRD→FRD renderer emits ("In Scope", "Assumptions, Constraints & Dependencies", …), the Project-ID line pattern, the requirement-ID families (`BR|REQ|FR|SRQ|SIR|NFR|MDST`), and the `TBD — pending client input…` placeholder that routes to `open_items`. Loaded via `frdsttm.label_contract`; **fails loudly** if missing or unversioned — no hardcoded fallback.

The **same file** is committed byte-identically to the `brd-to-frd-agent` repo. Any change bumps `version` and must land as identical copies on both sides in the same change set. Do not edit one repo alone.

### Top hard-won lessons

Full list is `docs/NATIVE_REBUILD_SPEC.md` §5. The ones that surprise people:

1. **Schema-in-prompt, no repair loop.** Anthropic's server-side `messages.parse(output_format=…)` failed deterministically on this schema ("grammar too large" — many optional properties, deeply nested nullable unions, `additionalProperties=false` everywhere). Fix: send the JSON schema as text inside the prompt, validate client-side with `extra="forbid"`. **No re-ask on validation failure** — a schema-invalid response is a model-quality signal that must be visible. See §5.1, §7.
2. **Truncation is failure.** A `max_tokens` stop reason (or a refusal) is a hard error; do not try to salvage truncated JSON. Set the output-token ceiling generously — cost of over-provisioning is zero because truncation is fail-loud.
3. **The D2 "confirm-and-clear on zero removals" bug.** A *better* extraction can produce *fewer* dictionary removals, which historically left the attribution ambiguity gated forever — "correct extraction → worse final status." Rule: when the attribution ambiguity's candidate feeds equal the dictionary-confirmed feed set, clear the flag even at zero removals. See §5.5 and `docs/LIVE_E2E_2026-08-07.md` for the incident.
4. **Non-determinism is a demo-choreography concern, not a bug.** Three live runs on the same demo FRD produced three different gate shapes (1 blocking flag → 1 detected/1 auto-confirmed → 0/0/0) at **identical** PASS-quality extractions and **identical** 94.1% eval. The framing: what varies run-to-run is how much the dictionary can prove on its own, never the mapping quality. Replay fixtures are the reliable path for demos where a specific gate outcome is required. See `docs/DEMO_RUNBOOK.md` §3, and rebuild spec §5.8.
5. **Mock mode is plumbing proof, not a benchmark.** Mock extractions copy real facts back from the FRD's parsed markdown, so they always ground. They prove the pipeline works; they say nothing about extraction quality. Enforced local-only (guarded off in the workspace runtime); mock+live cross-configuration is a hard failure.
6. **Fail loud on ambiguous config.** Two providers set at once, an unversioned label contract, a missing secret — every one raises on startup. Silent fallbacks in a compliance-sensitive pipeline are worse than a red startup.

### Where to read next

Point at these instead of re-deriving:

- `README.md` — pipeline overview, run instructions (Databricks bundle + local), review-app setup, branching model. Start here if you have never run the agent.
- `CLAUDE.md` — the working-notes file loaded into every session in this repo. Config doctrine, the widget-name mismatch between notebooks, provider-seam rules, fixture rules, known gaps. Read before making code changes.
- `docs/DEMO_RUNBOOK.md` — how to run the client-facing demo in `review_app_react/` (choreography, non-determinism framing, contingency to replay, hard rules on real client documents).
- `docs/NATIVE_REBUILD_SPEC.md` — **the authoritative deep reference**. Written to be sufficient to rebuild the agent from scratch on Databricks Genie Code / LDP / UC / Model Serving. Point people here for any question deeper than this skill file covers. §2 stages, §3 grounding, §4 HITL, §5 hard-won lessons, §6 the Databricks-native target, §7 acceptance criteria (the test suite as spec), §8 the Anthropic-specific rework items for the port.
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

The strongest specification of "correct behavior" for this pattern is the test suite. Rebuild spec §7 groups the tests by theme (grounding & enrichment, gating vocabulary, model contract, label contract, LLM extraction transport, render/attribution, review-app backend, end-to-end demo scenarios). A rebuild is not done until an equivalent suite is green.

### Porting to a new platform — this pattern is being re-targeted right now *(incidental — swap freely)*

This exact pattern is currently being ported from Python-notebooks + Anthropic-SDK + FastAPI-review-app to **Databricks-native** (Genie Code + Lakeflow Declarative Pipelines + Unity Catalog + Model Serving). `docs/NATIVE_REBUILD_SPEC.md` §6 is the target-architecture description for that port; §8 is the list of Anthropic-specific rework items (client, model id, secret scope, error taxonomy, retry policy, streaming, structured-outputs re-probe on the new endpoint, thinking modes, provider-gate values, subprocess env injection, `pyproject.toml`, docs). It is a working example of what "port this pattern to a new platform" actually costs — go read it before you assume swapping providers is a one-liner. What survives the port is the pipeline shape, the vocabularies, the audit design, and the HITL model. What changes is transport and identity.
