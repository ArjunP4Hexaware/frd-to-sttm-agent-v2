# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Extract: `frd_documents.content` → extraction JSON via Claude
# MAGIC
# MAGIC Phase 3 of the FRD→STTM pipeline. A single-turn structured-extraction
# MAGIC call, not an agent loop: reads each row of the `frd_documents` Delta
# MAGIC table, sends the `content` column to Claude via the Anthropic Python
# MAGIC SDK's structured-outputs endpoint
# MAGIC (`client.messages.parse(output_format=FrdIngestionSpec)`), and writes
# MAGIC one `<doc_id>.json` per document to `sttm_out/extractions/` — the
# MAGIC directory `03_contract_build` reads from.
# MAGIC
# MAGIC The SDK enforces the `FrdIngestionSpec` JSON schema server-side, so
# MAGIC the return value is either a validated Pydantic object or an
# MAGIC exception — never a silently-coerced dict. The same `FrdIngestionSpec`
# MAGIC is re-validated in Phase 4 with `extra="forbid"`, so any schema drift
# MAGIC fails loudly on both sides.
# MAGIC
# MAGIC **Why the base Anthropic SDK, not the Claude Agent SDK.** This step is
# MAGIC one prompt in, one JSON object out — no tool use, no filesystem access,
# MAGIC no multi-turn reasoning, no subagents. `messages.parse` is the direct
# MAGIC fit: server-enforced JSON schema, typed Pydantic return, no loop
# MAGIC machinery. The Claude Agent SDK (`ClaudeSDKClient` / `query()`) is
# MAGIC built for agentic loops with tools and permissions; wrapping a single
# MAGIC structured call in it would add layers with no runtime benefit and
# MAGIC would lose server-side schema enforcement unless the extraction were
# MAGIC re-expressed as a tool call. If this step ever grows into a
# MAGIC multi-turn workflow (e.g. it re-reads sections or calls a lookup
# MAGIC tool), the Agent SDK becomes the right home; today it isn't.
# MAGIC
# MAGIC **Fail-loudly semantics** (matching Phase 4's philosophy):
# MAGIC - Malformed model output → the SDK's server-side schema enforcement raises;
# MAGIC   we surface `doc_id` in the error and stop the run.
# MAGIC - `stop_reason` of `refusal` or `max_tokens` → raise. `max_tokens` means the
# MAGIC   extraction was truncated mid-JSON and would fail Phase 4 anyway.
# MAGIC - Transient `429`/`5xx` are retried by the SDK client (`max_retries`
# MAGIC   widget). Schema errors are NOT retried — they're deterministic model
# MAGIC   output shape errors, not transient.
# MAGIC
# MAGIC Run on serverless compute. Requires an `ANTHROPIC_API_KEY` — see the
# MAGIC credential-load cell below.

# COMMAND ----------

# MAGIC %pip install "anthropic>=0.60" "pydantic>=2"
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# Local-mode support: see notebooks/01_frd_ingest.py's parameter cell for the
# full explanation of IS_DATABRICKS / _param(). Databricks execution below is
# unchanged from before this cell existed.
import os
from pathlib import Path

IS_DATABRICKS = "dbutils" in globals()
LOCAL_ROOT = Path(__file__).resolve().parent.parent / "local_dev_fixtures"


def _param(name: str, default: str) -> str:
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)
        return dbutils.widgets.get(name)
    return os.environ.get(name.upper(), default)


CATALOG = _param("catalog", "soham_workspace")
SCHEMA = _param("schema", "sttm_agent")
DOCS_TABLE_NAME = _param("docs_table", "frd_documents")
OUT_VOLUME = _param("out_volume", "sttm_out")
MODEL = _param("model", "claude-opus-4-8")
MAX_TOKENS = int(_param("max_tokens", "16000"))
MAX_RETRIES = int(_param("max_retries", "2"))
SECRET_SCOPE = _param("secret_scope", "sttm_agent")
SECRET_KEY = _param("secret_key", "anthropic_api_key")

