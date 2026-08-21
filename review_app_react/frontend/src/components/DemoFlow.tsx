import { useEffect, useRef, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import {
  subscribeDemoRunEvents,
  useDemoArtifactSets,
  useDemoConfig,
  useDemoDocuments,
  useSharePointConfig,
  useSharePointDocuments,
  useSharePointImport,
  useDemoRunSnapshot,
  useDemoUpload,
  useStartDemoRun,
} from "../demoApi";
import type { DemoDocument, DemoRunEvent, DemoRunSnapshot } from "../demoApi";
import { DemoResults } from "./DemoResults";

/**
 * The client-facing demo tab: Live mode (billed pipeline run, with an
 * explicit cost-confirmation dialog) and Replay mode (any saved artifact
 * set, zero API calls). Both funnel into the same DemoResults view.
 */
type Phase =
  | { kind: "setup" }
  | { kind: "confirm"; doc: DemoDocument }
  | { kind: "running"; runId: string }
  | { kind: "results"; setId: string; docId: string };

export function DemoFlow() {
  const [phase, setPhase] = useState<Phase>({ kind: "setup" });

  return (
    <div className="flex flex-col gap-6">
      {phase.kind === "setup" && (
        <DemoSetup
          onConfirmLive={(doc) => setPhase({ kind: "confirm", doc })}
          onReplay={(setId, docId) => setPhase({ kind: "results", setId, docId })}
        />
      )}
      {phase.kind === "confirm" && (
        <ConfirmDialog
          doc={phase.doc}
          onCancel={() => setPhase({ kind: "setup" })}
          onStarted={(runId) => setPhase({ kind: "running", runId })}
        />
      )}
      {phase.kind === "running" && (
        <DemoRunProgress
          runId={phase.runId}
          onFinished={(setId, docId) => setPhase({ kind: "results", setId, docId })}
          onBack={() => setPhase({ kind: "setup" })}
        />
      )}
      {phase.kind === "results" && (
        <div className="flex flex-col gap-4">
          <div>
            <Button variant="ghost" onClick={() => setPhase({ kind: "setup" })}>
              ← Back to demo setup
            </Button>
          </div>
          <DemoResults setId={phase.setId} docId={phase.docId} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Setup: mode toggle + document picker / artifact-set picker
// ---------------------------------------------------------------------------
function DemoSetup({
  onConfirmLive,
  onReplay,
}: {
  onConfirmLive: (doc: DemoDocument) => void;
  onReplay: (setId: string, docId: string) => void;
}) {
  const [mode, setMode] = useState<"live" | "replay">("replay");
  const configQuery = useDemoConfig();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-2">
        <Button variant={mode === "replay" ? "default" : "ghost"} onClick={() => setMode("replay")}>
          Replay a saved run
        </Button>
        <Button variant={mode === "live" ? "default" : "ghost"} onClick={() => setMode("live")}>
          Live run
        </Button>
      </div>
      {mode === "live" ? (
        <LiveSetup config={configQuery.data} onConfirm={onConfirmLive} />
      ) : (
        <ReplaySetup onReplay={onReplay} />
      )}
    </div>
  );
}

function LiveSetup({
  config,
  onConfirm,
}: {
  config: ReturnType<typeof useDemoConfig>["data"];
  onConfirm: (doc: DemoDocument) => void;
}) {
  const documentsQuery = useDemoDocuments();
  const upload = useDemoUpload();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const keyPresent = config?.api_key_present ?? false;

  return (
    <div className="flex flex-col gap-4">
      {!keyPresent && (
        <Alert variant="destructive">
          <AlertTitle>Live runs unavailable</AlertTitle>
          <AlertDescription>
            ANTHROPIC_API_KEY is not configured on the backend (environment or repo .env). Replay mode still
            works without it.
          </AlertDescription>
        </Alert>
      )}
      <p className="text-sm text-muted-foreground">
        A live run executes the real 01→04 pipeline against the Anthropic API — pick the preloaded demo FRD or
        upload one. <strong>Prototype — synthetic or anonymized documents only.</strong>
      </p>
      <div className="flex flex-col gap-2">
        {documentsQuery.data?.documents.map((doc) => (
          <Card key={doc.path}>
            <CardContent className="py-2 flex items-center justify-between gap-4">
              <div>
                <span className="mono-id text-sm">{doc.name}</span>{" "}
                <Badge variant="outline">{doc.source}</Badge>{" "}
                {doc.is_golden && <Badge>golden pair — eval available</Badge>}
              </div>
              <Button disabled={!keyPresent} onClick={() => onConfirm(doc)}>
                Run live…
              </Button>
            </CardContent>
          </Card>
        ))}
        {documentsQuery.isSuccess && documentsQuery.data.documents.length === 0 && (
          <p className="text-sm text-muted-foreground">No documents available — upload one below.</p>
        )}
      </div>
      <div className="flex items-center gap-3">
        <input
          ref={fileInput}
          type="file"
          accept=".docx"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) {
              upload.mutate(file, {
                onSuccess: () => queryClient.invalidateQueries({ queryKey: ["demo", "documents"] }),
              });
            }
            e.target.value = "";
          }}
        />
        <Button variant="ghost" onClick={() => fileInput.current?.click()} disabled={upload.isPending}>
          {upload.isPending ? "Uploading…" : "Upload an FRD (.docx)"}
        </Button>
        <span className="text-xs text-muted-foreground">
          Uploads land in a gitignored directory and run through the same live pipeline; the eval panel shows a
          score only for the preloaded golden pair.
        </span>
      </div>
      {upload.isError && (
        <Alert variant="destructive">
          <AlertDescription>{(upload.error as Error).message}</AlertDescription>
        </Alert>
      )}
      <SharePointPicker keyPresent={keyPresent} onConfirm={onConfirm} />
    </div>
  );
}

/**
 * Pick an FRD straight from the SharePoint document library.
 *
 * The picked file is imported into the same demo-uploads directory an upload
 * lands in and comes back as a DemoDocument, so it starts through the exact
 * same live run path — there is no separate "run from SharePoint" flow.
 *
 * Renders nothing at all when SharePoint is not configured: an unwired tenant
 * should leave the demo tab looking exactly as it did before.
 */
function SharePointPicker({
  keyPresent,
  onConfirm,
}: {
  keyPresent: boolean;
  onConfirm: (doc: DemoDocument) => void;
}) {
  const configQuery = useSharePointConfig();
  const configured = configQuery.data?.configured ?? false;
  const listing = useSharePointDocuments(configured);
  const importDoc = useSharePointImport();

  if (!configured) return null;

  return (
    <div className="flex flex-col gap-2 border-t pt-4">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">SharePoint document library</span>
        <span className="text-xs text-muted-foreground mono-id">
          {configQuery.data?.site}/{configQuery.data?.library}
          {configQuery.data?.frd_folder ? `/${configQuery.data.frd_folder}` : ""}
        </span>
      </div>

      {listing.isLoading && <p className="text-sm text-muted-foreground">Loading documents…</p>}

      {listing.isError && (
        <Alert variant="destructive">
          <AlertDescription>{(listing.error as Error).message}</AlertDescription>
        </Alert>
      )}

      {listing.isSuccess && listing.data.documents.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No .docx FRDs in this folder.
        </p>
      )}

      {listing.data?.documents.map((doc) => (
        <Card key={doc.item_id}>
          <CardContent className="py-2 flex items-center justify-between gap-4">
            <div>
              <span className="mono-id text-sm">{doc.name}</span>{" "}
              <Badge variant="outline">sharepoint</Badge>{" "}
              <span className="text-xs text-muted-foreground">
                {(doc.size_bytes / 1024).toFixed(0)} KB · modified {doc.modified.slice(0, 10)}
              </span>
            </div>
            <Button
              disabled={!keyPresent || importDoc.isPending}
              onClick={() =>
                importDoc.mutate(
                  { item_id: doc.item_id, name: doc.name },
                  { onSuccess: onConfirm },
                )
              }
            >
              {importDoc.isPending ? "Fetching…" : "Run live…"}
            </Button>
          </CardContent>
        </Card>
      ))}

      {importDoc.isError && (
        <Alert variant="destructive">
          <AlertDescription>{(importDoc.error as Error).message}</AlertDescription>
        </Alert>
      )}

      <p className="text-xs text-muted-foreground">
        The document is downloaded into the same gitignored uploads directory and runs through the identical
        live pipeline. <strong>Prototype — synthetic or anonymized documents only.</strong>
      </p>
    </div>
  );
}

