import { useEffect, useRef, useState } from "react";
import { Button } from "@databricks/appkit-ui/react";
import { useDocumentDetail, useDocumentList } from "./api";
import { AgentHeader } from "./components/AgentHeader";
import { CorpusMetricRow } from "./components/CorpusMetricRow";
import { DocumentPicker } from "./components/DocumentPicker";
import { SummaryHeader } from "./components/SummaryHeader";
import { GatedItemList } from "./components/GatedItemList";
import { DemoFlow } from "./components/DemoFlow";
import { NewRunFlow } from "./components/NewRunFlow";
import { DocumentsConnectedEmpty, DocumentsUnreachable } from "./components/DocumentsSourceNotice";
import { WorkbookDownload } from "./components/WorkbookDownload";
import { documentDisplayName } from "./lib/displayText";
import { RunReviewProgressProvider } from "./lib/runReviewProgress";

type Tab = "documents" | "new-run" | "demo";

export default function App() {
  // Plain useState tab toggle, not react-router -- same single-page,
  // useState-driven pattern the rest of this app already follows.
  const [tab, setTab] = useState<Tab>("documents");

  // Shares the ["documents"] cache entry with ExistingDocuments below, so
  // this is the same request, not a second one.
  const documentsQuery = useDocumentList();

  // First-run affordance. A fresh clone ships no contracts -- the landing
  // list stays empty until a run completes through render and promotes its
  // contract (see orchestration.py's _promote_contract) -- so opening on
  // "Existing documents" would show nothing but its empty-state line, with
  // the one useful action hidden behind an untouched tab.
  //
  // Two separate latches, because they guard two different mistakes:
  //  - autoSwitchSettled: fires the decision at most once per mount, on the
  //    first *successful* load. Without it, promotion refilling the list
  //    mid-session would re-run this and fight the user for the tab.
  //  - tabChosenByUser: a click during the initial fetch wins outright. The
  //    query resolving afterwards must not yank the tab out from under it.
  const autoSwitchSettled = useRef(false);
  const tabChosenByUser = useRef(false);

  useEffect(() => {
    if (autoSwitchSettled.current || tabChosenByUser.current) return;
    // Gated on isSuccess, so a FAILED list never triggers the auto-switch.
    // That matters more than it looks: bouncing to "New upload" on a failed
    // load would hide the error notice behind an untouched tab and reproduce
    // exactly the "looks healthy and empty" symptom -- the reader would see a
    // working-looking upload form and never learn the backend was down.
    if (!documentsQuery.isSuccess) return;
    // Latch on the first success whatever the outcome: a list that is
    // non-empty now must never be reconsidered if it later empties.
    autoSwitchSettled.current = true;
    if (documentsQuery.data.documents.length === 0) setTab("new-run");
  }, [documentsQuery.isSuccess, documentsQuery.data]);

  function selectTab(next: Tab) {
    tabChosenByUser.current = true;
    setTab(next);
  }

  return (
    /* Provider above both the metric row and the tab content, so a gated
       run's ReviewScreen (inside NewRunFlow) can publish its review counts
       to the CorpusMetricRow tiles in the header -- the reconciliation
       described in lib/runReviewProgress.tsx. */
    <RunReviewProgressProvider>
    <div className="max-w-5xl mx-auto p-6 flex flex-col gap-6">
      {/* Beat 1 of the shared four-beat spine (HEADER -> INPUT -> PROGRESS ->
          RESULTS), above the tab strip so it frames both tabs. The metric row
          is corpus-level, so it belongs with the header rather than inside
          either tab -- and it reads off the query documentsQuery already
          issued for the first-run latch below, so this adds no request. */}
      <div className="flex flex-col gap-4">
        <AgentHeader />
        {documentsQuery.data && <CorpusMetricRow documents={documentsQuery.data.documents} />}
        {/* Connection truth sits ABOVE the tab strip, not inside the Documents
            tab, because the empty-list case auto-switches the view to "New
            upload" (see the latch below). Rendered inside that tab, the
            confirmation would be invisible in exactly the situation it exists
            for: the reader would land on a working-looking upload form with no
            evidence the backend is reachable -- which is the "looks healthy and
            empty" failure this is meant to end. Corpus-level state, so it lives
            with the header alongside the metric row.

            isError and isSuccess-with-zero are mutually exclusive; an in-flight
            request renders neither. */}
        {documentsQuery.isError && (
          <DocumentsUnreachable message={(documentsQuery.error as Error).message} />
        )}
        {documentsQuery.isSuccess && documentsQuery.data.documents.length === 0 && (
          <DocumentsConnectedEmpty contractsDir={documentsQuery.data.contracts_dir} />
        )}
      </div>
      <div className="flex gap-2 border-b pb-2">
        <Button variant={tab === "documents" ? "default" : "ghost"} onClick={() => selectTab("documents")}>
          Existing documents
        </Button>
        <Button variant={tab === "new-run" ? "default" : "ghost"} onClick={() => selectTab("new-run")}>
          New upload
        </Button>
        <Button variant={tab === "demo" ? "default" : "ghost"} onClick={() => selectTab("demo")}>
          Client demo
        </Button>
      </div>
      {tab === "documents" ? <ExistingDocuments /> : tab === "new-run" ? <NewRunFlow /> : <DemoFlow />}
    </div>
    </RunReviewProgressProvider>
  );
}

function ExistingDocuments() {
  const documentsQuery = useDocumentList();
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);

  const documents = documentsQuery.data?.documents;
  const docId = selectedDocId ?? documents?.[0]?.doc_id ?? null;
  const detailQuery = useDocumentDetail(docId);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="eyebrow mb-2">Documents</h2>
        {/* The confirmed-empty and unreachable notices are rendered once,
            above the tab strip in App() -- deliberately NOT repeated here.
            What remains is the picker, gated on isSuccess so a failed load
            can never fall through to an empty-looking list. */}
        {documentsQuery.isLoading && <p className="text-muted-foreground">Loading documents…</p>}
        {documentsQuery.isSuccess && documents && documents.length > 0 && (
          <DocumentPicker documents={documents} selectedDocId={docId} onSelect={setSelectedDocId} />
        )}
      </div>

      {docId && (
        <div className="flex flex-col gap-4 border-t pt-6">
          {/* Rendered text only. The same `docId` goes to useDocumentDetail
              above and to GatedItemList below -- both raw, both identity. */}
          {detailQuery.isLoading && (
            <p className="text-muted-foreground">Loading {documentDisplayName(docId)}…</p>
          )}
          {detailQuery.isError && (
            <p className="text-destructive">Failed to load document: {String(detailQuery.error)}</p>
          )}
          {detailQuery.data && (
            <>
              <SummaryHeader detail={detailQuery.data} />
              {/* Existing-document path only. ResultsScreen/NewRunFlow have
                  their own post-run download and are untouched. */}
              <WorkbookDownload
                docId={docId}
                available={detailQuery.data.workbook_available}
              />
              <GatedItemList docId={docId} detail={detailQuery.data} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
