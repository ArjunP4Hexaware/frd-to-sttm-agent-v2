/**
 * API layer for the client-facing demo tab (backend/demo.py): live runs +
 * replay. Separate module from api.ts on purpose — the demo endpoints are a
 * distinct feature area (/api/demo/*) with their own types, and the existing
 * review/orchestration hooks stay untouched.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import type { GatedItem, ResolutionSubmission } from "./types";

async function errorMessage(res: Response): Promise<string> {
  const body = await res.text().catch(() => "");
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  } catch {
    // Not JSON — fall through to the status line.
  }
  return `${res.status} ${res.statusText}${body ? `: ${body}` : ""}`;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types (mirror backend/demo.py response shapes)
// ---------------------------------------------------------------------------
export interface DemoConfig {
  provider: string;
  /** "local" = subprocess runs, gated on a backend API key; "databricks" =
   *  the deployed App triggering the real bundle job, where the key lives in
   *  the workspace secret scope so api_key_present is not a readiness gate. */
  mode: "local" | "databricks";
  call_estimate: { calls: number; usd: number; seconds: number };
  api_key_present: boolean;
  golden_doc_id: string;
  upload_max_bytes: number;
}

export interface DemoDocument {
  path: string;
  name: string;
  doc_id: string;
  source: "preloaded" | "upload" | "sharepoint";
  is_golden: boolean;
  size_bytes: number;
}

/** SharePoint tenant probe (backend/sharepoint_routes.py). `configured` is
 *  derived from presence only — no credential value ever crosses this
 *  boundary. `sttm_folder` is where a reviewer uploads a finished workbook:
 *  the folder the sync watches. */
export interface SharePointConfig {
  configured: boolean;
  site: string | null;
  library: string | null;
  frd_folder: string | null;
  sttm_folder: string | null;
}

export interface DemoStage {
  id: string;
  label: string;
  status: "pending" | "running" | "done" | "failed";
}

export interface DemoRunEvent {
  seq: number;
  kind: "console" | "stage" | "status";
  text: string;
  ts: string;
}

export interface DemoRunSnapshot {
  id: string;
  suffix: string;
  doc_id: string;
  artifact_set: string;
  is_golden: boolean;
  status: "running" | "done" | "failed";
  error: string | null;
  started_at: string;
  finished_at: string | null;
  /** Databricks-mode runs only: the workspace job-run page. Null in local mode. */
  run_page_url: string | null;
  stages: DemoStage[];
  seq: number;
  events: DemoRunEvent[];
}

export interface DemoArtifactSet {
  set_id: string;
  doc_id: string;
  source: "live_e2e" | "demo_run";
  modified_at: string;
  status: string | null;
  eval_pct: number | null;
  workbook_available: boolean;
  is_golden: boolean;
}

export interface DemoFeedSummary {
  feed_name: string | null;
  source_system: string | null;
  file_name_patterns: string[];
  stage: string;
  standard: string;
  n_rules: number;
  requirement_ids: string[];
}

export interface DemoGate {
  detected: number;
  auto_confirmed: number;
  human_resolved: number;
  awaiting_human: number;
  detected_items: { kind: string; text: string }[];
  auto_confirmed_notes: string[];
  awaiting_items: { kind: string; text: string }[];
  strict_checked: number | null;
  strict_failed: number;
  advisory_checked: number | null;
  gate_status: string | null;
}

export interface DemoMappingRow {
  source_column: string | null;
  datatype: string | null;
  stage: string;
  standard: string;
}

export interface DemoResults {
  set_id: string;
  doc_id: string;
  is_golden: boolean;
  extraction_summary: {
    n_feeds: number;
    n_tables: number;
    n_rules: number;
    project_id: string | null;
    project_name: string | null;
    feeds: DemoFeedSummary[];
  };
  gate: DemoGate;
  verdict: { status: string | null; n_feeds: number };
  eval: {
    available: boolean;
    matched_cells: number | null;
    total_cells: number | null;
    pct: number | null;
    is_golden: boolean;
  };
  mappings: { feed_name: string | null; rows: DemoMappingRow[] }[];
  /** Template decision from 04's provenance (docs/TEMPLATE_ARCHITECTURE.md);
   *  null for artifact sets rendered before the template architecture. */
  template: TemplateDecision | null;
  workbook_available: boolean;
}

// ---------------------------------------------------------------------------
// Template architecture + corpus (backend/corpus_routes.py, 2026-08-22)
// ---------------------------------------------------------------------------
export interface TemplateScore {
  reference: string;
  score: number;
  components: { columns: number; tables: number; tokens: number; name: number };
  excluded?: boolean;
}

