import type { DocumentDetail, ResolutionSubmission } from "../types";
import { useSubmitResolution } from "../api";
import { GatedItemCard } from "./GatedItemCard";

interface Props {
  docId: string;
  detail: DocumentDetail;
}

export function GatedItemList({ docId, detail }: Props) {
  const submitResolution = useSubmitResolution(docId);

  if (detail.items.length === 0) {
    return <p className="text-muted-foreground">No gated ambiguities in this contract — nothing to review.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {detail.items.map((item) => (
        <GatedItemCard
          // Composite key: doc_id + ambiguity id, not array index. This is
          // the React-side equivalent of the `{doc_id}_item_{i}` Streamlit
          // widget-key fix -- see GatedItemCard's docstring. `item.id` is
          // itself stable across a pipeline re-run of unchanged inputs (see
          // notebooks/_models.py's GatedAmbiguity), so this is even safer
          // than the old text-based key it replaces.
          key={`${docId}::${item.id}`}
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
