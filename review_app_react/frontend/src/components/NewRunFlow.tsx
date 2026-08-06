import { useState } from "react";
import { UploadScreen } from "./UploadScreen";
import { ProgressView } from "./ProgressView";
import { ReviewScreen } from "./ReviewScreen";
import { ResultsScreen } from "./ResultsScreen";

type Flow =
  | { stage: "upload" }
  | { stage: "progress"; runId: string }
  | { stage: "review"; runId: string }
  | { stage: "results"; runId: string };

/**
 * Single-page, useState-driven state machine -- same pattern as App.tsx's
 * document picker + review layout, no react-router. Upload -> progress
 * (polling) -> review (only if the run gates on ambiguities) -> back to
 * progress (rendering) -> results.
 */
export function NewRunFlow() {
  const [flow, setFlow] = useState<Flow>({ stage: "upload" });

  switch (flow.stage) {
    case "upload":
      return <UploadScreen onStarted={(runId) => setFlow({ stage: "progress", runId })} />;
    case "progress":
      return (
        <ProgressView
          runId={flow.runId}
          onGated={() => setFlow({ stage: "review", runId: flow.runId })}
          onDone={() => setFlow({ stage: "results", runId: flow.runId })}
        />
      );
    case "review":
      return (
        <ReviewScreen
          runId={flow.runId}
          onContinue={() => setFlow({ stage: "progress", runId: flow.runId })}
        />
      );
    case "results":
      return <ResultsScreen runId={flow.runId} onStartOver={() => setFlow({ stage: "upload" })} />;
  }
}