export interface TemplateDecision {
  /** single = one workbook drove layout+dictionary; amalgam = merged top-k,
   *  first-wins per sheet; freeform = nothing matched, best-effort, flagged. */
  mode: "single" | "amalgam" | "freeform";
  selections: TemplateScore[];
  ranked: TemplateScore[];
  own_reference: string | null;
  own_excluded: boolean;
  eval_reference: string | null;
  feed_sources: Record<string, string>;
  thresholds: Record<string, number>;
  demoted_from?: string[];
}

/** One background sync (the SharePoint sync job, or a network-free
 *  reindex). Exactly one runs at a time; the state survives until the next
 *  one starts. */
export interface CorpusSyncState {
  state: "idle" | "running" | "done" | "failed";
  mode: "sync" | "reindex" | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  /** Databricks mode only: the sync job-run page. */
  run_page_url: string | null;
  summary: Record<string, unknown> | null;
}

export interface CorpusSummary {
  built: boolean;
  generated_at: string | null;
  /** When the volumes were last brought in step with SharePoint (null for a
   *  reindex-only corpus, e.g. smoke fixtures). */
  synced_at: string | null;
  n_frds: number;
  n_references: number;
  n_pairs: number;
  n_unmapped: number;
  unpaired_references: string[];
  sync: CorpusSyncState;
}

/** One FRD as the corpus index knows it — the picker's whole world. */
export interface CorpusFrd {
  doc_id: string;
  name: string;
  path: string | null;
  runnable: boolean;
  content_sha256: string | null;
  web_url: string | null;
  modified: string | null;
  paired: boolean;
  reference: string | null;
  matched_by: "name" | "similarity" | null;
  score: number | null;
  confidence: "high" | "low" | null;
  components: { columns: number; tables: number; tokens: number; name: number } | null;
  reference_web_url: string | null;
  reference_modified: string | null;
  reference_size_bytes: number | null;
}

export interface CorpusConfig {
  /** A tenant is wired, so "Sync now" can be offered. */
  available: boolean;
  frd_folder: string | null;
  reference_folder: string | null;
  mode: "local" | "databricks";
  reference_volume: string;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------
export function useDemoConfig() {
  return useQuery({
    queryKey: ["demo", "config"],
    queryFn: () => fetchJson<DemoConfig>("/api/demo/config"),
    retry: false,
  });
}

export function useDemoDocuments() {
  return useQuery({
    queryKey: ["demo", "documents"],
    queryFn: () => fetchJson<{ documents: DemoDocument[] }>("/api/demo/documents"),
  });
}

export function useDemoArtifactSets() {
  return useQuery({
    queryKey: ["demo", "artifacts"],
    queryFn: () => fetchJson<{ artifact_sets: DemoArtifactSet[] }>("/api/demo/artifacts"),
  });
}

export function useDemoUpload() {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return fetchJson<DemoDocument>("/api/demo/uploads", { method: "POST", body: form });
    },
  });
}

/** Never retried: an unconfigured tenant (503) or a Graph refusal (502) is a
 *  standing condition, not a blip, and retrying just delays the message. */
export function useSharePointConfig() {
  return useQuery({
    queryKey: ["demo", "sharepoint", "config"],
    queryFn: () => fetchJson<SharePointConfig>("/api/demo/sharepoint/config"),
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// Corpus — the picker's source of truth (backend/corpus_routes.py)
// ---------------------------------------------------------------------------

/** Corpus state; unbuilt is a normal 200, so no retry noise. Polls while a
 *  sync is running so the stats and the list refresh as it lands. */
export function useCorpusSummary() {
  return useQuery({
    queryKey: ["demo", "corpus", "summary"],
    queryFn: () => fetchJson<CorpusSummary>("/api/demo/corpus"),
    retry: false,
    refetchInterval: (query) => (query.state.data?.sync.state === "running" ? 2000 : false),
  });
}

export function useCorpusFrds(enabled: boolean) {
  return useQuery({
    queryKey: ["demo", "corpus", "frds"],
    queryFn: () => fetchJson<{ built: boolean; frds: CorpusFrd[] }>("/api/demo/corpus/frds"),
    enabled,
    retry: false,
  });
}

export function useCorpusConfig() {
  return useQuery({
    queryKey: ["demo", "corpus", "config"],
    queryFn: () => fetchJson<CorpusConfig>("/api/demo/corpus/config"),
    retry: false,
  });
}

/**
 * "Sync now" / "Rebuild index": starts ONE background sync (202) — the
 * SharePoint sync job in the deployed App, the same code in-process
 * locally — or a network-free reindex of what the volumes already hold.
 * Zero model calls, but it rewrites the volumes and the index, so the
 * backend demands confirm:true and this hook only fires from the two-step
 * control. Progress comes back through useCorpusSummary's `sync` field.
 */
export function useCorpusSync() {
  return useMutation({
    mutationFn: (mode: "sync" | "reindex") =>
      fetchJson<CorpusSyncState>("/api/demo/corpus/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, mode }),
      }),
  });
}

/** Download URL for an approved STTM in the reference volume (served only
 *  for names the corpus index lists). */
export function corpusReferenceUrl(name: string): string {
  return `/api/demo/corpus/references/${encodeURIComponent(name)}`;
}

export function useStartDemoRun() {
  return useMutation({
    mutationFn: (frdPath: string) =>
      fetchJson<DemoRunSnapshot>("/api/demo/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ frd: frdPath }),
      }),
  });
}

/**
 * Polling fallback for run state. The primary channel is SSE
 * (subscribeDemoRunEvents below); this poll runs alongside at a slow tick
 * and is what keeps the view honest if the SSE connection drops — same
 * belt-and-braces posture as the sibling demo app.
 */
export function useDemoRunSnapshot(runId: string | null) {
  return useQuery({
    queryKey: ["demo", "runs", runId],
    queryFn: () => fetchJson<DemoRunSnapshot>(`/api/demo/runs/${encodeURIComponent(runId as string)}`),
    enabled: runId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && status !== "running" ? false : 2000;
    },
  });
}

