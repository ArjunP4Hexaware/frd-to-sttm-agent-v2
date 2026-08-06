# Databricks notebook source
# MAGIC %md
# MAGIC # Shim — the mock extraction specs moved to `src/frdsttm/mock_extractions.py`
# MAGIC
# MAGIC Local-mode only (`STTM_MOCK_EXTRACTION=1` in `02_extract`, plus the
# MAGIC review app's mock orchestration); gated on `not IS_DATABRICKS`, so a
# MAGIC real Databricks run never imports it. Edit the real module, not this
# MAGIC shim.

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

from frdsttm import mock_extractions as _frdsttm_mock_extractions  # noqa: E402

# Includes single-underscore names — review_app_react/backend/orchestration.py
# imports _tier1_filename_match from this shim.
globals().update(
    {k: v for k, v in vars(_frdsttm_mock_extractions).items() if not k.startswith("__")}
)
