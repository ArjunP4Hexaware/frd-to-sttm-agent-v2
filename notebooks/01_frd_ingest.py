# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — FRD Ingestion: docx → markdown → `frd_documents`
# MAGIC
# MAGIC Phase 2 of the FRD→STTM pipeline. Reads FRD files from the `frd_raw`
# MAGIC Unity Catalog volume, converts each to markdown (headings, tables,
# MAGIC bullets preserved in document order), and lands one row per document
# MAGIC in the Delta table **`<catalog>.<schema>.frd_documents`** — the table
# MAGIC the Agent Bricks Information Extraction agent reads (`content` column).
# MAGIC
# MAGIC Ported from `brd_to_frd_agent/src/ingestion/normalize.py` with three
# MAGIC fidelity fixes found by testing against the real IS-Methodology FRDs:
# MAGIC 1. **Custom heading styles** (`Document Heading N`) now map to `#` levels —
# MAGIC    the original only matched Word's built-in `Heading N`, yielding 0 headings.
# MAGIC 2. **`w:outlineLvl` fallback** for styles that don't name their level.
# MAGIC 3. **TOC noise dropped** (`toc N`, `TOC Header`) — but body paragraphs styled
# MAGIC    'Header' are KEPT (the template puts 'Project ID: NNNNNNN' in one).
# MAGIC 4. **Nested tables inside cells flattened inline** (cell.text drops them;
# MAGIC    the template's Region/LOB lists live there).
# MAGIC
# MAGIC Run on serverless compute. Re-running is safe: full refresh from the volume.

# COMMAND ----------

# MAGIC %pip install python-docx pypdf
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

from __future__ import annotations

# Local-mode support: when this file runs as a plain `python 01_frd_ingest.py`
# script (no Databricks runtime), `dbutils`/`spark` are never injected into
# globals(), so IS_DATABRICKS is False and every widget/Spark call below
# falls back to an env-var / local-filesystem equivalent. Inside an actual
# Databricks notebook, `dbutils` always exists, so this branch is a no-op --
# the Databricks execution path below is byte-for-byte what it was before.
import os
from pathlib import Path

IS_DATABRICKS = "dbutils" in globals()
LOCAL_ROOT = Path(__file__).resolve().parent.parent / "local_dev_fixtures" if not IS_DATABRICKS else None


def _param(name: str, default: str) -> str:
    """Widget value in Databricks; same-named (uppercased) env var locally."""
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)
        return dbutils.widgets.get(name)
    return os.environ.get(name.upper(), default)


CATALOG = _param("catalog", "arjun_workspace")
SCHEMA = _param("schema", "sttm_agent")
RAW_VOLUME = _param("raw_volume", "frd_raw")
TABLE_NAME = _param("table", "frd_documents")
PREVIEW_VOL = _param("preview_volume", "sttm_out")  # optional .md copies for eyeballing; "" to skip

if IS_DATABRICKS:
    RAW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{RAW_VOLUME}"
    TABLE = f"{CATALOG}.{SCHEMA}.{TABLE_NAME}"
    PREVIEW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{PREVIEW_VOL}/parsed_frd" if PREVIEW_VOL else None
else:
    RAW_DIR = str(LOCAL_ROOT / RAW_VOLUME)
    TABLE = TABLE_NAME  # local tables are scoped by catalog/schema on disk instead; see _local_tables
    PREVIEW_DIR = str(LOCAL_ROOT / PREVIEW_VOL / "parsed_frd") if PREVIEW_VOL else None

