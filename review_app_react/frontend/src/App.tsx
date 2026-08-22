import { AgentHeader } from "./components/AgentHeader";
import { DemoFlow as SelectFrdFlow } from "./components/DemoFlow";

/**
 * Single-surface app (2026-08-21; picker reshaped 2026-08-22): the agent
 * exists to CREATE new STTM documents, so the one flow is "Select FRD" —
 * pick an FRD from the corpus the SharePoint sync keeps in Unity Catalog;
 * an unmapped one runs the pipeline, a mapped one presents its approved
 * STTM. The app never writes to SharePoint: the reviewer uploads the
 * finished workbook themselves and the sync pulls it back in. There is
 * deliberately no browse/review tab: approved STTMs live in the company's
 * SharePoint STTM folder, which is where users view them. (The former
 * gated-ambiguity review tab and its corpus metric row were removed with
 * that decision; ReviewScreen and CorpusMetricRow remain in the tree,
 * unrendered, alongside the mock upload flow.)
 */
export default function App() {
  return (
    <div className="max-w-5xl mx-auto p-6 flex flex-col gap-6">
      <AgentHeader />
      <SelectFrdFlow />
    </div>
  );
}
