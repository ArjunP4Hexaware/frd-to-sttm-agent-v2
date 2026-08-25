import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import {
  corpusReferenceUrl,
  subscribeDemoRunEvents,
  useDemoConfig,
  useDemoRunSnapshot,
  useSharePointConfig,
  useStartDemoRun,
} from "../demoApi";
import type { CorpusFrd, DemoRunEvent, DemoRunSnapshot } from "../demoApi";
import { CorpusPanel } from "./CorpusPanel";
import { DemoResults } from "./DemoResults";

/**
 * The "Select FRD" flow — the app's primary entry point. Since 2026-08-22
 * the picker is the CORPUS: the list of FRDs the SharePoint sync has landed
 * in Unity Catalog, each marked mapped or unmapped by the corpus index
 * (exact name match first, deterministic similarity second). Nothing is
 * looked up in SharePoint on the request path, and nothing is ever written
 * there.
 *
 *  - an UNMAPPED FRD → "Generate STTM" → the billed-run confirmation → run
 *    → results. The finished workbook is downloaded and, after any final
 *    edits, uploaded to the library's STTM folder BY THE REVIEWER; the next
 *    sync pulls it in and pairs it.
 *  - a MAPPED FRD → its approved STTM is presented first (download from the
 *    reference volume, link to SharePoint). A deliberate two-step
 *    "regenerate anyway" re-enters the billed-run gate — every such run is
 *    an automatic golden-pair eval against the existing workbook, which is
 *    left untouched.
 */
type Phase =
  | { kind: "setup" }
  | { kind: "existing"; frd: CorpusFrd }
  | { kind: "confirm"; frd: CorpusFrd }
  | { kind: "running"; runId: string }
  | { kind: "results"; setId: string; docId: string };

