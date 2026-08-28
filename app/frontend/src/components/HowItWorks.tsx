/** The agent in one strip: what goes in, what it does, what comes out. */
const STEPS: { n: string; title: string; body: string }[] = [
  { n: "1", title: "Two documents", body: "The FRD (what to ingest, where it lands) and the vendor data dictionary (every source column)." },
  { n: "2", title: "Extract and check", body: "One Claude call reads the FRD. Every identifier it returns is checked verbatim against the document." },
  { n: "3", title: "Ask, never guess", body: "Anything the documents do not settle becomes a question for you. Missing inputs stop the run." },
  { n: "4", title: "Build the STTM", body: "Rows from the dictionary, targets from the FRD and the ACFC standards, in the client's own workbook layout." },
];

export function HowItWorks() {
  return (
    <ol className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
      {STEPS.map((s) => (
        <li key={s.n} className="acfc-panel acfc-panel--quiet">
          <div className="flex items-baseline gap-2">
            <span className="mono-id text-[var(--brand-blue)] font-bold">{s.n}</span>
            <span className="font-semibold text-[var(--brand-navy)]">{s.title}</span>
          </div>
          <p className="text-sm text-muted-foreground mt-1">{s.body}</p>
        </li>
      ))}
    </ol>
  );
}
