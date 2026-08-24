# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — SharePoint sync: document library → `frd_raw` + `sttm_reference` → corpus index
# MAGIC
# MAGIC The agent's "set up once, then stay in sync" stage (decided
# MAGIC 2026-08-22; replaces the review app's one-shot corpus bootstrap).
# MAGIC One run:
# MAGIC
# MAGIC 1. lists the FRD folder and the STTM folder of the AmeriHealth SharePoint
# MAGIC    library (Microsoft Graph, app-only auth, READ scope only),
# MAGIC 2. downloads only what is **new or changed** since the last run (Graph
# MAGIC    item id + eTag + modified + size, kept in `sync_manifest.json` next to
# MAGIC    the corpus index) — the first run is the bulk load, every later run is
# MAGIC    the incremental sync — and removes the local copy of anything that
# MAGIC    left the library,
# MAGIC 3. rebuilds `corpus_index.json`: parses every FRD, parses every reference
# MAGIC    workbook, records a content fingerprint for each, and pairs FRDs to
# MAGIC    STTMs (exact name match first, deterministic similarity second).
# MAGIC
# MAGIC **Zero model calls.** Ingest-all is not extract-all: the billed
# MAGIC extraction happens only when a person asks for a mapping in the app.
# MAGIC
# MAGIC **Triggered by the app (decided 2026-08-22 late evening — NOT a
# MAGIC schedule).** The review app runs this when it STARTS UP and on its
# MAGIC "Sync now" control, so whatever is in SharePoint — a newly approved FRD,
# MAGIC or an STTM a reviewer uploaded after finishing it — is in the volumes
# MAGIC and paired by the time anyone looks. In databricks mode the app triggers
# MAGIC `resources/frd_sttm_sync_job.yml` (this notebook) via the Jobs API.
# MAGIC
# MAGIC **Naming convention.** Every FRD in the library is `FRD_<name>.<ext>`,
# MAGIC every STTM `STTM_<name>.xlsx` (`frd_name_prefix` / `sttm_name_prefix`
# MAGIC widgets; blank disables the filter). Files without the prefix are
# MAGIC counted as ignored in the summary, never silently dropped.
# MAGIC
# MAGIC **`sync_mode`**: `sync` (the above) or `reindex` (step 3 only — rebuild
# MAGIC the index from whatever the volumes already hold, no network; the path
# MAGIC for an environment whose tenant is not wired yet, and for the synthetic
# MAGIC smoke fixtures). Anything else raises — no value is guessed.
# MAGIC
# MAGIC **Fail-loud, but one bad file never sinks the run.** Missing
# MAGIC configuration raises naming both remedies; a Graph refusal on the
# MAGIC LISTING raises; a single failed download or unparsable FRD is reported
# MAGIC in the summary (`skipped`) and the rest of the corpus still builds.
# MAGIC An EMPTY library is a warning, not a failure: a start-up sync against
# MAGIC a library nobody has populated yet must not break the app.

# COMMAND ----------

# MAGIC %pip install --quiet --upgrade pip openpyxl python-docx pypdf
# MAGIC # Graph is plain REST (standard library); the parsers need the three
# MAGIC # document libraries above.

# COMMAND ----------

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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

CATALOG = _param("catalog", "arjun_workspace")
SCHEMA = _param("schema", "sttm_agent")
RAW_VOLUME = _param("raw_volume", "frd_raw")
REFERENCE_VOLUME = _param("reference_volume", "sttm_reference")

SYNC_MODE = _param("sync_mode", "sync").strip().lower()
FRD_NAME_PREFIX = _param("frd_name_prefix", "FRD_").strip()
STTM_NAME_PREFIX = _param("sttm_name_prefix", "STTM_").strip()
if SYNC_MODE not in ("sync", "reindex"):
    raise ValueError(
        f"sync_mode must be 'sync' or 'reindex', got {SYNC_MODE!r}. "
        "No default is guessed for an unrecognized value."
    )

SECRET_SCOPE = _param("secret_scope", "sttm_agent")
SECRET_KEY = _param("sharepoint_secret_key", "sharepoint_client_secret")

if IS_DATABRICKS:
    RAW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{RAW_VOLUME}"
    REFERENCE_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{REFERENCE_VOLUME}"
else:
    RAW_DIR = str(LOCAL_ROOT / RAW_VOLUME)
    REFERENCE_DIR = str(LOCAL_ROOT / REFERENCE_VOLUME)

NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")

# COMMAND ----------

# MAGIC %run ./_sharepoint

# COMMAND ----------

if not IS_DATABRICKS:
    from _sharepoint import build_client, load_config  # noqa: F401

# The shim above put src/ on sys.path; the sync logic lives in the package
# (ONE implementation for this job and the review app's "Sync now").
from frdsttm.similarity import thresholds_from  # noqa: E402
from frdsttm.sync import reindex, sync_from_sharepoint  # noqa: E402

THRESHOLDS = thresholds_from(_param)

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


def _print_summary(summary: dict) -> None:
    index = summary["index"]
    print(json.dumps({k: v for k, v in summary.items() if k != "index"}, indent=2))
    print(
        f"\ncorpus: {len(index['frds'])} FRD(s), {len(index['references'])} reference "
        f"STTM(s), {len(index['pairs'])} pair(s), {len(index['unmapped'])} unmapped "
        f"-> {REFERENCE_DIR}/corpus_index.json"
    )
    for doc_id, pair in sorted(index["pairs"].items()):
        print(f"  paired   {doc_id}  ->  {pair['reference']}  "
              f"({pair['matched_by']}, {pair['confidence']}, {pair['score']:.2f})")
    for doc_id in index["unmapped"]:
        print(f"  unmapped {doc_id}")
    for s in summary.get("skipped", []):
        print(f"  SKIPPED  {s.get('kind', '?')} {s['name']}: {s['error']}")


# COMMAND ----------

if SYNC_MODE == "reindex":
    print(f"sync_mode=reindex — rebuilding the corpus index from {RAW_DIR} and "
          f"{REFERENCE_DIR} without touching SharePoint.")
    index, skipped = reindex(RAW_DIR, REFERENCE_DIR, THRESHOLDS, NOW)
    _print_summary({"mode": "reindex", "skipped": skipped, "index": index})
else:
    cfg = load_config(_param, _client_secret)
    print(f"site:      {cfg.host}{cfg.site_path}")
    print(f"library:   {cfg.library}")
    print(f"FRDs:      {cfg.frd_folder or '<root>'}  ->  {RAW_DIR}")
    print(f"STTMs:     {cfg.sttm_folder or '<root>'}  ->  {REFERENCE_DIR}")
    client = build_client(cfg)
    summary = sync_from_sharepoint(
        client, frd_dir=RAW_DIR, reference_dir=REFERENCE_DIR,
        reference_folder=cfg.sttm_folder, thresholds=THRESHOLDS, now_iso=NOW,
        frd_prefix=FRD_NAME_PREFIX, reference_prefix=STTM_NAME_PREFIX,
    )
    _print_summary(summary)
    if summary["frd_listed"] == 0:
        print(
            f"\nWARNING: no FRDs in {cfg.library}/{cfg.frd_folder or '<root>'} — "
            "nothing to map yet. (Not an error: a start-up sync against an "
            "empty folder is a normal state.)"
        )
