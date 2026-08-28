import { useEffect } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import {
  STATUS_LABEL,
  VDD_PROBLEM_LABEL,
  documentUrl,
  useDocuments,
  useReindex,
  useReindexState,
  useRuns,
  useStartRun,
  type DocumentEntry,
} from "../api";
import { HowItWorks } from "./HowItWorks";

/**
 * The picker: every FRD in the volumes, grouped by what it can do.
 *   Ready to map      — has a vendor data dictionary, no STTM yet
 *   Already mapped    — has an approved STTM; a new draft never reads it
 *   Waiting on a VDD  — cannot be generated; the row says why
 */
export function Picker({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const docs = useDocuments();
  const runs = useRuns();
  const reindex = useReindex();
  const reindexState = useReindexState();
  const start = useStartRun();
  const qc = useQueryClient();

  useEffect(() => {
    if (reindexState.data?.state === "done" || reindexState.data?.state === "failed") {
      qc.invalidateQueries({ queryKey: ["documents"] });
    }
  }, [reindexState.data?.state, reindexState.data?.finished_at, qc]);

  const all = docs.data?.documents ?? [];
  const ready = all.filter((d) => d.status === "ready");
  const mapped = all.filter((d) => d.status === "mapped");
  const blocked = all.filter((d) => !d.generatable);
  const reindexing = reindexState.data?.state === "running";
  const busy = start.isPending;

  function generate(d: DocumentEntry) {
    start.mutate(d.doc_id, { onSuccess: (r) => onOpenRun(r.run_id) });
  }

  return (
    <div className="flex flex-col gap-7">
      <div>
        <p className="eyebrow-blue mb-1">How it works</p>
        <HowItWorks />
      </div>

      <div className="flex flex-col gap-4">
        <div>
          <p className="eyebrow-blue mb-1">Select FRD</p>
          <h2 className="acfc-section-title text-xl mb-1.5">Pick the source to map</h2>
          <p className="text-sm text-muted-foreground max-w-3xl">
            Every FRD in the <span className="mono-id">frds</span> volume, paired by name with its vendor data
            dictionary (<span className="mono-id">VDD_&lt;same name&gt;.xlsx</span>) and, where one exists, the approved
            STTM. An FRD can be generated only when its dictionary is present.
          </p>
        </div>

        {docs.isLoading && (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Spinner /> Reading the volumes…
          </p>
        )}
        {docs.isError && (
          <Alert variant="destructive">
            <AlertTitle>Cannot read the document index</AlertTitle>
            <AlertDescription>{(docs.error as Error).message}</AlertDescription>
          </Alert>
        )}

        {docs.data && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Stat label="FRDs in the volume" value={all.length} />
            <Stat label="With a vendor dictionary" value={all.filter((d) => d.vdd).length} of={all.length} tone={all.some((d) => !d.vdd) ? "alert" : undefined} />
            <Stat label="With an approved STTM" value={mapped.length} tone="muted" />
            <Stat label="Can be generated" value={ready.length + mapped.length} tone={ready.length + mapped.length === 0 ? "alert" : undefined} />
          </div>
        )}

        {start.isError && (
          <Alert variant="destructive">
            <AlertTitle>The run could not be started</AlertTitle>
            <AlertDescription>{(start.error as Error).message}</AlertDescription>
          </Alert>
        )}

        {docs.data && !docs.data.built && (
          <div className="acfc-notice acfc-notice--warn">
            <b>No index yet</b>
            <span>Put documents in the volumes and press “Reindex the volumes”.</span>
          </div>
        )}

        {ready.length > 0 && (
          <Group title="Ready to map" hint="A vendor data dictionary is paired and no approved STTM exists yet.">
            {ready.map((d) => (
              <Row key={d.doc_id} d={d} action={<Button onClick={() => generate(d)} disabled={busy}>{busy ? "Starting…" : "Generate STTM"}</Button>} />
            ))}
          </Group>
        )}
        {mapped.length > 0 && (
          <Group title="Already mapped" hint="An approved STTM exists. It is never read for content — a new draft comes from the FRD and the dictionary alone, so it can be compared with the approved workbook honestly.">
            {mapped.map((d) => (
              <Row key={d.doc_id} d={d} action={<Button onClick={() => generate(d)} disabled={busy}>{busy ? "Starting…" : "Generate STTM"}</Button>} />
            ))}
          </Group>
        )}
        {blocked.length > 0 && (
          <Group title="Cannot be generated yet" hint="Nothing grounds the source columns until a vendor data dictionary with the same name is added.">
            {blocked.map((d) => (
              <Row key={d.doc_id} d={d} action={<span className="text-xs text-[var(--brand-red)] max-w-xs text-right">{d.reason}</span>} />
            ))}
          </Group>
        )}
        {docs.data && (docs.data.unpaired_vdds.length > 0 || Object.keys(docs.data.vdd_errors).length > 0) && (
          <div className="acfc-panel acfc-panel--warn text-sm flex flex-col gap-1">
            {docs.data.unpaired_vdds.length > 0 && (
              <p>
                <b className="text-[var(--brand-red)]">Dictionaries matching no FRD:</b>{" "}
                <span className="mono-id">{docs.data.unpaired_vdds.join(", ")}</span> — pairing is by exact name, never by guess.
              </p>
            )}
            {Object.entries(docs.data.vdd_errors).map(([n, e]) => (
              <p key={n}>
                <b className="text-[var(--brand-red)]">Unreadable dictionary</b> <span className="mono-id">{n}</span>: {e}
              </p>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3 flex-wrap border-t pt-4">
          <Button variant="outline" disabled={reindexing || reindex.isPending} onClick={() => reindex.mutate(undefined, { onSuccess: () => qc.invalidateQueries({ queryKey: ["reindex"] }) })}>
            {reindexing ? <><Spinner /> Reindexing…</> : "Reindex the volumes"}
          </Button>
          <span className="text-xs text-muted-foreground">
            Re-reads the four volumes and re-pairs FRDs, dictionaries and approved STTMs. No model call.
            {docs.data?.generated_at ? <> Last indexed <span className="mono-id">{docs.data.generated_at.slice(0, 16).replace("T", " ")} UTC</span>.</> : ""}
          </span>
          {reindexState.data?.url && (
            <a href={reindexState.data.url} target="_blank" rel="noreferrer" className="text-xs underline">job run ↗</a>
          )}
          {reindexState.data?.state === "failed" && <span className="text-xs text-[var(--brand-red)]">{reindexState.data.error}</span>}
        </div>
      </div>

      {runs.data && runs.data.runs.length > 0 && (
        <Group title="Runs" hint="Every run, newest first. Open one to answer its questions, regenerate, or download the workbook.">
          {runs.data.runs.slice(0, 12).map((r) => (
            <div key={r.run_id} className="acfc-row">
              <div className="flex items-center gap-3 min-w-0 flex-wrap">
                <span className="mono-id text-xs text-muted-foreground">{r.run_id}</span>
                <span className="mono-id text-sm truncate">{r.doc_id}</span>
                <span className={`acfc-chip ${r.status === "rendered" ? "acfc-chip--ok" : r.status === "failed" || r.status === "cannot_generate" ? "acfc-chip--gap" : "acfc-chip--quiet"}`}>
                  {r.live?.phase === "running" ? "running" : STATUS_LABEL[r.status] ?? r.status}
                </span>
                {r.n_questions > 0 && <span className="text-xs text-muted-foreground">{r.n_answered}/{r.n_questions} questions answered</span>}
                {r.n_sources > 0 && <span className="text-xs text-muted-foreground">{r.n_sources} source{r.n_sources === 1 ? "" : "s"}</span>}
              </div>
              <Button variant="outline" onClick={() => onOpenRun(r.run_id)}>Open</Button>
            </div>
          ))}
        </Group>
      )}
    </div>
  );
}

function Group({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="acfc-section-title">{title}</h3>
      <p className="text-sm text-muted-foreground -mt-1">{hint}</p>
      {children}
    </section>
  );
}

function Row({ d, action }: { d: DocumentEntry; action: React.ReactNode }) {
  const gaps = d.vdd_summary?.problems ?? [];
  return (
    <div className={`acfc-row${d.generatable ? " acfc-row--ready" : ""}`} style={d.generatable ? undefined : { borderLeftColor: "var(--brand-red)" }}>
      <div className="flex items-center gap-2 min-w-0 flex-wrap">
        <a className="mono-id text-sm truncate no-underline" href={documentUrl(d.doc_id, "frd")} title="Download the FRD">{d.frd}</a>
        {d.vdd ? (
          <a
            className={`acfc-chip ${gaps.length > 0 ? "acfc-chip--quiet" : "acfc-chip--ok"} no-underline`}
            href={documentUrl(d.doc_id, "vdd")}
            title={`${d.vdd}${gaps.length > 0 ? ` — ${gaps.map((g) => VDD_PROBLEM_LABEL[g] ?? g).join("; ")}` : ""}`}
          >
            dictionary · {d.vdd_summary?.n_fields ?? 0} columns · {d.vdd_summary?.n_files ?? 0} file{d.vdd_summary?.n_files === 1 ? "" : "s"}
            {gaps.length > 0 ? ` · ${gaps.length} gap${gaps.length === 1 ? "" : "s"}` : ""}
          </a>
        ) : (
          <span className="acfc-chip acfc-chip--gap">no vendor dictionary</span>
        )}
        {d.sttm && (
          <a className="acfc-chip acfc-chip--quiet no-underline" href={documentUrl(d.doc_id, "sttm")} title={`${d.sttm} — layout reference only`}>
            approved STTM · {d.sttm_summary?.n_columns ?? 0} rows
          </a>
        )}
      </div>
      <div className="flex-none">{action}</div>
    </div>
  );
}

function Stat({ label, value, of, tone }: { label: string; value: number; of?: number; tone?: "muted" | "alert" }) {
  return (
    <div className={`acfc-stat${tone ? ` acfc-stat--${tone}` : ""}`}>
      <b>
        {value}
        {of != null && <span className="opacity-45 font-normal"> / {of}</span>}
      </b>
      <span>{label}</span>
    </div>
  );
}
