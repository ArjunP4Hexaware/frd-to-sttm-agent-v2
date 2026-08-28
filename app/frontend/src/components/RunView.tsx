import { useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { QuestionCard } from "./QuestionCard";
import { STATUS_LABEL, reportUrl, useAnswer, useRender, useRun, workbookUrl, type RunView as Run } from "../api";

/**
 * One run, top to bottom in the order the reviewer needs it: what it read,
 * whether it can build the STTM, the questions it will not answer itself,
 * then the workbook.
 */
export function RunView({ runId, onBack }: { runId: string; onBack: () => void }) {
  const q = useRun(runId);
  const answer = useAnswer(runId);
  const render = useRender(runId);
  const qc = useQueryClient();
  const r = q.data;
  const running = r?.live?.phase === "running";

  if (q.isLoading) return <p className="text-muted-foreground">Loading run…</p>;
  if (q.isError || !r) {
    return (
      <div className="flex flex-col gap-3">
        <Button variant="ghost" onClick={onBack}>← Select FRD</Button>
        <Alert variant="destructive"><AlertTitle>Cannot load this run</AlertTitle><AlertDescription>{(q.error as Error)?.message}</AlertDescription></Alert>
      </div>
    );
  }
  const a = r.assessment;
  const questions = a?.questions ?? [];
  const unanswered = questions.filter((x) => !x.answer).length;
  const canRender = !!a && a.blockers.length === 0 && !running;
  const error = r.error ?? r.live?.error;

  return (
    <div className="flex flex-col gap-6">
      <div><Button variant="ghost" onClick={onBack}>← Select FRD</Button></div>

      <div className="flex items-center gap-3 flex-wrap">
        {running && <Spinner />}
        <h2 className="acfc-section-title text-xl">
          <span className="mono-id">{r.doc_id}</span>
        </h2>
        <span className={`acfc-chip ${r.status === "rendered" ? "acfc-chip--ok" : r.status === "failed" || r.status === "cannot_generate" ? "acfc-chip--gap" : "acfc-chip--quiet"}`}>
          {running ? (r.live?.task === "render" ? "generating the STTM…" : "reading the documents…") : STATUS_LABEL[r.status] ?? r.status}
        </span>
        <span className="mono-id text-xs text-muted-foreground">{r.run_id}</span>
        {r.live?.url && <a href={r.live.url} target="_blank" rel="noreferrer" className="text-xs underline">job run ↗</a>}
      </div>

      {error && !running && (
        <Alert variant="destructive"><AlertTitle>The run failed</AlertTitle><AlertDescription className="whitespace-pre-wrap">{error}</AlertDescription></Alert>
      )}

      {r.frd && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label="FRD" value={`${r.frd.heading_count} headings · ${r.frd.table_count} tables`} />
          <Stat label="Dictionary" value={r.vdd ? `${r.vdd.n_fields} columns · ${r.vdd.n_files} file${r.vdd.n_files === 1 ? "" : "s"}` : "none"} tone={r.vdd ? undefined : "alert"} />
          <Stat label="Grounding" value={a ? `${a.grounding.strict_checked - a.grounding.strict_failed.length} / ${a.grounding.strict_checked} verbatim` : "—"} />
          <Stat label="Questions" value={a ? `${questions.length - unanswered} / ${questions.length} answered` : "—"} tone={unanswered > 0 ? "alert" : undefined} />
        </div>
      )}

      {a && a.blockers.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>The STTM cannot be generated from these documents</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-5">{a.blockers.map((b) => <li key={b.kind}>{b.text}</li>)}</ul>
          </AlertDescription>
        </Alert>
      )}

      {a && a.sources.length > 0 && (
        <section>
          <h3 className="eyebrow-blue mb-2">Sources</h3>
          <div className="flex flex-col gap-2">
            {a.sources.map((s) => (
              <Card key={s.feed_index}>
                <CardHeader>
                  <CardTitle className="mono-id text-sm">{s.feed_name}</CardTitle>
                  <CardDescription>
                    {s.file ? <>dictionary file <span className="mono-id">{s.file}</span> · {s.n_columns} columns</> : <span className="text-[var(--brand-red)]">no dictionary file paired — see the questions</span>}
                  </CardDescription>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground flex flex-wrap gap-x-6 gap-y-1">
                  {(["stage", "standard"] as const).map((layer) => {
                    const l = s.layers[layer];
                    return (
                      <span key={layer}>
                        {layer} → <span className="mono-id">{[l.catalog, l.schema].filter(Boolean).join(".") || "?"}.{l.tables.join(",") || "?"}</span>
                        <span className="text-xs"> ({Object.entries(l.origin).map(([k, v]) => `${k}: ${v}`).join(", ")})</span>
                      </span>
                    );
                  })}
                </CardContent>
              </Card>
            ))}
          </div>
        </section>
      )}

      {questions.length > 0 && (
        <section>
          <h3 className="eyebrow-blue mb-2">Questions the agent will not answer itself</h3>
          <p className="text-sm text-muted-foreground mb-3">
            Each answer is saved on this run. When you are done, generate the STTM — no second model call is made.
          </p>
          <div className="flex flex-col gap-3">
            {questions.map((x) => (
              <QuestionCard
                key={x.id}
                q={x}
                disabled={running}
                onAnswer={(value) =>
                  answer.mutateAsync({ question_id: x.id, value }).then(() => qc.invalidateQueries({ queryKey: ["run", runId] }))
                }
              />
            ))}
          </div>
        </section>
      )}

      {a && (
        <div className="flex items-center gap-3 flex-wrap border-t pt-4">
          <Button disabled={!canRender || render.isPending} onClick={() => render.mutate(undefined, { onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }) })}>
            {running && r.live?.task === "render" ? <><Spinner /> Generating…</> : r.render ? "Regenerate the STTM with these answers" : "Generate the STTM"}
          </Button>
          {unanswered > 0 && a.blockers.length === 0 && (
            <span className="text-sm text-muted-foreground">{unanswered} question{unanswered === 1 ? "" : "s"} still open — the workbook will note them.</span>
          )}
          {render.isError && <span className="text-sm text-[var(--brand-red)]">{(render.error as Error).message}</span>}
        </div>
      )}

      {r.render && (
        <section>
          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
            <h3 className="eyebrow-blue">STTM workbook</h3>
            <div className="flex gap-2">
              <Button asChild><a href={workbookUrl(r.run_id)} download>Download {r.render.workbook}</a></Button>
              <Button variant="outline" asChild><a href={reportUrl(r.run_id)} target="_blank" rel="noreferrer">Report</a></Button>
            </div>
          </div>
          <p className="text-sm text-muted-foreground mb-3">
            {r.render.n_rows} rows{r.render.layout_from ? <> in the layout of <span className="mono-id">{r.render.layout_from}</span></> : " (built-in layout)"}
            {" · "}generated {r.render.rendered_at.slice(0, 16).replace("T", " ")}
            {r.render.unanswered.length > 0 ? ` · ${r.render.unanswered.length} question(s) were still open` : ""}
            {r.render.unpromoted_types.length > 0 ? ` · vendor types kept as stage type: ${r.render.unpromoted_types.join(", ")}` : ""}
          </p>
        </section>
      )}

      {r.preview.length > 0 && (
        <section className="flex flex-col gap-3">
          {r.preview.map((p) => (
            <PreviewTable key={p.source} p={p} />
          ))}
        </section>
      )}

      {a && a.notes.length > 0 && (
        <details className="text-sm text-muted-foreground">
          <summary className="cursor-pointer">Notes ({a.notes.length})</summary>
          <ul className="list-disc pl-5 mt-2">{a.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

function PreviewTable({ p }: { p: Run["preview"][number] }) {
  const [expanded, setExpanded] = useState(false);
  if (p.error) return <p className="text-sm text-[var(--brand-red)]">{p.error}</p>;
  const rows = expanded ? p.rows : p.rows.slice(0, 8);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="mono-id text-sm">{p.source}</CardTitle>
        <CardDescription>{p.n_rows} rows{p.file ? <> from <span className="mono-id">{p.file}</span></> : ""}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground border-b">
                <th className="py-1 pr-4 font-normal">Source column</th>
                <th className="py-1 pr-4 font-normal">Vendor type</th>
                <th className="py-1 pr-4 font-normal">Stage</th>
                <th className="py-1 font-normal">Standard</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} className={`border-b last:border-0${row.audit ? " text-muted-foreground" : ""}`}>
                  <td className="py-1 pr-4 mono-id">{row.source_column}</td>
                  <td className="py-1 pr-4 text-muted-foreground">{row.datatype}</td>
                  <td className="py-1 pr-4 mono-id">{[row.stage.schema, row.stage.table, row.stage.column].filter(Boolean).join(".")} <span className="text-muted-foreground">({row.stage.datatype})</span></td>
                  <td className="py-1 mono-id">{[row.standard.schema, row.standard.table, row.standard.column].filter(Boolean).join(".")} <span className="text-muted-foreground">({row.standard.datatype})</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {p.rows.length > 8 && (
          <Button variant="ghost" onClick={() => setExpanded(!expanded)}>{expanded ? "Show fewer" : `Show ${p.rows.length} rows`}</Button>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "muted" | "alert" }) {
  return (
    <div className={`acfc-stat${tone ? ` acfc-stat--${tone}` : ""}`}>
      <b className="!text-base">{value}</b>
      <span>{label}</span>
    </div>
  );
}
