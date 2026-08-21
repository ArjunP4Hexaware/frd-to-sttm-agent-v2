# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — SharePoint publish: rendered STTM workbooks → document library
# MAGIC
# MAGIC Final phase. Uploads what `04_sttm_render` wrote to
# MAGIC `sttm_out/rendered/<doc_id>.sttm.xlsx` into the SharePoint output
# MAGIC folder, closing the loop the program deck describes: the FRD comes in
# MAGIC from the library, the finished mapping goes back to it.
# MAGIC
# MAGIC **Publishing is a deliberate, separate step.** It is not folded into
# MAGIC `04` because `04` is re-run every time a reviewer resolves a gated
# MAGIC ambiguity, and a re-render is not automatically a re-publish — the
# MAGIC human gate sits between them. Running `04` must never push a
# MAGIC not-yet-approved workbook to the client's library.
# MAGIC
# MAGIC **Write scope.** This stage is the only thing in the repo that writes
# MAGIC to SharePoint, and it writes only inside `sharepoint_output_folder`.
# MAGIC Keep the app registration's write permission scoped to that folder.
# MAGIC
# MAGIC **Fail-loud.** Nothing to publish is an error, not a no-op: a silent
# MAGIC success here would look identical to a successful publish in the job
# MAGIC graph. Set `publish_doc_id` to publish exactly one document.

# COMMAND ----------

from __future__ import annotations

import os
from pathlib import Path

IS_DATABRICKS = "dbutils" in globals()


def _param(name: str, default: str) -> str:
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)  # noqa: F821
        return dbutils.widgets.get(name)     # noqa: F821
    return os.environ.get(name.upper(), default)


LOCAL_ROOT = Path(__file__).resolve().parent.parent / "local_dev_fixtures" if not IS_DATABRICKS else None

CATALOG = _param("catalog", "soham_workspace")
SCHEMA = _param("schema", "sttm_agent")
OUT_VOLUME = _param("out_volume", "sttm_out")
PUBLISH_DOC_ID = _param("publish_doc_id", "").strip()   # "" = every rendered workbook

SECRET_SCOPE = _param("secret_scope", "sttm_agent")
SECRET_KEY = _param("sharepoint_secret_key", "sharepoint_client_secret")

if IS_DATABRICKS:
    OUT_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{OUT_VOLUME}"
else:
    OUT_ROOT = str(LOCAL_ROOT / OUT_VOLUME)
RENDERED_DIR = f"{OUT_ROOT}/rendered"

# COMMAND ----------

# MAGIC %run ./_sharepoint

# COMMAND ----------

if not IS_DATABRICKS:
    from _sharepoint import build_client, load_config  # noqa: F401

# COMMAND ----------


def _client_secret() -> str:
    if IS_DATABRICKS:
        try:
            return dbutils.secrets.get(scope=SECRET_SCOPE, key=SECRET_KEY)  # noqa: F821
        except Exception:
            pass
    return os.environ.get("SHAREPOINT_CLIENT_SECRET", "")


cfg = load_config(_param, _client_secret)

workbooks = sorted(Path(RENDERED_DIR).glob("*.sttm.xlsx"))
if PUBLISH_DOC_ID:
    workbooks = [w for w in workbooks if w.name == f"{PUBLISH_DOC_ID}.sttm.xlsx"]

assert workbooks, (
    f"Nothing to publish from {RENDERED_DIR}"
    + (f" for publish_doc_id={PUBLISH_DOC_ID!r}" if PUBLISH_DOC_ID else "")
    + ". Run 04_sttm_render first. (Publishing nothing is treated as an error: a "
      "silent no-op is indistinguishable from a successful publish downstream.)"
)

print(f"site:      {cfg.host}{cfg.site_path}")
print(f"library:   {cfg.library}  folder: {cfg.output_folder or '<root>'}")
print(f"publishing {len(workbooks)} workbook(s) from {RENDERED_DIR}")

# COMMAND ----------

client = build_client(cfg)
for wb in workbooks:
    result = client.upload_file(wb)
    print(f"published {wb.name}: {wb.stat().st_size:,} bytes -> {result.get('webUrl', '<no url>')}")

print(f"\n{len(workbooks)} workbook(s) published to "
      f"{cfg.library}/{cfg.output_folder or '<root>'}.")
