import type { DocumentDetail, ResolutionSubmission } from "../types";
import { useSubmitRunResolution } from "../api";
import { GatedItemCard } from "./GatedItemCard";

interface Props {
  runId: string;
  detail: DocumentDetail;
}

/**
 * Run-scoped sibling of GatedItemList (same mapping, same GatedItemCard
 * reuse) -- kept as a separate file rather than parameterizing
 * GatedItemList itself, so the original shared-documents review flow's
 * component stays byte-for-byte unmodified. The only difference from
 * GatedItemList is which mutation hook backs `onSubmit`
 * (useSubmitRunResolution, hitting /api/runs/{run_id}/resolutions instead
 * of /api/documents/{doc_id}/resolutions) -- everything else, including
 * the doc/run + ambiguity-id composite key rationale, is identical.
 */
export function RunGatedItemList({ runId, detail }: Props) {
  const submitResolution = useSubmitRunResolution(runId);

  if (detail.items.length === 0) {
    return <p className="text-muted-foreground">No gated ambiguities in this contract — nothing to review.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {detail.items.map((item) => (
        <GatedItemCard
          key={`${runId}::${item.id}`}
          item={item}
          onSubmit={(payload) => {
            const submission: ResolutionSubmission = {
              ambiguity_id: item.id,
              kind: item.kind,
              candidates_snapshot: item.candidates,
              ...payload,
            };
            return submitResolution.mutateAsync(submission);
          }}
        />
      ))}
    </div>
  );
}