export function useDemoResults(setId: string | null, docId: string | null) {
  return useQuery({
    queryKey: ["demo", "results", setId, docId],
    queryFn: () =>
      fetchJson<DemoResults>(
        `/api/demo/artifacts/${encodeURIComponent(setId as string)}/results?doc=${encodeURIComponent(docId as string)}`,
      ),
    enabled: setId !== null && docId !== null,
  });
}

// ---------------------------------------------------------------------------
// Human-in-the-loop on a finished run (backend/demo.py, 2026-08-22 evening)
// ---------------------------------------------------------------------------
export interface DemoRerenderState {
  state: "idle" | "running" | "done" | "failed";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  /** Databricks mode only: the render job-run page. */
  run_page_url: string | null;
}

export interface DemoReview {
  set_id: string;
  doc_id: string;
  items: GatedItem[];
  n_items: number;
  n_resolved: number;
  rerender: DemoRerenderState;
}

/** The gated items of a finished run with their saved resolutions. Polls
 *  while a re-render is running so the panel flips to "applied" on its own. */
export function useDemoReview(setId: string | null, docId: string | null) {
  return useQuery({
    queryKey: ["demo", "review", setId, docId],
    queryFn: () =>
      fetchJson<DemoReview>(
        `/api/demo/artifacts/${encodeURIComponent(setId as string)}/review?doc=${encodeURIComponent(docId as string)}`,
      ),
    enabled: setId !== null && docId !== null,
    refetchInterval: (query) => (query.state.data?.rerender.state === "running" ? 2000 : false),
  });
}

/** Save ONE resolution into the run's contract (same rule as the legacy
 *  review flow: a candidate-having item needs a pick or none-of-these; a
 *  candidate-less one takes free text). Nothing is re-rendered here. */
export function useSubmitDemoResolution(setId: string, docId: string) {
  return useMutation({
    mutationFn: (submission: ResolutionSubmission) =>
      fetchJson<GatedItem>(
        `/api/demo/artifacts/${encodeURIComponent(setId)}/resolutions?doc=${encodeURIComponent(docId)}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(submission) },
      ),
  });
}

/** Fold the saved resolutions into the STTM: re-run stage 04 only (no model
 *  call, nothing billed). 202; progress via useDemoReview's `rerender`. */
export function useDemoRerender(setId: string, docId: string) {
  return useMutation({
    mutationFn: () =>
      fetchJson<DemoRerenderState>(
        `/api/demo/artifacts/${encodeURIComponent(setId)}/rerender?doc=${encodeURIComponent(docId)}`,
        { method: "POST" },
      ),
  });
}

export function demoWorkbookUrl(setId: string, docId: string): string {
  return `/api/demo/artifacts/${encodeURIComponent(setId)}/workbook?doc=${encodeURIComponent(docId)}`;
}

/**
 * SSE subscription to a run's event stream. Returns an unsubscribe fn.
 * On any EventSource error the caller's polling query keeps the UI live —
 * we just close the source and stop (no reconnect storm against a backend
 * that may have restarted and forgotten the run).
 */
export function subscribeDemoRunEvents(
  runId: string,
  after: number,
  onEvent: (e: DemoRunEvent) => void,
  onEnd: (status: string, error: string | null) => void,
  onError: () => void,
): () => void {
  const source = new EventSource(
    `/api/demo/runs/${encodeURIComponent(runId)}/events?after=${after}`,
  );
  const handle = (ev: MessageEvent) => onEvent(JSON.parse(ev.data) as DemoRunEvent);
  source.addEventListener("console", handle);
  source.addEventListener("stage", handle);
  source.addEventListener("status", handle);
  source.addEventListener("end", (ev) => {
    const data = JSON.parse((ev as MessageEvent).data) as { status: string; error: string | null };
    source.close();
    onEnd(data.status, data.error);
  });
  source.onerror = () => {
    source.close();
    onError();
  };
  return () => source.close();
}
