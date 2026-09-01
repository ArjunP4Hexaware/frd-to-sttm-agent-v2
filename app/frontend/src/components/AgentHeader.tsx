import acfcMark from "../assets/acfc.png";
import { useConfig } from "../api";

export function AgentHeader() {
  const config = useConfig();
  const mode = config.data?.mode ?? "local";
  return (
    <header className="acfc-masthead">
      <div className="acfc-topline" />
      <div className="acfc-container flex items-center gap-4 py-4">
        <img src={acfcMark} alt="AmeriHealth Caritas" className="acfc-mark" />
        <span className="acfc-mark-rule" aria-hidden="true" />
        <div className="min-w-0">
          <h1 className="display-heading text-[1.6rem] leading-tight">FRD to STTM Agent</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Source-to-target mapping, drafted from an approved FRD and the vendor’s data dictionary
          </p>
        </div>
        <span className="ml-auto acfc-envchip" title="Where this instance is running">
          Runtime <b>{mode === "databricks" ? "Databricks Apps" : "Local"}</b>
        </span>
      </div>
    </header>
  );
}
