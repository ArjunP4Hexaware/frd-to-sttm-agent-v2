import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button } from "@databricks/appkit-ui/react";
import { useContinueRun, useRunDocument } from "../api";
import { useRunReviewProgress } from "../lib/runReviewProgress";
import { SummaryHeader } from "./SummaryHeader";
import { RunGatedItemList } from "./RunGatedItemList";

interface Props {
  runId: string;
  onContinue: () => void;
}

/** Reuses SummaryHeader unmodified (it only ever reads a DocumentDetail-shaped
 * prop) plus RunGatedItemList (see that file for why it's a sibling of
 * GatedItemList rather than that file itself). */
export function ReviewScreen({ runId, onContinue }: Props) {
  const { data, isLoading, error } = useRunDocument(runId);
  const continueRun = useContinueRun();
  const [continueError, setContinueError] = useState<string | null>(null);
  const { setProgress } = useRunReviewProgress();

  // Publish this run's review counts so the header tiles (CorpusMetricRow)
  // agree with the card below them -- see lib/runReviewProgress.tsx for why
  // GET /api/documents alone cannot. Saving a resolution invalidates the run
  // document query (useSubmitRunResolution), so `data` refreshes and the
  // published resolved count tracks it. Cleared on unmount: once this screen
  // is gone there is no card for the tiles to disagree with.
  useEffect(() => {
    if (!data) return;
    setProgress({
      itemCount: data.items.length,
      resolvedCount: data.items.filter((it) => it.resolution !== null).length,
    });
  }, [data, setProgress]);
  useEffect(() => () => setProgress(null), [setProgress]);

  const handleContinue = async () => {
    setContinueError(null);
    try {
      await continueRun.mutateAsync(runId);
      onContinue();
    } catch (e) {
      setContinueError(e instanceof Error ? e.message : String(e));
    }
  };

  if (isLoading || !data) {
    return <p className="text-muted-foreground">Loading gated items…</p>;
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Couldn't load this run's contract</AlertTitle>
        <AlertDescription>{String(error)}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <SummaryHeader detail={data} />
      <RunGatedItemList runId={runId} detail={data} />
      {continueError && (
        <Alert variant="destructive">
          <AlertTitle>Couldn't continue this run</AlertTitle>
          <AlertDescription className="break-words whitespace-pre-wrap">{continueError}</AlertDescription>
        </Alert>
      )}
      <div className="flex justify-end border-t pt-4">
        <Button onClick={handleContinue} disabled={continueRun.isPending}>
          {continueRun.isPending ? "Continuing…" : "Continue to render"}
        </Button>
      </div>
    </div>
  );
}
