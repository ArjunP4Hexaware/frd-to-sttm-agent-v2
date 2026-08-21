import { AGENT_NAME } from "../lib/displayText";

/**
 * HEADER: the agent name alone, in amerihealthcaritas.com's headline voice
 * (bold geometric sans, brand blue #003DA5, sentence case, tight tracking).
 * Stripped to a single line by decision (2026-08-21) -- no eyebrow, no
 * tagline, no wordmark, no metric row; the page opens directly into the
 * Select FRD flow.
 */
export function AgentHeader() {
  return (
    <header className="border-b border-border pb-5">
      <h1 className="display-heading text-3xl leading-tight">{AGENT_NAME}</h1>
    </header>
  );
}
