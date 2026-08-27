import { AgentHeader } from "./components/AgentHeader";
import { DemoFlow as SelectFrdFlow } from "./components/DemoFlow";

/**
 * Single-surface app (2026-08-21; picker reshaped 2026-08-22): the agent
 * exists to CREATE new STTM documents, so the one flow is "Select FRD" —
 * pick an FRD from the corpus the sync keeps in Unity Catalog; an unmapped
 * one runs the pipeline, a mapped one presents its approved STTM. The app
 * never writes back to the document library: the reviewer uploads the
 * finished workbook themselves and the next sync pulls it in. There is
 * deliberately no browse/review tab — approved STTMs live in the company's
 * library, which is where users view them. (The former gated-ambiguity
 * review tab and its corpus metric row were removed with that decision;
 * ReviewScreen and CorpusMetricRow remain in the tree, unrendered, alongside
 * the mock upload flow.)
 *
 * SHELL rebuilt 2026-08-27 into the client's page structure: a masthead, the
 * work on a full-bleed band, and a navy footer — instead of one centred
 * column of floating cards on undifferentiated white. See index.css for the
 * primitives and for the portability rule that governs them (the frontend
 * must read the same in the Hexaware environment and in ACFC's rebuild;
 * nothing in the shell branches on environment).
 */
export default function App() {
  return (
    <div className="acfc-page">
      <AgentHeader />

      <main className="acfc-band flex-1">
        <div className="acfc-container py-7">
          <SelectFrdFlow />
        </div>
      </main>

      <footer className="acfc-footer">
        <div className="acfc-container">
          <span>
            <strong>The model proposes</strong> <span className="sep">·</span> deterministic code audits
            and decides <span className="sep">·</span> <strong>a person resolves and approves</strong>
          </span>
          <span>
            Nothing is written back to the document library — the reviewer uploads the approved workbook
          </span>
        </div>
      </footer>
    </div>
  );
}
