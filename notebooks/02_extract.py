# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Extract: `frd_documents.content` → extraction JSON via Claude
# MAGIC
# MAGIC Phase 3 of the FRD→STTM pipeline. A single-turn structured-extraction
# MAGIC call, not an agent loop: reads each row of the `frd_documents` Delta
# MAGIC table, sends the `content` column to Claude via a plain streaming
# MAGIC `messages.stream()` call with the `FrdIngestionSpec` JSON schema
# MAGIC rendered into the prompt (`frdsttm.live_extraction`), and writes
# MAGIC one `<doc_id>.json` per document to `sttm_out/extractions/` — the
# MAGIC directory `03_contract_build` reads from.
# MAGIC
# MAGIC **Why schema-in-prompt, not `messages.parse(output_format=...)`.**
# MAGIC Server-side structured outputs compile the schema into a grammar, and
# MAGIC FrdIngestionSpec's grammar deterministically exceeds the API compiler's
# MAGIC limit (`400 "The compiled grammar is too large"`) — measured on the
# MAGIC first live E2E; see docs/LIVE_E2E_2026-08-07.md defect D1 and
# MAGIC `frdsttm/live_extraction.py`'s docstring. The schema (field
# MAGIC descriptions and all, so the per-field guidance still reaches the
# MAGIC model) travels in the user prompt instead, and the response is
# MAGIC validated client-side with `FrdIngestionSpec.model_validate_json` —
# MAGIC whose `extra="forbid"` re-creates the drift guard. The same spec is
# MAGIC re-validated in Phase 4, so drift still fails loudly on both sides.
# MAGIC The call streams so a large `max_tokens` (default 64000, widget/env
# MAGIC `max_tokens`) cannot hit HTTP timeouts.
# MAGIC
# MAGIC **Why the base Anthropic SDK, not the Claude Agent SDK.** This step is
# MAGIC one prompt in, one JSON object out — no tool use, no filesystem access,
# MAGIC no multi-turn reasoning, no subagents. The Claude Agent SDK
# MAGIC (`ClaudeSDKClient` / `query()`) is built for agentic loops with tools
# MAGIC and permissions; wrapping a single call in it would add layers with no
# MAGIC runtime benefit. If this step ever grows into a multi-turn workflow
# MAGIC (e.g. it re-reads sections or calls a lookup tool), the Agent SDK
# MAGIC becomes the right home; today it isn't.
# MAGIC
# MAGIC **Fail-loudly semantics** (matching Phase 4's philosophy):
# MAGIC - Malformed model output → client-side validation raises naming every
# MAGIC   failed field; we surface `doc_id` in the error and stop the run.
# MAGIC   There is deliberately NO re-ask/repair loop.
# MAGIC - `stop_reason` of `refusal` or `max_tokens` → raise. `max_tokens` means the
# MAGIC   extraction was truncated mid-JSON and would fail Phase 4 anyway.
# MAGIC - Transient `429`/`5xx` are retried by the SDK client (`max_retries`
# MAGIC   widget). Schema errors are NOT retried — they're deterministic model
# MAGIC   output shape errors, not transient.
# MAGIC
# MAGIC Run on serverless compute. Requires an `ANTHROPIC_API_KEY` — see the
# MAGIC credential-load cell below — UNLESS `STTM_LLM_PROVIDER=databricks`,
# MAGIC which serves the same Claude models through this workspace's
# MAGIC Foundation Model APIs and authenticates with the workspace credential
# MAGIC instead (no key, no secret scope; Databricks bills per token).

# COMMAND ----------

# MAGIC %pip install "anthropic>=0.60" "pydantic>=2" "databricks-sdk" openpyxl
# MAGIC # databricks-sdk: needed only by STTM_LLM_PROVIDER=databricks, which
# MAGIC # reads the workspace credential to reach the serving endpoint. It ships
# MAGIC # with DBR, but pinning it here keeps the notebook self-contained.
# MAGIC # openpyxl: frdsttm.corpus imports frdsttm.reference_workbooks (to parse
# MAGIC # the template library for exemplars) whether or not a corpus is present.
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# Local-mode support: see notebooks/01_frd_ingest.py's parameter cell for the
# full explanation of IS_DATABRICKS / _param(). Databricks execution below is
# unchanged from before this cell existed.
import os
from pathlib import Path

IS_DATABRICKS = "dbutils" in globals()

