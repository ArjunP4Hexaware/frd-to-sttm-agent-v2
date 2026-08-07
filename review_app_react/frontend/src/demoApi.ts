/**
 * API layer for the client-facing demo tab (backend/demo.py): live runs +
 * replay. Separate module from api.ts on purpose — the demo endpoints are a
 * distinct feature area (/api/demo/*) with their own types, and the existing
 * review/orchestration hooks stay untouched.
 */
import { useMutation, useQuery } from "@tanstack/react-query";

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
  call_estimate: { calls: number; usd: number; seconds: number };
  api_key_present: boolean;
  golden_doc_id: string;
  upload_max_bytes: number;
}

export interface DemoDocument {
  path: string;
  name: string;
  doc_id: string;
  source: "preloaded" | "upload";
  is_golden: boolean;
  size_bytes: number;
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
  workbook_available: boolean;
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
