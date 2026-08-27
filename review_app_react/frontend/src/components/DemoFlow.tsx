import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import {
  corpusDictionaryUrl,
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
 *  - a MAPPED FRD → its approved STTM is presented, and that is all: since
 *    the eligibility rule (2026-08-27) a mapped FRD is NOT generatable, the
 *    run endpoint refuses it server-side, and the "regenerate anyway" control
 *    is gone. The approved workbook is the system of record.
 *  - an FRD with NO VENDOR DICTIONARY → listed, with what it is waiting on,
 *    but not selectable: without one the source columns cannot be grounded,
 *    and that is not worth a billed call.
 */
type Phase =
  | { kind: "setup" }
  | { kind: "existing"; frd: CorpusFrd }
  | { kind: "confirm"; frd: CorpusFrd }
  | { kind: "running"; runId: string }
  | { kind: "results"; setId: string; docId: string };

/**
 * A finished run's results are addressable: `?set=<artifact set>&doc=<doc id>`.
 *
 * Added 2026-08-27 while cataloguing the app's screens, because the catalogue
 * turned up a real gap rather than a documentation one: the results view was
 * reachable ONLY as the tail of a run in this browser tab. Navigate away, or
 * come back after an App restart, and a completed — billed — run could not be
 * looked at again, even though its artifact set is sitting in the volume. The
 * URL is now the handle, so a reviewer can bookmark a result, send it to a
 * colleague, or reopen it tomorrow.
 *
 * Deliberately NOT a router: one query pair, read once on mount and written
 * when the results phase is entered. Nothing else in the app is addressable,
 * and the picker stays the default view for a bare URL.
 */
function resultsFromUrl(): Phase | null {
  if (typeof window === "undefined") return null;
  const q = new URLSearchParams(window.location.search);
  const setId = q.get("set");
  const docId = q.get("doc");
  return setId && docId ? { kind: "results", setId, docId } : null;
}

export function DemoFlow() {
  const [phase, setPhase] = useState<Phase>(() => resultsFromUrl() ?? { kind: "setup" });

  // Keep the address bar in step with the phase, without a history entry per
  // click: a deep-linked result is shareable, and stepping back to the picker
  // clears it so a refresh does not bounce the reviewer into an old run.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (phase.kind === "results") {
      url.searchParams.set("set", phase.setId);
      url.searchParams.set("doc", phase.docId);
    } else {
      url.searchParams.delete("set");
      url.searchParams.delete("doc");
    }
    window.history.replaceState(null, "", url.toString());
  }, [phase]);

  return (
    <div className="flex flex-col gap-6">
      {phase.kind === "setup" && (
        <MappingSetup
          onExisting={(frd) => setPhase({ kind: "existing", frd })}
          onGenerate={(frd) => setPhase({ kind: "confirm", frd })}
        />
      )}
      {phase.kind === "existing" && (
        <ExistingSttmView frd={phase.frd} onBack={() => setPhase({ kind: "setup" })} />
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
  // Runs execute in the Databricks workspace only (the local subprocess
  // runner was removed 2026-08-27). A local-mode backend serves the corpus,
  // past runs and review; it cannot start one, and the backend says so with
  // a 400 if asked. No credential is checked here: the job's extract task
  // authenticates with the workspace credential.
  const canRun = mode === "databricks";
  const configured = spConfig.data?.configured ?? false;

  return (
    <div className="flex flex-col gap-4">
      {/* Backend configuration state — context, not the work. One tight line
          each; they were two full alert boxes owning the first screen. */}
      {!configured && spConfig.isSuccess && (
        <div className="acfc-notice">
          <b>No document source</b>
          <span>
            Neither a documents folder (STTM_LOCAL_SOURCE_DIR) nor a SharePoint tenant is configured on
            this backend, so nothing can be synced from here. FRDs already in the volumes can still be
            listed and run; “Rebuild index” re-pairs from them.
          </span>
        </div>
      )}
      {!canRun && configQuery.isSuccess && (
        <div className="acfc-notice acfc-notice--warn">
          <b>Runs execute in the Databricks workspace</b>
          <span>
            This backend is in local mode, which serves the corpus, past runs and review only. Start a
            run from the deployed App.
          </span>
        </div>
      )}

      <div>
        <p className="eyebrow-blue mb-1">Select FRD</p>
        <h2 className="acfc-section-title text-xl mb-1.5">Pick the feed to map</h2>
        <p className="text-sm text-muted-foreground max-w-3xl">
          The list below is what the last sync landed in the corpus. An FRD can be drafted only when it
          has a matching vendor data dictionary and does not already have an approved STTM — everything
          else is listed with what it is waiting on, so nothing quietly disappears.
        </p>
      </div>

      <CorpusPanel onGenerate={onGenerate} onExisting={onExisting} canRun={canRun} />
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
function ExistingSttmView({ frd, onBack }: { frd: CorpusFrd; onBack: () => void }) {
  const reference = frd.reference ?? "";
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button variant="ghost" onClick={onBack}>
          ← Select FRD
        </Button>
      </div>
      <Card className="border-l-[3px] border-l-[var(--brand-blue)]">
        <CardHeader>
          <p className="eyebrow-blue">Already mapped</p>
          <CardTitle className="acfc-section-title text-lg">This FRD already has an STTM</CardTitle>
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
          {/* The source side's grounding, on the screen where regeneration is
              decided. Without a dictionary a regenerated draft would take its
              source columns from the matched TEMPLATE — the implicit borrow
              the third input exists to remove — so the reviewer has to see
              this before spending a run, not after. */}
          <div className={`acfc-notice${frd.has_dictionary ? "" : " acfc-notice--warn"}`}>
            <b>{frd.has_dictionary ? "Vendor dictionary" : "No vendor dictionary"}</b>
            <span>
              {frd.has_dictionary ? (
                <>
                  <span className="mono-id">{frd.dictionary}</span> describes{" "}
                  {frd.dictionary_fields} source column{frd.dictionary_fields === 1 ? "" : "s"} across{" "}
                  {frd.dictionary_files} file{frd.dictionary_files === 1 ? "" : "s"}
                  {frd.dictionary_problems > 0
                    ? ` — with ${frd.dictionary_problems} gap(s): ${frd.dictionary_problem_kinds.join(", ")}. Those columns will be gated, not filled.`
                    : ". The source side of a regenerated draft would be grounded in it."}
                </>
              ) : (
                <>
                  Nothing grounds the source columns for this feed. A regenerated draft would render the
                  frame and gate every source column rather than invent one. Ask the vendor for{" "}
                  <span className="mono-id">DICT_&lt;feed&gt;.xlsx</span> and name it in the FRD’s
                  Structural Metadata › Source Data Dictionary row.
                </>
              )}
            </span>
          </div>
          <div className="flex gap-2 flex-wrap">
            <Button asChild>
              <a href={corpusReferenceUrl(reference)} download>
                Download the STTM (.xlsx)
              </a>
            </Button>
            {frd.has_dictionary && (
              <Button variant="outline" asChild>
                <a href={corpusDictionaryUrl(frd.dictionary!)} download>
                  Download the dictionary (.xlsx)
                </a>
              </Button>
            )}
            {frd.reference_web_url && (
              <Button variant="outline" asChild>
                <a href={frd.reference_web_url} target="_blank" rel="noreferrer">
                  Open in SharePoint ↗
                </a>
              </Button>
            )}
          </div>
          {/* "Regenerate this mapping anyway" was REMOVED 2026-08-27 with the
              eligibility rule: an FRD that already has an approved STTM is no
              longer generatable, and the run endpoint refuses it server-side,
              so a button here would only produce a 400. It existed for the
              2026-08-24 demo, where regenerating an already-mapped FRD was the
              only way to show the pipeline end to end — and the accuracy figure
              it produced was self-referential anyway, because the approved
              workbook was also the template. */}
          <p className="text-xs text-muted-foreground pt-1 border-t">
            The approved workbook stays the system of record and is not regenerated. To change it,
            edit it and upload it to the STTM folder yourself; the next sync re-pairs it with this
            FRD.
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
  const provider = configQuery.data?.provider ?? "";
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
            <>
              using{" "}
              {provider === "databricks"
                ? "this workspace's Databricks Foundation Model APIs"
                : "the Anthropic API"}
            </>
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