if IS_DATABRICKS:
    DOCS_TABLE = f"{CATALOG}.{SCHEMA}.{DOCS_TABLE_NAME}"
    OUT_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{OUT_VOLUME}"
else:
    DOCS_TABLE = DOCS_TABLE_NAME
    OUT_ROOT = str(LOCAL_ROOT / OUT_VOLUME)
EXTRACTIONS_DIR = f"{OUT_ROOT}/extractions"

# Zero-cost local testing only: skips the real Anthropic call entirely and
# returns a hand-authored FrdIngestionSpec per doc_id from
# notebooks/_mock_extractions.py instead. Deliberately local-only (ignored
# even if the env var is set) -- a real Databricks run should never
# silently skip the actual extraction call. See that module's docstring and
# README.md's "Local mode" section: this is for exercising the pipeline's
# plumbing and the review app, NOT a substitute for real extraction-quality
# evaluation (docs/STANDUP_NOTES.md has those real numbers).
MOCK_EXTRACTION = (not IS_DATABRICKS) and os.environ.get("STTM_MOCK_EXTRACTION", "") in ("1", "true", "True")

# --------------------------------------------------------------------------- #
# Provider selection -- ADDITIVE, added 2026-07-30 for the Gemini live path.
#
#   unset / "" / "anthropic"  -> EXACTLY today's behaviour: MOCK_EXTRACTION
#                                decides mock-vs-Anthropic, nothing changed.
#   "mock"                    -> same mock path STTM_MOCK_EXTRACTION=1 selects.
#   "gemini"                  -> the new live Google Gemini path.
#
# Deliberately does NOT re-define MOCK_EXTRACTION: the existing variable, the
# existing mock branch and the existing Anthropic branch are untouched, so an
# unset provider is bit-identical to the pre-change notebook.
#
# Mock stays non-Databricks-only. USE_GEMINI is allowed under Databricks --
# it is a real billed call, so it does not carry the mock path's local-only
# restriction.
# --------------------------------------------------------------------------- #
LLM_PROVIDER = _param("sttm_llm_provider", "").strip().lower()

_VALID_PROVIDERS = ("", "mock", "anthropic", "gemini")
if LLM_PROVIDER not in _VALID_PROVIDERS:
    raise ValueError(
        f"STTM_LLM_PROVIDER={LLM_PROVIDER!r} is not recognised. "
        f"Valid values: {_VALID_PROVIDERS!r}. Refusing to guess -- an unrecognised "
        f"provider must never silently degrade to mock or to Anthropic."
    )

# Ambiguous configuration raises rather than picking a winner. Silently
# honouring the mock here would reproduce the silent-mock failure mode the
# demo launcher exists to prevent: a run that looks live but made no call.
if MOCK_EXTRACTION and LLM_PROVIDER not in ("", "mock"):
    raise ValueError(
        f"Conflicting configuration: STTM_MOCK_EXTRACTION is set (mock mode) while "
        f"STTM_LLM_PROVIDER={LLM_PROVIDER!r} requests a live provider. Refusing to "
        f"guess which one you meant -- unset one of them."
    )

USE_GEMINI = (LLM_PROVIDER == "gemini")
USE_MOCK = MOCK_EXTRACTION or ((not IS_DATABRICKS) and LLM_PROVIDER == "mock")

GEMINI_MODEL = _param("gemini_model", "models/gemini-3.6-flash")
GEMINI_MAX_OUTPUT_TOKENS = int(_param("gemini_max_output_tokens", "16000"))
GEMINI_MAX_RETRIES = int(_param("gemini_max_retries", "5"))
GEMINI_RETRY_BASE_SECONDS = float(_param("gemini_retry_base_seconds", "2.0"))
GEMINI_SECRET_KEY = _param("gemini_secret_key", "gemini_api_key")

# Completeness-gate thresholds (see completeness_gate() below). Kept as
# parameters so a different corpus can raise the floor without a code change.
GATE_MIN_FEEDS = int(_param("gate_min_feeds", "1"))
GATE_MIN_SPEC_BYTES = int(_param("gate_min_spec_bytes", "2000"))

