import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  DocumentDetail,
  DocumentListResponse,
  GatedItem,
  ResolutionSubmission,
  RunContractFailure,
  RunContractResponse,
  RunCoverageResponse,
  RunStatusResponse,
  UploadResponse,
} from "./types";

/**
 * Every caller of fetchJson renders the thrown Error's message straight
 * into a user-facing Alert, so that message has to be the human-readable
 * sentence on its own. FastAPI puts that sentence under `detail`; including
 * the status line and the undecoded body around it put
 * `422 Unprocessable Entity: {"detail":"..."}` on screen -- the backend's
 * deliberate, well-worded refusals (a non-MIDS upload, a non-.docx file)
 * read as a raw protocol dump instead of an intentional "we can't accept
 * this" message. Falls back to the status line only when the response
 * carries no usable detail string.
 */
async function errorMessage(res: Response): Promise<string> {
  const body = await res.text().catch(() => "");
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  } catch {
    // Not JSON (a proxy error page, an empty body) -- fall through.
  }
  return `${res.status} ${res.statusText}${body ? `: ${body}` : ""}`;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  return res.json() as Promise<T>;
}

/**
 * Like fetchJson, but on a non-2xx response resolves with the parsed JSON
 * body instead of throwing -- for endpoints (like GET run contract) whose
 * error responses are themselves structured data the caller needs to
 * render (e.g. the FAIL-with-no-contract case's {reason, report_md}), not
 * just a message to surface as a generic failure.
 */
async function fetchJsonOrErrorBody<T>(url: string): Promise<T> {
  const res = await fetch(url);
  const body = await res.json().catch(() => null);
  if (body === null) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  // FastAPI's HTTPException wraps the raised `detail` under that key.
  return (res.ok ? body : body.detail) as T;
}

/**
 * `retry: false` is deliberate and load-bearing here.
 *
 * This query decides which of two very different things the Documents tab
 * shows: the confirmed-empty state, or the backend-unreachable error state.
 * react-query's default (3 retries with backoff) leaves `isError` false for
 * several seconds after the backend is already known to be down, and the
 * tab renders its neutral loading state throughout -- which in front of an
 * audience is indistinguishable from the quiet empty list this whole change
 * exists to disambiguate. Failing on the first attempt makes the error
 * state appear immediately, which is the honest reading of a refused
 * connection.
 */
export function useDocumentList() {
  return useQuery({
    queryKey: ["documents"],
    queryFn: () => fetchJson<DocumentListResponse>("/api/documents"),
    retry: false,
  });
}

/**
 * Query key includes docId, so each document gets its own cache entry --
 * switching the selected document can never surface another document's
 * stale/in-flight data under the same cache slot. This is the same
 * doc-scoping principle as the `{doc_id}_item_{i}` fix in the Streamlit
 * app's session_state keys, applied to the data-fetching layer.
 */
export function useDocumentDetail(docId: string | null) {
  return useQuery({
    queryKey: ["documents", docId],
    queryFn: () => fetchJson<DocumentDetail>(`/api/documents/${encodeURIComponent(docId as string)}`),
    enabled: docId !== null,
  });
}

export function useSubmitResolution(docId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (submission: ResolutionSubmission) =>
      fetchJson<GatedItem>(`/api/documents/${encodeURIComponent(docId)}/resolutions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(submission),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents", docId] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

// --------------------------------------------------------------------------- //
// Orchestration: upload -> pipeline run -> (review, if gated) -> results.
// See review_app_react/backend/orchestration.py.
// --------------------------------------------------------------------------- //
export function useUploadFrd() {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return fetchJson<UploadResponse>("/api/uploads", { method: "POST", body: form });
    },
  });
}

export function useStartRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      fetchJson<RunStatusResponse>(`/api/runs/${encodeURIComponent(runId)}/start`, { method: "POST" }),
    onSuccess: (data, runId) => {
      queryClient.setQueryData(["runs", runId, "status"], data);
    },
  });
}

/**
 * Seeding the "status" query cache with this response (rather than just
 * invalidating it) matters here specifically: /continue reuses the *same*
 * run_id -- and therefore the same ["runs", runId, "status"] query key --
 * that a moment ago cached a 'gated' status while this run sat in the
 * review screen. Without seeding, ProgressView's first render after
 * continuing would read that stale cached 'gated' value (react-query
 * serves cached data synchronously on mount, before the background
 * refetch resolves) and immediately bounce back to the review screen --
 * exactly the bug this fixes. Seeding with the fresh 'running_sttm_render'
 * response closes that window.
 */
export function useContinueRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      fetchJson<RunStatusResponse>(`/api/runs/${encodeURIComponent(runId)}/continue`, { method: "POST" }),
    onSuccess: (data, runId) => {
      queryClient.setQueryData(["runs", runId, "status"], data);
    },
  });
}

/**
 * Plain interval poll (react-query's `refetchInterval`), no websockets --
 * matches this app's existing "no more infrastructure than the feature
 * needs" posture (in-memory run state, no queue). Stops polling once a run
 * reaches a terminal status ('gated', 'done', or 'error') -- ProgressView
 * reacts to those via a `useEffect`, not this hook.
 */
export function useRunStatus(runId: string | null) {
  return useQuery({
    queryKey: ["runs", runId, "status"],
    queryFn: () => fetchJson<RunStatusResponse>(`/api/runs/${encodeURIComponent(runId as string)}/status`),
    enabled: runId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["gated", "done", "error"].includes(status) ? false : 1500;
    },
  });
}

export function useRunDocument(runId: string | null) {
  return useQuery({
    queryKey: ["runs", runId, "document"],
    queryFn: () => fetchJson<DocumentDetail>(`/api/runs/${encodeURIComponent(runId as string)}/document`),
    enabled: runId !== null,
  });
}

export function useSubmitRunResolution(runId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (submission: ResolutionSubmission) =>
      fetchJson<GatedItem>(`/api/runs/${encodeURIComponent(runId)}/resolutions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(submission),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runs", runId, "document"] });
    },
  });
}

// Loose union type: the success shape (RunContractResponse) and the
// FAIL-with-no-contract error shape (RunContractFailure) both come back
// through fetchJsonOrErrorBody -- callers narrow on `"contract" in result`.
export function useRunContract(runId: string | null) {
  return useQuery({
    queryKey: ["runs", runId, "contract"],
    queryFn: () =>
      fetchJsonOrErrorBody<RunContractResponse | RunContractFailure>(
        `/api/runs/${encodeURIComponent(runId as string)}/contract`,
      ),
    enabled: runId !== null,
  });
}

/**
 * `retry: false` on purpose: this query backs a tile that is omitted
 * whenever the figure isn't available, so a failure needs to settle
 * immediately rather than spend three backoff attempts before the Results
 * screen stops waiting on it. The endpoint already answers 200 with
 * available=false for every expected "no figure" case, so a thrown error
 * here means something genuinely unexpected -- and the tile omits itself
 * for that too.
 */
export function useRunCoverage(runId: string | null) {
  return useQuery({
    queryKey: ["runs", runId, "coverage"],
    queryFn: () =>
      fetchJson<RunCoverageResponse>(`/api/runs/${encodeURIComponent(runId as string)}/coverage`),
    enabled: runId !== null,
    retry: false,
  });
}

export function runSttmDownloadUrl(runId: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/sttm.xlsx`;
}
