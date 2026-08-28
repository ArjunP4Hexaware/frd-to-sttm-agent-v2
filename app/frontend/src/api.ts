/** API layer — mirrors app/backend/app.py. */
import { useMutation, useQuery } from "@tanstack/react-query";

async function errorMessage(res: Response): Promise<string> {
  const body = await res.text().catch(() => "");
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}${body ? `: ${body}` : ""}`;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json() as Promise<T>;
}

export interface Config {
  mode: "local" | "databricks";
  provider: string;
  model: string;
  catalog: string;
  schema: string;
  volumes: Record<string, string>;
  data_root: string;
}

export interface DocumentEntry {
  doc_id: string;
  frd: string;
  sha256: string;
  vdd: string | null;
  sttm: string | null;
  ambiguous_name: boolean;
  status: "ready" | "mapped" | "no_dictionary" | "ambiguous";
  generatable: boolean;
  reason: string;
  vdd_summary: { n_files: number; n_fields: number; files: string[]; problems: string[] } | null;
  sttm_summary: { dialect: string | null; n_sources: number; n_columns: number } | null;
}

export interface DocumentsResponse {
  built: boolean;
  documents: DocumentEntry[];
  unpaired_vdds: string[];
  unpaired_references: string[];
  vdd_errors: Record<string, string>;
  generated_at: string | null;
}

export interface Question {
  id: string;
  kind: string;
  source: string | null;
  text: string;
  options: string[];
  free_text: boolean;
  context: Record<string, unknown>;
  answer: { value: string; by: string | null; at: string | null } | null;
}

export interface SourceEntry {
  feed_index: number;
  feed_name: string;
  file: string | null;
  field_sheet: string | null;
  n_columns: number;
  layers: Record<
    "stage" | "standard",
    { catalog: string | null; schema: string | null; tables: string[]; origin: Record<string, string> }
  >;
}

export interface Live {
  phase: "running" | "done";
  task?: string;
  doc_id?: string;
  job_run_id?: number | null;
  url?: string | null;
  error?: string | null;
  started_at?: string;
}

export interface PreviewRow {
  source_column: string;
  datatype: string;
  audit: boolean;
  stage: { catalog: string; schema: string; table: string; column: string; datatype: string };
  standard: { catalog: string; schema: string; table: string; column: string; datatype: string };
}

export interface RunView {
  run_id: string;
  doc_id: string;
  status: string;
  error: string | null;
  created_at?: string;
  model?: string;
  provider?: string;
  frd?: { source_file: string; chars: number; heading_count: number; table_count: number };
  vdd?: { file: string; n_files: number; n_fields: number; files: string[]; problems: { kind: string; detail: string }[] } | null;
  extraction?: { feeds: { feed_name: string | null; file_name_patterns: string[]; validation_rules: string[] }[] };
  assessment?: {
    status: string;
    blockers: { kind: string; text: string }[];
    questions: Question[];
    sources: SourceEntry[];
    grounding: { strict_checked: number; strict_failed: string[]; advisory_checked: number; advisory_flagged: string[] };
    notes: string[];
  };
  render?: {
    workbook: string;
    rendered_at: string;
    dialect: string;
    layout_from: string | null;
    n_rows: number;
    rows_per_source: Record<string, number>;
    unfilled_columns: Record<string, string[]>;
    unpromoted_types: string[];
    unanswered: string[];
  };
  live: Live | null;
  /** The sources as applied after answers (falls back to the first assessment). */
  sources: SourceEntry[];
  preview: { source: string; file: string | null; n_rows: number; rows: PreviewRow[]; error?: string }[];
  summary: RunSummary | null;
}

export interface RunSummary {
  run_id: string;
  doc_id: string;
  status: string;
  created_at: string | null;
  triggered_by: string | null;
  error: string | null;
  n_sources: number;
  n_questions: number;
  n_answered: number;
  n_blockers: number;
  workbook: string | null;
  rendered_at: string | null;
  live?: Live | null;
}

export interface ReindexState {
  state: "idle" | "running" | "done" | "failed";
  error: string | null;
  url: string | null;
  finished_at: string | null;
}

export const useConfig = () =>
  useQuery({ queryKey: ["config"], queryFn: () => fetchJson<Config>("/api/config"), retry: false });

export const useDocuments = () =>
  useQuery({ queryKey: ["documents"], queryFn: () => fetchJson<DocumentsResponse>("/api/documents"), retry: false });

export const useReindexState = () =>
  useQuery({
    queryKey: ["reindex"],
    queryFn: () => fetchJson<ReindexState>("/api/reindex"),
    refetchInterval: (q) => (q.state.data?.state === "running" ? 2000 : false),
    refetchIntervalInBackground: true,
  });

export const useReindex = () =>
  useMutation({ mutationFn: () => fetchJson<ReindexState>("/api/reindex", { method: "POST" }) });

export const useRuns = () =>
  useQuery({ queryKey: ["runs"], queryFn: () => fetchJson<{ runs: RunSummary[] }>("/api/runs"), retry: false });

export const useRun = (runId: string | null) =>
  useQuery({
    queryKey: ["run", runId],
    queryFn: () => fetchJson<RunView>(`/api/runs/${encodeURIComponent(runId as string)}`),
    enabled: runId !== null,
    // Poll while the job is running — also when the App restarted mid-run and
    // only run.json's "extracting" status remains to tell us so.
    refetchInterval: (q) =>
      q.state.data?.live?.phase === "running" || q.state.data?.status === "extracting" ? 3000 : false,
    // Keep polling when the tab is not focused — a reviewer switches to Excel or the job page and
    // comes back expecting the result, not a stale spinner.
    refetchIntervalInBackground: true,
    retry: false,
  });

export const useStartRun = () =>
  useMutation({
    mutationFn: (docId: string) =>
      fetchJson<{ run_id: string }>("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: docId }),
      }),
  });

export const useAnswer = (runId: string) =>
  useMutation({
    mutationFn: (a: { question_id: string; value: string }) =>
      fetchJson<{ status: string; questions: Question[] }>(`/api/runs/${encodeURIComponent(runId)}/answers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(a),
      }),
  });

