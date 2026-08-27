# Databricks notebook source
# MAGIC %md
# MAGIC # 90 — Unity Catalog governance: comments, tags, the audit volume
# MAGIC
# MAGIC The ONE set-up step that makes this agent's data assets *describable*
# MAGIC inside Unity Catalog — and therefore inside whatever catalogs UC
# MAGIC (Collibra harvests UC tables/volumes, tags and comments; so do UC's own
# MAGIC Catalog Explorer and lineage views). Idempotent: re-run any time.
# MAGIC
# MAGIC What it does, in order:
# MAGIC 1. `CREATE VOLUME IF NOT EXISTS <catalog>.<schema>.<audit_volume>` — the
# MAGIC    append-only audit trail the review app writes
# MAGIC    (`review_app_react/backend/audit.py`).
# MAGIC 2. `COMMENT ON` every known table + volume (what it holds, who writes it).
# MAGIC 3. `SET TAGS` on every known table + volume, and on the document-text
# MAGIC    column of the documents tables — owner, steward, sensitivity, data
# MAGIC    class, source system, content kind, retention policy.
# MAGIC 4. **Access model (decided 2026-08-23, Arjun):** the people who may RUN
# MAGIC    the agent are exactly the people who may READ the data it touches —
# MAGIC    one group (the client's BSAs, an Entra ID group synced into
# MAGIC    Databricks). With `reviewer_group` set: `GRANT USE CATALOG / USE
# MAGIC    SCHEMA / READ VOLUME` on the read volumes (frd_raw, sttm_reference,
# MAGIC    sttm_out_app, the audit volume) to that group, and — with `app_name`
# MAGIC    set — `CAN USE` on the Databricks App to the same group (Permissions
# MAGIC    API via the SDK; falls back to printing the CLI command). Nothing
# MAGIC    else: no WRITE VOLUME, no MANAGE (those stay with the data owner /
# MAGIC    steward), no secret scopes, no jobs — the app's service principal
# MAGIC    does the work on the user's behalf. View = run: there is no second
# MAGIC    tier. Blank `reviewer_group` = grants skipped, never a guessed group.
# MAGIC
# MAGIC Run-scoped tables/volumes the review app creates (`frd_documents_demo_*`,
# MAGIC `sttm_out_app/<suffix>`) are tagged by PREFIX — run this after a batch
# MAGIC of demo runs and the new tables pick up the same tags.
# MAGIC
# MAGIC Why a SEPARATE notebook, not a per-run step in 01/03/04: tagging needs
# MAGIC the `APPLY TAG` privilege and is a data-owner decision, not a pipeline
# MAGIC side-effect. A pipeline that fails because its service principal lacks
# MAGIC APPLY TAG would be the wrong failure; a pipeline that silently skips
# MAGIC tagging would hide a governance gap. So: one explicit, separately-run,
# MAGIC fail-loud step, by someone who holds the privilege — and `bundle run
# MAGIC frd_sttm_uc_governance` is the hand to run it with.
# MAGIC
# MAGIC Local mode (no Spark): prints the SQL it WOULD run, so the plan can be
# MAGIC reviewed offline. Nothing is executed outside Databricks.

# COMMAND ----------

import os

IS_DATABRICKS = "dbutils" in globals()


def _param(name: str, default: str) -> str:
    if IS_DATABRICKS:
        dbutils.widgets.text(name, default)
        return dbutils.widgets.get(name)
    return os.environ.get(name.upper(), default)


CATALOG = _param("catalog", "arjun_workspace")
SCHEMA = _param("schema", "sttm_agent")
AUDIT_VOLUME = _param("audit_volume", "sttm_audit")
# Governance facts that only a person can supply. Defaults are placeholders
# that READ as placeholders in Catalog Explorer / Collibra — never a
# plausible-looking guess.
DATA_OWNER = _param("data_owner", "UNASSIGNED — set data_owner")
DATA_STEWARD = _param("data_steward", "UNASSIGNED — set data_steward")
RETENTION_POLICY = _param("retention_policy", "undecided — see docs/AI_GOVERNANCE.md")
# "confidential" is the floor: FRDs/STTMs are client requirement documents and
# may NAME PHI-bearing fields (the schema carries phi_pii_notes; 04 renders a
# PHI flag column). They are not expected to CONTAIN member data, but the tag
# says "treat as if it might" until a data-handling review says otherwise.
SENSITIVITY = _param("sensitivity", "confidential")
# Access model: the ONE group that may run the agent == may read its data.
# Blank = skip the grants (no guessed group name). `app_name` = the deployed
# Databricks App to grant CAN USE on (blank = print the CLI command instead).
REVIEWER_GROUP = _param("reviewer_group", "").strip()
APP_NAME = _param("app_name", "").strip()

