import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button, Spinner } from "@databricks/appkit-ui/react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "./ui/badge";
import {
  corpusDictionaryUrl,
  useCorpusConfig,
  useCorpusFrds,
  useCorpusSummary,
  useCorpusSync,
  useSharePointConfig,
} from "../demoApi";
import type { CorpusFrd } from "../demoApi";

/**
 * The corpus panel IS the picker (2026-08-22, docs/TEMPLATE_ARCHITECTURE.md):
 *
 * - "FRDs without an STTM" — the work queue. "Generate STTM" hands the FRD
 *   to the billed-run gate.
 * - "FRDs already mapped" — each with its reference STTM and how it was
 *   paired (exact name / similarity + score). "View STTM" presents the
 *   approved workbook; regeneration is a deliberate extra step there.
 * - the sync controls: "Sync now" (document source → volumes → index) and
 *   "Rebuild index" (index from what the volumes hold, no network). Both
 *   two-step, because they rewrite the volumes/index; zero AI calls, hence
 *   no cost copy.
 *
 * The source is SharePoint, or — since 2026-08-24, when Graph access did not
 * land in time for the demo — a folder on the reviewer's machine. The backend
 * picks (corpus_routes._source_client); this component only names it, via
 * `source` on the config probe.
 *
 * The list is read from the corpus index only — never from the source on the
 * request path — so what the reviewer sees is exactly what the volumes hold.
 */