print(f"raw:   {RAW_DIR}\ntable: {TABLE}\npreview: {PREVIEW_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Normalizer
# MAGIC Format conversion only (headings/tables/lists/paragraphs), no semantic
# MAGIC restructuring. docx fidelity notes:
# MAGIC - Tables → pipe tables; a merged cell's text is emitted once per span.
# MAGIC - Content controls (`<w:sdt>`) are recursed into — form-style templates
# MAGIC   put fillable requirement fields there; skipping them silently drops text.
# MAGIC - Requirement-id lines (BR/REQ/FR/SRQ/SIR/NFR/MDST + number) are reshaped
# MAGIC   to bold markers so they anchor cleanly for downstream extraction.

# COMMAND ----------

import hashlib
from datetime import datetime, timezone
from pathlib import Path

# COMMAND ----------

# MAGIC %run ./_frd_parsing

# COMMAND ----------

# The whole normalizer (docx/pdf/md/txt -> markdown, requirement-id markers,
# SDT unwrapping, label-contract wiring) was factored VERBATIM into
# `src/frdsttm/frd_parsing.py` (2026-08-22) so the review app's corpus
# bootstrap parses FRDs with exactly this stage's text -- one parser, no
# drift. `%run` above is a Databricks-only magic -- inert when this file
# executes as a plain script, so the names would never get defined locally
# without this explicit import.
if not IS_DATABRICKS:
    from _frd_parsing import (  # noqa: F401
        SUPPORTED_SUFFIXES,
        infer_project_id,
        normalize_to_markdown,
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Convert every file in `frd_raw`
# MAGIC Volumes are FUSE-mounted, so plain-Python file I/O works. Files with
# MAGIC unsupported extensions are reported and skipped, never fatal.

# COMMAND ----------

raw = Path(RAW_DIR)
assert raw.is_dir(), f"Volume path not found: {RAW_DIR} — check catalog/schema/volume widgets."

rows, skipped = [], []
for p in sorted(raw.iterdir()):
    if not p.is_file():
        continue
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        skipped.append(p.name)
        continue
    md = normalize_to_markdown(str(p))
    rows.append({
        "doc_id": p.stem,
        "project_id": infer_project_id(p.name),
        "source_file": p.name,
        # Provenance: the fingerprint of the SOURCE BYTES this row was parsed
        # from (docs/AI_GOVERNANCE.md). Same hash the corpus index and the
        # app's run manifest record, so a row, an index entry and a run can
        # be joined on exactly-which-document without trusting file names —
        # a revised FRD under the same name is a different sha.
        "content_sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "file_type": p.suffix.lower().lstrip("."),
        "char_count": len(md),
        "heading_count": sum(1 for l in md.splitlines() if l.startswith("#")),
        "table_count": sum(1 for l in md.splitlines() if l.startswith("| ---")),
        "content": md,
        "parsed_at": datetime.now(timezone.utc),
    })
    print(f"parsed {p.name}: {len(md):,} chars, "
          f"{rows[-1]['heading_count']} headings, {rows[-1]['table_count']} tables")

if skipped:
    print(f"skipped (unsupported type): {skipped}")
assert rows, f"No supported files found in {RAW_DIR} — upload the FRDs first."

# COMMAND ----------

# MAGIC %md
# MAGIC ## Land the Delta table
# MAGIC Full refresh (overwrite): the volume is the source of truth, so a rerun
# MAGIC after adding/replacing files rebuilds the table idempotently. A sanity
# MAGIC gate fails the run if any document parsed to (near-)empty content or
# MAGIC lost all structure — a mangled doc here is a doc the extraction agent
# MAGIC can't rescue.

# COMMAND ----------

for r in rows:
    assert r["char_count"] > 500, f"{r['source_file']}: suspiciously small ({r['char_count']} chars)"
    if r["file_type"] == "docx":
        assert r["heading_count"] > 0, f"{r['source_file']}: 0 headings — heading styles not recognized"

if IS_DATABRICKS:
    from pyspark.sql import types as T

    schema = T.StructType([
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("project_id", T.StringType(), True),
        T.StructField("source_file", T.StringType(), False),
        T.StructField("content_sha256", T.StringType(), False),
        T.StructField("file_type", T.StringType(), False),
        T.StructField("char_count", T.LongType(), False),
        T.StructField("heading_count", T.LongType(), False),
        T.StructField("table_count", T.LongType(), False),
        T.StructField("content", T.StringType(), False),
        T.StructField("parsed_at", T.TimestampType(), False),
    ])

    df = spark.createDataFrame(rows, schema=schema)
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLE)
    print(f"wrote {df.count()} row(s) to {TABLE}")
else:
    from _local_tables import write_table

    write_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, TABLE_NAME, rows)
    print(f"wrote {len(rows)} row(s) to local table {CATALOG}.{SCHEMA}.{TABLE_NAME}")

# COMMAND ----------

_preview_cols = ("doc_id", "project_id", "file_type", "char_count", "heading_count", "table_count", "parsed_at")
if IS_DATABRICKS:
    display(spark.table(TABLE).select(*_preview_cols))
else:
    from _local_tables import read_table

    for _r in read_table(LOCAL_ROOT / "warehouse", CATALOG, SCHEMA, TABLE_NAME):
        print({k: _r[k] for k in _preview_cols})

# COMMAND ----------

# MAGIC %md
# MAGIC ## Optional: markdown copies for eyeballing
# MAGIC Written to `sttm_out/parsed_frd/` so you can open the conversions and
# MAGIC spot-check the requirement tables before pointing the agent at them.
# MAGIC Set the `preview_volume` widget to "" to skip.

# COMMAND ----------

if PREVIEW_DIR:
    out_dir = Path(PREVIEW_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        (out_dir / f"{r['doc_id']}.md").write_text(r["content"], encoding="utf-8")
    print(f"wrote {len(rows)} preview file(s) to {PREVIEW_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next (Phase 3)
# MAGIC **Agents → Create Agent → Information Extraction → Select table** →
# MAGIC `frd_documents`, column **`content`**. Then paste the mapping-contract
# MAGIC JSON schema as the extraction schema and run against these documents.


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
