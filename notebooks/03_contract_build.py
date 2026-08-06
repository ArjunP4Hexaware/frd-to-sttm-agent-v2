# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Contract Build: validate → enrich → audit → gate → emit
# MAGIC
# MAGIC Phase 4 of the FRD→STTM pipeline. Takes Phase 3's extraction JSONs (produced
# MAGIC by `02_extract` — Anthropic Python SDK structured outputs) and applies the
# MAGIC deterministic layer:
# MAGIC
# MAGIC 1. **Validate** against a strict pydantic model (`extra="forbid"` — schema
# MAGIC    drift in agent output fails loudly, never silently).
# MAGIC 2. **Enrich deterministically** — regex-able facts the LLM shouldn't own:
# MAGIC    `project_id` from the 'Project ID: NNNNNNN' line, LOB Region/code pairs
# MAGIC    from the Descriptive Metadata table. Disagreement between LLM and regex
# MAGIC    is flagged, not silently overwritten.
# MAGIC 3. **Grounding audit** — every extracted identifier/table/path/pattern must
# MAGIC    appear in the source content (strict); prose fields are token-overlap
# MAGIC    checked (advisory). Catches inventions.
# MAGIC 4. **Attribution check** — identical column-conditioned or recycle rules on
# MAGIC    multiple feeds are gated as ambiguities (resolvable later against the
# MAGIC    STTM source dictionary), not auto-fixed.
# MAGIC 5. **Emit** — one feed-level mapping contract JSON per document (same family
# MAGIC    as `sttm_mapping_contracts.json`, plus provenance + ambiguities), a
# MAGIC    markdown run report, and a `frd_contracts` Delta table.
# MAGIC
# MAGIC **Gate semantics:** FAIL (invalid or strict-ungrounded) /
# MAGIC PASS_WITH_FLAGS (ambiguities or advisory-ungrounded) / PASS.
# MAGIC
# MAGIC **Input:** reads `<doc_id>.json` files under `sttm_out/extractions/` (loose
# MAGIC filename matching — spaces/underscores ok), which is where `02_extract`
# MAGIC writes.

# COMMAND ----------

# MAGIC %pip install "pydantic>=2"
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
CONTRACTS_TABLE_NAME = _param("contracts_table", "frd_contracts")
OUT_VOLUME = _param("out_volume", "sttm_out")

if IS_DATABRICKS:
    DOCS_TABLE = f"{CATALOG}.{SCHEMA}.{DOCS_TABLE_NAME}"
    CONTRACTS_TABLE = f"{CATALOG}.{SCHEMA}.{CONTRACTS_TABLE_NAME}"
    OUT_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{OUT_VOLUME}"
else:
    DOCS_TABLE = DOCS_TABLE_NAME
    CONTRACTS_TABLE = CONTRACTS_TABLE_NAME
    OUT_ROOT = str(LOCAL_ROOT / OUT_VOLUME)
EXTRACTIONS_DIR = f"{OUT_ROOT}/extractions"
CONTRACTS_DIR = f"{OUT_ROOT}/contracts"
REPORTS_DIR = f"{OUT_ROOT}/reports"

print(f"docs: {DOCS_TABLE}\nout:  {CONTRACTS_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Shared pydantic models
# MAGIC Extracted to `notebooks/_models.py` so `02_extract` and this notebook
# MAGIC use one definition of the schema — the pipeline's schema-drift guard
# MAGIC (`extra="forbid"`) only works if both sides agree on the shape.

# COMMAND ----------

# MAGIC %run ./_models

# COMMAND ----------

# `%run` above is a Databricks-only magic -- inert (just a comment) when this
# file executes as a plain script, so FrdIngestionSpec/Project would never
# get defined locally without this explicit import. _models.py has no
# Databricks/Spark dependency, so this import works unmodified either way.
if not IS_DATABRICKS:
    from _models import FrdIngestionSpec, GatedAmbiguity, Project  # noqa: F401

# COMMAND ----------

# MAGIC %md
# MAGIC ## Core: enrichment, grounding audit, gating
# MAGIC Pure Python — no Spark dependencies. Extracted verbatim to
# MAGIC `src/frdsttm/contract_build.py` (unit-tested in `tests/`); the shim
# MAGIC `notebooks/_contract_build.py` re-exports it for the `%run` below.

# COMMAND ----------

# MAGIC %run ./_contract_build

# COMMAND ----------

# `%run` above is a Databricks-only magic -- inert (just a comment) when this
# file executes as a plain script. The stdlib imports below run in both modes
# (they were previously part of the extracted cell and the driver cells rely
# on them); the explicit build_contract import restores the entry point
# locally.
import json
import re
from datetime import datetime, timezone

if not IS_DATABRICKS:
    from _contract_build import build_contract  # noqa: F401

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load documents + extractions

# COMMAND ----------

from pathlib import Path

if IS_DATABRICKS:
    docs = {
        r["doc_id"]: {"content": r["content"], "source_file": r["source_file"]}
        for r in spark.table(DOCS_TABLE).select("doc_id", "source_file", "content").collect()
    }
else:
    from _local_tables import read_table

    docs = {
        r["doc_id"]: {"content": r["content"], "source_file": r["source_file"]}
        for r in read_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, DOCS_TABLE_NAME)
    }
