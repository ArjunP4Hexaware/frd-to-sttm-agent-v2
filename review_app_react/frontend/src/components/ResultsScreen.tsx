import { Alert, AlertDescription, AlertTitle, Button } from "@databricks/appkit-ui/react";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { runSttmDownloadUrl, useRunContract, useRunCoverage } from "../api";
import { ContractJsonView } from "./ContractJsonView";
import {
  GATE_STATUS_LABEL,
  GATE_STATUS_VARIANT,
  ITEM_KIND_LABEL,
  UNRESOLVED_SOURCE_LABEL,
  displayLabel,
  documentDisplayName,
} from "../lib/displayText";
import type { RunContractFailure, RunContractResponse } from "../types";

interface Props {
  runId: string;
  onStartOver: () => void;
}

function isFailure(data: RunContractResponse | RunContractFailure): data is RunContractFailure {
  return !("contract" in data);
}

/**
 * Golden-pair eval totals for this run, as a headline figure.
 *
 * What the number actually is: of the mapping values the analyst-built
 * reference workbook fills in for this document, the share the pipeline
 * derived identically. It is an agreement-with-the-reference measure, NOT
 * a share-of-work-automated measure -- the copy below is worded to claim
 * only the former. See backend/eval_report.py and
 * 04_sttm_render.py's evaluate_against_reference().
 *
 * Omits itself entirely -- no spinner, no error text, no empty frame --
 * while loading, on any error, and whenever the backend reports the figure
 * unavailable. Nothing on this tile's path can blank the Results screen or
 * put the workbook download out of reach.
 */
function CoverageTile({ runId }: { runId: string }) {
  const { data, isLoading, isError } = useRunCoverage(runId);

  if (isLoading || isError || !data?.available) return null;

  const matched = data.matched_cells;
  const total = data.total_cells;
  // Belt-and-braces against a malformed body: available=true is supposed to
  // guarantee both counts, but a null or zero denominator here would render
  // "NaN%" on camera, so treat it the same as unavailable.
  if (matched === null || total === null || total <= 0) return null;

  const pct = ((100 * matched) / total).toFixed(1);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="eyebrow">Mapping accuracy</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="text-4xl font-semibold text-primary tabular-nums">{pct}%</span>
          <span className="text-sm">
            {matched.toLocaleString()} of {total.toLocaleString()} mapping details match the reference
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          Compared against the mapping document an analyst prepared for this file.
        </p>
      </CardContent>
    </Card>
  );
}

export function ResultsScreen({ runId, onStartOver }: Props) {
  const { data, isLoading, error } = useRunContract(runId);

  if (isLoading || !data) {
    return <p className="text-muted-foreground">Loading results…</p>;
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Couldn't load results</AlertTitle>
        <AlertDescription>{String(error)}</AlertDescription>
      </Alert>
    );
  }

  if (isFailure(data)) {
    // Explicit FAIL treatment: no contract JSON exists at all for this
    // doc_id -- 03_contract_build.py gated it FAIL before a contract could
    // be built (see orchestration.py's get_run_contract docstring), so
    // there's nothing to render a download link or JSON view for.
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">Results</h1>
          <Badge variant="destructive">{displayLabel(GATE_STATUS_LABEL, "FAIL")}</Badge>
        </div>
        <Alert variant="destructive">
          <AlertTitle>No contract was produced</AlertTitle>
          <AlertDescription>{data.reason}</AlertDescription>
        </Alert>
        {data.report_md && (
          <Card>
            <CardHeader>
              <CardTitle className="eyebrow">Report</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="text-xs font-mono whitespace-pre-wrap break-words">{data.report_md}</pre>
            </CardContent>
          </Card>
        )}
        <Button variant="outline" onClick={onStartOver} className="self-start">
          Start another run
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-2xl font-semibold">Results</h1>
        <Badge variant={GATE_STATUS_VARIANT[data.status] ?? "outline"}>
          {displayLabel(GATE_STATUS_LABEL, data.status)}
        </Badge>
        {/* Title, so no `mono-id` -- see SummaryHeader. The workbook download
            below deliberately keeps the raw doc_id: that one is a filename. */}
        <span className="text-sm text-muted-foreground">{documentDisplayName(data.doc_id)}</span>
      </div>

      {/* Success branch only -- the FAIL branch above is left exactly as it
          was, and never has a figure to show anyway (04_sttm_render.py, which
          computes it, never runs for a FAIL'd document). */}
      <CoverageTile runId={runId} />

      <Card>
        <CardHeader>
          <CardTitle className="eyebrow">Rendered STTM workbook</CardTitle>
        </CardHeader>
        <CardContent>
          <a href={runSttmDownloadUrl(runId)} download>
            <Button>Download {data.doc_id}.sttm.xlsx</Button>
          </a>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="eyebrow">
            Unresolved ambiguities (<span className="mono-id">{data.unresolved.length}</span>)
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data.unresolved.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing outstanding — every gated item was either resolved and applied, or has no
              structural write-back path required.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {data.unresolved.map((item) => (
                <li key={item.ambiguity_id} className="text-sm border border-border rounded-sm p-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="outline">
                      {item.kind ? displayLabel(ITEM_KIND_LABEL, item.kind) : "unknown"}
                    </Badge>
                    <Badge variant="warning">
                      {displayLabel(UNRESOLVED_SOURCE_LABEL, item.source)}
                    </Badge>
                  </div>
                  {item.text && <p className="font-mono text-xs mt-1 break-words">{item.text}</p>}
                  <p className="text-xs text-muted-foreground mt-1">{item.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="eyebrow">Contract JSON</CardTitle>
        </CardHeader>
        <CardContent>
          <ContractJsonView contract={data.contract} />
        </CardContent>
      </Card>

      <Button variant="outline" onClick={onStartOver} className="self-start">
        Start another run
      </Button>
    </div>
  );
}