export const useRender = (runId: string) =>
  useMutation({
    mutationFn: () => fetchJson<{ run_id: string }>(`/api/runs/${encodeURIComponent(runId)}/render`, { method: "POST" }),
  });

export const workbookUrl = (runId: string) => `/api/runs/${encodeURIComponent(runId)}/workbook`;
export const reportUrl = (runId: string) => `/api/runs/${encodeURIComponent(runId)}/report`;
export const documentUrl = (docId: string, kind: "frd" | "vdd" | "sttm") =>
  `/api/documents/${encodeURIComponent(docId)}/${kind}`;

export const STATUS_LABEL: Record<string, string> = {
  extracting: "Reading the documents",
  ready: "Ready to generate",
  needs_input: "Needs your answers",
  cannot_generate: "Cannot generate",
  rendered: "STTM generated",
  failed: "Failed",
};

/** One plain sentence per status — the first thing a reviewer reads on a run. */
export const STATUS_EXPLANATION: Record<string, string> = {
  extracting:
    "The agent is reading the FRD and the vendor data dictionary and extracting the source and target facts. This runs as a Databricks job and usually takes one to three minutes.",
  ready:
    "The agent found everything it needs in the two documents. Generate the STTM whenever you are ready.",
  needs_input:
    "The agent read both documents and will not guess the items below. Answer them, then generate the STTM — no second model call is made.",
  cannot_generate:
    "The STTM cannot be built from these documents. The reasons are listed below; fix the input and start a new run.",
  rendered:
    "The STTM workbook is ready to download. Every row comes from the vendor dictionary; the target side comes from the FRD and the client standards.",
  failed: "The run did not complete. The error is shown below; start a new run once it is addressed.",
};

export const VDD_PROBLEM_LABEL: Record<string, string> = {
  missing_descriptions: "some columns have no description",
  missing_datatypes: "some columns have no data type",
  missing_required_flags: "some columns have no required flag",
  missing_phi_flags: "some columns have no PHI flag",
  missing_segments: "some columns name no record segment",
  field_sheet_missing: "a field sheet named on FILES is missing",
  field_sheet_empty: "a field sheet is empty",
  template_example_rows: "the template's example rows were still present",
  field_count_mismatch: "declared and actual field counts differ",
  sheet_not_listed: "a sheet is not listed on FILES",
  no_field_sheet_named: "a FILES row names no field sheet",
  files_row_ignored: "a FILES row was read as a note",
  file_without_pattern: "a FILES row has no file name pattern",
};

export const QUESTION_KIND_LABEL: Record<string, string> = {
  unverified: "Not found in the FRD",
  weak_match: "Loose match to the FRD",
  attribution: "Which source does this rule apply to?",
  project_id: "Conflicting project ids",
  file_pairing: "Which dictionary file is this source?",
  target_gap: "Target not stated",
};