# The deployed Databricks App runs these notebooks as plain SUBPROCESSES, so
# `dbutils` is NOT in their globals() and IS_DATABRICKS is False -- even though
# this is a real workspace run that must never silently mock. `app.yaml` sets
# STTM_APP_MODE=databricks, so that is what distinguishes "deployed App" from
# "someone's laptop". Without this, the "mock can never run in Databricks"
# guarantee has a hole exactly where it matters most: a live client demo.
IS_DATABRICKS_APP = os.environ.get("STTM_APP_MODE", "").strip().lower() == "databricks"

# Mock is a laptop affordance. It is unavailable in a notebook task (dbutils
# present) AND in the deployed App (STTM_APP_MODE set).
MOCK_AVAILABLE = not (IS_DATABRICKS or IS_DATABRICKS_APP)

LOCAL_ROOT = Path(__file__).resolve().parent.parent / "local_dev_fixtures" if not IS_DATABRICKS else None

# Make src/ importable before ANY `from frdsttm...` below -- including the
# provider-banner import further down, which runs in an earlier cell than
# the `%run ./_models` / `%run ./_live_extraction` shims that would
# otherwise be the ones to do this. In a notebook task `__file__` is unset
# (unlike a plain script or a pip-installed frdsttm), so those two cases
# need their own candidate search; mirrors notebooks/_models.py's.
import sys as _sys
try:
    _here = Path(__file__).resolve().parent
except NameError:
    _here = Path.cwd()
_candidates = [_here.parent / "src", _here / "src", Path.cwd().parent / "src", Path.cwd() / "src"]
if IS_DATABRICKS:
    # cwd/`__file__` are unreliable inside a WORKSPACE notebook task (as
    # opposed to a Git-folder %run, where cwd is documented to be the
    # notebook's own directory) -- ask the notebook context directly for
    # its own workspace path instead of guessing.
    try:
        _nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        _nb_dir = Path("/Workspace" + _nb_path).resolve().parent if not _nb_path.startswith("/Workspace") \
            else Path(_nb_path).resolve().parent
        _candidates.insert(0, _nb_dir.parent / "src")
    except Exception:  # noqa: BLE001 — best-effort extra candidate, not fatal
        pass
for _cand in _candidates:
    if (_cand / "frdsttm").is_dir() and str(_cand) not in _sys.path:
        _sys.path.insert(0, str(_cand))


def _param(name: str, default: str) -> str:
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)
        return dbutils.widgets.get(name)
    return os.environ.get(name.upper(), default)


def _int_param(name: str, default: int) -> int:
    """A NUMERIC knob where blank means "not set" (fixed 2026-08-24).

    `.env.example` ships `STTM_EXEMPLARS_K` (and friends) blank, and the
    documented load (`set -a; . ./.env; set +a`) exports a blank as "", so
    `_param` hands back "" instead of the default and `int("")` raised. An
    emptied widget behaves the same way.

    Deliberately NOT folded into `_param`: for the STRING knobs blank is
    meaningful and differs from the default — `frd_name_prefix` documents
    "blank disables the filter" — so a blanket rule there would silently
    re-enable filtering. A non-numeric value still raises: that is a typo.
    """
    raw = _param(name, str(default)).strip()
    return int(raw) if raw else default


CATALOG = _param("catalog", "arjun_workspace")
SCHEMA = _param("schema", "sttm_agent")
DOCS_TABLE_NAME = _param("docs_table", "frd_documents")
OUT_VOLUME = _param("out_volume", "sttm_out")
# claude-opus-5 (was claude-opus-4-8, changed 2026-08-24, Arjun): the most
# capable Claude available to Hexaware on Databricks. Under
# STTM_LLM_PROVIDER=databricks this resolves to databricks-claude-opus-5,
# verified callable in the Hexaware workspace the same day. The first-party
# Anthropic path takes the same id unchanged.
MODEL = _param("model", "claude-opus-5")
# 64000 (was 16000): R3 mitigation from docs/LIVE_E2E_2026-08-07.md — the
# demo FRD used 3.3k output tokens at ~700-800/feed, so 16k capped out
# around 15-20 feeds. The call streams, so the larger ceiling cannot hit
# HTTP timeouts.
MAX_TOKENS = _int_param("max_tokens", 64000)
MAX_RETRIES = _int_param("max_retries", 2)
SECRET_SCOPE = _param("secret_scope", "sttm_agent")
SECRET_KEY = _param("secret_key", "anthropic_api_key")
# Provenance: the Databricks job run id ({{job.run_id}} in the job yml);
# blank on a local/subprocess run. Stamped on the extraction_meta sidecar.
JOB_RUN_ID = _param("job_run_id", "")

