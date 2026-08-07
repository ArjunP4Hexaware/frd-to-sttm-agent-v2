import { useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button } from "@databricks/appkit-ui/react";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { demoWorkbookUrl, useDemoResults } from "../demoApi";

/**
 * The unified results view — rendered identically for a finished live run
 * and a replayed artifact set (both are just an artifact set on disk by the
 * time this mounts). Section order IS the demo story: extraction summary →
 * the stage-03 gate moment (the HITL centerpiece) → stage-04 verdict →
 * eval-vs-golden → rendered STTM mappings + workbook download.
 */
export function DemoResults({ setId, docId }: { setId: string; docId: string }) {
  const resultsQuery = useDemoResults(setId, docId);

  if (resultsQuery.isLoading) return <p className="text-muted-foreground">Loading results…</p>;
  if (resultsQuery.isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Failed to load results</AlertTitle>
        <AlertDescription>{(resultsQuery.error as Error).message}</AlertDescription>
      </Alert>
    );
  }
  const r = resultsQuery.data;
  if (!r) return null;

  return (
    <div className="flex flex-col gap-6">
      <ExtractionSummary r={r} />
      <GateStrip r={r} />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <VerdictTile r={r} />
        <EvalPanel r={r} />
      </div>
      <MappingsSection r={r} />
    </div>
  );
}

type R = NonNullable<ReturnType<typeof useDemoResults>["data"]>;

