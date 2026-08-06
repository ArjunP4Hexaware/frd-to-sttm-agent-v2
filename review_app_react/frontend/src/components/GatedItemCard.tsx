import { useId, useState } from "react";
import {
  Button,
  Input,
  Label,
  RadioGroup,
  RadioGroupItem,
  Textarea,
} from "@databricks/appkit-ui/react";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "./ui/card";
import {
  ITEM_KIND_EXPLANATION,
  ITEM_KIND_LABEL,
  ITEM_RULE_LABEL,
  ITEM_TECHNICAL_DETAIL_LABEL,
  displayLabel,
} from "../lib/displayText";
import type { GatedItem, ResolutionType } from "../types";

const NONE_OF_THESE = "__none_of_these__";

interface Props {
  item: GatedItem;
  onSubmit: (payload: {
    resolution_type: ResolutionType;
    chosen_candidate: string | null;
    rationale: string | null;
  }) => Promise<unknown>;
}

/**
 * One gated ambiguity, one card, one independent form.
 *
 * Structural-pick-required policy: when `item.has_candidates`, the radio
 * group is the only way to set `chosen_candidate` -- there is no "other
 * (free text)" option mixed into it the way there used to be. A rationale
 * textarea is always visible alongside the pick, but it only ever becomes
 * `rationale` on the submitted resolution, never `chosen_candidate` --
 * 04_sttm_render.py's apply_human_resolutions() never applies it
 * structurally for a candidate-having ambiguity. When `has_candidates` is
 * false (advisory_grounding today), the single free-text input IS the
 * resolution, unchanged from before.
 *
 * This component is only ever mounted by GatedItemList with
 * `key={`${docId}::${item.id}`}` (see that file). That composite key --
 * not the item's position in the array -- is what React uses to decide
 * whether to reuse this component instance or throw it away and mount a
 * fresh one. Every `useState` below therefore lives and dies with that
 * doc+ambiguity identity: switching documents changes every key in the
 * list, so React unmounts every card here and mounts brand new instances
 * with fresh state, instead of recycling instance #2's state onto
 * whatever item #2 happens to be in the newly selected document. That
 * recycling-by-position is exactly the bug the Streamlit app's
 * `item_{i}` -> `{doc_id}_item_{i}` widget-key fix addressed; scoping the
 * React key by identity instead of index is the same fix in this
 * framework's terms -- `item.id` is a stable hash (see
 * notebooks/_models.py's GatedAmbiguity), so it's an even safer identity
 * than the display text this replaced.
 */
