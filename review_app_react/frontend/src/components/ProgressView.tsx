import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription, AlertTitle, Progress, Spinner } from "@databricks/appkit-ui/react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { useRunStatus } from "../api";
import { RUN_STAGE_LABEL, displayLabel, documentDisplayName } from "../lib/displayText";
import type { RunStatusValue } from "../types";

const STAGE_ORDER: RunStatusValue[] = [
  "pending", "running_ingest", "running_extract", "running_contract_build", "running_sttm_render",
];

interface Props {
  runId: string;
  onGated: () => void;
  onDone: () => void;
}

/** Plain interval poll of GET /status (see useRunStatus) -- no websockets.
 * Reacts to the three terminal statuses via effects rather than rendering
 * branchy JSX inline, so each transition fires exactly once. */
export function ProgressView({ runId, onGated, onDone }: Props) {
  const { data, isLoading, error } = useRunStatus(runId);
  const queryClient = useQueryClient();
  const firedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!data || firedFor.current === `${runId}:${data.status}`) return;
    if (data.status === "gated") {
      firedFor.current = `${runId}:${data.status}`;
      onGated();
    } else if (data.status === "done") {
      firedFor.current = `${runId}:${data.status}`;
      // A completed run promotes its contract into the shared contracts
      // directory (orchestration.py's _promote_contract), so the document
      // list -- fetched once at page load and the sole source of the header
      // metric tiles -- is stale the moment this status lands. Refetch it so
      // the tiles pick up the finished run's items without a page reload.
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      onDone();
    }
  }, [data, runId, onGated, onDone, queryClient]);

  if (isLoading || !data) {
    return (
      <Card>
        <CardContent className="py-6 flex items-center gap-3">
          <Spinner /> <span className="text-sm text-muted-foreground">Loading run status…</span>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Couldn't load run status</AlertTitle>
        <AlertDescription>{String(error)}</AlertDescription>
      </Alert>
    );
  }

  if (data.status === "error") {
    return (
      <Alert variant="destructive">
        <AlertTitle>Pipeline run failed</AlertTitle>
        <AlertDescription className="break-words whitespace-pre-wrap font-mono text-xs">
          {data.error ?? "Unknown error."}
        </AlertDescription>
      </Alert>
    );
  }

  const stageIndex = STAGE_ORDER.indexOf(data.status);
  const pct = stageIndex >= 0 ? Math.round(((stageIndex + 1) / STAGE_ORDER.length) * 100) : 100;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Spinner className="size-4" /> {displayLabel(RUN_STAGE_LABEL, data.status)}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Progress value={pct} />
        {/* Title mapped, uploaded filename left literal -- same split as
            SummaryHeader. `mono-id` stays on the filename only. A live upload
            mints a doc_id absent from DOCUMENT_DISPLAY_NAMES, which is exactly
            the case documentDisplayName's raw-id fallback covers. */}
        <p className="text-xs text-muted-foreground">
          {documentDisplayName(data.doc_id)} (<span className="mono-id">{data.source_filename}</span>)
        </p>
      </CardContent>
    </Card>
  );
}
