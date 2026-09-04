"""Upload a folder of documents into the four volumes, then reindex.

    python tools/push_documents.py ~/Desktop/documents [--dry-run]

Files are routed by prefix: FRD_* → frds, VDD_*/DICT_* → vdds, STTM_* →
reference_sttms. Everything else is listed and skipped. Missing volumes are
created. Then the bundle job runs `task=reindex` so corpus_index.json and
the frd_pairing table reflect what was pushed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

CATALOG = os.environ.get("CATALOG", "arjun_workspace")
SCHEMA = os.environ.get("SCHEMA", "sttm_agent")
VOLUMES = ("frds", "vdds", "reference_sttms", "output_sttms")


def route(name: str) -> str | None:
    n = name.upper()
    if n.startswith("FRD_") and name.lower().endswith((".docx", ".pdf", ".md", ".txt")):
        return "frds"
    if n.startswith(("VDD_", "DICT_")) and name.lower().endswith(".xlsx"):
        return "vdds"
    if n.startswith("STTM_") and name.lower().endswith(".xlsx"):
        return "reference_sttms"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-reindex", action="store_true")
    a = ap.parse_args()
    folder = Path(a.folder).expanduser()
    plan, skipped = [], []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.name.startswith(("~$", ".")):
            continue
        vol = route(p.name)
        (plan if vol else skipped).append((p, vol))
    for p, vol in plan:
        print(f"{vol:16s} <- {p.name}")
    for p, _ in skipped:
        print(f"{'(skipped)':16s}    {p.name}")
    if a.dry_run:
        return 0
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.catalog import VolumeType

    w = WorkspaceClient()
    existing = {v.name for v in w.volumes.list(CATALOG, SCHEMA)}
    for v in VOLUMES:
        if v not in existing:
            w.volumes.create(catalog_name=CATALOG, schema_name=SCHEMA, name=v, volume_type=VolumeType.MANAGED)
            print(f"created volume {CATALOG}.{SCHEMA}.{v}")
    for p, vol in plan:
        # databricks-sdk >= 0.60 wants a file-like object, not bytes.
        with p.open("rb") as fh:
            w.files.upload(f"/Volumes/{CATALOG}/{SCHEMA}/{vol}/{p.name}", fh, overwrite=True)
        print(f"uploaded {p.name}")
    if a.no_reindex:
        return 0
    job_id = os.environ.get("STTM_JOB_ID", "").strip()
    name = os.environ.get("STTM_JOB_NAME", "frd_sttm_pipeline")
    if not job_id:
        hits = [j for j in w.jobs.list(name=name) if j.settings and j.settings.name == name]
        if len(hits) != 1:
            print(f"cannot find exactly one job named {name!r}; set STTM_JOB_ID. Skipping reindex.")
            return 1
        job_id = hits[0].job_id
    r = w.jobs.run_now(job_id=int(job_id), job_parameters={"task": "reindex", "catalog": CATALOG, "schema": SCHEMA})
    rid = getattr(r, "run_id", None) or r.response.run_id
    print(f"reindex job run {rid} started")
    while True:
        jr = w.jobs.get_run(rid)
        life = getattr(jr.state.life_cycle_state, "value", str(jr.state.life_cycle_state))
        if life in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
            res = getattr(jr.state.result_state, "value", str(jr.state.result_state))
            print(f"reindex {res}")
            return 0 if res == "SUCCESS" else 1
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