FQ = f"{CATALOG}.{SCHEMA}"

# Volumes the reviewer group may READ: inputs, past runs, the audit trail.
# demo_raw is deliberately absent (per-run staging the app's SP writes), as
# is any WRITE VOLUME / MANAGE — those stay with owner/steward.
READ_VOLUMES = ("frd_raw", "sttm_reference", "vdd_raw", "sttm_out_app")   # + AUDIT_VOLUME, added in access_statements

# --------------------------------------------------------------------------- #
# The asset inventory — what the agent reads and writes, and what each IS.
# Kept here (not scattered) so this notebook is also the human-readable
# answer to "what data does this agent touch?" (docs/AI_GOVERNANCE.md §3).
# --------------------------------------------------------------------------- #
COMMON_TAGS = {
    "agent": "frd_to_sttm",
    "agent_repo": "frd-to-sttm-agent-v2",
    "data_owner": DATA_OWNER,
    "data_steward": DATA_STEWARD,
    "sensitivity": SENSITIVITY,
    "retention_policy": RETENTION_POLICY,
    "managed_by": "notebooks/90_uc_governance.py",
}

VOLUMES = {
    # name: (comment, extra tags)
    "frd_raw": (
        "Source FRD documents (.docx/.pdf/.md/.txt) synced READ-ONLY from the client's "
        "SharePoint library by 00_sharepoint_sync / 00_sharepoint_fetch. Input to 01_frd_ingest. "
        "Never edited here; SharePoint is the system of record.",
        {"source_system": "sharepoint", "content_kind": "client_document",
         "data_class": "requirements_document", "phi_possible": "true", "direction": "input"}),
    "sttm_reference": (
        "Approved STTM workbooks synced READ-ONLY from SharePoint, plus corpus_index.json "
        "(FRD<->STTM pairing, content_sha256 per document) and sync_manifest.json (Graph item "
        "id/eTag/modified per synced file). Templates + eval references for 04_sttm_render.",
        {"source_system": "sharepoint", "content_kind": "client_document",
         "data_class": "source_to_target_mapping", "phi_possible": "true", "direction": "input"}),
    "vdd_raw": (
        "Vendor DATA DICTIONARIES (DICT_<name>.xlsx) synced READ-ONLY from SharePoint — the "
        "third input (2026-08-27). Named to sit beside frd_raw: two raw source documents, two "
        "raw volumes. One workbook per feed: a FILES sheet naming each delivered "
        "file, and one field sheet per file carrying every source column's name, position, "
        "type, length, null rule, description, allowed values and PHI flag. Held in its OWN "
        "volume because frdsttm.corpus globs sttm_reference for TEMPLATE workbooks and a "
        "dictionary is not one. Vendor-authored; describes PHI columns explicitly.",
        {"source_system": "sharepoint", "content_kind": "vendor_document",
         "data_class": "source_data_dictionary", "phi_possible": "true", "direction": "input"}),
    "sttm_out": (
        "Curated pipeline outputs of a hand-run frd_sttm_pipeline: extractions/ (LLM structured "
        "output + extraction_meta sidecars), contracts/ (validated feed contracts incl. "
        "_provenance + human resolutions), reports/, rendered/ (STTM workbooks).",
        {"content_kind": "llm_generated", "data_class": "source_to_target_mapping",
         "phi_possible": "true", "direction": "output", "human_review_required": "true"}),
    "sttm_out_app": (
        "Per-run artifact sets (<suffix>/...) of review-app-triggered runs; same layout as "
        "sttm_out plus run_manifest.json (who triggered, FRD sha256, job run id, outcome).",
        {"content_kind": "llm_generated", "data_class": "source_to_target_mapping",
         "phi_possible": "true", "direction": "output", "human_review_required": "true"}),
    "demo_raw": (
        "Per-run staging of the ONE FRD a review-app run ingests (<suffix>/<file>). "
        "Copy of a frd_raw document; insulation so 01 never ingests the whole corpus.",
        {"source_system": "sharepoint", "content_kind": "client_document",
         "data_class": "requirements_document", "phi_possible": "true", "direction": "input"}),
    AUDIT_VOLUME: (
        "Append-only audit trail of the review app: one JSON file per governed action "
        "(run started/finished, human resolution, re-render, workbook download = hand-off, "
        "corpus sync, upload) under events/. Query with read_files(..., format => 'json'). "
        "Never document content; actors, ids, hashes, statuses.",
        {"content_kind": "audit_log", "data_class": "audit_trail", "phi_possible": "false",
         "direction": "control", "audit_trail": "true", "append_only": "true"}),
}