export function GatedItemCard({ item, onSubmit }: Props) {
  const baseId = useId();
  const prior = item.resolution;
  const hasCandidates = item.has_candidates;

  // `null` means "the reviewer has not chosen anything yet" -- it is not a
  // selectable value and has no radio item. Nothing is pre-selected on a
  // fresh item: this used to fall back to NONE_OF_THESE, which meant a
  // reviewer who hit Save without touching the radios had an explicit
  // rejection of every candidate recorded on their behalf. A prior
  // `none_of_these` resolution is still restored below, because that one
  // *was* a real human decision -- what's gone is the manufactured default.
  const [choice, setChoice] = useState<string | null>(() => {
    if (
      prior?.resolution_type === "candidate_pick" &&
      prior.chosen_candidate &&
      item.candidates.includes(prior.chosen_candidate)
    ) {
      return prior.chosen_candidate;
    }
    if (prior?.resolution_type === "none_of_these") return NONE_OF_THESE;
    return null;
  });
  const [rationale, setRationale] = useState(hasCandidates ? (prior?.rationale ?? "") : "");
  const [freeText, setFreeText] = useState(!hasCandidates ? (prior?.rationale ?? "") : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(prior?.resolved_at ?? null);

  const isPick = choice !== null && choice !== NONE_OF_THESE;
  // Two-branch guard, mirroring apply_human_resolutions()'s has_candidates
  // split: a candidate-having item needs an affirmative radio selection
  // ("None of these" counts -- it is a decision), while a candidate-less
  // item has no radios at all, so requiring one would make it permanently
  // unsavable. That case stays on the free-text rule it already had.
  const canSave = hasCandidates ? choice !== null : freeText.trim().length > 0;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = hasCandidates
        ? {
            resolution_type: (isPick ? "candidate_pick" : "none_of_these") as ResolutionType,
            chosen_candidate: isPick ? choice : null,
            rationale: rationale || null,
          }
        : {
            resolution_type: "free_text" as ResolutionType,
            chosen_candidate: null,
            rationale: freeText || null,
          };
      await onSubmit(payload);
      setSavedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  // The one thing the reviewer must actually read. Server-extracted rule
  // text when available; the full rationale otherwise, so the quote block
  // is never blank.
  const quotedRule = item.rule_text?.trim() || item.text;
  const explanation = ITEM_KIND_EXPLANATION[item.kind];

  return (
    <Card>
      {/* Reads top to bottom in the reviewer's order of need: the question
          (title + status chip), the quoted FRD rule, why it's being asked,
          then the machine rationale demoted to small muted text. The
          rationale is item.text verbatim -- demoted, never deleted. */}
      <CardHeader>
        <CardTitle className="flex items-center gap-2 flex-wrap">
          <span>{displayLabel(ITEM_KIND_LABEL, item.kind)}</span>
          <Badge variant={savedAt ? "success" : "secondary"}>
            {savedAt ? "resolved" : "unresolved"}
          </Badge>
        </CardTitle>
        <blockquote className="border-l-2 border-primary pl-3 py-1 mt-1 flex flex-col gap-1">
          <span className="eyebrow">{ITEM_RULE_LABEL}</span>
          <p className="text-sm text-foreground break-words">“{quotedRule}”</p>
        </blockquote>
        {explanation && <p className="text-sm text-muted-foreground">{explanation}</p>}
        <div className="mt-1 flex flex-col gap-0.5">
          <span className="eyebrow">{ITEM_TECHNICAL_DETAIL_LABEL}</span>
          <p className="font-mono text-xs text-muted-foreground break-words whitespace-pre-wrap">
            {item.text}
          </p>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {hasCandidates ? (
          <>
            {/* `choice ?? ""` rather than `undefined`: an empty string is a
                defined value that matches no RadioGroupItem, so nothing
                renders as checked while the group stays controlled the whole
                time. Passing `undefined` would make Radix treat the group as
                uncontrolled until the first pick, then flip it to controlled. */}
            <RadioGroup
              value={choice ?? ""}
              onValueChange={setChoice}
              aria-label="Choose how to resolve this item"
              className="flex flex-wrap gap-4"
            >
              {item.candidates.map((candidate, idx) => (
                <div key={candidate} className="flex items-center gap-2">
                  <RadioGroupItem value={candidate} id={`${baseId}-c${idx}`} />
                  <Label htmlFor={`${baseId}-c${idx}`}>{candidate}</Label>
                </div>
              ))}
              <div className="flex items-center gap-2">
                <RadioGroupItem value={NONE_OF_THESE} id={`${baseId}-none`} />
                <Label htmlFor={`${baseId}-none`}>None of these</Label>
              </div>
            </RadioGroup>
            <Textarea
              placeholder="Rationale (optional — recorded for audit, never applied automatically)"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
            />
          </>
        ) : (
          <Input
            placeholder="Resolution (free text — this ambiguity has no candidates to pick from)"
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
          />
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>

      <CardFooter className="justify-between items-center gap-4">
        <span className="text-xs text-muted-foreground mono-id">
          {savedAt ? `Saved ${new Date(savedAt).toLocaleString()}` : "Not yet saved"}
        </span>
        <div className="flex items-center gap-3">
          {/* A greyed-out button with no explanation reads as broken, which
              is the last thing we want on camera. Same muted type scale the
              rest of this footer already uses -- no new styling invented. */}
          {!canSave && !saving && (
            <span className="text-xs text-muted-foreground text-right">
              {hasCandidates
                ? "Choose an option above to save your review."
                : "Enter your answer above to save your review."}
            </span>
          )}
          <Button onClick={handleSave} disabled={!canSave || saving}>
            {saving ? "Saving…" : "Save resolution"}
          </Button>
        </div>
      </CardFooter>
    </Card>
  );
}
