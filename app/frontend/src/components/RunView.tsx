import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { QuestionCard } from "./QuestionCard";
import {
  STATUS_EXPLANATION,
  STATUS_LABEL,
  VDD_PROBLEM_LABEL,
  reportUrl,
  useAnswer,
  useRender,
  useRun,
  useStartRun,
  workbookUrl,
  type RunView as Run,
  type SourceEntry,
} from "../api";

/**
 * One run, top to bottom in the order the reviewer needs it: what is
 * happening now, what the agent read, whether it can build the STTM, the
 * questions it will not answer itself, then the workbook.
 */
export function RunView({ runId, onBack, onOpenRun }: { runId: string; onBack: () => void; onOpenRun?: (runId: string) => void }) {
  const q = useRun(runId);
  const answer = useAnswer(runId);
  const render = useRender(runId);
  const startAgain = useStartRun();
  const qc = useQueryClient();
  const r = q.data;
  const running = r?.live?.phase === "running" || r?.status === "extracting";

  if (q.isLoading) return <p className="text-muted-foreground flex items-center gap-2"><Spinner /> Loading run…</p>;
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
  const task = r.live?.task;
  const statusKey = running ? "extracting" : error ? "failed" : r.status;
  const tone = statusKey === "rendered" ? "ok" : statusKey === "failed" || statusKey === "cannot_generate" ? "gap" : statusKey === "needs_input" ? "warn" : "quiet";

  return (
    <div className="flex flex-col gap-6">
      <div><Button variant="ghost" onClick={onBack}>← Select FRD</Button></div>

      <div>
        <p className="eyebrow-blue mb-1">Run</p>
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="acfc-section-title text-xl"><span className="mono-id">{r.doc_id}</span></h2>
          <span className="mono-id text-xs text-muted-foreground">{r.run_id}</span>
          {r.live?.url && <a href={r.live.url} target="_blank" rel="noreferrer" className="text-xs underline">view the job run ↗</a>}
        </div>
      </div>

      {/* The one sentence that says where this run is. */}
      <div className={`acfc-notice${tone === "gap" ? " acfc-notice--warn" : ""}`} style={tone === "ok" ? { borderLeftColor: "var(--brand-pass)" } : tone === "warn" ? { borderLeftColor: "var(--brand-flag)" } : undefined}>
        <b className="flex items-center gap-2">
          {running && <Spinner />}
          {running ? (task === "render" ? "Generating the STTM" : "Reading the documents") : STATUS_LABEL[statusKey] ?? statusKey}
        </b>
        <span>
          {running && task === "render"
            ? "Building the workbook from the dictionary rows and the answers you gave. No model call — this takes about a minute."
            : STATUS_EXPLANATION[statusKey] ?? ""}
        </span>
      </div>

      {running && <Progress startedAt={r.live?.started_at ?? r.created_at} task={task} />}

      {error && !running && (
        <Alert variant="destructive">
          <AlertTitle>The run failed</AlertTitle>
          <AlertDescription className="whitespace-pre-wrap">{error}</AlertDescription>
          {!r.render && onOpenRun && (
            <div className="mt-3 flex items-center gap-3">
              <Button disabled={startAgain.isPending} onClick={() => startAgain.mutate(r.doc_id, { onSuccess: (x) => onOpenRun(x.run_id) })}>
                {startAgain.isPending ? "Starting…" : "Run again"}
              </Button>
              <span className="text-sm">Starts a fresh run on the same FRD and dictionary.</span>
              {startAgain.isError && <span className="text-sm text-[var(--brand-red)]">{(startAgain.error as Error).message}</span>}
            </div>
          )}
        </Alert>
      )}

      {r.frd && a && (
        <section>
          <h3 className="eyebrow-blue mb-2">What the agent read — both documents</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Stat label="FRD — read by Claude" value={`${r.frd.heading_count} headings · ${r.frd.table_count} tables`} sub={`${r.frd.source_file} → source files, target tables, rules`} />
            <Stat
              label={r.vdd?.normalised_by_model ? "Vendor data dictionary — normalised by Claude" : "Vendor data dictionary — read column by column"}
              value={r.vdd ? `${r.vdd.n_fields} columns · ${r.vdd.n_files} file${r.vdd.n_files === 1 ? "" : "s"}` : "none paired"}
              sub={r.vdd ? `${r.vdd.file} → every row of the STTM` : "the source columns have no grounding"}
              tone={r.vdd ? undefined : "alert"}
            />
            <Stat
              label="Grounding check"
              value={`${a.grounding.strict_checked - a.grounding.strict_failed.length} / ${a.grounding.strict_checked} verbatim`}
              sub="identifiers the model returned that appear word-for-word in the FRD"
              tone={a.grounding.strict_failed.length > 0 ? "alert" : undefined}
            />
            <Stat
              label="Questions for you"
              value={questions.length === 0 ? "none" : `${questions.length - unanswered} / ${questions.length} answered`}
              sub={questions.length === 0 ? "the documents settle everything" : unanswered > 0 ? "answer below, then generate" : "all answered"}
              tone={unanswered > 0 ? "alert" : undefined}
            />
          </div>
          {r.vdd?.normalised_by_model && (
            <p className="text-xs mt-2 text-[var(--brand-navy)]">
              This dictionary did not follow the template, so Claude normalised it into the template shape. Every column name it returned was verified word-for-word against the workbook; anything it could not find was dropped, never invented.
            </p>
          )}
          {r.vdd && r.vdd.problems.filter((p) => p.kind !== "normalised_by_model").length > 0 && (
            <p className="text-xs text-muted-foreground mt-2">
              Dictionary gaps: {Array.from(new Set(r.vdd.problems.filter((p) => p.kind !== "normalised_by_model").map((p) => VDD_PROBLEM_LABEL[p.kind] ?? p.kind))).join("; ")}. Those cells stay blank in the workbook.
            </p>
          )}
        </section>
      )}

      {a && a.blockers.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>The STTM cannot be generated from these documents</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-5">{a.blockers.map((b) => <li key={b.kind}>{b.text}</li>)}</ul>
          </AlertDescription>
        </Alert>
      )}

      {a && r.sources.length > 0 && (
        <section>
          <h3 className="eyebrow-blue mb-2">Sources and where each target comes from</h3>
          <div className="flex flex-col gap-2">
            {r.sources.map((s) => <SourceCard key={s.feed_index} s={s} />)}
          </div>
        </section>
      )}

      {questions.length > 0 && (
        <section>
          <h3 className="eyebrow-blue mb-2">Questions the agent will not answer itself</h3>
          <p className="text-sm text-muted-foreground mb-3">
            Each answer is saved on this run and applied when the STTM is generated. Nothing is re-extracted.
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

      {a && a.blockers.length === 0 && (
        <div className="acfc-panel flex items-center gap-3 flex-wrap">
          <Button disabled={!canRender || render.isPending} onClick={() => render.mutate(undefined, { onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }) })}>
            {running && task === "render" ? <><Spinner /> Generating…</> : r.render ? "Regenerate the STTM with these answers" : "Generate the STTM"}
          </Button>
          <span className="text-sm text-muted-foreground">
            {unanswered > 0
              ? `${unanswered} question${unanswered === 1 ? "" : "s"} still open — you can generate now and the workbook will note them, or answer first.`
              : questions.length > 0
                ? "All questions answered. Generating applies them to the workbook."
                : "Nothing is missing."}
          </span>
          {render.isError && <span className="text-sm text-[var(--brand-red)]">{(render.error as Error).message}</span>}
        </div>
      )}

      {r.render && !running && (
        <section>
          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
            <h3 className="eyebrow-blue">STTM workbook</h3>
            <div className="flex gap-2">
              <Button asChild><a href={workbookUrl(r.run_id)} download>Download the STTM workbook</a></Button>
              <Button variant="outline" asChild><a href={reportUrl(r.run_id)} target="_blank" rel="noreferrer">Run report</a></Button>
            </div>
          </div>
          <Card>
            <CardContent className="py-3 text-sm flex flex-col gap-1">
              <span><span className="mono-id">{r.render.workbook}</span> — <b>{r.render.n_rows} rows</b> across {Object.keys(r.render.rows_per_source).length} sheet{Object.keys(r.render.rows_per_source).length === 1 ? "" : "s"}{r.render.layout_from ? <>, written into the layout of <span className="mono-id">{r.render.layout_from}</span> (structure only — no content was read from it)</> : ", in the built-in layout"}.</span>
              <span className="text-muted-foreground">Generated {r.render.rendered_at.slice(0, 16).replace("T", " ")} UTC. Column names are the vendor's, carried as-is. Stage types are String; standard types are promoted from the vendor type per the ACFC coding standard.</span>
              {r.render.unanswered.length > 0 && <span className="text-[var(--brand-flag)]">{r.render.unanswered.length} question(s) were still open when this was generated.</span>}
              {r.render.unpromoted_types.length > 0 && <span className="text-muted-foreground">Vendor types with no promotion rule (kept as String in standard): {r.render.unpromoted_types.join(", ")}.</span>}
              {Object.entries(r.render.unfilled_columns ?? {}).map(([sheet, cols]) => (
                <span key={sheet} className="text-muted-foreground">{sheet}: template columns left empty because no input supplies them — {cols.join(", ")}.</span>
              ))}
            </CardContent>
          </Card>
        </section>
      )}

      {r.preview.length > 0 && !running && (
        <section className="flex flex-col gap-3">
          <h3 className="eyebrow-blue">Rows{r.render ? "" : " the workbook will carry"}</h3>
          {r.preview.map((p) => <PreviewTable key={p.source} p={p} />)}
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

const EXTRACT_STEPS = [
  "Read the FRD (.docx → text, headings and tables kept)",
  "Read the vendor data dictionary (every file, every column, type and flag — parsed exactly, no model involved)",
  "Extract the source files, target tables and rules from the FRD's prose with Claude",
  "Check every identifier verbatim against the FRD; pair each source with its dictionary file",
  "Decide the target side from the FRD and the ACFC standards; list what is still missing",
];
const RENDER_STEPS = [
  "Apply your answers",
  "Build the rows from the dictionary and the audit columns from the standards",
  "Write the workbook into the client's own layout",
];

function Progress({ startedAt, task }: { startedAt?: string; task?: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = startedAt ? Math.max(0, Math.round((now - new Date(startedAt).getTime()) / 1000)) : null;
  const steps = task === "render" ? RENDER_STEPS : EXTRACT_STEPS;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <Spinner /> Running as a Databricks job{elapsed != null ? ` · ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")} elapsed` : ""}
        </CardTitle>
        <CardDescription>The steps below run inside the job; this page refreshes on its own when it finishes.</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="list-decimal pl-5 text-sm flex flex-col gap-1">
          {steps.map((s) => <li key={s}>{s}</li>)}
        </ol>
      </CardContent>
    </Card>
  );
}

function originLabel(v: string | undefined): string {
  if (!v) return "";
  if (v === "frd") return "stated in the FRD";
  if (v.startsWith("standards")) return `from the ACFC naming standards (${v.replace("standards ", "")})`;
  if (v.startsWith("reviewer")) return `your answer${v.includes("(") ? " " + v.slice(v.indexOf("(")) : ""}`;
  if (v === "unknown") return "not stated anywhere — asked below";
  return v;
}

function SourceCard({ s }: { s: SourceEntry }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="mono-id text-sm">{s.feed_name}</CardTitle>
        <CardDescription>
          {s.file ? <>rows from dictionary file <span className="mono-id">{s.file}</span> · {s.n_columns} columns</> : <span className="text-[var(--brand-red)]">no dictionary file paired — see the questions</span>}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <table className="text-sm w-full">
          <tbody>
            {(["stage", "standard"] as const).map((layer) => {
              const l = s.layers[layer];
              return (
                <tr key={layer} className="align-top">
                  <td className="pr-4 py-0.5 text-muted-foreground w-20">{layer}</td>
                  <td className="pr-4 py-0.5 mono-id">{[l.catalog, l.schema].filter(Boolean).join(".") || "?"}.{l.tables.join(", ") || "?"}</td>
                  <td className="py-0.5 text-xs text-muted-foreground">
                    catalog {originLabel(l.origin.catalog)} · schema {originLabel(l.origin.schema)}
                    {l.origin.tables ? ` · tables ${originLabel(l.origin.tables)}` : l.tables.length ? " · tables stated in the FRD" : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
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
        <CardDescription>{p.n_rows} rows{p.file ? <> from <span className="mono-id">{p.file}</span></> : ""} — the vendor's columns plus the ACFC audit columns (source “NA”)</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground border-b">
                <th className="py-1 pr-4 font-normal">Source column</th>
                <th className="py-1 pr-4 font-normal">Vendor type</th>
                <th className="py-1 pr-4 font-normal">Stage target</th>
                <th className="py-1 font-normal">Standard target</th>
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

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "muted" | "alert" }) {
  return (
    <div className={`acfc-stat${tone ? ` acfc-stat--${tone}` : ""}`}>
      <b className="!text-base">{value}</b>
      <span>{label}</span>
      {sub && <span className="!mt-0.5 truncate" title={sub}>{sub}</span>}
    </div>
  );
}