# Provider-accurate banner. The old form printed `MODEL` unconditionally, which
# is the ANTHROPIC model param -- so a Gemini run announced "claude-opus-4-8"
# and a mock run announced a model it never called. Resolved from the provider
# actually selected, so the header can be read aloud during a demo.
if USE_GEMINI:
    ACTIVE_PROVIDER = "gemini"
    ACTIVE_MODEL = GEMINI_MODEL
elif MOCK_EXTRACTION or USE_MOCK:
    ACTIVE_PROVIDER = "mock"
    ACTIVE_MODEL = "mock (cached extraction, no API call)"
else:
    ACTIVE_PROVIDER = "anthropic"
    ACTIVE_MODEL = MODEL

print(
    f"docs:     {DOCS_TABLE}\n"
    f"provider: {ACTIVE_PROVIDER}\n"
    f"model:    {ACTIVE_MODEL}\n"
    f"out:      {EXTRACTIONS_DIR}"
)
if MOCK_EXTRACTION:
    print("STTM_MOCK_EXTRACTION=1 -- using hand-authored mock specs, no API calls will be made")
if USE_GEMINI:
    print(f"STTM_LLM_PROVIDER=gemini -- LIVE billed call to {GEMINI_MODEL}")
elif USE_MOCK and not MOCK_EXTRACTION:
    print("STTM_LLM_PROVIDER=mock -- using hand-authored mock specs, no API calls will be made")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Credentials
# MAGIC Read `ANTHROPIC_API_KEY` from the Databricks secret scope; fall back to
# MAGIC an environment variable for local dry-runs. Never hardcoded, never logged.
# MAGIC Set the secret once with:
# MAGIC ```
# MAGIC databricks secrets create-scope sttm_agent
# MAGIC databricks secrets put-secret sttm_agent anthropic_api_key
# MAGIC ```

# COMMAND ----------

import os

# GUARD EXTENDED 2026-07-30 (additive): `or USE_MOCK or USE_GEMINI`. The
# Anthropic credential-load below is unchanged in every respect -- when
# STTM_LLM_PROVIDER is unset or "anthropic", USE_MOCK == MOCK_EXTRACTION and
# USE_GEMINI is False, so this cell behaves bit-identically to before. The
# extra clauses only stop the Gemini path from demanding an Anthropic key it
# will never use.
if MOCK_EXTRACTION or USE_MOCK or USE_GEMINI:
    pass  # no Anthropic call on these paths -- nothing to authenticate here
else:
    _api_key = None
    try:
        _api_key = dbutils.secrets.get(scope=SECRET_SCOPE, key=SECRET_KEY)
    except Exception:
        _api_key = os.environ.get("ANTHROPIC_API_KEY")

    assert _api_key, (
        f"ANTHROPIC_API_KEY not found. Create secret {SECRET_SCOPE}/{SECRET_KEY} "
        f"in this workspace, or export ANTHROPIC_API_KEY for a local dry-run. "
        f"See notebook docstring for setup."
    )
    os.environ["ANTHROPIC_API_KEY"] = _api_key
    del _api_key

# COMMAND ----------

# Gemini credentials -- separate cell, mirrors the Anthropic block's posture:
# secret scope first, environment variable fallback, assert loudly, never log.
if USE_GEMINI:
    _gemini_key = None
    try:
        _gemini_key = dbutils.secrets.get(scope=SECRET_SCOPE, key=GEMINI_SECRET_KEY)
    except Exception:
        _gemini_key = os.environ.get("GEMINI_API_KEY")

    assert _gemini_key, (
        f"GEMINI_API_KEY not found. Create secret {SECRET_SCOPE}/{GEMINI_SECRET_KEY} "
        f"in this workspace, or export GEMINI_API_KEY for a local run "
        f"(set -a; . ./.env; set +a). See .env.example."
    )
    os.environ["GEMINI_API_KEY"] = _gemini_key
    del _gemini_key

# COMMAND ----------

# MAGIC %md
# MAGIC ## Shared pydantic model
# MAGIC `FrdIngestionSpec` and children — the single source of truth for the
# MAGIC extraction schema, mirrored 1:1 from `schema/sttm_extraction_schema.json`.
# MAGIC Same file that `03_contract_build` imports.

