# frd-to-sttm-agent — skill authoring notes

Moved out of `.claude/skills/frd-to-sttm-agent/SKILL.md` on 2026-08-12.

## Judgment calls made while writing this skill

- **No `references/` subfolder.** The rebuild spec is already the deep reference and lives in `docs/`. Duplicating it under `.claude/skills/frd-to-sttm-agent/references/` would create the exact "edit one, forget the other" trap the shared label contract exists to avoid. Instead this file links to `docs/NATIVE_REBUILD_SPEC.md` and the other in-repo docs by relative path.
- **Two-audience description string.** The frontmatter deliberately names both trigger sets in one string (people working in this repo AND people designing a similar agent elsewhere). Splitting into two skills would fragment the material; making the description generic would lose the pushy, concrete-trigger language the model uses to decide when to load.
- **Included the D2 bug by name.** It is repo-specific in its details but pattern-general in its shape (better extraction → fewer removals → stale gate), so it appears in both Part A (as a lesson) and Part B (as a rule) rather than being buried.
- **Left non-determinism in Part B.** It is inherent to LLM-plus-strict-audit designs, not a demo-only quirk, so it belongs in the reusable pattern — not just the runbook pointer in Part A.
- **Did not restate `NATIVE_REBUILD_SPEC.md` §6 content** even though it is directly relevant to Part B. The prompt was explicit about pointing to that section rather than summarizing it, so Part B ends with a single paragraph flagging the port and directing the reader there.
- **Left `disable-model-invocation` and `user-invocable` unset**, per the prompt.
- **Kept the file well under 500 lines** (≈220 lines) without needing to split.
