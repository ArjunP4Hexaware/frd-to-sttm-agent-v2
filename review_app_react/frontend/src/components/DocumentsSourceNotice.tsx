import {
  CONNECTED_EMPTY_DETAIL,
  CONNECTED_EMPTY_HEADLINE,
  DOCUMENTS_UNREACHABLE_HEADLINE,
  NO_DOCUMENTS_MESSAGE,
  documentsUnreachableDetail,
} from "../lib/displayText";

/**
 * The two mutually exclusive answers to "why is the document list empty?".
 *
 * They live in one file, side by side, on purpose. The recurring defect this
 * component exists to kill is that an empty corpus and a dead backend both
 * rendered as the same quiet blank panel, so the app looked healthy and
 * empty while nothing was actually working. Keeping both states in one place
 * makes it structurally obvious that they must never converge -- different
 * copy, different colour, different border, different icon.
 *
 * Neither state is ever a fallback for the other, and neither is ever the
 * default: App.tsx renders `Connected` only on `isSuccess` (a real 200 whose
 * body named the scanned directory) and `Unreachable` only on `isError`.
 * A request still in flight renders neither.
 */

/** Success path. Only reachable when GET /api/documents returned 200. */
export function DocumentsConnectedEmpty({ contractsDir }: { contractsDir: string }) {
  return (
    <div
      role="status"
      className="rounded-md border border-emerald-600/40 bg-emerald-500/5 p-4 flex flex-col gap-2"
    >
      <div className="flex items-center gap-2">
        {/* Filled dot, not an outline: reads as "live" at a glance from the
            back of a room, where the copy itself is unreadable. */}
        <span aria-hidden="true" className="size-2 rounded-full bg-emerald-500 shrink-0" />
        <p className="text-sm font-medium text-emerald-700 dark:text-emerald-400">
          {CONNECTED_EMPTY_HEADLINE}
        </p>
      </div>
      <p className="text-sm text-muted-foreground">{CONNECTED_EMPTY_DETAIL}</p>
      {/* The resolved directory the backend actually globbed, verbatim from
          the response body (DocumentListResponse.contracts_dir) -- not
          reconstructed here, so it cannot drift from what was scanned. */}
      <code className="mono-id text-xs break-all rounded bg-muted px-2 py-1">{contractsDir}</code>
      <p className="text-sm text-muted-foreground">{NO_DOCUMENTS_MESSAGE}</p>
    </div>
  );
}

/**
 * Failure path. Only reachable when the query errored -- a refused
 * connection, a timeout, or any non-2xx (including the backend's own 503
 * for an unreadable contracts directory, whose `detail` names the offending
 * path and arrives here as `message`).
 */
export function DocumentsUnreachable({ message }: { message: string }) {
  // Read at render time rather than hardcoded: reports the port the browser
  // genuinely talked to, which stays correct when the app is served from
  // somewhere other than the usual demo port.
  const port = typeof window === "undefined" ? "" : window.location.port;

  return (
    <div
      role="alert"
      className="rounded-md border-2 border-destructive bg-destructive/10 p-4 flex flex-col gap-2"
    >
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className="text-destructive text-lg leading-none">
          &#9888;
        </span>
        <p className="text-sm font-semibold text-destructive">{DOCUMENTS_UNREACHABLE_HEADLINE}</p>
      </div>
      <p className="text-sm text-destructive">{documentsUnreachableDetail(port, message)}</p>
    </div>
  );
}
