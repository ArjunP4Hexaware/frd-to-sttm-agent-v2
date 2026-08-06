import { Badge } from "./ui/badge";
import { GATE_STATUS_LABEL, GATE_STATUS_VARIANT, displayLabel, documentDisplayName } from "../lib/displayText";
import type { DocumentDetail } from "../types";

export function SummaryHeader({ detail }: { detail: DocumentDetail }) {
  const resolved = detail.items.filter((it) => it.resolution !== null).length;
  return (
    <div className="flex flex-col gap-1">
      <h1 className="text-2xl font-semibold">Items needing your review</h1>
      <p className="text-sm text-muted-foreground">
        {/* Title is prose now, so it drops `mono-id` -- that class is
            identifier styling (monospace + tabular-nums) and would render the
            mapped title as if it were still a raw key. The provenance
            filename beside it keeps `mono-id` and stays literal: what
            generated this contract is a fact about a file, not a label. */}
        <span>{documentDisplayName(detail.doc_id)}</span>
        {detail.generated_from_frd && <> — generated from <span className="mono-id">{detail.generated_from_frd}</span></>}
      </p>
      <div className="flex items-center gap-4 mt-2 text-sm">
        <span>
          Gate status:{" "}
          <Badge variant={GATE_STATUS_VARIANT[detail.status] ?? "outline"}>
            {displayLabel(GATE_STATUS_LABEL, detail.status)}
          </Badge>
        </span>
        <span>
          Feeds: <span className="mono-id">{detail.feed_count}</span>
        </span>
        <span>
          Gated items: <span className="mono-id">{detail.items.length}</span> (
          <span className="mono-id">{resolved}</span> resolved)
        </span>
      </div>
    </div>
  );
}
