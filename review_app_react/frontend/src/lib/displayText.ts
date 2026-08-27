/**
 * Display-only vocabulary for a non-technical (health-plan business) reader.
 *
 * Nothing here crosses the wire. The backend keeps sending, and this app
 * keeps receiving and storing, the verbatim enum values (`still_gated`,
 * `PASS_WITH_FLAGS`, `running_contract_build`, ...) -- these maps are
 * applied at render time only, at the last possible moment. Never map a
 * value on the way *into* state or into a request payload.
 *
 * Every lookup goes through `displayLabel`, which falls back to the raw
 * value when a key is unmapped: a stakeholder seeing a bare enum is bad,
 * but a blank chip or a crash mid-demo is worse.
 */

/** Total, safe lookup: unmapped keys render verbatim rather than `undefined`. */
export function displayLabel(map: Record<string, string>, value: string | null | undefined): string {
  if (value == null) return "";
  return map[value] ?? value;
}

/**
 * Presentable title per document. EDIT THE STRING BELOW to retitle a document
 * -- nothing else in the app needs to change.
 *
 * The key is the doc_id, which is also the primary key: it is the path segment
 * in GET /api/documents/{doc_id} and in the POST that saves a human
 * resolution. So this map is read at render time only, exactly like every
 * other map in this file. Never map a doc_id on the way into a request path,
 * a payload field, or a React key -- doing so would send a title where the
 * backend expects an id, and the resolution save would 404.
 */
export const DOCUMENT_DISPLAY_NAMES: Record<string, string> = {
  demo_frd: "FRD - Community Risk Ingestion (demo)",
};

/**
 * doc_id -> presentable title, falling back to the raw doc_id unchanged.
 *
 * THE FALLBACK IS LOAD-BEARING -- DO NOT "SIMPLIFY" IT AWAY. A live upload
 * during a demo mints a doc_id that is not in the map above, and it must
 * render as the raw id. A blank title, the string "undefined", or a crash in
 * front of an audience is far worse than an unpolished but truthful label.
 * This is the same total-lookup rule `displayLabel` exists to enforce, which
 * is why this resolver is written in terms of it rather than indexing the map
 * directly.
 */
export function documentDisplayName(docId: string | null | undefined): string {
  return displayLabel(DOCUMENT_DISPLAY_NAMES, docId);
}

/**
 * Contract gate status. The wire values (PASS / PASS_WITH_FLAGS / FAIL) are
 * what `03_contract_build` writes into the contract JSON and what the API
 * returns -- only the chip text changes.
 */
export const GATE_STATUS_LABEL: Record<string, string> = {
  PASS: "Passed",
  PASS_WITH_FLAGS: "Passed with notes",
  FAIL: "Did not pass",
};

/**
 * Gate status -> status-chip variant. PASS is the "eligible" (success) case,
 * PASS_WITH_FLAGS is "advisory" (warning), FAIL is "blocked" (destructive).
 * Previously copy-pasted into SummaryHeader/DocumentPicker/ResultsScreen;
 * kept next to the labels so the two can't drift apart.
 */
export const GATE_STATUS_VARIANT: Record<string, "success" | "warning" | "destructive"> = {
  PASS: "success",
  PASS_WITH_FLAGS: "warning",
  FAIL: "destructive",
};

/** Why an ambiguity is still listed as unresolved on the results screen. */
export const UNRESOLVED_SOURCE_LABEL: Record<string, string> = {
  still_gated: "Not yet reviewed",
  resolution_not_applied: "Your choice couldn't be applied automatically",
};

/**
 * Ambiguity kind, as shown on each review card and results row. Phrased as
 * the question the reviewer is actually being asked, since these are the
 * titles of the cards a reviewer works through.
 *
 * - `attribution`: 03_contract_build.py's attribution_check() fires when the
 *   same validation rule text appears on more than one feed (the FRD's "the
 *   below files" prose). The candidates are feed names -- the source files --
 *   so the open question is which file the rule actually applies to.
 * - `disagreement`: enrich() fires this when the extraction agent and a regex
 *   over the raw document text read the same field differently. The two
 *   candidates are those two conflicting values; the agent's was kept and
 *   flagged rather than silently overwritten.
 */
