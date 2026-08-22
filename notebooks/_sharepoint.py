# Databricks notebook source
# MAGIC %md
# MAGIC # Shim — the SharePoint transport lives in `src/frdsttm/sharepoint.py`
# MAGIC
# MAGIC Re-exports `frdsttm.sharepoint` so both consumption paths keep working:
# MAGIC
# MAGIC - Databricks: `00_sharepoint_sync` / `00_sharepoint_fetch` execute
# MAGIC   `%run ./_sharepoint`, which runs this file's cells in the calling
# MAGIC   notebook's globals (and put `src/` on sys.path, so `frdsttm.sync`
# MAGIC   imports afterwards).
# MAGIC - Local scripts: `from _sharepoint import ...` resolves here because
# MAGIC   `python notebooks/0N_*.py` puts `notebooks/` on `sys.path`.
# MAGIC
# MAGIC Edit the real module, not this shim.

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

from frdsttm import sharepoint as _frdsttm_sharepoint  # noqa: E402

globals().update(
    {k: v for k, v in vars(_frdsttm_sharepoint).items() if not k.startswith("__")}
)
