# Databricks notebook source
# MAGIC %md
# MAGIC # FRD → STTM pipeline
# MAGIC
# MAGIC One notebook, three tasks, selected by the `task` widget:
# MAGIC
# MAGIC | task | reads | writes |
# MAGIC |---|---|---|
# MAGIC | `extract` | `frds/<doc_id>.docx`, `vdds/VDD_<doc_id>.xlsx` | `output_sttms/<run_id>/run.json` (+ the .xlsx when nothing is missing) |
# MAGIC | `render`  | `output_sttms/<run_id>/run.json` (with the reviewer's answers) | `output_sttms/<run_id>/<doc_id>.xlsx` |
# MAGIC | `reindex` | the four volumes | `reference_sttms/corpus_index.json` + table `frd_pairing` |
# MAGIC
# MAGIC All logic lives in `src/frdsttm/pipeline.py`; this file only wires
# MAGIC widgets to it. The review app triggers this job via the Jobs API.

# COMMAND ----------

# MAGIC %pip install "anthropic>=0.60" "pydantic>=2" openpyxl python-docx pypdf databricks-sdk
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
from pathlib import Path

# src/ onto sys.path. In a workspace task cwd/__file__ are unreliable, so ask
# the notebook context for this notebook's own path.
_candidates = []
try:
    _nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    _nb_dir = Path(_nb if _nb.startswith("/Workspace") else "/Workspace" + _nb).parent
    _candidates.append(_nb_dir.parent / "src")
except Exception:  # noqa: BLE001
    pass
_candidates += [Path.cwd().parent / "src", Path.cwd() / "src"]
for _c in _candidates:
    if (_c / "frdsttm").is_dir() and str(_c) not in sys.path:
        sys.path.insert(0, str(_c))
        break

from frdsttm import pipeline  # noqa: E402
from frdsttm.corpus import pairing_rows  # noqa: E402


def _param(name, default):
    dbutils.widgets.text(name, default)
    return dbutils.widgets.get(name).strip()


TASK = _param("task", "extract")
CATALOG = _param("catalog", "arjun_workspace")
SCHEMA = _param("schema", "sttm_agent")
RUN_ID = _param("run_id", "")
DOC_ID = _param("doc_id", "")
PROVIDER = _param("provider", "databricks")
MODEL = _param("model", "claude-opus-5")
TRIGGERED_BY = _param("triggered_by", "manual")
PAIRING_TABLE = _param("pairing_table", "frd_pairing")

paths = pipeline.Paths.under(
    f"/Volumes/{CATALOG}/{SCHEMA}",
    frds=_param("frds_volume", "frds"), vdds=_param("vdds_volume", "vdds"),
    reference=_param("reference_volume", "reference_sttms"),
    output=_param("output_volume", "output_sttms"))
print(f"task={TASK} run_id={RUN_ID} doc_id={DOC_ID}\n{paths}")

# COMMAND ----------

if TASK == "extract":
    assert RUN_ID and DOC_ID, "extract needs run_id and doc_id"
    run = pipeline.run_extract(paths, RUN_ID, DOC_ID, provider=PROVIDER, model=MODEL,
                               triggered_by=TRIGGERED_BY)
    print(pipeline.report_md(run))
elif TASK == "render":
    assert RUN_ID, "render needs run_id"
    run = pipeline.run_render(paths, RUN_ID)
    print(pipeline.report_md(run))
elif TASK == "reindex":
    index = pipeline.reindex(paths)
    rows = pairing_rows(index)
    from pyspark.sql import types as T

    schema = T.StructType([
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("frd_file", T.StringType(), False),
        T.StructField("frd_sha256", T.StringType(), False),
        T.StructField("vdd_file", T.StringType(), True),
        T.StructField("vdd_files", T.IntegerType(), False),
        T.StructField("vdd_columns", T.IntegerType(), False),
        T.StructField("sttm_file", T.StringType(), True),
        T.StructField("sttm_columns", T.IntegerType(), False),
        T.StructField("status", T.StringType(), False),
        T.StructField("generatable", T.BooleanType(), False),
        T.StructField("reason", T.StringType(), False),
        T.StructField("indexed_at", T.StringType(), False),
    ])
    table = f"{CATALOG}.{SCHEMA}.{PAIRING_TABLE}"
    spark.createDataFrame(rows, schema).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
    print(f"{len(rows)} FRD(s) indexed → {table}")
    display(spark.table(table))
else:
    raise ValueError(f"unknown task {TASK!r} — extract | render | reindex")