export const ITEM_KIND_LABEL: Record<string, string> = {
  attribution: "Which file does this rule apply to?",
  disagreement: "Conflicting values — needs your decision",
  advisory_grounding: "Weak match to source document",
};

/**
 * Label over the quoted FRD rule on each review card. Rendered as an
 * `.eyebrow`, so it displays uppercase.
 */
export const ITEM_RULE_LABEL = "The FRD says";

/**
 * One plain-English sentence per ambiguity kind explaining WHY the reviewer
 * is being asked -- shown between the quoted rule and the answer options.
 * Keyed by the same wire values as ITEM_KIND_LABEL; an unmapped kind simply
 * renders no explanation line rather than a raw enum posing as a sentence.
 */
export const ITEM_KIND_EXPLANATION: Record<string, string> = {
  attribution:
    "This rule was found in the document, but the document doesn't clearly say which " +
    "incoming source it applies to. Choose the source it belongs to, or 'None of these'.",
  disagreement:
    "Two readings of the document produced conflicting values for this field. " +
    "Choose the one that is correct, or 'None of these'.",
  advisory_grounding:
    "This wording couldn't be confidently matched back to the source document. " +
    "Describe the correct reading in your own words.",
};

/**
 * Label over the demoted machine rationale at the bottom of each card. The
 * rationale itself is kept verbatim (never deleted) -- it is the audit trail
 * -- but it sits under this label in small muted text instead of leading
 * the card.
 */
export const ITEM_TECHNICAL_DETAIL_LABEL = "Technical detail";

/**
 * Orientation copy on the Upload screen. Lives here rather than inline so
 * the stage vocabulary stays in one place: it walks the same four steps as
 * RUN_STAGE_LABEL below, in the same voice and the same order, so what the
 * reader is promised here is what they then watch happen.
 */
export const UPLOAD_INTRO =
  "Upload a Functional Requirements Document (.docx) and we'll read it, pull out the data " +
  "fields, check for anything ambiguous, and build your mapping workbook.";

/**
 * The demo's input constraint, kept deliberately separate from UPLOAD_INTRO
 * and rendered next to the file input rather than in the opening paragraph.
 * It belongs beside the control it constrains, not at the head of the first
 * thing the viewer reads.
 *
 * Not removable: uploads that don't resolve to the demo fixture are refused
 * with a 422 (see orchestration.py's _assert_demo_scoped), so dropping this
 * would leave anyone clicking around to hit a refusal with no warning.
 */
export const UPLOAD_SCOPE_NOTE =
  "This preview is set up for the Medicare Expansion sample document.";

/**
 * Shown on the Documents tab when no contracts are available to review.
 *
 * This is the intended first-run state, not a failure mode. No contract is
 * tracked in git, so a fresh clone starts with an empty list and stays that
 * way until a run completes through render and its contract is promoted into
 * the scanned directory (see orchestration.py's _promote_contract). App.tsx
 * opens on the "New upload" tab when it sees an empty list, so this line is
 * normally read as confirmation rather than as an obstacle.
 *
 * It therefore follows the same rule as RUN_STAGE_LABEL below -- no notebook
 * filenames, no filesystem paths, no internal artifact names -- and instead
 * names the action that moves the reader forward, using the "New upload"
 * tab's own vocabulary.
 */
export const NO_DOCUMENTS_MESSAGE =
  "No documents yet. Upload a Functional Requirements Document to build your first mapping workbook.";

/**
 * POSITIVE CONFIRMATION for the confirmed-empty state.
 *
 * This one string breaks the rule stated above about filesystem paths, and
 * the exception is the entire point. An empty list and a dead backend used
 * to render identically -- a quiet, blank panel -- which is the single most
 * recurring defect on this engagement: the app looked healthy and empty
 * while the backend was down. So the empty state now has to *prove* itself,
 * and the proof is that the client received a successful response naming
 * the directory the server actually scanned. A broken backend cannot
 * produce that sentence, because it cannot produce that response at all.
 *
 * Rendered only when GET /api/documents returned 200 -- never as a default,
 * never from cached or assumed state. See CONNECTED_EMPTY_HEADLINE's use in
 * DocumentsSourceNotice.
 */
