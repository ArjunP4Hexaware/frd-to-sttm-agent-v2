import { AgentHeader } from "./components/AgentHeader";
import { DemoFlow as SelectFrdFlow } from "./components/DemoFlow";

/**
 * Single-surface app (2026-08-21): the agent exists to CREATE new STTM
 * documents, so the one flow is "Select FRD" — name an FRD, the app locates
 * it in SharePoint and either presents its already-published STTM or runs
 * the pipeline. There is deliberately no browse/review tab: finished,
 * approved STTMs live in the company's SharePoint output folder, which is
 * where users view them. (The former gated-ambiguity review tab and its
 * corpus metric row were removed with that decision; ReviewScreen and
 * CorpusMetricRow remain in the tree, unrendered, alongside the mock
 * upload flow.)
 */
export default function App() {
  return (
    <div className="max-w-5xl mx-auto p-6 flex flex-col gap-6">
      <AgentHeader />
      <SelectFrdFlow />
    </div>
  );
}
