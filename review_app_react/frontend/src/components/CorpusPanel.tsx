import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "./ui/badge";
import { Card, CardContent } from "./ui/card";
import {
  useCorpusConfig,
  useCorpusFrds,
  useCorpusSummary,
  useCorpusSync,
  useSharePointConfig,
} from "../demoApi";
import type { CorpusFrd } from "../demoApi";

/**
 * The corpus panel IS the picker (2026-08-22, docs/TEMPLATE_ARCHITECTURE.md):
 *
 * - "FRDs without an STTM" — the work queue. "Generate STTM" hands the FRD
 *   to the billed-run gate.
 * - "FRDs already mapped" — each with its reference STTM and how it was
 *   paired (exact name / similarity + score). "View STTM" presents the
 *   approved workbook; regeneration is a deliberate extra step there.
 * - the sync controls: "Sync now" (SharePoint → volumes → index; the
 *   scheduled job does this on its own every tick — this is the on-demand
 *   trigger) and "Rebuild index" (index from what the volumes hold, no
 *   network). Both two-step, because they rewrite the volumes/index; zero
 *   AI calls, hence no cost copy.
 *
 * The list is read from the corpus index only — never from SharePoint on
 * the request path — so what the reviewer sees is exactly what Unity
 * Catalog holds.
 */
