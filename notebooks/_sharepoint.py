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
_candidates = [_here.parent / "src", _here / "src", Path.cwd().parent / "src", Path.cwd() / "src"]
if "dbutils" in globals():
    # cwd/__file__ are unreliable inside a WORKSPACE notebook task (as
    # opposed to a Git-folder %run, where cwd is documented to be the
    # notebook's own directory) -- ask the notebook context directly.
    try:
        _nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        _nb_dir = Path(_nb_path if _nb_path.startswith("/Workspace") else "/Workspace" + _nb_path).resolve().parent
        _candidates.insert(0, _nb_dir.parent / "src")
    except Exception:  # noqa: BLE001 -- best-effort extra candidate, not fatal
        pass
for _cand in _candidates:
    if (_cand / "frdsttm").is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

# COMMAND ----------

from frdsttm import sharepoint as _frdsttm_sharepoint  # noqa: E402

globals().update(
    {k: v for k, v in vars(_frdsttm_sharepoint).items() if not k.startswith("__")}
)
