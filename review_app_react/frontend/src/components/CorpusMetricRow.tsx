import {
  METRIC_LABEL_DOCUMENTS,
  METRIC_LABEL_FEEDS,
  METRIC_LABEL_ITEMS,
  METRIC_LABEL_REVIEWED,
} from "../lib/displayText";
import { useRunReviewProgress } from "../lib/runReviewProgress";
import type { DocumentSummary } from "../types";

/**
 * Metric row for the four-beat spine's top block, sitting under the HEADER.
 *
 * Markup copied from demo_shell's metric tiles (the `<dl>` in
 * ../../../../demo_shell/frontend/src/components/ResultsView.tsx): `.eyebrow`
 * <dt> label over a `.mono-id` <dd> figure, which is where the tabular-nums
 * comes from -- `.mono-id` sets `font-variant-numeric: tabular-nums` in
 * index.css, so the digits stay column-aligned without a local class.
 *
 * EVERY FIGURE HERE IS SUMMED FROM API RESPONSES. `feed_count`,
 * `item_count` and `resolved_count` are fields on GET /api/documents
 * (DocumentSummary in backend/app.py); `Documents` is the array length. The
 * items/reviewed tiles additionally include the review counts of an active
 * gated run (useRunReviewProgress -- fed from GET /api/runs/{run_id}/document
 * by the ReviewScreen showing that run's cards on this same page), because a
 * gated run's contract is not promoted into the /api/documents scan until
 * after render. No tile is estimated, hardcoded, or derived from anything
 * off-screen.
 *
 * Two tiles that would have been natural here are deliberately absent, and
 * should stay absent until the API carries them:
 *
 *  - **Grounding pass rate.** The stage-3 grounding audit result (the
 *    "103/103 grounded" figure in STANDUP_NOTES.md) is written into the
 *    contract-build markdown report and never surfaced by any endpoint this
 *    screen calls. `advisory_grounding` exists only as a gated-item *kind*,
 *    which is a count of flags, not a pass rate.
 *  - **STTM cell-match / eval %.** Available only from
 *    GET /api/runs/{run_id}/coverage, which needs a run_id from an in-flight
 *    run in this process's memory (orchestration.py's RUNS dict, lost on
 *    restart). The landing screen has no run_id, so the number does not
 *    exist here. ResultsScreen's CoverageTile already shows it in the one
 *    place it is real -- immediately after a run.
 *
 * Renders nothing when there are no documents: a row of zeroes reads as a
 * broken app rather than as the genuine first-run state, and NO_DOCUMENTS_MESSAGE
 * already explains that state in words.
 */
export function CorpusMetricRow({ documents }: { documents: DocumentSummary[] }) {
  const { progress: runReview } = useRunReviewProgress();

  // Still renders nothing when there is nothing real to count -- but an
  // active gated run's items ARE real, so they keep the row alive even
  // before any contract has been promoted into the document list.
  if (documents.length === 0 && runReview === null) return null;

  const feeds = documents.reduce((n, d) => n + d.feed_count, 0);
  const items = documents.reduce((n, d) => n + d.item_count, 0) + (runReview?.itemCount ?? 0);
  const resolved =
    documents.reduce((n, d) => n + d.resolved_count, 0) + (runReview?.resolvedCount ?? 0);

  const tiles: { label: string; value: string }[] = [
    { label: METRIC_LABEL_DOCUMENTS, value: String(documents.length) },
    { label: METRIC_LABEL_FEEDS, value: String(feeds) },
    { label: METRIC_LABEL_ITEMS, value: String(items) },
    // Both halves are real returned numbers, shown as a fraction rather than
    // a percentage so nothing is rounded into existence.
    { label: METRIC_LABEL_REVIEWED, value: `${resolved} / ${items}` },
  ];

  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-3">
      {tiles.map((t) => (
        <div key={t.label} className="flex flex-col gap-0.5">
          <dt className="eyebrow">{t.label}</dt>
          <dd className="text-sm font-medium mono-id">{t.value}</dd>
        </div>
      ))}
    </dl>
  );
}
