# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — SharePoint fetch: document library → `frd_raw`
# MAGIC
# MAGIC Phase 1 of the FRD→STTM pipeline. Pulls approved FRDs from the
# MAGIC AmeriHealth SharePoint document library (Microsoft Graph, app-only
# MAGIC auth) into the `frd_raw` volume that `01_frd_ingest` scans.
# MAGIC
# MAGIC **Why a separate stage rather than Graph calls inside `01`.** `01`'s
# MAGIC contract is "parse every supported file in a directory". Keeping the
# MAGIC network at the edge means `01`–`04` stay offline and credential-free,
# MAGIC the test suite keeps running with zero network, and a fetch failure is
# MAGIC distinguishable from a parse failure in the job graph. It also means a
# MAGIC re-run of the pipeline after a parse fix does not re-download anything.
# MAGIC
# MAGIC **Files are fetched by Graph item id from one named library** — the
# MAGIC site is never crawled. Read scope is all this stage needs; the publish
# MAGIC side lives in `05_sharepoint_publish`.
# MAGIC
# MAGIC **Fail-loud.** Missing configuration raises and names both remedies
# MAGIC (widget/env var, and the secret scope). A truncated download raises
# MAGIC rather than leaving a partial .docx for `01` to parse. There is no
# MAGIC fallback to whatever happens to be sitting in `frd_raw` already: a run
# MAGIC that cannot reach SharePoint fails instead of silently ingesting a
# MAGIC stale copy.

# COMMAND ----------

# MAGIC %pip install --quiet --upgrade pip
# MAGIC # No SDK needed: Graph is plain REST and frdsttm.sharepoint uses only
# MAGIC # the standard library.

# COMMAND ----------

from __future__ import annotations

import os
from pathlib import Path

# See 01_frd_ingest's header for the full explanation of IS_DATABRICKS /
# _param(). Same dual-mode contract: widgets in Databricks, same-named
# uppercased env vars locally.
IS_DATABRICKS = "dbutils" in globals()


def _param(name: str, default: str) -> str:
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)  # noqa: F821
        return dbutils.widgets.get(name)     # noqa: F821
    return os.environ.get(name.upper(), default)


LOCAL_ROOT = Path(__file__).resolve().parent.parent / "local_dev_fixtures" if not IS_DATABRICKS else None

CATALOG = _param("catalog", "soham_workspace")
SCHEMA = _param("schema", "sttm_agent")
RAW_VOLUME = _param("raw_volume", "frd_raw")

# "fetch" pulls from the library as documented above. "skip" is for a caller
# that has ALREADY staged the document(s) into raw_volume itself — the review
# app's job-triggered demo run uploads the reviewer's chosen FRD and passes
# skip explicitly. This is an explicit opt-out validated against a closed
# set, not a fallback: an unrecognized value raises (same provider-gate
# idiom as 02_extract), and an unconfigured tenant still fails loudly in
# "fetch" mode.
FETCH_MODE = _param("sharepoint_fetch_mode", "fetch")
if FETCH_MODE not in ("fetch", "skip"):
    raise ValueError(
        f"sharepoint_fetch_mode must be 'fetch' or 'skip', got {FETCH_MODE!r}. "
        "No default is guessed for an unrecognized value."
    )
if FETCH_MODE == "skip":
    print(
        "sharepoint_fetch_mode=skip — fetch explicitly skipped by the caller, "
        f"which staged the input document(s) into {RAW_VOLUME!r} itself. "
        "01_frd_ingest will parse whatever is there and fails loudly if empty."
    )
    if IS_DATABRICKS:
        dbutils.notebook.exit("skipped")  # noqa: F821
    raise SystemExit(0)

SECRET_SCOPE = _param("secret_scope", "sttm_agent")
SECRET_KEY = _param("sharepoint_secret_key", "sharepoint_client_secret")

if IS_DATABRICKS:
    RAW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{RAW_VOLUME}"
else:
    RAW_DIR = str(LOCAL_ROOT / RAW_VOLUME)

# Mirrors 01_frd_ingest.SUPPORTED_SUFFIXES: the picker and this stage must
# never land a document the parser cannot read.
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}

# COMMAND ----------

# MAGIC %run ./_sharepoint

# COMMAND ----------

if not IS_DATABRICKS:
    from _sharepoint import build_client, load_config  # noqa: F401

# COMMAND ----------


def _client_secret() -> str:
    """Secret scope in Databricks, env var locally — same shape as the
    Anthropic key in `02_extract`. The value is never printed or returned
    anywhere else."""
    if IS_DATABRICKS:
        try:
            return dbutils.secrets.get(scope=SECRET_SCOPE, key=SECRET_KEY)  # noqa: F821
        except Exception:
            pass
    return os.environ.get("SHAREPOINT_CLIENT_SECRET", "")


cfg = load_config(_param, _client_secret)
print(f"site:    {cfg.host}{cfg.site_path}")
print(f"library: {cfg.library}  folder: {cfg.frd_folder or '<root>'}")
print(f"dest:    {RAW_DIR}")

# COMMAND ----------

client = build_client(cfg)
fetched = client.fetch_to_dir(RAW_DIR, suffixes=SUPPORTED_SUFFIXES)

for item in fetched:
    print(f"fetched {item.name}: {item.size:,} bytes, modified {item.modified}")

assert fetched, (
    f"No supported documents found in {cfg.library}/{cfg.frd_folder or '<root>'} "
    f"(supported: {sorted(SUPPORTED_SUFFIXES)}). Upload an FRD to the library, or "
    f"check the sharepoint_frd_folder widget/env var."
)
print(f"\n{len(fetched)} document(s) -> {RAW_DIR}; 01_frd_ingest can run.")
