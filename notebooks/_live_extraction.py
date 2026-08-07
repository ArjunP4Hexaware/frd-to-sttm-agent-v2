# Databricks notebook source
# MAGIC %md
# MAGIC # Shim — the live extraction transport lives in `src/frdsttm/live_extraction.py`
# MAGIC
# MAGIC This file re-exports everything from `frdsttm.live_extraction` so both
# MAGIC consumption paths keep working unchanged:
# MAGIC
# MAGIC - Databricks: `02_extract` executes `%run ./_live_extraction`, which runs
# MAGIC   this file's cells in the calling notebook's globals.
# MAGIC - Local scripts: `from _live_extraction import ...` resolves here because
# MAGIC   `python notebooks/0N_*.py` puts `notebooks/` on `sys.path`.
# MAGIC
# MAGIC Edit the real module, not this shim.

# COMMAND ----------

import sys
from pathlib import Path

# Make src/ importable in every context this file runs in: as a module import
# from a local script (__file__ set), or inline via Databricks %run in a Git
# folder (__file__ unset; cwd is the notebooks/ directory). A pip-installed
# frdsttm (pip install -e .) makes this a no-op.
try:
    _here = Path(__file__).resolve().parent
except NameError:
    _here = Path.cwd()
for _cand in (_here.parent / "src", _here / "src", Path.cwd().parent / "src", Path.cwd() / "src"):
    if (_cand / "frdsttm").is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

# COMMAND ----------

from frdsttm import live_extraction as _frdsttm_live_extraction  # noqa: E402

# Re-bind every name (including single-underscore helpers) so `%run` callers
# and `from _live_extraction import X` both see exactly what the original
# file defined.
globals().update(
    {k: v for k, v in vars(_frdsttm_live_extraction).items() if not k.startswith("__")}
)
