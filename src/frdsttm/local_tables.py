# Databricks notebook source
# MAGIC %md
# MAGIC # Local filesystem stand-in for `spark.table(...)` / `.write.saveAsTable(...)`
# MAGIC
# MAGIC Used only when a notebook detects it isn't running inside a Databricks
# MAGIC runtime (no `dbutils`/`spark` injected into its globals -- see the
# MAGIC `IS_DATABRICKS` check at the top of each notebook's parameter cell).
# MAGIC Backed by the `deltalake` package (delta-rs bindings): writes/reads real
# MAGIC local Delta tables with no Spark, JVM, or cluster involved, under
# MAGIC `local_dev_fixtures/warehouse/<catalog>/<schema>/<table>/` -- mirroring the
# MAGIC `<catalog>.<schema>.<table>` three-level namespace the real Unity Catalog
# MAGIC tables use. Not imported by, and has no effect on, real Databricks runs.

# COMMAND ----------

from pathlib import Path
from typing import Any


def _table_path(warehouse_dir: Path, catalog: str, schema: str, table: str) -> Path:
    return Path(warehouse_dir) / catalog / schema / table


def write_table(
    warehouse_dir: Path, catalog: str, schema: str, table: str, rows: list[dict[str, Any]]
) -> str:
    """Overwrite a local Delta table with `rows`. Mirrors
    `.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(...)`.
    """
    path = _table_path(warehouse_dir, catalog, schema, table)
    path.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Nothing to infer a schema from; leave any existing table as-is
        # rather than guess a shape (mirrors Spark's explicit-schema write,
        # which this local mode has no equivalent for on zero rows).
        return str(path)

    import pyarrow as pa
    from deltalake import write_deltalake

    write_deltalake(str(path), pa.Table.from_pylist(rows), mode="overwrite", schema_mode="overwrite")
    return str(path)


def read_table(warehouse_dir: Path, catalog: str, schema: str, table: str) -> list[dict[str, Any]]:
    """Read a local Delta table back as a list of dicts (drop-in replacement
    for `spark.table(...).collect()`, since each dict supports `row["col"]`
    the same way a pyspark Row does)."""
    path = _table_path(warehouse_dir, catalog, schema, table)
    if not path.exists():
        return []

    from deltalake import DeltaTable
    from deltalake.exceptions import TableNotFoundError

    try:
        dt = DeltaTable(str(path))
    except TableNotFoundError:
        # write_table() no-ops on zero rows rather than guess a pyarrow
        # schema (see its docstring) -- so "directory exists but was never
        # actually written" means "table is empty," not an error.
        return []
    return dt.to_pyarrow_table().to_pylist()