export function CorpusPanel({
  onGenerate,
  onExisting,
  canRun,
}: {
  onGenerate: (frd: CorpusFrd) => void;
  onExisting: (frd: CorpusFrd) => void;
  canRun: boolean;
}) {
  const spConfig = useSharePointConfig();
  const corpusConfig = useCorpusConfig();
  const summary = useCorpusSummary();
  const frds = useCorpusFrds(summary.data?.built ?? false);
  const sync = useCorpusSync();
  const [confirming, setConfirming] = useState<null | "sync" | "reindex">(null);
  const queryClient = useQueryClient();

  const syncState = summary.data?.sync;
  const running = syncState?.state === "running";

  // When a running sync lands, refresh the list (the summary already polls).
  useEffect(() => {
    if (syncState && syncState.state !== "running") {
      queryClient.invalidateQueries({ queryKey: ["demo", "corpus", "frds"] });
    }
  }, [syncState?.state, syncState?.finished_at, queryClient, syncState]);

  function start(mode: "sync" | "reindex") {
    setConfirming(null);
    sync.mutate(mode, {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: ["demo", "corpus"] }),
    });
  }

  const built = summary.data?.built ?? false;
  const configured = spConfig.data?.configured ?? false;
  const canSync = corpusConfig.data?.available ?? false;
  const unmapped = (frds.data?.frds ?? []).filter((f) => !f.paired);
  const mapped = (frds.data?.frds ?? []).filter((f) => f.paired);

  return (
    <div className="flex flex-col gap-4">
      {built && summary.data && (
        <div className="grid grid-cols-4 gap-2">
          <Stat label="FRDs" value={summary.data.n_frds} />
          <Stat label="Approved STTMs" value={summary.data.n_references} />
          <Stat label="Mapped pairs" value={summary.data.n_pairs} />
          <Stat label="Awaiting an STTM" value={summary.data.n_unmapped} />
        </div>
      )}

      {!built && summary.isSuccess && !running && (
        <p className="text-sm text-muted-foreground">
          Nothing has been synced yet. The scheduled sync (or “Sync now”) pulls every FRD and approved STTM
          from the SharePoint library into Unity Catalog, pairs them, and lists them here. No AI calls are
          made.
        </p>
      )}

      {built && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">FRDs without an STTM</h3>
          {unmapped.length === 0 && (
            <p className="text-sm text-muted-foreground">Every FRD in the corpus has an STTM.</p>
          )}
          {unmapped.map((f) => (
            <Card key={f.doc_id}>
              <CardContent className="py-2 flex items-center justify-between gap-4">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="mono-id text-sm truncate">{f.name}</span>
                  <Badge variant="warning">no STTM yet</Badge>
                  {f.web_url && (
                    <a
                      href={f.web_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs underline text-muted-foreground"
                    >
                      SharePoint ↗
                    </a>
                  )}
                </div>
                <Button onClick={() => onGenerate(f)} disabled={!canRun || !f.runnable}>
                  Generate STTM
                </Button>
              </CardContent>
            </Card>
          ))}
        </section>
      )}

      {built && mapped.length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">FRDs already mapped</h3>
          {mapped.map((f) => (
            <Card key={f.doc_id}>
              <CardContent className="py-2 flex items-center justify-between gap-4">
                <div className="flex items-center gap-2 min-w-0 flex-wrap">
                  <span className="mono-id text-sm truncate">{f.name}</span>
                  <Badge variant={f.confidence === "high" ? "success" : "secondary"}>
                    {f.matched_by === "name" ? "name match" : `${Math.round((f.score ?? 0) * 100)}% match`} ·{" "}
                    {f.reference}
                  </Badge>
                </div>
                <Button variant="outline" onClick={() => onExisting(f)}>
                  View STTM
                </Button>
              </CardContent>
            </Card>
          ))}
        </section>
      )}

      {syncState?.state === "running" && (
        <Alert>
          <AlertTitle className="flex items-center gap-2">
            <Spinner /> {syncState.mode === "reindex" ? "Rebuilding the index…" : "Syncing from SharePoint…"}
          </AlertTitle>
          <AlertDescription>
            {syncState.run_page_url ? (
              <a href={syncState.run_page_url} target="_blank" rel="noreferrer" className="underline">
                View the sync job run in Databricks ↗
              </a>
            ) : (
              "No AI calls are made."
            )}
          </AlertDescription>
        </Alert>
      )}
      {syncState?.state === "failed" && (
        <Alert variant="destructive">
          <AlertTitle>Sync failed</AlertTitle>
          <AlertDescription>{syncState.error}</AlertDescription>
        </Alert>
      )}
      {sync.isError && (
        <Alert variant="destructive">
          <AlertDescription>{(sync.error as Error).message}</AlertDescription>
        </Alert>
      )}
      {syncState?.state === "done" && Array.isArray(syncState.summary?.skipped) &&
        (syncState.summary.skipped as { name: string; error: string }[]).length > 0 && (
          <Alert>
            <AlertTitle>Some library files were skipped</AlertTitle>
            <AlertDescription>
              {(syncState.summary.skipped as { name: string; error: string }[])
                .map((s) => `${s.name}: ${s.error}`)
                .join("; ")}
            </AlertDescription>
          </Alert>
        )}

      <div className="flex items-center gap-2 flex-wrap">
        {confirming === null ? (
          <>
            <Button
              variant="outline"
              disabled={running || sync.isPending || !canSync}
              onClick={() => setConfirming("sync")}
              title={canSync ? undefined : "SharePoint is not configured on this backend"}
            >
              {running && syncState?.mode === "sync" ? "Syncing…" : "Sync from SharePoint now…"}
            </Button>
            <Button
              variant="ghost"
              disabled={running || sync.isPending}
              onClick={() => setConfirming("reindex")}
            >
              Rebuild index…
            </Button>
          </>
        ) : confirming === "sync" ? (
          <>
            <span className="text-sm text-muted-foreground">
              Pulls new and changed FRDs and STTMs from{" "}
              <span className="mono-id">
                {spConfig.data?.site}/{spConfig.data?.library}
              </span>{" "}
              into Unity Catalog and re-pairs them (no AI calls). The scheduled sync does this on its own;
              this runs it now. Continue?
            </span>
            <Button onClick={() => start("sync")}>Sync now</Button>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <span className="text-sm text-muted-foreground">
              Rebuilds the pairing index from the FRDs and STTMs already in the volumes — no SharePoint
              access, no AI calls. Continue?
            </span>
            <Button onClick={() => start("reindex")}>Rebuild now</Button>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </>
        )}
        {built && summary.data?.generated_at && (
          <span className="text-xs text-muted-foreground mono-id">
            index {summary.data.generated_at.slice(0, 16).replace("T", " ")}
            {summary.data.synced_at ? ` · synced ${summary.data.synced_at.slice(0, 16).replace("T", " ")}` : ""}
          </span>
        )}
      </div>
      {configured && spConfig.data?.sttm_folder != null && (
        <p className="text-xs text-muted-foreground">
          Finished STTMs belong in{" "}
          <span className="mono-id">
            {spConfig.data.site}/{spConfig.data.library}
            {spConfig.data.sttm_folder ? `/${spConfig.data.sttm_folder}` : ""}
          </span>{" "}
          — upload yours there and the next sync pairs it with its FRD.
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="py-2 text-center">
        <div className="text-2xl font-semibold mono-id">{value}</div>
        <div className="text-sm text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}
