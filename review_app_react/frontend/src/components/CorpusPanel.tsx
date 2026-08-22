import { useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "./ui/badge";
import { Card, CardContent } from "./ui/card";
import { useCorpusBootstrap, useCorpusFrds, useCorpusSummary, useSharePointConfig } from "../demoApi";

/**
 * The reference corpus panel (2026-08-22, docs/TEMPLATE_ARCHITECTURE.md):
 *
 * - a two-step "Sync corpus from SharePoint" control (bootstrap: downloads
 *   every FRD + reference STTM, pairs them deterministically, rebuilds the
 *   index — zero AI calls, hence no cost copy, but still confirm-gated
 *   because it rewrites the reference volume),
 * - the pairing summary, and
 * - the FRD list: paired documents show their reference + match score,
 *   unmapped ones are flagged. "Select" hands the document name to the SAME
 *   locate flow the free-text entry uses — one run path, no second one to
 *   keep in sync (an FRD whose STTM exists routes through the existing-STTM
 *   view, where regeneration is a deliberate extra step).
 *
 * Renders nothing when SharePoint is unconfigured, exactly like the picker.
 */
export function CorpusPanel({ onPick }: { onPick: (name: string) => void }) {
  const spConfig = useSharePointConfig();
  const summary = useCorpusSummary();
  const frds = useCorpusFrds(summary.data?.built ?? false);
  const bootstrap = useCorpusBootstrap();
  const [confirming, setConfirming] = useState(false);
  const queryClient = useQueryClient();

  if (!(spConfig.data?.configured ?? false)) return null;

  function runBootstrap() {
    setConfirming(false);
    bootstrap.mutate(undefined, {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: ["demo", "corpus"] }),
    });
  }

  const built = summary.data?.built ?? false;

  return (
    <div className="flex flex-col gap-3">
      <h2 className="eyebrow mb-0">Reference corpus</h2>

      {!built && summary.isSuccess && (
        <p className="text-sm text-muted-foreground">
          No corpus yet. Syncing downloads every FRD and reference STTM from the SharePoint library,
          pairs them deterministically, and makes every approved pair a template for new mappings.
          No AI calls are made.
        </p>
      )}

      {built && summary.data && (
        <div className="grid grid-cols-4 gap-2">
          <Stat label="FRDs" value={summary.data.n_frds} />
          <Stat label="Reference STTMs" value={summary.data.n_references} />
          <Stat label="Approved pairs" value={summary.data.n_pairs} />
          <Stat label="Unmapped FRDs" value={summary.data.n_unmapped} />
        </div>
      )}

      {built && frds.data && frds.data.frds.length > 0 && (
        <div className="flex flex-col gap-2">
          {frds.data.frds.map((f) => (
            <Card key={f.doc_id}>
              <CardContent className="py-2 flex items-center justify-between gap-4">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="mono-id text-sm truncate">{f.name}</span>
                  {f.paired ? (
                    <Badge variant={f.confidence === "high" ? "success" : "secondary"}>
                      paired · {f.reference} ({Math.round((f.score ?? 0) * 100)}%)
                    </Badge>
                  ) : (
                    <Badge variant="warning">unmapped — no STTM yet</Badge>
                  )}
                </div>
                <Button variant="outline" onClick={() => onPick(f.name)}>
                  {f.paired ? "Select" : "Generate STTM"}
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {bootstrap.isError && (
        <Alert variant="destructive">
          <AlertDescription>{(bootstrap.error as Error).message}</AlertDescription>
        </Alert>
      )}
      {bootstrap.isSuccess && bootstrap.data.skipped.length > 0 && (
        <Alert>
          <AlertTitle>Some library files were skipped</AlertTitle>
          <AlertDescription>
            {bootstrap.data.skipped.map((s) => `${s.name}: ${s.error}`).join("; ")}
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-center gap-2">
        {!confirming ? (
          <Button
            variant="outline"
            disabled={bootstrap.isPending}
            onClick={() => setConfirming(true)}
          >
            {bootstrap.isPending
              ? "Syncing…"
              : built
                ? "Re-sync corpus from SharePoint…"
                : "Sync corpus from SharePoint…"}
          </Button>
        ) : (
          <>
            <span className="text-sm text-muted-foreground">
              Downloads the library&apos;s FRDs and STTMs into the reference volume and rebuilds the
              corpus index (no AI calls). Continue?
            </span>
            <Button onClick={runBootstrap}>Sync now</Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        )}
        {built && summary.data?.generated_at && (
          <span className="text-xs text-muted-foreground mono-id">
            built {summary.data.generated_at.slice(0, 16).replace("T", " ")}
          </span>
        )}
      </div>
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
