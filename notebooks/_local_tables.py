# Databricks notebook source
# MAGIC %md
# MAGIC # Shim — the local-table helpers moved to `src/frdsttm/local_tables.py`
# MAGIC
# MAGIC Local-mode only (`from _local_tables import read_table, write_table`
# MAGIC inside the notebooks' `not IS_DATABRICKS` branches); never imported by
# MAGIC a real Databricks run. Edit the real module, not this shim.

# COMMAND ----------

import sys
from pathlib import Path

try:
    _here = Path(__file__).resolve().parent
except NameError:
    _here = Path.cwd()
for _cand in (_here.parent / "src", _here / "src", Path.cwd().parent / "src", Path.cwd() / "src"):
    if (_cand / "frdsttm").is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

# COMMAND ----------

from frdsttm import local_tables as _frdsttm_local_tables  # noqa: E402

globals().update(
    {k: v for k, v in vars(_frdsttm_local_tables).items() if not k.startswith("__")}
)