if IS_DATABRICKS:
    DOCS_TABLE = f"{CATALOG}.{SCHEMA}.{DOCS_TABLE_NAME}"
    OUT_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{OUT_VOLUME}"
else:
    DOCS_TABLE = DOCS_TABLE_NAME
    OUT_ROOT = str(LOCAL_ROOT / OUT_VOLUME)
EXTRACTIONS_DIR = f"{OUT_ROOT}/extractions"

# Template-architecture inputs (2026-08-22, docs/TEMPLATE_ARCHITECTURE.md):
# the reference volume also carries corpus_index.json, built by the review
# app's corpus bootstrap. When it exists (and the run is live), the k most
# similar APPROVED FRD->STTM pairs contribute a conventions digest to the
# extraction prompt (frdsttm.exemplars). Facts still come only from the FRD:
# 03's grounding audit rejects anything not present in the target document,
# exemplars included.
#   sttm_exemplars: auto (use the corpus when present) | on (require it) | off
REFERENCE_VOLUME = _param("reference_volume", "sttm_reference")
if IS_DATABRICKS:
    REFERENCE_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{REFERENCE_VOLUME}"
else:
    REFERENCE_DIR = str(LOCAL_ROOT / REFERENCE_VOLUME)
# Blank means "not set" -> the default, for the same reason _int_param exists:
# .env.example ships STTM_EXEMPLARS blank and `set -a; . ./.env; set +a`
# exports it as "", which used to fall through to the raise below.
EXEMPLARS_MODE = _param("sttm_exemplars", "auto").strip().lower() or "auto"
if EXEMPLARS_MODE not in ("auto", "on", "off"):
    raise ValueError(
        f"sttm_exemplars={EXEMPLARS_MODE!r} is not recognised (auto|on|off). "
        f"Refusing to guess."
    )
EXEMPLARS_K = _int_param("sttm_exemplars_k", 2)

# Zero-cost local testing only: skips the real Anthropic call entirely and
# returns a hand-authored FrdIngestionSpec per doc_id from
# notebooks/_mock_extractions.py instead. Deliberately laptop-only --
# unavailable both in a notebook task and in the deployed App (see
# MOCK_AVAILABLE above); a workspace run must never silently skip the
# actual extraction call. See that module's docstring and
# README.md's "Local mode" section: this is for exercising the pipeline's
# plumbing and the review app, NOT a substitute for real extraction-quality
# evaluation (docs/STANDUP_NOTES.md has those real numbers).
_MOCK_REQUESTED = os.environ.get("STTM_MOCK_EXTRACTION", "") in ("1", "true", "True")

# In the deployed App a mock request RAISES rather than being ignored. The
# notebook-task branch keeps its long-standing silent-ignore behaviour (there,
# the env var is a leftover from a laptop, not an instruction). In the App the
# env var had to be put there deliberately, so the operator believes mock is
# on -- and a run that looks live while returning hand-authored specs is the
# single worst thing that can happen in front of a client.
if _MOCK_REQUESTED and IS_DATABRICKS_APP:
    raise ValueError(
        "STTM_MOCK_EXTRACTION is set while STTM_APP_MODE=databricks (the deployed "
        "Databricks App). Mock extraction is not available in a workspace run: it "
        "would present hand-authored specs as a live extraction. Unset "
        "STTM_MOCK_EXTRACTION in the App environment, or run on a laptop for mock."
    )

MOCK_EXTRACTION = MOCK_AVAILABLE and _MOCK_REQUESTED

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

