import { useState } from "react";
import { Alert, AlertDescription, AlertTitle, Button } from "@databricks/appkit-ui/react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { UPLOAD_INTRO, UPLOAD_SCOPE_NOTE } from "../lib/displayText";
import { useStartRun, useUploadFrd } from "../api";

interface Props {
  onStarted: (runId: string) => void;
}

/**
 * File picker -> POST /api/uploads -> POST /api/runs/{run_id}/start, in one
 * user-facing step. This demo's mock-extraction pipeline is scoped to the
 * demo fixture only (see backend/orchestration.py's module docstring) --
 * anything else is refused by the start call with a clear error surfaced
 * here, not a crash.
 */
export function UploadScreen({ onStarted }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const upload = useUploadFrd();
  const start = useStartRun();

  const busy = upload.isPending || start.isPending;

  const handleSubmit = async () => {
    if (!file) return;
    setError(null);
    try {
      const { run_id } = await upload.mutateAsync(file);
      await start.mutateAsync(run_id);
      onStarted(run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload an FRD</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">{UPLOAD_INTRO}</p>
        {/* The control and the note that constrains it are grouped at a
            tighter gap than the card's gap-4, so the note reads as attached
            to the input rather than as another standalone paragraph. This
            wrapper is still `flex flex-col`, so the input wrapper below is
            still a flex item and `w-fit` still behaves as described. */}
        <div className="flex flex-col gap-2">
          {/* `w-fit` is load-bearing: the parent is `flex flex-col`, so this
              wrapper is a flex item and is blockified per spec -- an
              `inline-block` (class or inline style) would compute to `block`
              and stretch. `w-fit` is what makes it hug the control. */}
          <div className="w-fit rounded-sm border border-border bg-muted p-2">
            <input
              type="file"
              accept=".docx"
              aria-label="Choose a Functional Requirements Document (.docx) to upload"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm text-muted-foreground file:mr-3 file:cursor-pointer file:rounded-sm file:border-0 file:bg-primary file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
          </div>
          <p className="text-xs text-muted-foreground">{UPLOAD_SCOPE_NOTE}</p>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertTitle>Couldn't start this run</AlertTitle>
            <AlertDescription className="break-words whitespace-pre-wrap">{error}</AlertDescription>
          </Alert>
        )}
        <Button onClick={handleSubmit} disabled={!file || busy} className="self-start">
          {busy ? "Uploading…" : "Upload and run"}
        </Button>
      </CardContent>
    </Card>
  );
}
