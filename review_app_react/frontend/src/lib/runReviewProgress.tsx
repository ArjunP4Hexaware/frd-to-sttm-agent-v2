import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * Shared state reconciling the header metric tiles with the run review card.
 *
 * WHY THIS EXISTS: an in-flight upload run that gates on ambiguities keeps
 * its contract under `sttm_out_runs/<run_id>/` until the render stage
 * promotes it (orchestration.py's _promote_contract runs post-render only),
 * so GET /api/documents -- the sole source the CorpusMetricRow tiles sum --
 * cannot see the gated items the ReviewScreen card on the same page is
 * showing. The tiles said "Items needing review: 0" directly above a card
 * saying "Gated items: 1". Nothing here changes what counts as a gated item;
 * it only lets the tiles include the review the user is looking at.
 *
 * ReviewScreen publishes {itemCount, resolvedCount} from the run document it
 * renders (and clears on unmount); CorpusMetricRow adds those figures into
 * its sums. Once the run completes, ProgressView invalidates the
 * ["documents"] query, the promoted contract shows up in the list itself,
 * and this context is back to null -- so a run's items are never counted
 * twice.
 */
export interface RunReviewProgress {
  itemCount: number;
  resolvedCount: number;
}

interface RunReviewProgressState {
  progress: RunReviewProgress | null;
  setProgress: (progress: RunReviewProgress | null) => void;
}

const RunReviewProgressContext = createContext<RunReviewProgressState>({
  progress: null,
  setProgress: () => {},
});

export function RunReviewProgressProvider({ children }: { children: ReactNode }) {
  const [progress, setProgress] = useState<RunReviewProgress | null>(null);
  // setProgress is identity-stable (useState setter), so this memo only
  // changes when the published figures do.
  const value = useMemo(() => ({ progress, setProgress }), [progress]);
  return (
    <RunReviewProgressContext.Provider value={value}>{children}</RunReviewProgressContext.Provider>
  );
}

export function useRunReviewProgress(): RunReviewProgressState {
  return useContext(RunReviewProgressContext);
}
