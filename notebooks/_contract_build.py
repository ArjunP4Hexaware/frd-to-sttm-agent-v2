# Databricks notebook source
# MAGIC %md
# MAGIC # Shim — the Phase-4 core moved to `src/frdsttm/contract_build.py`
# MAGIC
# MAGIC `03_contract_build` executes `%run ./_contract_build` (Databricks) or
# MAGIC `from _contract_build import build_contract` (local script), exactly
# MAGIC mirroring the `_models` shim pattern. Re-binds everything the original
# MAGIC inline cell defined — including its stdlib imports (json, re,
# MAGIC datetime, ...), which the notebook's driver cells also use. Edit the
# MAGIC real module, not this shim.

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

from frdsttm import contract_build as _frdsttm_contract_build  # noqa: E402

globals().update(
    {k: v for k, v in vars(_frdsttm_contract_build).items() if not k.startswith("__")}
)