# Curated table names and the run-scoped PREFIXES the review app creates.
TABLES = {
    "frd_documents": (
        "Parsed FRD text (markdown) per document, one row per file in frd_raw, written by "
        "01_frd_ingest (full refresh). content_sha256 = sha256 of the source bytes.",
        {"content_kind": "client_document", "data_class": "requirements_document",
         "phi_possible": "true", "direction": "input", "writer": "01_frd_ingest"}),
    "frd_contracts": (
        "Validated feed-level mapping contracts from 03_contract_build (grounding audit, "
        "ambiguity gating, status PASS/PASS_WITH_FLAGS/FAIL).",
        {"content_kind": "llm_generated", "data_class": "source_to_target_mapping",
         "phi_possible": "true", "direction": "output", "writer": "03_contract_build",
         "human_review_required": "true"}),
    "frd_sttm_runs": (
        "APPEND-ONLY render log from 04_sttm_render: one row per document per render — "
        "status, template decision, eval %, triggered_by, run_label, job_run_id, provider/model, "
        "system_prompt_sha256, token usage, frd_sha256, rendered_sha256, run_at.",
        {"content_kind": "audit_log", "data_class": "audit_trail", "phi_possible": "false",
         "direction": "control", "audit_trail": "true", "append_only": "true",
         "writer": "04_sttm_render"}),
}
TABLE_PREFIXES = {   # run-scoped copies the review app's Jobs-API runs create
    "frd_documents_": "frd_documents",
    "frd_contracts_": "frd_contracts",
    "frd_sttm_runs_": "frd_sttm_runs",
}
# Column-level tags: the one column that holds whole document text.
COLUMN_TAGS = {
    "frd_documents": {"content": {"contains_document_text": "true", "sensitivity": SENSITIVITY}},
}


def _q(s: str) -> str:
    return "'" + s.replace("'", "\\'") + "'"


def _tags_sql(tags: dict) -> str:
    return ", ".join(f"{_q(k)} = {_q(v)}" for k, v in tags.items())


def volume_statements(name: str) -> list[str]:
    comment, extra = VOLUMES[name]
    return [
        f"COMMENT ON VOLUME {FQ}.{name} IS {_q(comment)}",
        f"ALTER VOLUME {FQ}.{name} SET TAGS ({_tags_sql({**COMMON_TAGS, **extra})})",
    ]


def table_statements(table: str, base: str) -> list[str]:
    comment, extra = TABLES[base]
    suffix_note = "" if table == base else f" [run-scoped copy of {base}, created by a review-app run]"
    stmts = [
        f"COMMENT ON TABLE {FQ}.{table} IS {_q(comment + suffix_note)}",
        f"ALTER TABLE {FQ}.{table} SET TAGS ({_tags_sql({**COMMON_TAGS, **extra})})",
    ]
    for col, tags in COLUMN_TAGS.get(base, {}).items():
        stmts.append(f"ALTER TABLE {FQ}.{table} ALTER COLUMN {col} SET TAGS ({_tags_sql(tags)})")
    return stmts


def base_of(table: str) -> str | None:
    """The inventory entry a table name belongs to — exact, or by run-scoped
    prefix — or None for a table this agent does not own."""
    if table in TABLES:
        return table
    for prefix, base in TABLE_PREFIXES.items():
        if table.startswith(prefix):
            return base
    return None


def _principal(name: str) -> str:
    # Group names may contain spaces / hyphens / '@'; backticks are the UC quoting.
    return "`" + name.replace("`", "``") + "`"


def access_statements(group: str, volumes: set[str]) -> list[str]:
    """The reviewer group's grants: USE on catalog + schema, READ VOLUME on
    each read volume that exists. READ only — the app's service principal
    holds the write side. Empty group -> no statements."""
    if not group:
        return []
    g = _principal(group)
    stmts = [f"GRANT USE CATALOG ON CATALOG {CATALOG} TO {g}",
             f"GRANT USE SCHEMA ON SCHEMA {FQ} TO {g}"]
    for v in (*READ_VOLUMES, AUDIT_VOLUME):
        if v in volumes:
            stmts.append(f"GRANT READ VOLUME ON VOLUME {FQ}.{v} TO {g}")
    return stmts


