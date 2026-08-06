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
for _cand in (_here.parent / "src", _here / "src", Path.cwd().parent / "src", Path.cwd() / "src"):
    if (_cand / "frdsttm").is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

# COMMAND ----------

from frdsttm import contract_build as _frdsttm_contract_build  # noqa: E402

globals().update(
    {k: v for k, v in vars(_frdsttm_contract_build).items() if not k.startswith("__")}
)