print(f"{len(docs)} document(s) in {DOCS_TABLE}")


def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


extractions = {}
ed = Path(EXTRACTIONS_DIR)
assert ed.is_dir(), (
    f"{EXTRACTIONS_DIR} not found. Run 02_extract first, or save one JSON per "
    f"document under this directory (filename ~ doc_id; spacing/underscores "
    f"don't need to match exactly)."
)
loose_docs = {_loose(d): d for d in docs}
for jf in sorted(ed.glob("*.json")):
    key = _loose(jf.stem)
    match = next((d for lk, d in loose_docs.items() if key in lk or lk in key), None)
    if match is None:
        print(f"WARNING: {jf.name} matches no doc_id — skipped")
        continue
    extractions[match] = json.loads(jf.read_text(encoding="utf-8"))
    print(f"matched {jf.name} -> {match}")

missing = set(docs) - set(extractions)
if missing:
    print(f"NOTE: no extraction for: {sorted(missing)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build, gate, and persist

# COMMAND ----------

results = []
for doc_id, extraction in extractions.items():
    res = build_contract(doc_id, docs[doc_id]["source_file"], extraction, docs[doc_id]["content"])
    results.append(res)
    print(f"{res['status']:16s} {doc_id}")

Path(CONTRACTS_DIR).mkdir(parents=True, exist_ok=True)
Path(REPORTS_DIR).mkdir(parents=True, exist_ok=True)
for res in results:
    if res["contract"] is not None:
        (Path(CONTRACTS_DIR) / f"{res['doc_id']}.contract.json").write_text(
            json.dumps(res["contract"], indent=2, ensure_ascii=False), encoding="utf-8")
    (Path(REPORTS_DIR) / f"{res['doc_id']}.report.md").write_text(res["report_md"], encoding="utf-8")
print(f"contracts -> {CONTRACTS_DIR}\nreports   -> {REPORTS_DIR}")

rows = [{
    "doc_id": r["doc_id"],
    "status": r["status"],
    "n_feeds": len(r["contract"]["feeds"]) if r["contract"] else 0,
    "n_ambiguities": len(r["contract"]["_provenance"]["ambiguities"]) if r["contract"] else 0,
    "n_strict_failed": len(r["contract"]["_provenance"]["grounding"]["strict_failed"]) if r["contract"] else -1,
    "contract": json.dumps(r["contract"], ensure_ascii=False) if r["contract"] else None,
    "audited_at": datetime.now(timezone.utc),
} for r in results]

_preview_cols = ("doc_id", "status", "n_feeds", "n_ambiguities", "n_strict_failed", "audited_at")
if IS_DATABRICKS:
    from pyspark.sql import types as T

    cschema = T.StructType([
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("status", T.StringType(), False),
        T.StructField("n_feeds", T.IntegerType(), False),
        T.StructField("n_ambiguities", T.IntegerType(), False),
        T.StructField("n_strict_failed", T.IntegerType(), False),
        T.StructField("contract", T.StringType(), True),
        T.StructField("audited_at", T.TimestampType(), False),
    ])
    spark.createDataFrame(rows, cschema).write.mode("overwrite").option(
        "overwriteSchema", "true").saveAsTable(CONTRACTS_TABLE)
    display(spark.table(CONTRACTS_TABLE).select(*_preview_cols))
else:
    from _local_tables import read_table, write_table

    write_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, CONTRACTS_TABLE_NAME, rows)
    for _r in read_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, CONTRACTS_TABLE_NAME):
        print({k: _r[k] for k in _preview_cols})

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read the reports
# MAGIC Open `sttm_out/reports/*.report.md` — PASS_WITH_FLAGS items are the human
# MAGIC review queue. **Next (Phase 5):** the STTM renderer (contract → client
# MAGIC workbook via openpyxl) and the source-dictionary cross-check that resolves
# MAGIC attribution ambiguities deterministically.
