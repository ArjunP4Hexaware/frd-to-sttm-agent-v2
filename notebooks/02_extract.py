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
# Provider selection. The program is Anthropic-only as model vendor; the
# only seam kept is mock-vs-Anthropic:
#
#   unset / "" / "anthropic"  -> MOCK_EXTRACTION decides mock-vs-Anthropic.
#   "mock"                    -> same mock path STTM_MOCK_EXTRACTION=1 selects.
#
# Deliberately does NOT re-define MOCK_EXTRACTION: the existing variable, the
# existing mock branch and the existing Anthropic branch are untouched, so an
# unset provider is bit-identical to the pre-change notebook.
# --------------------------------------------------------------------------- #
LLM_PROVIDER = _param("sttm_llm_provider", "").strip().lower()

_VALID_PROVIDERS = ("", "mock", "anthropic")
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

USE_MOCK = MOCK_EXTRACTION or ((not IS_DATABRICKS) and LLM_PROVIDER == "mock")

# Provider-accurate banner. The old form printed `MODEL` unconditionally,
# so a mock run announced a model it never called. Resolved from the provider
# actually selected, so the header can be read aloud during a demo.
if MOCK_EXTRACTION or USE_MOCK:
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
elif USE_MOCK:
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

if MOCK_EXTRACTION or USE_MOCK:
    pass  # no Anthropic call on the mock path -- nothing to authenticate here
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

if not MOCK_EXTRACTION and not USE_MOCK:
    import anthropic

    client = anthropic.Anthropic(max_retries=MAX_RETRIES)

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
