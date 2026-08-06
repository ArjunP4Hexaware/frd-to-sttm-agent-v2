import { useState } from "react";
import { Button, Collapsible, CollapsibleContent, CollapsibleTrigger } from "@databricks/appkit-ui/react";

interface Props {
  contract: Record<string, unknown>;
}

const PRE_CLASS =
  "text-xs font-mono whitespace-pre-wrap break-words bg-muted/40 rounded p-3 max-h-[32rem] overflow-auto";

function FeedFieldsSection({ name, fields }: { name: string; fields: unknown[] }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" className="self-start">
          {open ? "Hide" : "Show"} {name}: fields ({fields.length})
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        {/* Conditional on `open`, not just CSS-hidden by Collapsible's default
            behavior -- this is what makes the (potentially hundreds-of-item)
            JSON.stringify below a genuinely lazy render, not just a hidden one. */}
        {open && <pre className={`${PRE_CLASS} mt-2`}>{JSON.stringify(fields, null, 2)}</pre>}
      </CollapsibleContent>
    </Collapsible>
  );
}

/**
 * Full contract JSON, with `feeds[*].fields` collapsed by default -- that's
 * the bulk of the file (300+ KB on MIDS, one entry per source column with
 * its derived stage/standard mapping). Everything else -- top-level
 * metadata (project, in_scope, assumptions_constraints_dependencies, etc.)
 * and `_provenance` (ambiguities, resolution_audit, human_resolutions) --
 * is shown expanded immediately below, since that's what's actually small
 * and worth seeing without an extra click.
 */
export function ContractJsonView({ contract }: Props) {
  const feeds = Array.isArray(contract.feeds) ? (contract.feeds as Record<string, unknown>[]) : [];

  const sanitized = {
    ...contract,
    feeds: feeds.map(({ fields, ...rest }) => ({
      ...rest,
      fields: Array.isArray(fields) ? `[${fields.length} field(s) — see below]` : fields,
    })),
  };

  return (
    <div className="flex flex-col gap-3">
      <pre className={PRE_CLASS}>{JSON.stringify(sanitized, null, 2)}</pre>
      {feeds.map((feed, idx) => {
        const fields = Array.isArray(feed.fields) ? (feed.fields as unknown[]) : [];
        const name = typeof feed.feed_name === "string" ? feed.feed_name : `feed ${idx}`;
        return <FeedFieldsSection key={`${name}-${idx}`} name={name} fields={fields} />;
      })}
    </div>
  );
}
