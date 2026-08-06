import { AGENT_NAME, AGENT_PIPELINE_POSITION, AGENT_TAGLINE } from "../lib/displayText";

/**
 * BEAT 1 -- HEADER. eyebrow (pipeline position) + agent name + one-line purpose.
 *
 * Structure copied verbatim from demo_shell's Header (see
 * ../../../../demo_shell/frontend/src/components/Header.tsx) -- same element
 * order, same class names -- because the point of the four-beat spine is that
 * all five agent demos read as one product. Change it there and here together,
 * or they drift.
 *
 * The one difference is where the strings come from: demo_shell reads them off
 * its `/api/descriptor` response so its component never needs editing per
 * agent. This app has no descriptor endpoint, so they are constants in
 * lib/displayText.ts alongside the rest of the display vocabulary.
 */
export function AgentHeader() {
  return (
    <header className="flex flex-col gap-1 border-b border-border pb-4">
      <p className="eyebrow">{AGENT_PIPELINE_POSITION}</p>
      <h1 className="text-xl font-semibold leading-tight">{AGENT_NAME}</h1>
      <p className="text-sm text-muted-foreground">{AGENT_TAGLINE}</p>
    </header>
  );
}
