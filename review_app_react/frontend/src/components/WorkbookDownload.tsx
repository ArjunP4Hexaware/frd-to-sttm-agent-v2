import { Button } from "@databricks/appkit-ui/react";
import {
  WORKBOOK_DOWNLOAD_LABEL,
  WORKBOOK_MISSING_DETAIL,
  WORKBOOK_MISSING_HEADLINE,
  workbookDownloadHint,
} from "../lib/displayText";

/**
 * Download control for an EXISTING document's rendered STTM workbook.
 *
 * Two states, both visible, neither ever a silent no-op:
 *
 *  - available  -> a real download link to
 *                  GET /api/documents/{doc_id}/workbook
 *  - unavailable-> an explicit "no workbook generated" notice
 *
 * The unavailable state is a rendered sentence, NOT a hidden button and NOT
 * a greyed-out one. A missing control tells the reader nothing about why it
 * is missing, and a disabled button without explanation reads as an app
 * defect rather than as a fact about this document. The absence has a cause
 * -- stage 04 never ran for this doc_id -- and saying so is the difference
 * between "broken" and "nothing to download yet", which is the same
 * empty-vs-broken distinction this app draws on the document list.
 *
 * Availability comes from DocumentDetail.workbook_available, resolved by the
 * backend against the exact doc_id. It is a boolean and nothing more: no
 * cell counts, no eval percentage, no coverage figure is shown on this path.
 *
 * A plain <a download> rather than a fetch+blob: the browser's own download
 * handles the Content-Disposition filename the endpoint already sets, and
 * there is no intermediate state that could fail silently.
 */
export function WorkbookDownload({
  docId,
  available,
}: {
  docId: string;
  available: boolean;
}) {
  if (!available) {
    return (
      <div
        role="status"
        className="rounded-md border border-flag-bright/40 bg-flag-bright/5 p-3 flex flex-col gap-1"
      >
        <p className="text-sm font-medium text-flag">
          {WORKBOOK_MISSING_HEADLINE}
        </p>
        <p className="text-sm text-muted-foreground">{WORKBOOK_MISSING_DETAIL}</p>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {/* asChild keeps Button's styling on a real anchor, so this stays a
          genuine browser download rather than a JS-mediated one. */}
      <Button asChild>
        <a href={`/api/documents/${encodeURIComponent(docId)}/workbook`} download>
          {WORKBOOK_DOWNLOAD_LABEL}
        </a>
      </Button>
      <span className="text-sm text-muted-foreground">{workbookDownloadHint(docId)}</span>
    </div>
  );
}
