// Mirrors review_app_react/backend/app.py's Pydantic response models --
// keep these in sync by hand (no generated client in this pass).

export type ItemKind = "attribution" | "disagreement" | "advisory_grounding";
export type ResolutionType = "candidate_pick" | "free_text" | "none_of_these";

export interface GatedItemResolution {
  resolution_type: ResolutionType;
  chosen_candidate: string | null;
  rationale: string | null;
  candidates_snapshot: string[];
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface GatedItem {
  id: string;
  text: string;
  /**
   * The FRD rule this ambiguity is about, extracted server-side (see
   * ambiguity_parsing.rule_text): the contract's structured `context.rule`
   * when present, else the first quoted span in `text`. Null when neither
   * exists -- render `text` in its place so the quote block is never blank.
   * Optional so a stale backend without the field can't crash the card.
   */
  rule_text?: string | null;
  kind: ItemKind;
  has_candidates: boolean;
  candidates: string[];
  context: Record<string, unknown>;
  resolution: GatedItemResolution | null;
}

export interface DocumentDetail {
  doc_id: string;
  status: string;
  generated_from_frd: string | null;
  feed_count: number;
  items: GatedItem[];
  /**
   * Whether GET /api/documents/{doc_id}/workbook would serve a file, resolved
   * server-side by exact doc_id. Drives WorkbookDownload's two visible states.
   * An availability flag only -- carries no accuracy or coverage figure.
   */
  workbook_available: boolean;
}

export interface DocumentSummary {
  doc_id: string;
  status: string;
  generated_from_frd: string | null;
  feed_count: number;
  item_count: number;
  resolved_count: number;
}

/**
 * GET /api/documents. The envelope, not a bare array -- `contracts_dir` is
 * what lets the Documents tab prove an empty list is a real, successful
 * "nothing here" rather than a failed load rendering as blank. See
 * DocumentListResponse in backend/app.py.
 */
export interface DocumentListResponse {
  contracts_dir: string;
  app_mode: string;
  documents: DocumentSummary[];
}

export interface ResolutionSubmission {
  ambiguity_id: string;
  kind: string;
  resolution_type: ResolutionType;
  chosen_candidate: string | null;
  rationale: string | null;
  candidates_snapshot: string[];
}

// Mirrors review_app_react/backend/orchestration.py's Pydantic response
// models -- same hand-kept-in-sync convention as the rest of this file.
export type RunStatusValue =
  | "pending"
  | "running_ingest"
  | "running_extract"
  | "running_contract_build"
  | "running_sttm_render"
  | "gated"
  | "done"
  | "error";

export interface UploadResponse {
  run_id: string;
}

export interface RunStatusResponse {
  run_id: string;
  status: RunStatusValue;
  doc_id: string;
  source_filename: string;
  error: string | null;
}

export interface UnresolvedItem {
  ambiguity_id: string;
  kind: string | null;
  text: string | null;
  reason: string;
  source: "still_gated" | "resolution_not_applied";
}

export interface RunContractResponse {
  run_id: string;
  doc_id: string;
  status: string;
  contract: Record<string, unknown>;
  unresolved: UnresolvedItem[];
}

/**
 * Golden-pair eval totals for a run (GET /api/runs/{run_id}/coverage).
 *
 * Counts only -- the backend deliberately sends no percentage and no
 * display string, so the rounding and digit grouping happen once, here.
 * `available` is false for every normal reason the figure can be missing
 * (run not rendered, FAIL path, report absent or unparseable); the counts
 * are null in that case and the tile is omitted.
 */
export interface RunCoverageResponse {
  run_id: string;
  doc_id: string;
  available: boolean;
  matched_cells: number | null;
  total_cells: number | null;
  reason: string | null;
}

// Shape of the 422 response body get_run_contract() raises when no contract
// JSON exists at all (the FAIL-before-a-contract-could-be-built case).
export interface RunContractFailure {
  run_id: string;
  doc_id: string;
  status: "FAIL";
  reason: string;
  report_md: string | null;
}
