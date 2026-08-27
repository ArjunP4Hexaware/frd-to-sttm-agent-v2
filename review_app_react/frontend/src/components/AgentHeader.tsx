import acfcMark from "../assets/acfc.png";
import { AGENT_NAME } from "../lib/displayText";
import { useDemoConfig } from "../demoApi";

/**
 * MASTHEAD — rebuilt 2026-08-27 into the client's own header language.
 *
 * It was a single blue line on a white page (2026-08-21), which carried the
 * right COLOUR and none of the client's LAYOUT. Their header is: a thin brand
 * rule along the top of the page, a white band, the mark on the left, the
 * headline in brand blue, utility text on the right, and a hairline beneath.
 * The ACFC MARK was cleared for use by Arjun on 2026-08-27, so it replaces the
 * flag device that had been standing in for it. It is the client's own header
 * lockup, at the size their site uses it, with a hairline separating it from
 * the app's own title — a co-branding rule, not a merge: the mark identifies
 * whose programme this is, the blue headline identifies the tool.
 *
 * PORTABILITY (Arjun, 2026-08-27) — the frontend has to be essentially the
 * same in the Hexaware environment and in ACFC's rebuild, because the backend
 * will differ and the UI is the half that must survive the move. The env chip
 * is the ONLY element here that knows an environment exists, and it changes
 * its TEXT, never its box: same chip, same place, either side. Everything else
 * is tokens.
 */
export function AgentHeader() {
  const config = useDemoConfig();
  // "local" until the probe lands, so the chip never pops in or reflows.
  const mode = config.data?.mode ?? "local";
  const runtime = mode === "databricks" ? "Databricks Apps" : "Local";

  return (
    <header className="acfc-masthead">
      <div className="acfc-topline" />
      <div className="acfc-container flex items-center gap-4 py-4">
        <img
          src={acfcMark}
          alt="AmeriHealth Caritas"
          className="acfc-mark"
        />
        <span className="acfc-mark-rule" aria-hidden="true" />
        <div className="min-w-0">
          <h1 className="display-heading text-[1.6rem] leading-tight">{AGENT_NAME}</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Source-to-target mapping, drafted from an approved FRD and the vendor’s data dictionary
          </p>
        </div>
        <span className="ml-auto acfc-envchip" title="Where this instance is running">
          Runtime <b>{runtime}</b>
        </span>
      </div>
    </header>
  );
}
