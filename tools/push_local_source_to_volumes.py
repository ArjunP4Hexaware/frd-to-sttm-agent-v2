"""Push a LOCAL documents folder into the Unity Catalog volumes the deployed
App reads. Creates the volumes if they do not exist.

Why this exists (2026-08-24). The local-folder source added the same day
(`frdsttm.local_folder`, STTM_LOCAL_SOURCE_DIR) works when the review app runs
ON YOUR MACHINE. A DEPLOYED Databricks App does not run on your machine -- it
runs inside Databricks and reads Unity Catalog -- so it can never see
`~/Desktop/<your folder>`. This tool is the one-way bridge: it takes the same
folder, in the same layout, and lands it where the deployed App looks.

    laptop folder  ──this tool──▶  frd_raw + sttm_reference  ──App reads──▶ UI

Design note: the split, the manifest, the pairing and the corpus index are NOT
reimplemented here. The folder is staged through the pipeline's own
``sync_from_sharepoint`` with a ``LocalFolderClient`` -- byte-identical to what
the app does locally -- and only the RESULT is uploaded. One set of semantics,
one place to fix them.

Uploading ``corpus_index.json`` matters: the app's read path mirrors the two
volumes down with the SDK (``corpus_routes._mirror_from_uc`` ->
``jobs_runner.mirror_corpus``), which needs NO job and NO warehouse. So after
this runs, a deployed App shows the corpus even with none of the bundle jobs
deployed.

Cost: volume creation is a metadata operation and file upload is storage only.
Neither starts a cluster, a warehouse, or a job.

Usage
-----
    python tools/push_local_source_to_volumes.py [SOURCE_DIR] [options]

SOURCE_DIR defaults to $STTM_LOCAL_SOURCE_DIR. Catalog/schema default to
$CATALOG/$SCHEMA, then to the repo defaults (arjun_workspace / sttm_agent) --
the same names the notebooks and app.yaml use.

    --dry-run          show what would be created and uploaded; touch nothing
    --catalog NAME     override the catalog
    --schema NAME      override the schema
    --profile NAME     ~/.databrickscfg profile (else DATABRICKS_CONFIG_PROFILE)
    --all-volumes      also create demo_raw / sttm_out_app / sttm_audit, which
                       the app writes to at run time (see app.yaml)

The documents must follow the library's naming convention, because it is what
splits the two kinds and what pairs them:

    FRD_<name>.docx   (or .pdf/.txt/.md)      STTM_<name>.xlsx

<name> must match exactly across a pair. Files matching neither prefix are
COUNTED and named in the summary, never silently skipped.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from frdsttm.corpus import CORPUS_INDEX_NAME  # noqa: E402
from frdsttm.local_folder import (  # noqa: E402
    LocalFolderClient,
    LocalFolderConfigError,
    load_local_folder_config,
)
from frdsttm.similarity import thresholds_from  # noqa: E402
from frdsttm.sync import sync_from_sharepoint  # noqa: E402

RAW_VOLUME = "frd_raw"
REFERENCE_VOLUME = "sttm_reference"
#: Written by the app at run time, not by this tool -- created only with
#: --all-volumes so a first deploy does not fail on a missing volume.
RUNTIME_VOLUMES = ("demo_raw", "sttm_out_app", "sttm_audit")
#: The vendor-dictionary volume (2026-08-27). Created alongside the two
#: source volumes rather than under --all-volumes: it is a SOURCE store,
#: and a deployed App with no vdd_raw reports every FRD as having no
#: dictionary, which is indistinguishable from "no vendor has returned one"
#: — a state that must not be reachable by accident.
DICT_VOLUME = "vdd_raw"
DICT_PREFIX = ("VDD_", "DICT_")

FRD_PREFIX = "FRD_"
STTM_PREFIX = "STTM_"


def _thresholds():
    return thresholds_from(lambda name, default: os.environ.get(name.upper(), default))


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Push a local FRD/STTM folder into the Unity Catalog volumes "
                    "the deployed Databricks App reads.")
    p.add_argument("source_dir", nargs="?", default=None,
                   help="folder holding FRD_*/STTM_* documents "
                        "(default: $STTM_LOCAL_SOURCE_DIR)")
    p.add_argument("--catalog", default=os.environ.get("CATALOG", "arjun_workspace"))
    p.add_argument("--schema", default=os.environ.get("SCHEMA", "sttm_agent"))
    p.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    p.add_argument("--all-volumes", action="store_true",
                   help="also create demo_raw / sttm_out_app / sttm_audit")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def resolve_source(arg: str | None) -> Path:
    """The folder, from the argument or STTM_LOCAL_SOURCE_DIR, validated by the
    same loader the app uses -- so a path that works here works there."""
    if arg:
        cfg = load_local_folder_config(lambda name, default: arg)
    else:
        cfg = load_local_folder_config()   # reads STTM_LOCAL_SOURCE_DIR
    return cfg.root


def stage(source: Path, staging: Path) -> dict:
    """Run the folder through the pipeline's own sync into a temp tree.

    Produces staging/frd_raw/ and staging/sttm_reference/ exactly as the app
    would locally, including corpus_index.json and sync_manifest.json.
    ``now_iso`` is taken from the sync itself so the index carries a real
    generated-at; the manifest is uploaded too, so a later run over the same
    folder is still incremental on the LOCAL side.
    """
    from datetime import datetime, timezone

    client = LocalFolderClient(load_local_folder_config(lambda n, d: str(source)))
    return sync_from_sharepoint(
        client,
        frd_dir=staging / RAW_VOLUME,
        reference_dir=staging / REFERENCE_VOLUME,
        reference_folder=None,
        thresholds=_thresholds(),
        now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        frd_prefix=FRD_PREFIX,
        reference_prefix=STTM_PREFIX,
        # The third input (2026-08-27). The stand-in folder is FLAT — FRD_,
        # STTM_ and DICT_ all sit in it — so the folder argument stays None
        # and the prefix does the separating, exactly as it does for the
        # other two kinds. LocalFolderClient ignores `folder` by design.
        dictionary_dir=staging / DICT_VOLUME,
        dictionary_folder=None,
        dictionary_prefix=DICT_PREFIX,
    )


def ensure_volumes(w, catalog: str, schema: str, names, dry_run: bool) -> list[str]:
    """CREATE VOLUME IF NOT EXISTS, via the SDK rather than SQL.

    Deliberately not SQL: a `CREATE VOLUME` statement needs a warehouse, and
    starting one to create a metadata object would cost more than everything
    else this tool does put together.
    """
    from databricks.sdk.service.catalog import VolumeType

    existing = {v.name for v in w.volumes.list(catalog_name=catalog, schema_name=schema)}
    created = []
    for name in names:
        if name in existing:
            print(f"  volume exists: {catalog}.{schema}.{name}")
            continue
        if dry_run:
            print(f"  WOULD CREATE volume: {catalog}.{schema}.{name}")
            created.append(name)
            continue
        w.volumes.create(catalog_name=catalog, schema_name=schema, name=name,
                         volume_type=VolumeType.MANAGED,
                         comment="FRD-to-STTM agent: created by "
                                 "tools/push_local_source_to_volumes.py")
        print(f"  CREATED volume: {catalog}.{schema}.{name}")
        created.append(name)
    return created


def upload_dir(w, local_dir: Path, volume_path: str, dry_run: bool) -> int:
    """Upload every file in one staged directory into one volume (flat)."""
    n = 0
    for path in sorted(local_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        dest = f"{volume_path}/{path.name}"
        if dry_run:
            print(f"  WOULD UPLOAD {path.name:44} -> {dest}")
            n += 1
            continue
        with path.open("rb") as fh:
            w.files.upload(file_path=dest, contents=fh, overwrite=True)
        print(f"  uploaded {path.name:48} -> {dest}")
        n += 1
    return n


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        source = resolve_source(args.source_dir)
    except LocalFolderConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"source folder : {source}")
    print(f"target        : {args.catalog}.{args.schema}")
    print(f"mode          : {'DRY RUN (nothing is written)' if args.dry_run else 'LIVE'}\n")

    # -- stage locally first: if the folder is wrong, fail before touching UC
    with tempfile.TemporaryDirectory(prefix="sttm_push_") as tmp:
        staging = Path(tmp)
        result = stage(source, staging)

        print("staged from the folder:")
        print(f"  FRDs       : {result['frd_downloaded']} "
              f"(ignored, wrong prefix: {result['frd_ignored']})")
        print(f"  STTMs      : {result['reference_downloaded']} "
              f"(ignored, wrong prefix: {result['reference_ignored']})")
        print(f"  DICTs      : {result.get('dictionary_downloaded', 0)} "
              f"(ignored, wrong prefix: {result.get('dictionary_ignored', 0)})")
        pairs = result["index"]["pairs"]
        unmapped = result["index"]["unmapped"]
        for doc_id, name in sorted(result["index"].get("dictionary_pairs", {}).items()):
            d = result["index"]["dictionaries"][name]
            print(f"    {doc_id}  <->  {name}  ({d['n_fields']} column(s))")
        for name in result["index"].get("unpaired_dictionaries", []):
            print(f"    (unpaired) {name} — no FRD shares its name key")
        print(f"  paired     : {len(pairs)}  unmapped: {len(unmapped)}")
        for doc_id, p in pairs.items():
            print(f"    {doc_id}  <->  {p['reference']}  ({p['matched_by']})")
        for doc_id in unmapped:
            print(f"    {doc_id}  <->  (no STTM -- check the FRD_/STTM_ names match)")
        if result["skipped"]:
            print("  skipped (unreadable):")
            for s in result["skipped"]:
                print(f"    {s['name']}: {s['error']}")
        if not result["frd_downloaded"] and not result["reference_downloaded"]:
            print("\nnothing matched FRD_*/STTM_* in that folder — refusing to "
                  "create volumes for an empty push.", file=sys.stderr)
            return 1

        # -- Unity Catalog
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
        wanted = [RAW_VOLUME, REFERENCE_VOLUME]
        # Only when the folder actually held one. Creating an empty
        # vdd_raw would make "no vendor has returned a dictionary" and
        # "the volume is there and empty" look identical to the App.
        n_dict_staged = len(list((staging / DICT_VOLUME).glob("*.xlsx"))) \
            if (staging / DICT_VOLUME).is_dir() else 0
        if n_dict_staged:
            wanted.append(DICT_VOLUME)
        if args.all_volumes:
            wanted += list(RUNTIME_VOLUMES)

        print("\nvolumes:")
        ensure_volumes(w, args.catalog, args.schema, wanted, args.dry_run)

        root = f"/Volumes/{args.catalog}/{args.schema}"
        print("\nupload:")
        n_frd = upload_dir(w, staging / RAW_VOLUME, f"{root}/{RAW_VOLUME}", args.dry_run)
        n_ref = upload_dir(w, staging / REFERENCE_VOLUME,
                           f"{root}/{REFERENCE_VOLUME}", args.dry_run)
        n_dict = (upload_dir(w, staging / DICT_VOLUME, f"{root}/{DICT_VOLUME}", args.dry_run)
                  if n_dict_staged else 0)

    print(f"\n{'would upload' if args.dry_run else 'uploaded'}: "
          f"{n_frd} into {RAW_VOLUME}, {n_ref} into {REFERENCE_VOLUME} "
          f"(the latter includes {CORPUS_INDEX_NAME})"
          + (f", {n_dict} into {DICT_VOLUME}" if n_dict else ""))
    if not args.dry_run:
        print("\nThe deployed App mirrors these volumes on read — no job or "
              "warehouse needed. Start the app and the corpus is there.")
        print("Remaining, if the App writes at run time: create demo_raw / "
              "sttm_out_app / sttm_audit (--all-volumes) and grant the App's "
              "service principal READ VOLUME on the two above, WRITE on those.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