# COMMAND ----------

# MAGIC %run ./_models

# COMMAND ----------

# `%run` above is a Databricks-only magic -- inert (just a comment) when this
# file executes as a plain script, so FrdIngestionSpec would never get
# defined locally without this explicit import. _models.py has no
# Databricks/Spark dependency, so this import works unmodified either way.
if not IS_DATABRICKS:
    from _models import FrdIngestionSpec  # noqa: F401

# COMMAND ----------

# MAGIC %md
# MAGIC ## System prompt
# MAGIC Short framing only. Per-field guidance lives on the Pydantic model in
# MAGIC `_models.py` as `Field(description=...)` — `messages.parse()` sends
# MAGIC that schema (descriptions and all) server-side, so per-field rules
# MAGIC (verbatim identifiers, attribution splitting, "NA" overrides) reach
# MAGIC the model via the schema, not this prompt.

# COMMAND ----------

# COMMAND ----------

# MAGIC %md
# MAGIC ## Gemini support — schema adapter and completeness gate
# MAGIC Both are additive and used only by the Gemini branch. Neither is
# MAGIC referenced by the mock or Anthropic paths.

# COMMAND ----------


def gemini_response_schema(model_cls) -> dict:
    """Return `model_cls.model_json_schema()` with every `additionalProperties`
    key removed, recursively, at every depth including inside `$defs`.

    WHY THIS EXISTS -- measured, not assumed (2026-07-30):

    Gemini's `responseSchema` dialect has no `additionalProperties` field. The
    google-genai SDK converts the Pydantic-generated key to snake_case and the
    API rejects the request outright with:

        400 INVALID_ARGUMENT
        Unknown name "additional_properties" at
          'generation_config.response_schema': Cannot find field.

    It is reported at SIX sites for FrdIngestionSpec -- one per model, with
    TableTarget counted twice because it appears as both `stage_target` and
    `standard_target`:

        response_schema                                          (FrdIngestionSpec)
        properties[0].value                                      (project -> Project)
        properties[3].value.items                                (acd[] -> AcdItem)
        properties[4].value.items                                (feeds[] -> Feed)
        properties[4].value.items.properties[12].value           (stage_target)
        properties[4].value.items.properties[13].value           (standard_target)

    Removing that one key is the ONLY change Gemini needs. Depth-4 nesting, 38
    properties, zero required fields and `$ref`/`$defs` are all accepted as-is
    -- verified by sending the stripped schema and getting past schema
    validation. Do NOT inline `$defs`; it is unnecessary and only inflates the
    payload.

    The cost of stripping it is real and is why completeness_gate() exists:
    `additionalProperties: false` is the wire form of the model's
    `extra="forbid"`, so removing it moves that guard from server-side to
    client-side. It is not lost -- `FrdIngestionSpec.model_validate_json()`
    still enforces `extra="forbid"` -- but it now fails AFTER a billed call
    instead of before one.

    Pure function: takes a class, returns a new dict, mutates nothing.
    """

    def _strip(node):
        if isinstance(node, dict):
            return {k: _strip(v) for k, v in node.items() if k != "additionalProperties"}
        if isinstance(node, list):
            return [_strip(v) for v in node]
        return node

    return _strip(model_cls.model_json_schema())


