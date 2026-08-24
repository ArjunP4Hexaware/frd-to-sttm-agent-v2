"""
frdsttm.live_extraction — the live Anthropic extraction transport.

Schema-in-prompt + client-side validation, NOT server-side structured
outputs. `client.messages.parse(output_format=FrdIngestionSpec)` cannot be
used: the API compiles the schema into a server-side grammar, and
FrdIngestionSpec's grammar (38 properties, 25 nullable anyOf unions,
additionalProperties:false throughout) deterministically exceeds the
compiler's complexity limit with `400 "The compiled grammar is too large"`
— measured, not assumed; see docs/LIVE_E2E_2026-08-07.md defect D1, where
this exact transport was validated live (0/50 hallucinated identifiers,
strict grounding 45/45, eval identical to the curated baseline).

The posture matches the removed Gemini path's: the full JSON schema —
field descriptions and all, so the per-field guidance still reaches the
model — is rendered into the user prompt, the call is a plain streaming
`messages.stream()` (streaming so a large max_tokens cannot hit HTTP
timeouts — R3 mitigation), and the response is validated client-side with
`FrdIngestionSpec.model_validate_json`, whose `extra="forbid"` re-creates
the schema-drift guard the server can no longer provide.

Fail-loudly semantics: a validation failure raises immediately, naming the
failed fields. There is deliberately NO re-ask/repair loop — a schema
error is a deterministic model-output shape error, and silently re-asking
would blur the quality signal the first failure carries.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from pydantic import ValidationError

from frdsttm.models import FrdIngestionSpec

#: Databricks Foundation Model APIs serve Claude under a `databricks-` prefixed
#: name (`databricks-claude-opus-5`). Same models, same Messages API.
DATABRICKS_MODEL_PREFIX = "databricks-"

#: The Anthropic-compatible endpoint on a workspace. Databricks' own docs use
#: the literal api_key "unused" — the bearer token in the header authenticates.
DATABRICKS_ANTHROPIC_PATH = "/serving-endpoints/anthropic"
DATABRICKS_UNUSED_API_KEY = "unused"


def databricks_model_name(name: str) -> str:
    """First-party model id -> its Databricks-served twin.

    `claude-opus-5` -> `databricks-claude-opus-5`; an already-prefixed name
    is returned unchanged so the model can be pinned explicitly. Deterministic
    rename with one possible answer — the repo's "refusing to guess" rule is
    about ambiguous CONFIGURATION, not about a 1:1 mapping.
    """
    return name if name.startswith(DATABRICKS_MODEL_PREFIX) else f"{DATABRICKS_MODEL_PREFIX}{name}"


def build_live_client(provider: str, *, max_retries: int,
                      anthropic_module=None, workspace_config=None):
    """The Anthropic SDK client for the selected live provider.

    ``provider`` is "databricks" or anything else (treated as first-party
    Anthropic). Both return the SAME `anthropic.Anthropic` type, so
    `extract_live` and everything downstream stay provider-agnostic — only the
    front door and the credential differ:

      anthropic   ANTHROPIC_API_KEY from the environment / secret scope
      databricks  the workspace credential; NO Anthropic key exists or is read

    Verified against the Hexaware workspace 2026-08-24, including
    `messages.stream()` at max_tokens=64000, which `extract_live` requires.

    CAVEAT: the Databricks auth header is snapshotted at construction. A token
    that expires mid-run would 401 rather than refresh; for a run longer than
    the token's lifetime, rebuild the client.

    ``anthropic_module`` / ``workspace_config`` are injection seams for tests
    so the suite never imports the SDK or touches a workspace.
    """
    if anthropic_module is None:
        import anthropic as anthropic_module  # noqa: PLC0415 — optional at import time

    if provider != "databricks":
        return anthropic_module.Anthropic(max_retries=max_retries)

    if workspace_config is None:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415

        workspace_config = WorkspaceClient().config

    host = workspace_config.host.rstrip("/")
    return anthropic_module.Anthropic(
        api_key=DATABRICKS_UNUSED_API_KEY,
        base_url=host + DATABRICKS_ANTHROPIC_PATH,
        default_headers=workspace_config.authenticate(),
        max_retries=max_retries,
    )


def build_extraction_prompt(content: str, schema: dict | None = None,
                            exemplars: str | None = None) -> str:
    """The full user prompt: output contract, JSON schema (with every field
    description — that is where the extraction guidance lives), optionally a
    retrieved-exemplars block (frdsttm.exemplars — client conventions from
    approved pairs, NEVER a source of facts; stage 03's grounding audit
    enforces that), then the parsed FRD markdown.

    Ordering is deliberate for prompt caching: the instruction + schema
    prefix is byte-identical across every call; exemplars vary per document
    and sit after it; the FRD comes last. `exemplars=None` reproduces the
    pre-corpus prompt byte-for-byte.
    """
    if schema is None:
        schema = FrdIngestionSpec.model_json_schema()
    exemplar_section = ("\n\n" + exemplars.rstrip("\n")) if exemplars else ""
    return (
        "Extract the ingestion specification from the FRD below.\n\n"
        "Respond with ONLY a single JSON object (no markdown fences, no "
        "prose) that validates against this JSON schema. Emit every property "
        "explicitly; use null or [] for anything the document does not "
        "state; never add properties not in the schema.\n\n"
        "JSON SCHEMA:\n" + json.dumps(schema, indent=2) +
        exemplar_section +
        "\n\nFRD DOCUMENT (parsed markdown):\n" + content
    )


def _strip_fences(raw: str) -> str:
    """Tolerate a fenced ```json block despite the no-fences instruction —
    cheap to strip, and refusing to would fail the run over formatting."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def parse_extraction_response(raw_text: str, doc_id: str) -> FrdIngestionSpec:
    """Validate the model's JSON into FrdIngestionSpec (`extra="forbid"`).

    Raises RuntimeError naming every failed field. No repair loop — see
    module docstring.
    """
    cleaned = _strip_fences(raw_text)
    try:
        return FrdIngestionSpec.model_validate_json(cleaned)
    except ValidationError as exc:
        failed = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
            for e in exc.errors()
        )
        raise RuntimeError(
            f"{doc_id}: live extraction JSON failed FrdIngestionSpec "
            f"validation ({exc.error_count()} error(s)) — {failed}. "
            f"Schema errors are deterministic model-output shape errors and "
            f"are not retried; investigate the raw response."
        ) from exc


def extract_live(client, doc_id: str, content: str, *, model: str,
                 max_tokens: int, system_prompt: str,
                 exemplars: str | None = None) -> SimpleNamespace:
    """One streaming extraction call. Returns a namespace mirroring the mock
    branch's response shape (`stop_reason`, `parsed_output`, `usage`) so
    everything downstream stays provider-agnostic.

    An unusable stop_reason (`refusal` / `max_tokens`) is returned with
    `parsed_output=None` rather than raised here — the caller's existing
    assertions own that error message. Transient 429/5xx retries belong to
    the client (`max_retries`), unchanged.
    """
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user",
                   "content": build_extraction_prompt(content, exemplars=exemplars)}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason in {"refusal", "max_tokens"}:
        return SimpleNamespace(
            stop_reason=msg.stop_reason,
            stop_details=getattr(msg, "stop_details", None),
            parsed_output=None,
            usage=msg.usage,
        )

    raw = "".join(b.text for b in msg.content if b.type == "text")
    spec = parse_extraction_response(raw, doc_id)
    return SimpleNamespace(
        stop_reason=msg.stop_reason,
        parsed_output=spec,
        usage=msg.usage,
    )
