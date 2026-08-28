import { useEffect, useState } from "react";
import { AgentHeader } from "./components/AgentHeader";
import { Picker } from "./components/Picker";
import { RunView } from "./components/RunView";

type View = { kind: "picker" } | { kind: "run"; runId: string };

function fromUrl(): View {
  const run = new URLSearchParams(window.location.search).get("run");
  return run ? { kind: "run", runId: run } : { kind: "picker" };
}

/**
 * One surface: pick an FRD → the run (answers, then the workbook). A run is
 * addressable as ?run=<id> so a result can be reopened or shared.
 */
export default function App() {
  const [view, setView] = useState<View>(fromUrl);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (view.kind === "run") url.searchParams.set("run", view.runId);
    else url.searchParams.delete("run");
    window.history.replaceState(null, "", url.toString());
  }, [view]);

  return (
    <div className="acfc-page">
      <AgentHeader />
      <main className="acfc-band flex-1">
        <div className="acfc-container py-7">
          {view.kind === "picker" ? (
            <Picker onOpenRun={(runId) => setView({ kind: "run", runId })} />
          ) : (
            <RunView runId={view.runId} onBack={() => setView({ kind: "picker" })} onOpenRun={(runId) => setView({ kind: "run", runId })} />
          )}
        </div>
      </main>
      <footer className="acfc-footer">
        <div className="acfc-container">
          <span>
            <strong>Two inputs, both read</strong> <span className="sep">·</span> the FRD (by Claude) and the vendor data dictionary (by code){" "}
            <span className="sep">·</span> <strong>nothing is guessed</strong> — what is missing is asked
          </span>
          <span>Column names are carried as-is from the dictionary; the client standards decide the target side</span>
        </div>
      </footer>
    </div>
  );
}