export const CONNECTED_EMPTY_HEADLINE = "Connected · no documents yet";
export const CONNECTED_EMPTY_DETAIL =
  "The review service responded successfully. It scanned this location and found no mapping contracts:";

/**
 * FAILURE state copy. Names the failure and the port so the reader can act
 * on it, and is styled as an error rather than as an absence -- the whole
 * contract of this pair is that the two states can never be confused.
 *
 * `port` is read from window.location at render time, so it reports the port
 * the browser genuinely talked to rather than a hardcoded guess that would
 * be wrong the moment the app is served somewhere else.
 */
export const DOCUMENTS_UNREACHABLE_HEADLINE = "Cannot reach the review service";

export function documentsUnreachableDetail(port: string, message: string): string {
  const where = port ? `on port ${port}` : "at this address";
  return (
    `The document list could not be loaded ${where}. ` +
    `This is a connection or server error, NOT an empty document list -- ` +
    `nothing can be said about how many documents exist until this succeeds. ` +
    `Reported error: ${message}`
  );
}

/**
 * WORKBOOK DOWNLOAD copy, for an existing document's rendered STTM .xlsx.
 *
 * The missing-workbook strings name the CAUSE, not just the absence. "No
 * workbook" alone would leave the reader unable to tell a document that
 * never reached stage 04 from a download feature that is broken -- the same
 * ambiguity the document list's empty-vs-unreachable split exists to
 * remove, applied one level down.
 *
 * Nothing here reports accuracy, cell matches, or coverage: this path is
 * download-only by design.
 */
export const WORKBOOK_DOWNLOAD_LABEL = "Download STTM workbook";

export function workbookDownloadHint(docId: string): string {
  return `${docId}.sttm.xlsx — the mapping workbook generated for this document.`;
}

export const WORKBOOK_MISSING_HEADLINE = "No workbook generated for this document";
export const WORKBOOK_MISSING_DETAIL =
  "This document has a mapping contract, but the STTM workbook render (stage 04) has not " +
  "produced a file for it. There is nothing to download yet — this is not a download failure.";

/**
 * BEAT 1 (HEADER) copy -- the agent's own identity, not run data.
 *
 * These three strings are the FRD->STTM instance of the four-beat spine
 * (HEADER -> INPUT -> PROGRESS -> RESULTS) that every agent in the demo
 * shares; see ../../../demo_shell/frontend/src/components/Header.tsx, which
 * reads the same three fields from its `/api/descriptor` response. This app
 * has no descriptor endpoint, so they are local constants -- but they are
 * agent *identity*, which is a display string like everything else in this
 * file, and never a measured value. Anything numeric belongs in the metric
 * row and must come from the API (see CorpusMetricRow).
 *
 * The header carries the agent name ALONE by decision (2026-08-21): no
 * eyebrow, no tagline, no pipeline position, no wordmark. "to", not "→",
 * also by decision.
 */
export const AGENT_NAME = "FRD to STTM Agent";

/**
 * Metric-row tile labels. Phrased in the same business-reader voice as the
 * rest of this file -- "Items needing review", not "gated ambiguities".
 *
 * Every tile these label is computed by summing fields the API actually
 * returns on GET /api/documents (feed_count, item_count, resolved_count).
 * There is deliberately no label here for grounding pass rate or STTM
 * cell-match: neither number appears in any response this screen can see,
 * and a fabricated tile in front of a client is worse than a missing one.
 */
export const METRIC_LABEL_DOCUMENTS = "Documents";
export const METRIC_LABEL_FEEDS = "Sources mapped";
export const METRIC_LABEL_ITEMS = "Items needing review";
export const METRIC_LABEL_REVIEWED = "Reviewed";

/**
 * Pipeline stage shown while a run is in flight. Deliberately free of
 * notebook filenames (`01_frd_ingest` etc.) -- the viewer of the demo has
 * no way to interpret those, and they are an implementation detail of the
 * pipeline, not a step the business reader is tracking.
 */
export const RUN_STAGE_LABEL: Record<string, string> = {
  pending: "Queued",
  running_ingest: "Reading your document",
  running_extract: "Pulling out the data fields",
  running_contract_build: "Checking for ambiguities",
  running_sttm_render: "Building your mapping workbook",
  gated: "Waiting on your review",
  done: "Done",
  error: "Failed",
};
