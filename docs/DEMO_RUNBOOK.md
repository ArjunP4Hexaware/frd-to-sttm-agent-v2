# FRD→STTM client demo — runbook

How to run the client-facing demo in `review_app_react/` (the "Client demo"
tab). Companion to `review_app_react/README.md` (setup detail, guardrails)
and `docs/LIVE_E2E_2026-08-07.md` (where the numbers below come from).
Rehearsed end-to-end in the browser on 2026-08-06/07.

## 1. Startup

One-time frontend build, then a single process:

```bash
cd review_app_react/frontend && npm install && npm run build
cd ../..   # repo root
set -a; . ./.env; set +a          # ANTHROPIC_API_KEY — live mode only
DATABRICKS_APP_PORT=8020 .venv/bin/python review_app_react/backend/app.py
# open http://localhost:8020 → "Client demo" tab
```

- **Replay-only startup needs no key and works on a fresh clone, offline**:
  the tracked replay set (`local_dev_fixtures/sttm_out_live_e2e_20260807b/`)
  ships in git. Skip the `.env` line; the Live panel will show its
  "runs unavailable" notice and Replay works fully.
- Sanity check before the audience arrives: open the Replay list (both
  preserved sets should show), click `_20260807b`, confirm the results view
  renders. If running live, confirm the Live panel shows no missing-key
  warning.

## 2. Choreography

**Open with the live run** — it is ~30 seconds, so run it on the spot:

1. Live mode → pick `demo_frd.docx` (badged *golden pair — eval
   available*) → "Run live…". Read the confirmation dialog aloud: ~1 billed
   API call, ~$0.15, ~35s — *"the demo tells you what it costs before it
   spends anything."*
2. Narrate the four stages as the dots light up: **Ingest** (docx →
   markdown), **Extract** (the one live Anthropic call — the model reads
   the FRD against the full extraction schema), **Contract build + gate**
   (validation, verbatim-grounding audit, ambiguity gating), **Render +
   eval** (STTM workbook + comparison against the golden reference).
3. "View results", then walk the page top to bottom — the section order is
   the story:
   - **Extraction summary** — 3 feeds, their file patterns, stage/standard
     targets, rules captured. Everything on this card came out of the
     document; nothing is templated.
   - **Gate strip — the HITL centerpiece.** Talking point: *"the agent
     flags what it isn't sure of; the dictionary cross-check auto-confirms
     what the data proves; anything else waits for a human."* Give this
     beat time even when the counts are zero — zero means "nothing needed a
     human this run", which is itself the point.
   - **PASS tile.** Talking point: a dictionary-correct extraction now
     *earns* its PASS — the pipeline used to leave a correct run flagged
     (the D2 fix, `docs/LIVE_E2E_2026-08-07.md`); now a flag survives only
     when a human genuinely needs to look.
   - **Eval 94.1%.** Talking point: every non-matching cell is a
     deliberate fixture divergence (datatype differences built into the
     golden pair) — *the eval harness catches exactly what it should, and
     nothing else.*
   - **Mappings + workbook download** — the tangible deliverable. Open a
     mapping table, then download the .xlsx and open it: *"this is the
     artifact a mapping analyst would otherwise hand-build."*

## 3. Non-determinism framing (grounded in the observed runs)

The three live runs to date produced three different gate shapes — 1
blocking flag, then 1 detected/1 auto-confirmed, then 0/0/0 — with
**identical PASS-quality extractions and an identical 94.1% eval** every
time (grounding 45/45 strict each run; advisory 30 vs 31 checks is the same
kind of surface variation). The framing: *"the agent proposes what it
cannot know; the reviewer confirms — what varies run to run is how much the
dictionary can prove on its own, never the quality of the mapping."*

- If the live run shows a **non-zero gate strip: that is the BEST outcome**
  — the flag-then-confirm narrative renders on screen in front of the
  audience. Slow down and read it.
- If it shows **0/0/0**: pivot to the `_20260807b` replay for the
  flag-then-confirm case ("here's a run where the model attributed a rule
  to two feeds and the dictionary cross-check proved it right") — one
  click, same view.

## 4. The replay list is a story in itself

- `_20260807` — PASS_WITH_FLAGS: the **pre-fix** run, where a correct
  extraction still sat flagged.
- `_20260807b` — PASS: the **post-fix** run, same document, same eval —
  the D2 improvement made visible as a pair of list entries.
- The `demo_*` entries are the runs done in front of audiences —
  repeatability on display. Each finished live run appears here
  immediately as a "demo run".

## 5. Contingency

Any API or network trouble → switch to Replay, `_20260807b`. Identical
results view, identical story, zero API calls, works offline. (If the
backend itself died: restart it — replay needs no key and no state; a
crashed live run's artifacts and console log survive on disk.)

## 6. Hard rules

- **No real client documents are ever uploaded** — HIPAA/BAA gate. The
  upload path exists for synthetic/anonymized FRDs only, and the UI says so
  next to the control ("Prototype — synthetic or anonymized documents
  only"). This is an operator obligation, not just a UI notice.
- The fixture content (demo pair, replay sets) is anonymized and cleared
  for client viewing per Venu's §9a approval. Nothing else is.

## 7. Encore — the deeper HITL

For audiences that want the full human-in-the-loop workflow: switch to the
**Existing documents** tab — per-ambiguity candidate picks, structural-pick
policy, resolutions persisted into the contract, workbook re-download. (If
the list is empty on this machine, run the "New upload" mock flow once to
promote a contract, or note it as the reviewer-workstation view.)

## 8. Databricks framing

*"Built for the workspace, currently demoed locally."* The app is
Databricks-Apps-shaped (app.yaml, root requirements.txt as the Apps
manifest, data_access.py's databricks mode, secret-scope key path in the
pipeline). Three named gaps are the port work, flagged in
`review_app_react/README.md`:

1. **Bundle-job trigger** — the live run executes pipeline stages as local
   subprocesses; the workspace version triggers the `frd_sttm_pipeline`
   bundle job and reads outputs from UC volumes.
2. **Apps secret resource** — the API key reaches the app via a Databricks
   Apps secret/env resource, not a repo `.env`.
3. **SSE through the Apps proxy** — expected to work, not yet exercised
   against a real workspace.
