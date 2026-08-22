import { useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button } from "@databricks/appkit-ui/react";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { demoWorkbookUrl, useDemoResults, useSharePointConfig, useSharePointPublish } from "../demoApi";

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
      <TemplatePanel r={r} />
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
      <h2 className="eyebrow mb-2">Ambiguity gate</h2>
      <Card className="border-2">
        <CardContent className="py-2">
          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <div className="text-3xl font-semibold mono-id">{g.detected}</div>
              <div className="text-sm text-muted-foreground">ambiguities detected</div>
            </div>
            <div>
              <div className="text-3xl font-semibold mono-id text-pass">{g.auto_confirmed}</div>
              <div className="text-sm text-muted-foreground">auto-confirmed against the data dictionary</div>
            </div>
            <div>
              <div className={`text-3xl font-semibold mono-id ${g.awaiting_human > 0 ? "text-flag" : ""}`}>
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
                <li key={i} className="text-pass">✓ {note}</li>
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
    status === "PASS" ? "text-pass" : status === "PASS_WITH_FLAGS" ? "text-flag" : "text-destructive";
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

/**
 * Which template(s) drove the render, with the deterministic evidence
 * (docs/TEMPLATE_ARCHITECTURE.md). Absent for pre-template artifact sets.
 */
function TemplatePanel({ r }: { r: R }) {
  const t = r.template;
  if (!t) return null;
  const modeLabel =
    t.mode === "single"
      ? "Single template"
      : t.mode === "amalgam"
        ? "Amalgam of templates"
        : "Freeform — no template matched";
  return (
    <div>
      <h2 className="eyebrow mb-2">Template decision</h2>
      <Card>
        <CardContent className="py-3 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Badge variant={t.mode === "freeform" ? "warning" : "success"}>{modeLabel}</Badge>
            {t.own_excluded && t.own_reference && (
              <span className="text-xs text-muted-foreground">
                own reference <span className="mono-id">{t.own_reference}</span> excluded from candidacy;
                used for scoring only
              </span>
            )}
            {t.demoted_from && (
              <span className="text-xs text-muted-foreground">
                demoted from <span className="mono-id">{t.demoted_from.join(", ")}</span> — no structural
                feed match
              </span>
            )}
          </div>
          <div className="flex flex-col gap-1">
            {t.ranked.slice(0, 5).map((c) => {
              const chosen = t.selections.some((sel) => sel.reference === c.reference);
              return (
                <div key={c.reference} className="flex items-center gap-2 text-sm">
                  <span className="mono-id">{c.reference}</span>
                  <span className="text-muted-foreground">
                    {Math.round(c.score * 100)}% — columns {Math.round(c.components.columns * 100)}%,
                    tables {Math.round(c.components.tables * 100)}%, prose{" "}
                    {Math.round(c.components.tokens * 100)}%
                  </span>
                  {c.excluded && <Badge variant="outline">own — excluded</Badge>}
                  {chosen && <Badge variant="success">chosen</Badge>}
                </div>
              );
            })}
          </div>
          {t.mode === "freeform" && (
            <p className="text-xs text-muted-foreground">
              No approved pair was similar enough to this document, so the workbook was rendered from the
              contract alone (feed metadata and rules; no column dictionary). Review it accordingly, or
              add a closer reference STTM to the corpus and regenerate.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function EvalPanel({ r }: { r: R }) {
  const ev = r.eval;
  return (
    <div>
      <h2 className="eyebrow mb-2">Accuracy vs golden STTM</h2>
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
              No golden reference workbook exists for this document — the accuracy score is shown only where a
              hand-built reference STTM is available to compare against.
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
      {r.workbook_available && <PublishControl setId={r.set_id} docId={r.doc_id} />}
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

/**
 * The manual SharePoint publish gate (decided 2026-08-21). Publishing a
 * workbook to the client's library is a reviewer's deliberate act, per
 * document, after looking at the result — never a side effect of a run.
 * Hence the two-step shape mirroring the billed-run ConfirmDialog: the first
 * click only reveals the target and the warning; only the second click sends
 * confirm:true. Renders nothing when SharePoint is unconfigured, same as the
 * document picker.
 */
function PublishControl({ setId, docId }: { setId: string; docId: string }) {
  const configQuery = useSharePointConfig();
  const publish = useSharePointPublish();
  const [confirming, setConfirming] = useState(false);

  const cfg = configQuery.data;
  if (!cfg?.configured) return null;

  if (publish.isSuccess) {
    const p = publish.data;
    return (
      <Alert className="mb-3">
        <AlertTitle>Published to SharePoint</AlertTitle>
        <AlertDescription>
          <span className="mono-id">{p.name}</span> is now in <span className="mono-id">{p.target}</span>.{" "}
          {p.web_url && (
            <a href={p.web_url} target="_blank" rel="noreferrer" className="underline">
              Open in SharePoint
            </a>
          )}
        </AlertDescription>
      </Alert>
    );
  }

  if (!confirming) {
    return (
      <div className="flex justify-end mb-3">
        <Button variant="outline" onClick={() => setConfirming(true)}>
          Publish to SharePoint…
        </Button>
      </div>
    );
  }

  return (
    <Card className="border-2 mb-3">
      <CardHeader>
        <CardTitle className="text-base">Publish this workbook to the client's library?</CardTitle>
        <CardDescription>
          <span className="mono-id">{docId}.sttm.xlsx</span> will be uploaded to{" "}
          <span className="mono-id">
            {cfg.site}/{cfg.library}
            {cfg.output_folder ? `/${cfg.output_folder}` : ""}
          </span>
          , replacing any same-named workbook. This is the hand-off of record — publish only after the mapping
          has been reviewed.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {publish.isError && (
          <Alert variant="destructive">
            <AlertDescription>{(publish.error as Error).message}</AlertDescription>
          </Alert>
        )}
        <div className="flex gap-2">
          <Button
            disabled={publish.isPending}
            onClick={() => publish.mutate({ set_id: setId, doc_id: docId })}
          >
            {publish.isPending ? "Publishing…" : "Publish it"}
          </Button>
          <Button variant="ghost" onClick={() => setConfirming(false)} disabled={publish.isPending}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
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