def completeness_gate(spec, doc_id: str, min_feeds: int, min_bytes: int,
                      expected_feeds: int | None = None) -> None:
    """Raise unless `spec` is substantively complete. Runs AFTER schema
    validation and BEFORE the extraction JSON is written.

    WHY THIS IS NOT REDUNDANT WITH 03_contract_build -- do not delete it:

    Every one of FrdIngestionSpec's 38 properties has a default and NOTHING in
    the tree is required. `{}` is a structurally VALID FrdIngestionSpec. It
    passes `model_validate`, it passes 03_contract_build's first gate, and it
    renders an almost-empty STTM workbook while stage 3 reports PASS.

    Anthropic's `messages.parse` enforced the schema server-side and, in
    practice, returned complete extractions. Gemini cannot enforce
    completeness -- see gemini_response_schema() for why the only enforceable
    part of the schema (`additionalProperties`) has to be stripped. So the
    guarantee that used to come from the provider has to be re-created here.

    03 validates SHAPE. This validates SUBSTANCE. Nothing else in the pipeline
    does the latter, and a thin response is invisible without it.

    Raises RuntimeError naming the specific failed check. Never returns a
    partial result, never warns-and-continues on a hard check.
    """
    failures = []

    if not spec.feeds:
        failures.append("feeds[] is empty -- the extraction found no source feeds at all")

    if len(spec.feeds) < min_feeds:
        failures.append(
            f"feed count {len(spec.feeds)} is below the floor of {min_feeds} "
            f"(gate_min_feeds)"
        )

    for i, f in enumerate(spec.feeds):
        if not (f.feed_name or "").strip():
            failures.append(f"feeds[{i}].feed_name is empty")
        if not (f.source_system or "").strip():
            failures.append(f"feeds[{i}].source_system is empty")
        if not f.file_name_patterns:
            failures.append(f"feeds[{i}].file_name_patterns is empty")

    if spec.project is None:
        failures.append("project block is null")
    else:
        if not any([
            (spec.project.project_id or "").strip(),
            (spec.project.project_name or "").strip(),
            (spec.project.business_context_summary or "").strip(),
        ]):
            failures.append("project block is present but entirely empty")

    serialized = spec.model_dump_json(by_alias=True, indent=2)
    n_bytes = len(serialized.encode("utf-8"))
    if n_bytes < min_bytes:
        failures.append(
            f"serialized spec is {n_bytes} bytes, below the floor of {min_bytes} "
            f"(gate_min_spec_bytes) -- indicates a thin or degraded extraction"
        )

    if failures:
        raise RuntimeError(
            f"{doc_id}: COMPLETENESS GATE FAILED -- refusing to write "
            f"{doc_id}.json.\n  - " + "\n  - ".join(failures) +
            "\n\nThis is not a schema error: the response was structurally valid. "
            "All 38 fields are optional, so a thin extraction validates cleanly "
            "and would render a near-empty workbook with stage 3 reporting PASS. "
            "Investigate the model output rather than lowering the gate."
        )

    # Soft check: a feed-count disagreement with the known mock spec is a
    # strong signal of a degraded extraction, but it is corpus-specific, so it
    # warns rather than raises.
    if expected_feeds is not None and len(spec.feeds) != expected_feeds:
        print(
            f"  *** WARNING: {doc_id} returned {len(spec.feeds)} feeds but the "
            f"hand-authored mock spec for this document has {expected_feeds}. "
            f"The live extraction disagrees with the known-good baseline -- "
            f"inspect before demoing. ***"
        )

    print(
        f"  completeness gate PASSED: {len(spec.feeds)} feed(s), "
        f"{n_bytes:,} bytes serialized"
    )


# COMMAND ----------

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

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load documents

# COMMAND ----------

if IS_DATABRICKS:
    docs = [
        {"doc_id": r["doc_id"], "source_file": r["source_file"], "content": r["content"]}
        for r in spark.table(DOCS_TABLE).select("doc_id", "source_file", "content").collect()
    ]
else:
    from _local_tables import read_table

    docs = [
        {"doc_id": r["doc_id"], "source_file": r["source_file"], "content": r["content"]}
        for r in read_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, DOCS_TABLE_NAME)
    ]
print(f"{len(docs)} document(s) in {DOCS_TABLE}")
assert docs, f"No documents in {DOCS_TABLE} — run 01_frd_ingest first."

# COMMAND ----------

# MAGIC %md
# MAGIC ## Extract
# MAGIC One `messages.parse()` call per document. Server-side schema enforcement
# MAGIC means the return value is either a validated `FrdIngestionSpec` or an
# MAGIC exception — never a silently-coerced dict. Per-doc failures are fatal;
# MAGIC we surface `doc_id` in the error so a re-run knows which one to investigate.

# COMMAND ----------