_VALID_PROVIDERS = ("", "mock", "anthropic", "databricks")
if LLM_PROVIDER not in _VALID_PROVIDERS:
    raise ValueError(
        f"STTM_LLM_PROVIDER={LLM_PROVIDER!r} is not recognised. "
        f"Valid values: {_VALID_PROVIDERS!r}. Refusing to guess -- an unrecognised "
        f"provider must never silently degrade to mock or to a live provider."
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

# Same rule for the explicit provider seam: STTM_LLM_PROVIDER=mock must not be
# a back door into mock inside the App.
if LLM_PROVIDER == "mock" and IS_DATABRICKS_APP:
    raise ValueError(
        "STTM_LLM_PROVIDER='mock' while STTM_APP_MODE=databricks (the deployed "
        "Databricks App). Mock extraction is not available in a workspace run -- "
        "refusing to present hand-authored specs as a live extraction."
    )

USE_MOCK = MOCK_EXTRACTION or (MOCK_AVAILABLE and LLM_PROVIDER == "mock")

# Provider-accurate banner. The old form printed `MODEL` unconditionally,
# so a mock run announced a model it never called. Resolved from the provider
# actually selected, so the header can be read aloud during a demo.
if MOCK_EXTRACTION or USE_MOCK:
    ACTIVE_PROVIDER = "mock"
    ACTIVE_MODEL = "mock (cached extraction, no API call)"
elif LLM_PROVIDER == "databricks":
    # Claude, served by Databricks Foundation Model APIs. Same vendor, same
    # Messages API, different front door -- so "Anthropic-only as model vendor"
    # still holds; what changes is who bills and who authenticates.
    ACTIVE_PROVIDER = "databricks"
    from frdsttm.live_extraction import databricks_model_name

    ACTIVE_MODEL = databricks_model_name(MODEL)
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
elif LLM_PROVIDER == "databricks":
    # Nothing to fetch: the workspace credential IS the credential. In a job or
    # notebook the SDK picks up the runtime identity, in the deployed App the
    # service principal, and locally ~/.databrickscfg. No ANTHROPIC_API_KEY, no
    # secret scope -- which is the whole point of this provider.
    pass
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

# MAGIC %run ./_live_extraction

# COMMAND ----------

# `%run` above is a Databricks-only magic -- inert (just a comment) when this
# file executes as a plain script, so FrdIngestionSpec would never get
# defined locally without this explicit import. Neither module has a
# Databricks/Spark dependency, so these imports work unmodified either way.
if not IS_DATABRICKS:
    from _models import FrdIngestionSpec  # noqa: F401
    from _live_extraction import extract_live  # noqa: F401

# Importable in both modes: the %run/_live_extraction shim above (Databricks)
# and the local import (script mode) both put src/ on sys.path first.
from frdsttm.corpus import load_corpus_index  # noqa: E402
from frdsttm.exemplars import build_exemplar_block  # noqa: E402
# The client's standards contracts do not steer extraction -- 02 reads the FRD,
# not the target rules -- but their fingerprint belongs on every artifact this
# run produces, so a reviewer can tell which revision of the client's naming /
# engineering documents was in force. Same role as system_prompt_sha256.
from frdsttm.standards import standards_sha256  # noqa: E402

# COMMAND ----------

# MAGIC %md
# MAGIC ## System prompt
# MAGIC Short framing only. Per-field guidance lives on the Pydantic model in
# MAGIC `_models.py` as `Field(description=...)` — `frdsttm.live_extraction`
# MAGIC renders that schema (descriptions and all) into the user prompt, so
# MAGIC per-field rules (verbatim identifiers, attribution splitting, "NA"
# MAGIC overrides) reach the model via the schema, not this prompt.

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

def _doc_row(r) -> dict:
    # content_sha256 is 01's provenance column (added with the governance
    # pass); a table written before it still loads, with None.
    try:
        sha = r["content_sha256"]
    except (KeyError, ValueError, IndexError):
        sha = None
    return {"doc_id": r["doc_id"], "source_file": r["source_file"],
            "content": r["content"], "content_sha256": sha}


if IS_DATABRICKS:
    _tbl = spark.table(DOCS_TABLE)
    _cols = ["doc_id", "source_file", "content"] + (
        ["content_sha256"] if "content_sha256" in _tbl.columns else [])
    docs = [_doc_row(r) for r in _tbl.select(*_cols).collect()]
else:
    from _local_tables import read_table

    docs = [_doc_row(r) for r in read_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, DOCS_TABLE_NAME)]
print(f"{len(docs)} document(s) in {DOCS_TABLE}")
assert docs, f"No documents in {DOCS_TABLE} — run 01_frd_ingest first."