export function CorpusPanel({
  onGenerate,
  onExisting,
  canRun,
}: {
  onGenerate: (frd: CorpusFrd) => void;
  onExisting: (frd: CorpusFrd) => void;
  canRun: boolean;
}) {
  const spConfig = useSharePointConfig();
  const corpusConfig = useCorpusConfig();
  const summary = useCorpusSummary();
  const frds = useCorpusFrds(summary.data?.built ?? false);
  const sync = useCorpusSync();
  const [confirming, setConfirming] = useState<null | "sync" | "reindex">(null);
  const queryClient = useQueryClient();

  const syncState = summary.data?.sync;
  const running = syncState?.state === "running";

  // When a running sync lands, refresh the list (the summary already polls).
  useEffect(() => {
    if (syncState && syncState.state !== "running") {
      queryClient.invalidateQueries({ queryKey: ["demo", "corpus", "frds"] });
    }
  }, [syncState?.state, syncState?.finished_at, queryClient, syncState]);

  function start(mode: "sync" | "reindex") {
    setConfirming(null);
    sync.mutate(mode, {
      onSuccess: () => queryClient.invalidateQueries({ queryKey: ["demo", "corpus"] }),
    });
  }

  const built = summary.data?.built ?? false;
  const configured = spConfig.data?.configured ?? false;
  const canSync = corpusConfig.data?.available ?? false;

  // Where the documents come from. The local folder is the 2026-08-24 demo
  // stand-in for the library (no Graph access in time); the backend decides
  // which is active, this only names it. `sourcePath` is one string because
  // the two shapes differ: a folder is its own full path, a library is
  // site/library[/folder].
  const isLocalFolder = spConfig.data?.source === "local_folder";
  const sourceLabel = isLocalFolder ? "the documents folder" : "SharePoint";
  const sourcePath = isLocalFolder
    ? (spConfig.data?.site ?? "")
    : `${spConfig.data?.site ?? ""}/${spConfig.data?.library ?? ""}`;
  const sttmPath = isLocalFolder
    ? (spConfig.data?.site ?? "")
    : `${sourcePath}${spConfig.data?.sttm_folder ? `/${spConfig.data.sttm_folder}` : ""}`;
  // Three groups, one rule (2026-08-27): only an FRD with a vendor dictionary
  // and no approved STTM may be generated. The other two are still LISTED —
  // hiding them would leave a reviewer wondering where their document went,
  // and each row says exactly what is missing.
  const all = frds.data?.frds ?? [];
  const ready = all.filter((f) => f.eligibility_status === "ready");
  const blocked = all.filter((f) => f.eligibility_status === "no_dictionary");
  const mapped = all.filter((f) => f.eligibility_status === "mapped");

  return (
    <div className="flex flex-col gap-4">
      {built && summary.data && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          <Stat label="FRDs" value={summary.data.n_frds} />
          <Stat label="Approved STTMs" value={summary.data.n_references} />
          <Stat label="Mapped pairs" value={summary.data.n_pairs} />
          <Stat
            label="Awaiting an STTM"
            value={summary.data.n_unmapped}
            tone={summary.data.n_unmapped > 0 ? "alert" : "muted"}
          />
          {/* The third input (2026-08-27). Shown as "paired / total FRDs"
              rather than a bare count, because the number that matters is
              how many feeds have a grounded SOURCE side — not how many
              workbooks happen to sit in the volume. */}
          <Stat
            label="With a dictionary"
            value={summary.data.n_dictionary_pairs ?? 0}
            of={summary.data.n_frds}
            tone={(summary.data.n_dictionary_pairs ?? 0) < summary.data.n_frds ? "alert" : undefined}
          />
          {/* The number that actually matters on this screen: how many feeds
              can be started right now. */}
          <Stat
            label="Ready to map"
            value={summary.data.n_generatable ?? 0}
            tone={(summary.data.n_generatable ?? 0) === 0 ? "muted" : undefined}
          />
        </div>
      )}

      {built && (summary.data?.unpaired_dictionaries?.length ?? 0) > 0 && (
        <div className="acfc-panel acfc-panel--warn text-sm">
          <p className="font-semibold text-[var(--brand-red)]">
            {summary.data!.unpaired_dictionaries.length} vendor dictionary(ies) match no FRD
          </p>
          <p className="text-muted-foreground mt-1">
            <span className="mono-id">{summary.data!.unpaired_dictionaries.join(", ")}</span> — pairing is
            by exact name (<span className="mono-id">DICT_&lt;feed&gt;.xlsx</span> ↔{" "}
            <span className="mono-id">FRD_&lt;feed&gt;.docx</span>) and is never guessed by similarity:
            attaching the wrong vendor’s spec would put real column names and PHI flags on a feed they do
            not describe. Rename the file, or add the FRD.
          </p>
        </div>
      )}

      {!built && summary.isSuccess && !running && (
        <p className="text-sm text-muted-foreground">
          Nothing has been synced yet. “Sync now” pulls every FRD and approved STTM from {sourceLabel}, pairs
          them, and lists them here. No AI calls are made.
        </p>
      )}

      {built && (
        <section className="flex flex-col gap-2">
          <h3 className="acfc-section-title">Ready to map</h3>
          <p className="text-sm text-muted-foreground -mt-1">
            An FRD with a vendor data dictionary and no STTM yet. These are the only ones that can
            be generated.
          </p>
          {ready.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing is ready to map right now — see the two groups below for what each FRD is
              waiting on.
            </p>
          )}
          {ready.map((f) => (
            <div key={f.doc_id} className="acfc-row acfc-row--ready">
              <div className="flex items-center gap-2 min-w-0 flex-wrap">
                <span className="mono-id text-sm truncate">{f.name}</span>
                <DictionaryChip frd={f} />
                {f.web_url && !isLocalFolder && (
                  <a
                    href={f.web_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs underline text-muted-foreground"
                  >
                    SharePoint ↗
                  </a>
                )}
              </div>
              <Button onClick={() => onGenerate(f)} disabled={!canRun || !f.runnable}>
                Generate STTM
              </Button>
            </div>
          ))}
        </section>
      )}

      {built && blocked.length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="acfc-section-title">Waiting on a vendor data dictionary</h3>
          <p className="text-sm text-muted-foreground -mt-1">
            No STTM yet, and nothing to ground the source columns. Ask the vendor for{" "}
            <span className="mono-id">VDD_&lt;feed&gt;.xlsx</span> and name it in the FRD's
            Structural Metadata › Source Data Dictionary row.
          </p>
          {blocked.map((f) => (
            <div key={f.doc_id} className="acfc-row" style={{ borderLeftColor: "var(--brand-red)" }}>
              <div className="flex items-center gap-2 min-w-0 flex-wrap">
                <span className="mono-id text-sm truncate">{f.name}</span>
                <DictionaryChip frd={f} />
              </div>
              <span className="text-xs text-muted-foreground">Cannot be generated</span>
            </div>
          ))}
        </section>
      )}

      {built && mapped.length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="acfc-section-title">Already mapped</h3>
          <p className="text-sm text-muted-foreground -mt-1">
            The approved STTM is the system of record and is never touched. A regenerated draft is
            independent of it — the run reads only the FRD and the vendor dictionary — so the two
            can be compared honestly.
          </p>
          {mapped.map((f) => (
            <div key={f.doc_id} className="acfc-row">
              <div className="flex items-center gap-2 min-w-0 flex-wrap">
                <span className="mono-id text-sm truncate">{f.name}</span>
                <Badge variant={f.confidence === "high" ? "success" : "secondary"}>
                  {f.matched_by === "name" ? "name match" : `${Math.round((f.score ?? 0) * 100)}% match`} ·{" "}
                  {f.reference}
                </Badge>
                <DictionaryChip frd={f} />
              </div>
              <div className="flex gap-2 flex-none">
                <Button variant="outline" onClick={() => onExisting(f)}>
                  View STTM
                </Button>
                {/* Regeneration returned 2026-08-27 with the two-input rule:
                    the run never opens this feed's own workbook, so the draft
                    is independent and the comparison is a real measurement. */}
                {f.generatable && (
                  <Button onClick={() => onGenerate(f)} disabled={!canRun || !f.runnable}>
                    Regenerate
                  </Button>
                )}
              </div>
            </div>
          ))}
        </section>
      )}

      {syncState?.state === "running" && (
        <Alert>
          <AlertTitle className="flex items-center gap-2">
            <Spinner /> {syncState.mode === "reindex" ? "Rebuilding the index…" : `Syncing from ${sourceLabel}…`}
          </AlertTitle>
          <AlertDescription>
            {syncState.run_page_url ? (
              <a href={syncState.run_page_url} target="_blank" rel="noreferrer" className="underline">
                View the sync job run in Databricks ↗
              </a>
            ) : (
              "No AI calls are made."
            )}
          </AlertDescription>
        </Alert>
      )}
      {syncState?.state === "failed" && (
        <Alert variant="destructive">
          <AlertTitle>Sync failed</AlertTitle>
          <AlertDescription>{syncState.error}</AlertDescription>
        </Alert>
      )}
      {sync.isError && (
        <Alert variant="destructive">
          <AlertDescription>{(sync.error as Error).message}</AlertDescription>
        </Alert>
      )}
      {syncState?.state === "done" && Array.isArray(syncState.summary?.skipped) &&
        (syncState.summary.skipped as { name: string; error: string }[]).length > 0 && (
          <Alert>
            <AlertTitle>Some library files were skipped</AlertTitle>
            <AlertDescription>
              {(syncState.summary.skipped as { name: string; error: string }[])
                .map((s) => `${s.name}: ${s.error}`)
                .join("; ")}
            </AlertDescription>
          </Alert>
        )}

      <div className="flex items-center gap-2 flex-wrap">
        {confirming === null ? (
          <>
            <Button
              variant="outline"
              disabled={running || sync.isPending || !canSync}
              onClick={() => setConfirming("sync")}
              title={canSync ? undefined : "No document source is configured on this backend"}
            >
              {running && syncState?.mode === "sync" ? "Syncing…" : `Sync from ${sourceLabel} now…`}
            </Button>
            <Button
              variant="ghost"
              disabled={running || sync.isPending}
              onClick={() => setConfirming("reindex")}
            >
              Rebuild index…
            </Button>
          </>
        ) : confirming === "sync" ? (
          <>
            <span className="text-sm text-muted-foreground">
              Pulls new and changed FRDs and STTMs from{" "}
              <span className="mono-id">{sourcePath}</span> into the raw/reference volumes and re-pairs them
              (no AI calls). Continue?
            </span>
            <Button onClick={() => start("sync")}>Sync now</Button>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <span className="text-sm text-muted-foreground">
              Rebuilds the pairing index from the FRDs and STTMs already in the volumes — it does not
              re-read {sourceLabel}, and makes no AI calls. Continue?
            </span>
            <Button onClick={() => start("reindex")}>Rebuild now</Button>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </>
        )}
        {built && summary.data?.generated_at && (
          <span className="text-xs text-muted-foreground mono-id">
            index {summary.data.generated_at.slice(0, 16).replace("T", " ")}
            {summary.data.synced_at ? ` · synced ${summary.data.synced_at.slice(0, 16).replace("T", " ")}` : ""}
          </span>
        )}
      </div>
      {configured && spConfig.data?.sttm_folder != null && (
        <p className="text-xs text-muted-foreground">
          Finished STTMs belong in <span className="mono-id">{sttmPath}</span>{" "}
          — {isLocalFolder ? "save yours there" : "upload yours there"} and the next sync pairs it with its
          FRD.
        </p>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  of,
  tone,
}: {
  label: string;
  value: number;
  /** Renders "3 / 7" — used where the count is only meaningful against a
   *  denominator (feeds WITH a dictionary, out of all feeds). */
  of?: number;
  tone?: "muted" | "alert";
}) {
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

/**
 * The vendor data dictionary's state for one FRD — the third input made
 * visible (2026-08-27).
 *
 * "No vendor dictionary" is rendered as a RED chip, not as an absent one, and
 * that is the whole point of the component: without a dictionary the source
 * side of the STTM has no grounded input, so the run will leave those columns
 * blank and raise a named question. A reviewer about to spend a billed run
 * has to be able to see that BEFORE they spend it, not after.
 *
 * When a dictionary IS present the chip carries its column count, and — if the
 * parser found gaps in it — how many. "A dictionary exists" and "a dictionary
 * that describes every column exists" are different facts.
 */
function DictionaryChip({ frd }: { frd: CorpusFrd }) {
  if (!frd.has_dictionary) {
    return (
      <span className="acfc-chip acfc-chip--gap" title="The source side cannot be grounded without one">
        no vendor dictionary
      </span>
    );
  }
  const gaps = frd.dictionary_problems;
  return (
    <a
      href={corpusDictionaryUrl(frd.dictionary!)}
      download
      className={`acfc-chip ${gaps > 0 ? "acfc-chip--quiet" : "acfc-chip--ok"} no-underline`}
      title={
        gaps > 0
          ? `${frd.dictionary} — ${gaps} gap(s): ${frd.dictionary_problem_kinds.join(", ")}`
          : `${frd.dictionary} — download`
      }
    >
      dictionary · {frd.dictionary_fields} columns
      {gaps > 0 ? ` · ${gaps} gap${gaps === 1 ? "" : "s"}` : ""}
    </a>
  );
}