import json
from pathlib import Path
from types import SimpleNamespace

# GUARD EXTENDED 2026-07-30 (additive): `and not USE_GEMINI`, so the Gemini
# path never imports the Anthropic SDK. Mock mode still imports neither.
# Unchanged when STTM_LLM_PROVIDER is unset or "anthropic".
if not MOCK_EXTRACTION and not USE_MOCK and not USE_GEMINI:
    import anthropic

    client = anthropic.Anthropic(max_retries=MAX_RETRIES)

if USE_GEMINI:
    # Imported INSIDE the guard, mirroring `import anthropic` above: neither
    # SDK is imported on the mock path, and the Anthropic SDK is not imported
    # on the Gemini path.
    import time

    from google import genai
    from google.genai import types as genai_types

    _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    _GEMINI_SCHEMA = gemini_response_schema(FrdIngestionSpec)

    print(
        f"gemini: model={GEMINI_MODEL} max_output_tokens={GEMINI_MAX_OUTPUT_TOKENS} "
        f"schema_chars={len(json.dumps(_GEMINI_SCHEMA)):,} "
        f"additionalProperties_remaining="
        f"{json.dumps(_GEMINI_SCHEMA).count('additionalProperties')}"
    )

    def _gemini_extract(doc_id: str, content: str):
        """One structured extraction call. Returns (spec, usage, seconds).

        Retries transient failures only. A 400 is deterministic -- the schema
        or request is malformed and will fail identically every time -- so it
        is re-raised immediately rather than burning the retry budget.
        """
        last_exc = None
        for attempt in range(GEMINI_MAX_RETRIES):
            t0 = time.time()
            try:
                resp = _gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=content,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=_GEMINI_SCHEMA,
                        # Thinking tokens are charged against THIS budget and are
                        # invisible in prompt/output counts. Measured on demo_frd:
                        # 3,944 thinking vs 1,768 output -- thinking was 69% of
                        # what the cap had to absorb. Gemini does not pre-reserve
                        # this the way Groq reserves max_tokens against TPM, so a
                        # generous cap costs nothing and a tight one silently
                        # truncates into MAX_TOKENS.
                        max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                    ),
                )
                elapsed = time.time() - t0
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code == 400:
                    raise RuntimeError(
                        f"{doc_id}: Gemini rejected the request (400 INVALID_ARGUMENT) "
                        f"-- deterministic, not retried. {exc}"
                    ) from exc
                last_exc = exc
                if attempt == GEMINI_MAX_RETRIES - 1:
                    break
                delay = GEMINI_RETRY_BASE_SECONDS * (2 ** attempt)
                retry_after = None
                for attr in ("retry_after", "retry_delay"):
                    if getattr(exc, attr, None):
                        retry_after = getattr(exc, attr)
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except (TypeError, ValueError):
                        pass
                print(
                    f"  {doc_id}: transient error (code={code}), attempt "
                    f"{attempt + 1}/{GEMINI_MAX_RETRIES}, retrying in {delay:.1f}s"
                )
                time.sleep(delay)
                continue

            cand = resp.candidates[0]
            finish = getattr(cand.finish_reason, "name", str(cand.finish_reason))
            if finish != "STOP":
                # MAX_TOKENS here means thinking consumed the output budget and
                # the JSON is truncated mid-object. It is NOT a short-but-valid
                # answer and must never be treated as one.
                raise RuntimeError(
                    f"{doc_id}: Gemini finish_reason={finish!r}, expected 'STOP'. "
                    f"Refusing to use this response. "
                    + (
                        "MAX_TOKENS means thinking tokens exhausted "
                        f"max_output_tokens={GEMINI_MAX_OUTPUT_TOKENS}; the JSON is "
                        "truncated. Raise gemini_max_output_tokens."
                        if finish == "MAX_TOKENS" else
                        "Investigate before re-running."
                    )
                )

            raw = resp.text or ""
            # .parsed is a plain dict here because the schema is passed as a
            # dict, not a Pydantic class -- so validation is ours to do. This
            # is what re-applies extra="forbid" client-side.
            spec_obj = FrdIngestionSpec.model_validate_json(raw)
            return spec_obj, resp.usage_metadata, elapsed

        raise RuntimeError(
            f"{doc_id}: Gemini extraction failed after {GEMINI_MAX_RETRIES} "
            f"attempts -- {last_exc}"
        ) from last_exc

