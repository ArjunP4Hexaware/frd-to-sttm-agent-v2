"""Configuration — env vars with defaults. Nothing else reads os.environ."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SRC = ROOT / "src"
if (_SRC / "frdsttm").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))          # the deployed App never pip-installs this repo

from frdsttm.pipeline import Paths  # noqa: E402

MODE = os.environ.get("STTM_APP_MODE", "local").strip().lower()
IS_DATABRICKS = MODE == "databricks"
CATALOG = os.environ.get("CATALOG", "arjun_workspace")
SCHEMA = os.environ.get("SCHEMA", "sttm_agent")
VOLUMES = {
    "frds": os.environ.get("STTM_FRDS_VOLUME", "frds"),
    "vdds": os.environ.get("STTM_VDDS_VOLUME", "vdds"),
    "reference": os.environ.get("STTM_REFERENCE_VOLUME", "reference_sttms"),
    "output": os.environ.get("STTM_OUTPUT_VOLUME", "output_sttms"),
}
PROVIDER = os.environ.get("STTM_PROVIDER", "databricks").strip().lower() or "databricks"
MODEL = os.environ.get("STTM_MODEL", "claude-opus-5").strip() or "claude-opus-5"
JOB_NAME = os.environ.get("STTM_JOB_NAME", "frd_sttm_pipeline")
JOB_ID = os.environ.get("STTM_JOB_ID", "").strip()

# Local mode: the four folders. Databricks mode: a container-side mirror of
# the four volumes (the volumes are the truth; this is a cache).
LOCAL_ROOT = Path(os.environ.get("STTM_LOCAL_DATA") or (ROOT / "local_data")).resolve()
PATHS = Paths.under(LOCAL_ROOT, VOLUMES["frds"], VOLUMES["vdds"], VOLUMES["reference"], VOLUMES["output"])
VOLUME_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}"


def volume_path(kind: str) -> str:
    return f"{VOLUME_ROOT}/{VOLUMES[kind]}"
