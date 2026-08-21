import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import {
  sharePointSttmUrl,
  subscribeDemoRunEvents,
  useDemoConfig,
  useDemoRunSnapshot,
  useLocateFrd,
  useSharePointConfig,
  useStartDemoRun,
} from "../demoApi";
import type { DemoDocument, DemoRunEvent, DemoRunSnapshot, SharePointItemInfo } from "../demoApi";
import { DemoResults } from "./DemoResults";

/**
 * The "Select FRD" flow — the app's primary entry point (decided
 * 2026-08-21, superseding the upload/demo framing): the user NAMES an FRD,
 * the app locates it in the SharePoint library itself, and then either
 *
 *  - presents the already-published STTM if one exists in the output folder
 *    (no regeneration, and deliberately NO publish option — it is already
 *    in SharePoint), or
 *  - runs the real pipeline on it, behind the billed-run confirmation.
 *
 * Matching is exact-or-explicit-pick: an ambiguous name renders candidates
 * for the user to choose from; nothing is ever auto-picked.
 */
type Phase =
  | { kind: "setup" }
  | { kind: "existing"; frd: SharePointItemInfo; sttm: SharePointItemInfo }
  | { kind: "confirm"; doc: DemoDocument }
  | { kind: "running"; runId: string }
  | { kind: "results"; setId: string; docId: string };

export function DemoFlow() {
  const [phase, setPhase] = useState<Phase>({ kind: "setup" });

  return (
    <div className="flex flex-col gap-6">
      {phase.kind === "setup" && (
        <MappingSetup
          onExisting={(frd, sttm) => setPhase({ kind: "existing", frd, sttm })}
          onReadyToRun={(doc) => setPhase({ kind: "confirm", doc })}
        />
      )}
      {phase.kind === "existing" && (
        <ExistingSttmView
          frd={phase.frd}
          sttm={phase.sttm}
          onBack={() => setPhase({ kind: "setup" })}
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
// Setup: name an FRD → locate in SharePoint; past runs below
// ---------------------------------------------------------------------------
function MappingSetup({
  onExisting,
  onReadyToRun,
}: {
  onExisting: (frd: SharePointItemInfo, sttm: SharePointItemInfo) => void;
  onReadyToRun: (doc: DemoDocument) => void;
}) {
  const configQuery = useDemoConfig();
  const spConfig = useSharePointConfig();
  const locate = useLocateFrd();
  const [name, setName] = useState("");

  const mode = configQuery.data?.mode ?? "local";
  const keyPresent = mode === "databricks" ? true : (configQuery.data?.api_key_present ?? false);
  const configured = spConfig.data?.configured ?? false;

  function submit(candidateName?: string) {
    const query = (candidateName ?? name).trim();
    if (!query) return;
    locate.mutate(query, {
      onSuccess: (result) => {
        if (result.status === "existing_sttm") onExisting(result.frd, result.sttm);
        else if (result.status === "ready") onReadyToRun(result.document);
        // "candidates" renders below from locate.data.
      },
    });
  }

  return (
    <div className="flex flex-col gap-4">
      {!configured && spConfig.isSuccess && (
        <Alert variant="destructive">
          <AlertTitle>SharePoint is not connected</AlertTitle>
          <AlertDescription>
            This app locates FRDs in the SharePoint document library, which is not configured on this
            backend (tenant, client id, host, site, and client secret). Wire the SharePoint environment
            settings and reload.
          </AlertDescription>
        </Alert>
      )}
      {!keyPresent && (
        <Alert variant="destructive">
          <AlertTitle>Pipeline runs unavailable</AlertTitle>
          <AlertDescription>
            ANTHROPIC_API_KEY is not configured on the backend (environment or repo .env), so a located FRD
            cannot be processed. Existing STTMs and past runs remain viewable.
          </AlertDescription>
        </Alert>
      )}

      <div>
        <h2 className="eyebrow mb-2">Select FRD</h2>
        <p className="text-sm text-muted-foreground mb-3">
          Name the FRD and the app finds it in{" "}
          <span className="mono-id">
            {spConfig.data?.site ?? "SharePoint"}/{spConfig.data?.library ?? ""}
            {spConfig.data?.frd_folder ? `/${spConfig.data.frd_folder}` : ""}
          </span>
          . If a mapping for it has already been published, it is presented as-is; otherwise the pipeline
          runs on the document.
        </p>
        <div className="flex gap-2">
          <input
            className="flex-1 border border-input rounded-md px-3 py-2 text-sm bg-card"
            placeholder="FRD document name, e.g. Community Risk FRD"
            value={name}
            disabled={!configured}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          <Button disabled={!configured || locate.isPending || !name.trim()} onClick={() => submit()}>
            {locate.isPending ? "Locating…" : "Locate FRD"}
          </Button>
        </div>
      </div>

      {locate.isError && (
        <Alert variant="destructive">
          <AlertDescription>{(locate.error as Error).message}</AlertDescription>
        </Alert>
      )}

      {locate.data?.status === "candidates" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">
            No exact match — did you mean one of these? Nothing is picked automatically.
          </p>
          {locate.data.candidates.map((c) => (
            <Card key={c.item_id}>
              <CardContent className="py-2 flex items-center justify-between gap-4">
                <div>
                  <span className="mono-id text-sm">{c.name}</span>{" "}
                  <span className="text-xs text-muted-foreground">
                    {(c.size_bytes / 1024).toFixed(0)} KB · modified {c.modified.slice(0, 10)}
                  </span>
                </div>
                <Button variant="outline" onClick={() => submit(c.name)}>
                  Use this one
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

    </div>
  );
}

/**
 * An FRD that already has a published STTM: present it, full stop. No
 * regeneration, and deliberately NO publish control — the workbook is
 * already in the SharePoint output folder, which is the system of record.
 */
function ExistingSttmView({
  frd,
  sttm,
  onBack,
}: {
  frd: SharePointItemInfo;
  sttm: SharePointItemInfo;
  onBack: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button variant="ghost" onClick={onBack}>
          ← Select FRD
        </Button>
      </div>
      <Card className="border-2">
        <CardHeader>
          <CardTitle>This FRD already has a published STTM</CardTitle>
          <CardDescription>
            <span className="mono-id">{frd.name}</span> is mapped by{" "}
            <span className="mono-id">{sttm.name}</span>, published to the SharePoint output folder
            (modified {sttm.modified.slice(0, 10)}, {(sttm.size_bytes / 1024).toFixed(0)} KB). It is
            presented as-is — nothing was regenerated.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex gap-2">
            <Button asChild>
              <a href={sharePointSttmUrl(sttm.item_id)} download>
                Download the STTM (.xlsx)
              </a>
            </Button>
            {sttm.web_url && (
              <Button variant="outline" asChild>
                <a href={sttm.web_url} target="_blank" rel="noreferrer">
                  Open in SharePoint ↗
                </a>
              </Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            This mapping already lives in SharePoint, so there is nothing to publish. If the FRD has been
            revised and needs a fresh mapping, remove or rename the published workbook in the output folder
            first — the app will then treat it as unmapped.
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
  const isJob = configQuery.data?.mode === "databricks";
  const duration =
    est && est.seconds >= 120 ? `~${Math.round(est.seconds / 60)} min` : `roughly ${est?.seconds}s`;

  return (
    <Card className="border-2">
      <CardHeader>
        <CardTitle>Start a live, billed run?</CardTitle>
        <CardDescription>
          <span className="mono-id">{doc.name}</span> will be processed end to end{" "}
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
            run-scoped location.
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
        <span className="text-xs text-muted-foreground mono-id">run {runId}</span>
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
