import { Badge } from "./ui/badge";
import { Card, CardContent } from "./ui/card";
import { GATE_STATUS_LABEL, GATE_STATUS_VARIANT, displayLabel, documentDisplayName } from "../lib/displayText";
import type { DocumentSummary } from "../types";

interface Props {
  documents: DocumentSummary[];
  selectedDocId: string | null;
  onSelect: (docId: string) => void;
}

export function DocumentPicker({ documents, selectedDocId, onSelect }: Props) {
  return (
    <div className="flex flex-col gap-2">
      {documents.map((doc) => {
        const selected = doc.doc_id === selectedDocId;
        return (
          <Card
            key={doc.doc_id}
            onClick={() => onSelect(doc.doc_id)}
            className={`cursor-pointer transition-colors ${selected ? "border-primary ring-1 ring-primary" : "hover:bg-accent"}`}
          >
            <CardContent className="py-3">
              <div className="flex items-center justify-between gap-2">
                {/* Title only. doc.doc_id stays raw in the key, the selection
                    comparison and the onSelect payload above -- it is the
                    primary key everywhere except this one span. */}
                <span className="text-sm font-medium truncate">{documentDisplayName(doc.doc_id)}</span>
                <Badge variant={GATE_STATUS_VARIANT[doc.status] ?? "outline"}>
                  {displayLabel(GATE_STATUS_LABEL, doc.status)}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {doc.feed_count} feed{doc.feed_count === 1 ? "" : "s"} · {doc.resolved_count}/{doc.item_count} resolved
              </p>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