Path(EXTRACTIONS_DIR).mkdir(parents=True, exist_ok=True)

total_input = total_output = 0
results = []

for d in docs:
    if MOCK_EXTRACTION or USE_MOCK:
        from _mock_extractions import mock_spec_for

        response = SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=mock_spec_for(d["doc_id"], d["content"]),
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )
    elif USE_GEMINI:
        _spec, _usage, _elapsed = _gemini_extract(d["doc_id"], d["content"])

        print(
            f"  {d['doc_id']}: {_elapsed:.2f}s  "
            f"prompt={_usage.prompt_token_count}  "
            f"output={_usage.candidates_token_count}  "
            f"thoughts={_usage.thoughts_token_count}  "
            f"total={_usage.total_token_count}"
        )

        # Feed count from the hand-authored mock spec for the same document,
        # used only as a soft baseline comparison. Never substituted for the
        # live result -- if it cannot be resolved the gate simply skips the
        # comparison rather than falling back to mock data.
        _expected = None
        try:
            from _mock_extractions import mock_spec_for as _msf

            _expected = len(_msf(d["doc_id"], d["content"]).feeds)
        except Exception:
            _expected = None

        completeness_gate(
            _spec,
            d["doc_id"],
            min_feeds=GATE_MIN_FEEDS,
            min_bytes=GATE_MIN_SPEC_BYTES,
            expected_feeds=_expected,
        )

        # Mirrors the mock branch's shape exactly so everything below is
        # provider-agnostic. Gemini has no stop_reason concept; finish_reason
        # was already checked and is STOP, which maps to "end_turn".
        response = SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=_spec,
            usage=SimpleNamespace(
                input_tokens=_usage.prompt_token_count or 0,
                output_tokens=_usage.candidates_token_count or 0,
            ),
        )
    else:
        try:
            response = client.messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": d["content"]}],
                output_format=FrdIngestionSpec,
            )
        except anthropic.APIError as exc:
            raise RuntimeError(f"{d['doc_id']}: extraction failed — {exc}") from exc

    assert response.stop_reason not in {"refusal", "max_tokens"}, (
        f"{d['doc_id']}: unusable stop_reason={response.stop_reason!r} "
        f"(stop_details={getattr(response, 'stop_details', None)!r}). "
        f"For 'max_tokens' the JSON was truncated — raise the max_tokens widget."
    )
    assert response.parsed_output is not None, (
        f"{d['doc_id']}: parsed_output is None despite stop_reason="
        f"{response.stop_reason!r} — the SDK could not parse the response into "
        f"FrdIngestionSpec. Re-run or investigate the raw content."
    )

    spec = response.parsed_output
    out_path = Path(EXTRACTIONS_DIR) / f"{d['doc_id']}.json"
    out_path.write_text(
        spec.model_dump_json(by_alias=True, indent=2),
        encoding="utf-8",
    )

    u = response.usage
    total_input += u.input_tokens
    total_output += u.output_tokens

    n_feeds = len(spec.feeds)
    print(
        f"{d['doc_id']:40s} feeds={n_feeds}  "
        f"in={u.input_tokens:>6}  out={u.output_tokens:>5}"
    )
    results.append({"doc_id": d["doc_id"], "n_feeds": n_feeds, "path": str(out_path)})

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC Confirms which docs were extracted, and reports the token/cache totals
# MAGIC so cost is auditable per run.

# COMMAND ----------

print(f"\nextracted {len(results)} document(s) to {EXTRACTIONS_DIR}")
print(f"tokens: input={total_input:,}  output={total_output:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next (Phase 4)
# MAGIC Run `03_contract_build` — it reads the files this notebook just wrote
# MAGIC from `sttm_out/extractions/` and validates, enriches, audits, gates,
# MAGIC and persists the feed-level mapping contracts.