function ReplaySetup({ onReplay }: { onReplay: (setId: string, docId: string) => void }) {
  const setsQuery = useDemoArtifactSets();
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-muted-foreground">
        Replay renders a previously saved run — the exact same results view, zero API calls.
      </p>
      {setsQuery.isLoading && <p className="text-muted-foreground">Scanning artifact sets…</p>}
      {setsQuery.data?.artifact_sets.map((s) => (
        <Card key={`${s.set_id}/${s.doc_id}`}>
          <CardContent className="py-2 flex items-center justify-between gap-4">
            <div className="flex flex-col">
              <span className="mono-id text-sm">
                {s.doc_id} · {s.set_id}
              </span>
              <span className="text-xs text-muted-foreground">
                {s.source === "live_e2e" ? "preserved live E2E run" : "demo run"} · {s.modified_at}
                {s.status && ` · ${s.status}`}
                {s.eval_pct !== null && ` · eval ${s.eval_pct}%`}
              </span>
            </div>
            <Button onClick={() => onReplay(s.set_id, s.doc_id)}>View results</Button>
          </CardContent>
        </Card>
      ))}
      {setsQuery.isSuccess && setsQuery.data.artifact_sets.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No saved artifact sets found — run the pipeline live once, or check out the tracked replay set.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Confirmation dialog — the billed-call gate
// ---------------------------------------------------------------------------
function ConfirmDialog({
  doc,
  onCancel,
  onStarted,
}: {
  doc: DemoDocument;
  onCancel: () => void;
  onStarted: (runId: string) => void;
}) {
  const configQuery = useDemoConfig();
  const start = useStartDemoRun();
  const est = configQuery.data?.call_estimate;

  return (
    <Card className="border-2">
      <CardHeader>
        <CardTitle>Start a live, billed run?</CardTitle>
        <CardDescription>
          <span className="mono-id">{doc.name}</span> will run through the real 01→04 pipeline with provider{" "}
          <span className="mono-id">anthropic</span>.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {est && (
          <p className="text-sm">
            Expected scale: <strong>~{est.calls} billed API call</strong> (~${est.usd.toFixed(2)}), roughly{" "}
            {est.seconds}s end to end. Outputs go to a run-scoped scratch location; curated baselines are never
            written.
          </p>
        )}
        {start.isError && (
          <Alert variant="destructive">
            <AlertDescription>{(start.error as Error).message}</AlertDescription>
          </Alert>
        )}
        <div className="flex gap-2">
          <Button
            disabled={start.isPending}
            onClick={() => start.mutate(doc.path, { onSuccess: (snap) => onStarted(snap.id) })}
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
function DemoRunProgress({
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
          Live run <span className="mono-id">{runId}</span>
        </h2>
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
            ← Back to demo setup
          </Button>
        </div>
      )}
    </div>
  );
}

function StageDot({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "bg-emerald-500"
      : status === "running"
        ? "bg-blue-500 animate-pulse"
        : status === "failed"
          ? "bg-destructive"
          : "bg-muted-foreground/30";
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${tone}`} />;
}