def app_permission_cli(group: str, app_name: str) -> str:
    """The equivalent CLI for CAN USE on the App (what the SDK call below
    does) — printed when app_name is blank or the SDK call is unavailable."""
    return (f"databricks apps update-permissions {app_name or '<app-name>'} --json "
            f"'{{\"access_control_list\": [{{\"group_name\": \"{group}\", "
            f"\"permission_level\": \"CAN_USE\"}}]}}'")


def plan(tables: list[str] | None = None, volumes: set[str] | None = None,
         reviewer_group: str | None = None) -> list[str]:
    """Every statement, in order, for the objects that EXIST (`tables` /
    `volumes` as listed in the workspace; None = assume the curated set).
    Pure; tested offline. The audit volume is always created first, so it is
    always present to tag and to grant. `reviewer_group` (None = the widget)
    appends the access-model grants last."""
    group = REVIEWER_GROUP if reviewer_group is None else reviewer_group
    stmts = [f"CREATE VOLUME IF NOT EXISTS {FQ}.{AUDIT_VOLUME}"]
    present = set(volumes) if volumes is not None else set(VOLUMES)
    present.add(AUDIT_VOLUME)
    for name in VOLUMES:
        if name in present:
            stmts += volume_statements(name)
    for t in (tables if tables is not None else list(TABLES)):
        base = base_of(t)
        if base is not None:
            stmts += table_statements(t, base)
    stmts += access_statements(group, present)
    return stmts


def grant_app_can_use(group: str, app_name: str) -> str:
    """CAN USE on the Databricks App for the reviewer group, via the
    Permissions API (adds; never replaces the existing ACL). Returns a
    one-line outcome. Any failure returns the CLI to run by hand instead of
    raising — the SQL half of the access model must not be rolled back by a
    permissions-API hiccup, and the operator sees exactly what is left."""
    if not (group and app_name):
        return "app permission: skipped (set both reviewer_group and app_name) — " + app_permission_cli(group, app_name)
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.apps import AppAccessControlRequest, AppPermissionLevel

        WorkspaceClient().apps.update_permissions(
            app_name=app_name,
            access_control_list=[AppAccessControlRequest(group_name=group,
                                                         permission_level=AppPermissionLevel.CAN_USE)])
        return f"app permission: CAN USE on app {app_name!r} granted to group {group!r}"
    except Exception as exc:  # noqa: BLE001 — reported with the manual remedy, never fatal
        return (f"app permission: NOT applied ({type(exc).__name__}: {exc}) — run by hand: "
                + app_permission_cli(group, app_name))


# COMMAND ----------

# MAGIC %md
# MAGIC ## Execute (Databricks) / print the plan (local)

# COMMAND ----------

if IS_DATABRICKS:
    existing_tables = [r.tableName for r in spark.sql(f"SHOW TABLES IN {FQ}").collect()]
    existing_volumes = {r.volume_name for r in spark.sql(f"SHOW VOLUMES IN {FQ}").collect()}
    missing = [v for v in VOLUMES if v not in existing_volumes and v != AUDIT_VOLUME]
    if missing:
        # Not an error: sttm_out_app / demo_raw are created lazily by the first
        # run. Said out loud so nobody reads "applied" as "everything tagged".
        print(f"volumes not present yet (skipped, re-run after they exist): {missing}")
    statements = plan(existing_tables, existing_volumes)
    for sql in statements:
        spark.sql(sql)
    n_grants = len(access_statements(REVIEWER_GROUP, existing_volumes | {AUDIT_VOLUME}))
    print(f"governance: {len(statements)} statement(s) applied on {FQ} "
          f"({len([t for t in existing_tables if base_of(t)])} table(s), "
          f"{len([v for v in VOLUMES if v in existing_volumes or v == AUDIT_VOLUME])} volume(s), "
          f"{n_grants} grant(s) to {REVIEWER_GROUP or '<no reviewer_group: grants skipped>'})")
    print(grant_app_can_use(REVIEWER_GROUP, APP_NAME))
    display(spark.sql(f"SHOW VOLUMES IN {FQ}"))
else:
    print(f"# LOCAL MODE — plan for {FQ} (nothing executed):")
    for sql in plan():
        print(sql)
    if REVIEWER_GROUP:
        print("# app permission (not SQL): " + app_permission_cli(REVIEWER_GROUP, APP_NAME))
    else:
        print("# reviewer_group blank: access-model grants skipped (set REVIEWER_GROUP / the widget)")