# Corpus for retrieved exemplars. Absent index -> None (the ordinary
# fallback: the prompt is byte-identical to the pre-corpus prompt); corrupt
# index -> load_corpus_index raises (never silently degrade). Mock runs skip
# exemplars entirely — they make no call for a prompt to influence.
corpus_index = None
if EXEMPLARS_MODE != "off" and not (MOCK_EXTRACTION or USE_MOCK):
    corpus_index = load_corpus_index(REFERENCE_DIR)
    if EXEMPLARS_MODE == "on" and corpus_index is None:
        raise ValueError(
            f"sttm_exemplars=on but no corpus index at {REFERENCE_DIR} — run "
            f"the review app's corpus bootstrap first, or set sttm_exemplars=auto."
        )
    print("exemplars: "
          + (f"corpus of {len(corpus_index.get('pairs', {}))} pair(s)"
             if corpus_index else "no corpus index — none"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Extract
# MAGIC One streaming call per document via `extract_live()` — schema-in-prompt,
# MAGIC client-side `extra="forbid"` validation. The return value is either a
# MAGIC validated `FrdIngestionSpec` or an exception — never a silently-coerced
# MAGIC dict. Per-doc failures are fatal; we surface `doc_id` in the error so a
# MAGIC re-run knows which one to investigate.

# COMMAND ----------

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

if not MOCK_EXTRACTION and not USE_MOCK:
    import anthropic

    # One builder for both providers (frdsttm.live_extraction.build_live_client)
    # so the choice is made -- and tested -- in ONE place. Both return the same
    # anthropic.Anthropic type, so everything downstream is provider-agnostic.
    from frdsttm.live_extraction import build_live_client

    client = build_live_client(LLM_PROVIDER, max_retries=MAX_RETRIES,
                               anthropic_module=anthropic)

Path(EXTRACTIONS_DIR).mkdir(parents=True, exist_ok=True)

total_input = total_output = 0
results = []

for d in docs:
    exemplar_block = None
    if MOCK_EXTRACTION or USE_MOCK:
        from _mock_extractions import mock_spec_for

        response = SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=mock_spec_for(d["doc_id"], d["content"]),
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )
    else:
        exemplar_block = build_exemplar_block(
            d["doc_id"], d["content"], corpus_index, REFERENCE_DIR,
            k=EXEMPLARS_K,
        ) if corpus_index else None
        try:
            response = extract_live(
                client,
                d["doc_id"],
                d["content"],
                model=ACTIVE_MODEL,   # resolved per provider (see the banner)
                max_tokens=MAX_TOKENS,
                system_prompt=SYSTEM_PROMPT,
                exemplars=exemplar_block["text"] if exemplar_block else None,
            )
        except anthropic.APIError as exc:
            raise RuntimeError(f"{d['doc_id']}: extraction failed — {exc}") from exc
        # Provenance sidecar: which approved pairs informed this prompt.
        # Written even when empty is skipped — absence of the file means
        # "no exemplars were used", a reviewable fact.
        if exemplar_block:
            (Path(EXTRACTIONS_DIR) / f"{d['doc_id']}.exemplars.json").write_text(
                json.dumps(exemplar_block["exemplars"], indent=2),
                encoding="utf-8",
            )

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

    # Provenance sidecar (docs/AI_GOVERNANCE.md): exactly which model, which
    # prompt + schema (by fingerprint), over which input bytes, at what
    # cost, produced this extraction. Facts the extraction JSON itself
    # cannot carry and the runs table (04) reads back. Written for mock
    # runs too — "provider: mock" is a reviewable fact, not an omission.
    meta = {
        "doc_id": d["doc_id"],
        "source_file": d["source_file"],
        "content_sha256": d.get("content_sha256"),
        "provider": ACTIVE_PROVIDER,
        "model": ACTIVE_MODEL if ACTIVE_PROVIDER != "mock" else None,
        "max_tokens": MAX_TOKENS,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256(
            json.dumps(FrdIngestionSpec.model_json_schema(by_alias=True), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "standards_sha256": standards_sha256(),
        "stop_reason": response.stop_reason,
        "usage": {
            "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None),
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None),
        },
        "exemplars_used": (
            [e.get("reference") or e.get("name") or str(e) for e in exemplar_block["exemplars"]]
            if (ACTIVE_PROVIDER != "mock" and exemplar_block) else []
        ),
        "extraction_file": out_path.name,
        "extraction_sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(),
        "anthropic_sdk_version": (getattr(anthropic, "__version__", None)
                                  if ACTIVE_PROVIDER != "mock" else None),
        "job_run_id": JOB_RUN_ID or None,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (Path(EXTRACTIONS_DIR) / f"{d['doc_id']}.extraction_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")

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


# COMMAND ----------

if not IS_DATABRICKS:
    # Local-mode exit guard (2026-08-22). On the py3.14 venv the interpreter can
    # deadlock at SHUTDOWN in C finalizers (deltalake/pyarrow) after every line
    # above has run and every artifact is written and closed — observed on 03
    # as a >13-minute hang at 0% CPU, while the same file exits instantly under
    # runpy. The review app's local-mode runner waits on process exit, so a
    # hang here is a failed demo run. Exit explicitly: nothing in these stages
    # relies on atexit handlers. Never reached in Databricks (no process to
    # exit — the notebook task returns normally).
    import os as _os
    import sys as _sys
    _sys.stdout.flush()
    _sys.stderr.flush()
    _os._exit(0)
