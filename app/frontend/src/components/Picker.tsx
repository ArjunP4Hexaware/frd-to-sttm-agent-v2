import { useEffect, useRef, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import {
  VDD_PROBLEM_LABEL,
  documentUrl,
  useDocuments,
  useReindexState,
  useStartRun,
  useUploadPair,
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
    start.mutate({ doc_id: d.doc_id }, { onSuccess: (r) => onOpenRun(r.run_id) });
  }

  return (
    <div className="flex flex-col gap-7">
      <div>
        <p className="eyebrow-blue mb-1">How it works</p>
        <HowItWorks />
      </div>

      <UploadPair onOpenRun={onOpenRun} disabled={busy} />

      <div className="flex flex-col gap-4">
        <div>
          <p className="eyebrow-blue mb-1">Or select an FRD already in the volumes</p>
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

        {docs.data && !docs.data.built && !reindexing && (
          <div className="acfc-notice acfc-notice--warn">
            <b>No documents indexed yet</b>
            <span>Put FRD_ / VDD_ / STTM_ files in the volumes; the app indexes them on start-up.</span>
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

        <div className="flex items-center gap-3 flex-wrap border-t pt-3 text-xs text-muted-foreground">
          {reindexing ? (
            <span className="flex items-center gap-2"><Spinner /> Indexing the volumes — pairing FRDs, dictionaries and approved STTMs. No model call.</span>
          ) : (
            <span>
              The volumes are indexed automatically when the app starts and after every upload.
              {docs.data?.generated_at ? <> Last indexed <span className="mono-id">{docs.data.generated_at.slice(0, 16).replace("T", " ")} UTC</span>.</> : ""}
            </span>
          )}
          {reindexState.data?.url && reindexing && (
            <a href={reindexState.data.url} target="_blank" rel="noreferrer" className="underline">indexing job ↗</a>
          )}
          {reindexState.data?.state === "failed" && <span className="text-[var(--brand-red)]">Indexing failed: {reindexState.data.error}</span>}
        </div>
      </div>

    </div>
  );
}

/**
 * Generate from two files chosen here and now. The pair is stored under the run
 * it starts, not in the volumes, and is used exactly as given: a person said
 * these two go together, so no `FRD_<x>` / `VDD_<x>` name has to match.
 */
function UploadPair({ onOpenRun, disabled }: { onOpenRun: (runId: string) => void; disabled: boolean }) {
  const upload = useUploadPair();
  const [frd, setFrd] = useState<File | null>(null);
  const [vdd, setVdd] = useState<File | null>(null);
  const frdRef = useRef<HTMLInputElement>(null);
  const vddRef = useRef<HTMLInputElement>(null);
  const busy = upload.isPending || disabled;

  function submit() {
    if (!frd || !vdd) return;
    upload.mutate(
      { frd, vdd },
      {
        onSuccess: (r) => {
          setFrd(null);
          setVdd(null);
          if (frdRef.current) frdRef.current.value = "";
          if (vddRef.current) vddRef.current.value = "";
          onOpenRun(r.run_id);
        },
      },
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <div>
        <p className="eyebrow-blue mb-1">Upload a pair</p>
        <h2 className="acfc-section-title text-xl mb-1.5">Generate from your own two files</h2>
        <p className="text-sm text-muted-foreground max-w-3xl">
          Pick an FRD and the vendor data dictionary that goes with it. They are used exactly as you pair them —
          the <span className="mono-id">FRD_&lt;x&gt;</span> / <span className="mono-id">VDD_&lt;x&gt;</span> naming
          does not have to match, because you said these two belong together. Neither file is added to the volumes:
          both are kept with the run they start.
        </p>
      </div>

      <div className="acfc-panel flex flex-col gap-3">
        <div className="grid sm:grid-cols-2 gap-3">
          <FilePick
            label="FRD"
            hint=".docx, .pdf, .md or .txt"
            accept=".docx,.pdf,.md,.markdown,.txt"
            file={frd}
            inputRef={frdRef}
            onPick={setFrd}
            disabled={busy}
          />
          <FilePick
            label="Vendor data dictionary"
            hint=".xlsx"
            accept=".xlsx"
            file={vdd}
            inputRef={vddRef}
            onPick={setVdd}
            disabled={busy}
          />
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <Button onClick={submit} disabled={busy || !frd || !vdd}>
            {upload.isPending ? "Uploading…" : "Generate STTM"}
          </Button>
          <span className="text-xs text-muted-foreground">
            The agent reads both documents, then asks you only what they do not settle.
          </span>
        </div>
      </div>

      {upload.isError && (
        <Alert variant="destructive">
          <AlertTitle>The upload could not be started</AlertTitle>
          <AlertDescription>{(upload.error as Error).message}</AlertDescription>
        </Alert>
      )}
    </section>
  );
}

function FilePick({
  label,
  hint,
  accept,
  file,
  inputRef,
  onPick,
  disabled,
}: {
  label: string;
  hint: string;
  accept: string;
  file: File | null;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onPick: (f: File | null) => void;
  disabled: boolean;
}) {
  return (
    <label className={`acfc-row${file ? " acfc-row--ready" : ""} cursor-pointer items-start flex-col gap-1`}>
      <span className="text-sm font-medium">
        {label} <span className="text-xs text-muted-foreground font-normal">— {hint}</span>
      </span>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        disabled={disabled}
        className="text-xs max-w-full"
        onChange={(e) => onPick(e.target.files?.[0] ?? null)}
      />
      {file && <span className="mono-id text-xs truncate max-w-full">{file.name}</span>}
    </label>
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
