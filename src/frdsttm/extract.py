"""
frdsttm.extract — the one Claude call: FRD markdown → FrdIngestionSpec.

Schema-in-prompt + client-side validation (server-side structured outputs
reject this schema as too large — measured). The call streams so a large
max_tokens cannot hit an HTTP timeout. A validation failure raises naming
the failed fields; there is no repair loop.

Providers: ``databricks`` (Claude through the workspace's Foundation Model
APIs — the workspace credential authenticates, no Anthropic key) or
``anthropic`` (ANTHROPIC_API_KEY). Same SDK type either way.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from pydantic import ValidationError

from frdsttm.models import FrdIngestionSpec

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 64000
DATABRICKS_MODEL_PREFIX = "databricks-"
DATABRICKS_ANTHROPIC_PATH = "/serving-endpoints/anthropic"

SYSTEM_PROMPT = (
    "You are extracting a structured feed-level ingestion specification from a "
    "Functional Requirements Document (FRD) for onboarding vendor/state source "
    "files into a healthcare data lake (source-to-stage-to-standard).\n\n"
    "Extract ONLY facts stated in the document. Use null (or an empty array) for "
    "anything the document does not state. Never invent file names, table names, "
    "schemas, schedules, or rules. Preserve identifiers, file name patterns, "
    "paths, and table names verbatim, including placeholders like YYYYMMDD.\n\n"
    "Per-feed rules are often written as prose inside 'Data Ingestion "
    "Requirements' subsections and cover several files at once — split them and "
    "attribute each rule to the feed whose file or table it names, even when "
    "nearby template rows read 'NA' or are blank. Recycle rules and column-"
    "conditioned rules are especially prone to this attribution problem; "
    "downstream code will gate ambiguities for human review, so it is safer to "
    "attach a rule to every plausibly-named feed than to guess a single owner."
)


def databricks_model_name(name: str) -> str:
    return name if name.startswith(DATABRICKS_MODEL_PREFIX) else f"{DATABRICKS_MODEL_PREFIX}{name}"


def build_client(provider: str, *, max_retries: int = 2, anthropic_module=None,
                 workspace_config=None):
    """The Anthropic SDK client for ``provider`` ('databricks' | 'anthropic')."""
    if anthropic_module is None:
        import anthropic as anthropic_module  # noqa: PLC0415
    if provider != "databricks":
        return anthropic_module.Anthropic(max_retries=max_retries)
    if workspace_config is None:
        from databricks.sdk import WorkspaceClient  # noqa: PLC0415
        workspace_config = WorkspaceClient().config
    host = workspace_config.host.rstrip("/")
    return anthropic_module.Anthropic(
        api_key="unused",
        base_url=host + DATABRICKS_ANTHROPIC_PATH,
        default_headers=workspace_config.authenticate(),
        max_retries=max_retries,
    )


def build_prompt(content: str, schema: dict | None = None) -> str:
    if schema is None:
        schema = FrdIngestionSpec.model_json_schema()
    return (
        "Extract the ingestion specification from the FRD below.\n\n"
        "Respond with ONLY a single JSON object (no markdown fences, no "
        "prose) that validates against this JSON schema. Emit every property "
        "explicitly; use null or [] for anything the document does not "
        "state; never add properties not in the schema.\n\n"
        "JSON SCHEMA:\n" + json.dumps(schema, indent=2) +
        "\n\nFRD DOCUMENT (parsed markdown):\n" + content
    )


_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def clean_json_text(raw: str) -> str:
    """Tolerate the two formatting slips a model makes: a ```json fence and a
    trailing comma before } or ]. Content is never changed."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return _TRAILING_COMMA.sub(r"\1", raw.strip())


_strip_fences = clean_json_text


class MalformedJson(RuntimeError):
    """The model's text is not JSON at all (as opposed to JSON of the wrong shape)."""


def parse_response(raw_text: str, doc_id: str) -> FrdIngestionSpec:
    cleaned = clean_json_text(raw_text)
    try:
        json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MalformedJson(f"{doc_id}: the model's answer is not valid JSON ({exc})") from exc
    try:
        return FrdIngestionSpec.model_validate_json(cleaned)
    except ValidationError as exc:
        failed = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors())
        raise RuntimeError(
            f"{doc_id}: the model's JSON does not match FrdIngestionSpec "
            f"({exc.error_count()} error(s)) — {failed}") from exc


def schema_sha256() -> str:
    return hashlib.sha256(json.dumps(FrdIngestionSpec.model_json_schema(by_alias=True),
                                     sort_keys=True).encode("utf-8")).hexdigest()


def _call(client, doc_id, content, model, max_tokens):
    with client.messages.stream(
        model=model, max_tokens=max_tokens, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(content)}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason in {"refusal", "max_tokens"}:
        raise RuntimeError(
            f"{doc_id}: unusable stop_reason={msg.stop_reason!r}"
            + (" — the JSON was truncated; raise max_tokens" if msg.stop_reason == "max_tokens" else ""))
    return msg, "".join(b.text for b in msg.content if b.type == "text")


def extract(client, doc_id: str, content: str, *, model: str,
            max_tokens: int = DEFAULT_MAX_TOKENS) -> tuple[FrdIngestionSpec, dict]:
    """One streaming call. Returns (spec, meta). Raises on refusal, truncation
    or a schema mismatch — never returns a half-answer.

    Malformed JSON (not the wrong shape — not JSON at all, after the trailing-
    comma / fence cleanup) is retried ONCE: it is a formatting slip the model
    makes occasionally, and a demo should not fail on a comma."""
    attempts = 0
    while True:
        attempts += 1
        msg, raw = _call(client, doc_id, content, model, max_tokens)
        try:
            spec = parse_response(raw, doc_id)
            break
        except MalformedJson:
            if attempts >= 2:
                raise
    u = msg.usage
    meta = {
        "model": model,
        "stop_reason": msg.stop_reason,
        "attempts": attempts,
        "input_tokens": getattr(u, "input_tokens", 0),
        "output_tokens": getattr(u, "output_tokens", 0),
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "schema_sha256": schema_sha256(),
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return spec, meta