function ExtractionSummary({ r }: { r: R }) {
  const es = r.extraction_summary;
  return (
    <div>
      <h2 className="eyebrow mb-2">Extraction</h2>
      <div className="grid grid-cols-3 gap-3 mb-3">
        <Stat label="Feeds" value={es.n_feeds} />
        <Stat label="Target tables" value={es.n_tables} />
        <Stat label="Rules captured" value={es.n_rules} />
      </div>
      <div className="flex flex-col gap-2">
        {es.feeds.map((f) => (
          <Card key={f.feed_name ?? Math.random()}>
            <CardHeader>
              <CardTitle className="mono-id text-sm">{f.feed_name}</CardTitle>
              <CardDescription>
                {f.source_system} · {f.file_name_patterns.join(", ")}
              </CardDescription>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground flex flex-wrap gap-x-6 gap-y-1">
              <span>stage → <span className="mono-id">{f.stage}</span></span>
              <span>standard → <span className="mono-id">{f.standard}</span></span>
              <span>{f.n_rules} rule{f.n_rules === 1 ? "" : "s"}</span>
              {f.requirement_ids.length > 0 && (
                <span>reqs: {f.requirement_ids.join(", ")}</span>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

/**
 * The HITL centerpiece. Deliberately prominent even when every count is
 * zero-awaiting: the story is "the pipeline flags what it isn't sure of,
 * the dictionary cross-check clears what the data can prove, and a human
 * only sees what genuinely needs one".
 */
function GateStrip({ r }: { r: R }) {
  const g = r.gate;
  return (
    <div>
      <h2 className="eyebrow mb-2">Ambiguity gate (stage 03 → 04)</h2>
      <Card className="border-2">
        <CardContent className="py-2">
          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <div className="text-3xl font-semibold mono-id">{g.detected}</div>
              <div className="text-sm text-muted-foreground">ambiguities detected</div>
            </div>
            <div>
              <div className="text-3xl font-semibold mono-id text-emerald-600">{g.auto_confirmed}</div>
              <div className="text-sm text-muted-foreground">auto-confirmed against the data dictionary</div>
            </div>
            <div>
              <div className={`text-3xl font-semibold mono-id ${g.awaiting_human > 0 ? "text-amber-600" : ""}`}>
                {g.awaiting_human}
              </div>
              <div className="text-sm text-muted-foreground">awaiting human review</div>
            </div>
          </div>
          <div className="mt-3 text-sm text-muted-foreground border-t pt-2 flex flex-wrap gap-x-6 gap-y-1">
            <span>
              Grounding: {g.strict_checked ?? "?"} strict checks, {g.strict_failed} failed ·{" "}
              {g.advisory_checked ?? "?"} advisory checks
            </span>
            {g.human_resolved > 0 && <span>{g.human_resolved} resolved by a reviewer</span>}
          </div>
          {g.detected_items.length > 0 && (
            <ul className="mt-2 text-sm flex flex-col gap-1">
              {g.detected_items.map((item, i) => (
                <li key={i} className="flex gap-2 items-baseline">
                  <Badge variant="outline">{item.kind}</Badge>
                  <span className="text-muted-foreground">{item.text}</span>
                </li>
              ))}
            </ul>
          )}
          {g.auto_confirmed_notes.length > 0 && (
            <ul className="mt-2 text-sm flex flex-col gap-1">
              {g.auto_confirmed_notes.map((note, i) => (
                <li key={i} className="text-emerald-700">✓ {note}</li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function VerdictTile({ r }: { r: R }) {
  const status = r.verdict.status ?? "?";
  const tone =
    status === "PASS" ? "text-emerald-600" : status === "PASS_WITH_FLAGS" ? "text-amber-600" : "text-destructive";
  return (
    <div>
      <h2 className="eyebrow mb-2">Pipeline verdict</h2>
      <Card>
        <CardContent className="py-4 text-center">
          <div className={`text-3xl font-semibold ${tone}`}>{status}</div>
          <div className="text-sm text-muted-foreground mt-1">
            {r.verdict.n_feeds} feed{r.verdict.n_feeds === 1 ? "" : "s"} in the mapping contract
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function EvalPanel({ r }: { r: R }) {
  const ev = r.eval;
  return (
    <div>
      <h2 className="eyebrow mb-2">Eval vs golden STTM</h2>
      <Card>
        <CardContent className="py-4 text-center">
          {ev.available ? (
            <>
              <div className="text-3xl font-semibold mono-id">{ev.pct}%</div>
              <div className="text-sm text-muted-foreground mt-1">
                {ev.matched_cells?.toLocaleString()} / {ev.total_cells?.toLocaleString()} target cells match the
                golden workbook
              </div>
              {ev.is_golden && (
                <div className="text-xs text-muted-foreground mt-2">
                  The non-matching cells are the fixture's deliberate datatype divergences — they are expected to
                  differ.
                </div>
              )}
            </>
          ) : (
            <div className="text-sm text-muted-foreground py-3">
              No golden reference for uploaded documents — the eval score only exists for the preloaded demo
              pair, where a hand-built golden STTM is available to compare against.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function MappingsSection({ r }: { r: R }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h2 className="eyebrow">Rendered STTM</h2>
        {r.workbook_available && (
          <Button asChild>
            <a href={demoWorkbookUrl(r.set_id, r.doc_id)} download>
              Download workbook (.xlsx)
            </a>
          </Button>
        )}
      </div>
      <div className="flex flex-col gap-3">
        {r.mappings.map((m) => (
          <MappingTable key={m.feed_name ?? "?"} feedName={m.feed_name} rows={m.rows} />
        ))}
        {r.mappings.length === 0 && (
          <p className="text-sm text-muted-foreground">No per-mapping rows in this contract.</p>
        )}
      </div>
    </div>
  );
}

function MappingTable({
  feedName,
  rows,
}: {
  feedName: string | null;
  rows: R["mappings"][number]["rows"];
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? rows : rows.slice(0, 8);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="mono-id text-sm">{feedName}</CardTitle>
        <CardDescription>{rows.length} column mappings</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground border-b">
                <th className="py-1 pr-4 font-normal">Source column</th>
                <th className="py-1 pr-4 font-normal">Type</th>
                <th className="py-1 pr-4 font-normal">Stage target</th>
                <th className="py-1 font-normal">Standard target</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row, i) => (
                <tr key={i} className="border-b last:border-0">
                  <td className="py-1 pr-4 mono-id">{row.source_column}</td>
                  <td className="py-1 pr-4 text-muted-foreground">{row.datatype}</td>
                  <td className="py-1 pr-4 mono-id">{row.stage}</td>
                  <td className="py-1 mono-id">{row.standard}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length > 8 && (
          <Button variant="ghost" onClick={() => setExpanded(!expanded)}>
            {expanded ? "Show fewer" : `Show all ${rows.length} mappings`}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="py-2 text-center">
        <div className="text-2xl font-semibold mono-id">{value}</div>
        <div className="text-sm text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}