export function DemoFlow() {
  const [phase, setPhase] = useState<Phase>({ kind: "setup" });

  return (
    <div className="flex flex-col gap-6">
      {phase.kind === "setup" && (
        <MappingSetup
          onExisting={(frd) => setPhase({ kind: "existing", frd })}
          onGenerate={(frd) => setPhase({ kind: "confirm", frd })}
        />
      )}
      {phase.kind === "existing" && (
        <ExistingSttmView
          frd={phase.frd}
          onBack={() => setPhase({ kind: "setup" })}
          onRegenerate={() => setPhase({ kind: "confirm", frd: phase.frd })}
        />
      )}
      {phase.kind === "confirm" && (
        <ConfirmDialog
          frd={phase.frd}
          onCancel={() => setPhase({ kind: "setup" })}
          onStarted={(runId) => setPhase({ kind: "running", runId })}
        />
      )}
      {phase.kind === "running" && (
        <RunProgress
          runId={phase.runId}
          onFinished={(setId, docId) => setPhase({ kind: "results", setId, docId })}
          onBack={() => setPhase({ kind: "setup" })}
        />
      )}
      {phase.kind === "results" && (
        <div className="flex flex-col gap-4">
          <div>
            <Button variant="ghost" onClick={() => setPhase({ kind: "setup" })}>
              ← Select FRD
            </Button>
          </div>
          <DemoResults setId={phase.setId} docId={phase.docId} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Setup: the corpus picker (unmapped FRDs first, mapped ones below)
// ---------------------------------------------------------------------------
function MappingSetup({
  onExisting,
  onGenerate,
}: {
  onExisting: (frd: CorpusFrd) => void;
  onGenerate: (frd: CorpusFrd) => void;
}) {
  const configQuery = useDemoConfig();
  const spConfig = useSharePointConfig();

  const mode = configQuery.data?.mode ?? "local";
  const keyPresent = mode === "databricks" ? true : (configQuery.data?.api_key_present ?? false);
  const configured = spConfig.data?.configured ?? false;

  return (
    <div className="flex flex-col gap-4">
      {!configured && spConfig.isSuccess && (
        <Alert>
          <AlertTitle>No document source is connected</AlertTitle>
          <AlertDescription>
            Neither a documents folder (STTM_LOCAL_SOURCE_DIR) nor a SharePoint tenant is configured on this
            backend, so nothing can be synced from here. FRDs already in the volumes can still be listed and
            run; the index can be rebuilt from them with “Rebuild index”.
          </AlertDescription>
        </Alert>
      )}
      {!keyPresent && (
        <Alert variant="destructive">
          <AlertTitle>Pipeline runs unavailable</AlertTitle>
          <AlertDescription>
            ANTHROPIC_API_KEY is not configured on the backend (environment or repo .env), so an FRD cannot
            be processed. Existing STTMs and past runs remain viewable.
          </AlertDescription>
        </Alert>
      )}

      <div>
        <h2 className="eyebrow mb-2">Select FRD</h2>
        <p className="text-sm text-muted-foreground">
          The list below is what the last sync landed in the corpus. Pick an FRD that has no STTM yet to
          draft one; an FRD that already has an STTM is presented as-is.
        </p>
      </div>

      <CorpusPanel onGenerate={onGenerate} onExisting={onExisting} canRun={keyPresent} />
    </div>
  );
}

/**
 * An FRD that already has an approved STTM: present it FIRST — nothing is
 * regenerated on selection. A deliberate two-step "regenerate anyway"
 * re-enters the billed-run gate (every such run is an automatic golden-pair
 * eval against the existing workbook). Nothing here writes anywhere: the
 * existing STTM stays exactly where it is in SharePoint.
 */
function ExistingSttmView({
  frd,
  onBack,
  onRegenerate,
}: {
  frd: CorpusFrd;
  onBack: () => void;
  onRegenerate: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const reference = frd.reference ?? "";
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button variant="ghost" onClick={onBack}>
          ← Select FRD
        </Button>
      </div>
      <Card className="border-2">
        <CardHeader>
          <CardTitle>This FRD already has an STTM</CardTitle>
          <CardDescription>
            <span className="mono-id">{frd.name}</span> is mapped by{" "}
            <span className="mono-id">{reference}</span>
            {frd.reference_modified ? ` (modified ${frd.reference_modified.slice(0, 10)}` : ""}
            {frd.reference_size_bytes != null
              ? `${frd.reference_modified ? ", " : " ("}${(frd.reference_size_bytes / 1024).toFixed(0)} KB)`
              : frd.reference_modified
                ? ")"
                : ""}
            . Paired by {frd.matched_by === "name" ? "exact name" : "content similarity"}
            {frd.score != null ? ` (${Math.round(frd.score * 100)}% match)` : ""}. It is presented as-is —
            nothing was regenerated.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex gap-2">
            <Button asChild>
              <a href={corpusReferenceUrl(reference)} download>
                Download the STTM (.xlsx)
              </a>
            </Button>
            {frd.reference_web_url && (
              <Button variant="outline" asChild>
                <a href={frd.reference_web_url} target="_blank" rel="noreferrer">
                  Open in SharePoint ↗
                </a>
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2 pt-1 border-t">
            {!confirming ? (
              <Button variant="outline" onClick={() => setConfirming(true)} disabled={!frd.runnable}>
                Regenerate this mapping anyway…
              </Button>
            ) : (
              <>
                <span className="text-sm text-muted-foreground">
                  Runs the full pipeline on <span className="mono-id">{frd.name}</span> again. The existing
                  STTM is untouched — nothing is written to SharePoint — and the new draft is scored against
                  it.
                </span>
                <Button onClick={onRegenerate}>Continue</Button>
                <Button variant="ghost" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            The approved workbook in SharePoint stays the system of record. If you want the new draft to
            replace it, upload the draft there yourself after review; the next sync re-pairs it.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Confirmation — the billed-call gate
// ---------------------------------------------------------------------------
function ConfirmDialog({
  frd,
  onCancel,
  onStarted,
}: {
  frd: CorpusFrd;
  onCancel: () => void;
  onStarted: (runId: string) => void;
}) {
  const configQuery = useDemoConfig();
  const start = useStartDemoRun();
  const est = configQuery.data?.call_estimate;
  const isJob = configQuery.data?.mode === "databricks";
  const duration =
    est && est.seconds >= 120 ? `~${Math.round(est.seconds / 60)} min` : `roughly ${est?.seconds}s`;

  return (
    <Card className="border-2">
      <CardHeader>
        <CardTitle>Start a live, billed run?</CardTitle>
        <CardDescription>
          <span className="mono-id">{frd.name}</span> will be processed end to end{" "}
          {isJob ? (
            <>
              as the Databricks Job <span className="mono-id">frd_sttm_pipeline</span> in this workspace
            </>
          ) : (
            <>using the Anthropic API</>
          )}
          .
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {est && (
          <p className="text-sm">
            Expected scale: <strong>~{est.calls} billed API call</strong> (~${est.usd.toFixed(2)}), {duration}{" "}
            end to end{isJob ? " (most of it Databricks task startup)" : ""}. Outputs are written to a
            run-scoped location; nothing is written to SharePoint.
          </p>
        )}
        {!frd.path && (
          <Alert variant="destructive">
            <AlertDescription>
              This FRD is in the corpus index but its file is not on this backend yet — run a sync first.
            </AlertDescription>
          </Alert>
        )}
        {start.isError && (
          <Alert variant="destructive">
            <AlertDescription>{(start.error as Error).message}</AlertDescription>
          </Alert>
        )}
        <div className="flex gap-2">
          <Button
            disabled={start.isPending || !frd.path}
            onClick={() => frd.path && start.mutate(frd.path, { onSuccess: (snap) => onStarted(snap.id) })}
          >
            {start.isPending ? "Starting…" : "Run it"}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={start.isPending}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Progress: SSE primary, slow poll as the fallback truth
// ---------------------------------------------------------------------------
function RunProgress({
  runId,
  onFinished,
  onBack,
}: {
  runId: string;
  onFinished: (setId: string, docId: string) => void;
  onBack: () => void;
}) {
  const snapshotQuery = useDemoRunSnapshot(runId);
  const [liveEvents, setLiveEvents] = useState<DemoRunEvent[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    return subscribeDemoRunEvents(
      runId,
      0,
      (e) => setLiveEvents((prev) => (prev.some((p) => p.seq === e.seq) ? prev : [...prev, e])),
      () => queryClient.invalidateQueries({ queryKey: ["demo", "runs", runId] }),
      () => {
        /* SSE dropped — the poll in useDemoRunSnapshot keeps the view live. */
      },
    );
  }, [runId, queryClient]);

  const snap: DemoRunSnapshot | undefined = snapshotQuery.data;
  const consoleLines = liveEvents.filter((e) => e.kind === "console").slice(-14);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        {snap?.status === "running" && <Spinner />}
        <h2 className="text-lg font-semibold">
          {snap?.doc_id ? <>Processing <span className="mono-id">{snap.doc_id}</span></> : "Processing"}
        </h2>
        {snap?.run_page_url && (
          <a
            href={snap.run_page_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm underline text-muted-foreground"
          >
            View job run in Databricks ↗
          </a>
        )}
      </div>
      <div className="flex flex-col gap-1">
        {snap?.stages.map((stage) => (
          <div key={stage.id} className="flex items-center gap-2 text-sm">
            <StageDot status={stage.status} />
            <span className={stage.status === "pending" ? "text-muted-foreground" : ""}>{stage.label}</span>
          </div>
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Console</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="text-xs bg-muted rounded p-2 overflow-x-auto max-h-64 overflow-y-auto">
            {consoleLines.map((e) => e.text).join("\n") || "waiting for output…"}
          </pre>
        </CardContent>
      </Card>
      {snap?.status === "failed" && (
        <Alert variant="destructive">
          <AlertTitle>Run failed</AlertTitle>
          <AlertDescription>{snap.error}</AlertDescription>
        </Alert>
      )}
      {snap?.status === "done" && (
        <div className="flex gap-2">
          <Button onClick={() => onFinished(snap.artifact_set, snap.doc_id)}>View results</Button>
        </div>
      )}
      {snap?.status !== "running" && (
        <div>
          <Button variant="ghost" onClick={onBack}>
            ← Select FRD
          </Button>
        </div>
      )}
    </div>
  );
}

function StageDot({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "bg-pass-bright"
      : status === "running"
        ? "bg-brand-sky animate-pulse"
        : status === "failed"
          ? "bg-destructive"
          : "bg-muted-foreground/30";
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${tone}`} />;
}
