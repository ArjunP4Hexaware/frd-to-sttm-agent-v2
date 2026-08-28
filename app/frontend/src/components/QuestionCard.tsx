import { useId, useState } from "react";
import { Button, Input, Label, RadioGroup, RadioGroupItem } from "@databricks/appkit-ui/react";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "./ui/card";
import { QUESTION_KIND_LABEL, type Question } from "../api";

const OTHER = "__other__";

/** One question, one card. Options as radios; a free-text answer where the
 *  agent allows one. Saving records the answer on the run — nothing is
 *  re-extracted. */
export function QuestionCard({ q, onAnswer, disabled }: { q: Question; onAnswer: (value: string) => Promise<unknown>; disabled: boolean }) {
  const baseId = useId();
  const prior = q.answer?.value ?? null;
  const priorIsOption = prior !== null && q.options.includes(prior);
  const [choice, setChoice] = useState<string | null>(priorIsOption ? prior : prior !== null ? OTHER : null);
  const [text, setText] = useState(prior !== null && !priorIsOption ? prior : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const value = choice === OTHER || q.options.length === 0 ? text.trim() : choice;
  const canSave = !!value && !saving && !disabled;

  async function save() {
    if (!value) return;
    setSaving(true);
    setError(null);
    try {
      await onAnswer(value);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 flex-wrap text-base">
          <span>{QUESTION_KIND_LABEL[q.kind] ?? q.kind}</span>
          {q.source && <span className="mono-id text-xs text-muted-foreground">{q.source}</span>}
          <Badge variant={q.answer ? "success" : "secondary"}>{q.answer ? "answered" : "open"}</Badge>
        </CardTitle>
        <p className="text-sm">{q.text}</p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {q.options.length > 0 && (
          <RadioGroup value={choice ?? ""} onValueChange={setChoice} className="flex flex-wrap gap-4" aria-label="Answer">
            {q.options.map((o, i) => (
              <div key={o} className="flex items-center gap-2">
                <RadioGroupItem value={o} id={`${baseId}-${i}`} disabled={disabled} />
                <Label htmlFor={`${baseId}-${i}`}>{o}</Label>
              </div>
            ))}
            {q.free_text && (
              <div className="flex items-center gap-2">
                <RadioGroupItem value={OTHER} id={`${baseId}-other`} disabled={disabled} />
                <Label htmlFor={`${baseId}-other`}>Other…</Label>
              </div>
            )}
          </RadioGroup>
        )}
        {(q.free_text || q.options.length === 0) && (choice === OTHER || q.options.length === 0) && (
          <Input placeholder="Type the value" value={text} onChange={(e) => setText(e.target.value)} disabled={disabled} />
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>
      <CardFooter className="justify-between">
        <span className="text-xs text-muted-foreground mono-id">
          {q.answer ? `answered ${q.answer.at?.slice(0, 16).replace("T", " ") ?? ""}${q.answer.by ? ` by ${q.answer.by}` : ""}` : "not answered"}
        </span>
        <Button onClick={save} disabled={!canSave}>{saving ? "Saving…" : "Save answer"}</Button>
      </CardFooter>
    </Card>
  );
}
