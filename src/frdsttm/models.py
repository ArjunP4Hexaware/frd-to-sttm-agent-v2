# Library module, imported as `frdsttm.<name>`. NOT a notebook.
#
# Deliberately carries no Databricks notebook magic header (removed
# 2026-08-24). `databricks sync` classifies any .py whose FIRST line is
# the notebook marker as a NOTEBOOK object, stored without a .py
# extension -- so with that header this module did not exist to `import`
# on a deployed Databricks App, and app.py died at startup with
# ModuleNotFoundError. Do not re-add the marker to this file.
# MAGIC %md
# MAGIC # Shared pydantic models — `FrdIngestionSpec` and children
# MAGIC
# MAGIC Single source of truth for the extraction schema, mirroring
# MAGIC `schema/sttm_extraction_schema.json` 1:1. Consumed by:
# MAGIC
# MAGIC - `02_extract` — passes `FrdIngestionSpec` to
# MAGIC   `client.messages.parse(output_format=...)` for server-side schema
# MAGIC   enforcement during LLM extraction. Per-field `Field(description=...)`
# MAGIC   values ride to the model via the JSON schema the SDK builds from this
# MAGIC   Pydantic model, so the guidance stays with the schema, not the prompt.
# MAGIC - `03_contract_build` — calls `FrdIngestionSpec.model_validate(...)` on
# MAGIC   the extraction JSON as the first gate of the deterministic pipeline.
# MAGIC
# MAGIC `extra="forbid"` on the base model is the pipeline's schema-drift guard:
# MAGIC any field the LLM emits that isn't declared here fails the run loudly.
# MAGIC
# MAGIC No Databricks or Spark dependencies — safe to `%run ./_models` from a
# MAGIC notebook or `from _models import FrdIngestionSpec` from a local test.

# COMMAND ----------

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Project(_Model):
    """Document-level project identification."""

    project_id: Optional[str] = Field(
        default=None,
        description="Project identifier exactly as stated, e.g. '1007412'. Null if not stated.",
    )
    project_name: Optional[str] = Field(
        default=None,
        description="Project name as stated on the title page or header.",
    )
    business_context_summary: Optional[str] = Field(
        default=None,
        description="1-3 sentence summary of the business goal, using the document's own terms.",
    )


class AcdItem(_Model):
    """Row of the Assumptions, Constraints & Dependencies table."""

    name: Optional[str] = Field(default=None, description="The 'Name' cell.")
    description: Optional[str] = Field(default=None, description="The 'Description' cell.")
    acd_type: Optional[str] = Field(
        default=None,
        description=(
            "The stated type: Assumption, Constraint, or Dependency "
            "(normalize singular/plural to singular)."
        ),
    )


class TableTarget(_Model):
    """Stage (silver) or Standard (gold) layer target as stated in the document."""

    catalog: Optional[str] = Field(
        default=None,
        description=(
            "Catalog/workspace if stated (e.g. 'PR_DLK' for the stage layer, "
            "'PR_STD' for the standard layer). Null otherwise."
        ),
    )
    schema_: Optional[str] = Field(
        default=None,
        alias="schema",
        description=(
            "Schema name (e.g. 'stg_mbr' or 'stg_sdh' for the stage layer; "
            "'MBR', 'sdh', or 'care' for the standard layer)."
        ),
    )
    tables: List[str] = Field(
        default_factory=list,
        description="All table names for this feed at this layer, verbatim.",
    )
    load_strategy: Optional[str] = Field(
        default=None,
        description=(
            "As stated, e.g. 'Truncate and Load' for the stage layer or "
            "'Append' for the standard layer."
        ),
    )


class Feed(_Model):
    """One distinct source file/feed the document requires to be ingested.

    A feed is one source file pattern (or one family of patterns sharing a
    layout) with its own target tables. If the document describes three files
    with three target tables, emit three feeds.
    """

    feed_name: Optional[str] = Field(
        default=None,
        description=(
            "Short name for the feed, preferring the document's own naming "
            "(e.g. a target table name like 'cv_community_risk', or "
            "'CVX Coverage Inbound Files')."
        ),
    )
    source_system: Optional[str] = Field(
        default=None,
        description=(
            "The vendor/source that produces the file, e.g. 'CVX', "
            "'Civic Vantage (CV)', a state system. Null if not stated."
        ),
    )
    file_name_patterns: List[str] = Field(
        default_factory=list,
        description=(
            "Every file name pattern stated for this feed, verbatim including "
            "date placeholders and wildcards (e.g. "
            "'demographic_extract_CCYY_MM.csv', "
            "'YYYYMMDD_2044_1540_CoverageReport_*_P_I_1_T.txt')."
        ),
    )
    file_format: Optional[str] = Field(
        default=None,
        description="File type as stated: csv, psv, txt, fixed-width, etc. Null if not stated.",
    )
    delimiter: Optional[str] = Field(
        default=None,
        description="Field delimiter if stated (e.g. ',', '|'). Null if not stated.",
    )
    record_segments: List[str] = Field(
        default_factory=list,
        description=(
            "Record segments if the file is multi-record "
            "(e.g. ['Header','Detail','Trailer']). Empty array if the document "
            "does not describe segments."
        ),
    )
    frequency: Optional[str] = Field(
        default=None,
        description=(
            "Delivery frequency as stated, e.g. 'Weekly Monday 8 PM', "
            "'Monthly', 'Yearly twice (Jan-Feb and Aug-Sep)'."
        ),
    )
    load_windows_sla: List[str] = Field(
        default_factory=list,
        description=(
            "Stated load-timing/SLA rules, close to verbatim (e.g. 'File "
            "should be loaded before business hours 8 AM', 'received between "
            "11-15th of every month before 5 PM EST')."
        ),
    )
    lobs: List[str] = Field(
        default_factory=list,
        description=(
            "Lines of business in scope for this feed, as stated: names like "
            "'OHDS', bare codes like '0100', or Region/LOB code pairs listed "
            "in metadata tables (e.g. 'REG#1 0100') - capture every code listed."
        ),
    )
    domain: Optional[str] = Field(
        default=None,
        description="Data domain if stated, e.g. 'Member'.",
    )
    sub_domain: Optional[str] = Field(
        default=None,
        description=(
            "Sub-domain if stated, e.g. 'Third Party Liability', "
            "'Social Determinants of Health'."
        ),
    )
    landing_location: Optional[str] = Field(
        default=None,
        description=(
            "ADLS/landing path verbatim if stated, e.g. "
            "'mftlanding/inbound/member/coverage/cvx'. Null if not stated."
        ),
    )
    stage_target: Optional[TableTarget] = Field(
        default=None,
        description="Stage (silver) layer target as stated.",
    )
    standard_target: Optional[TableTarget] = Field(
        default=None,
        description="Standard (gold) layer target as stated.",
    )
    validation_rules: List[str] = Field(
        default_factory=list,
        description=(
            "Stated processing/validation rules for this feed, one per rule, "
            "close to verbatim. Check ALL sections, especially 'Data Ingestion "
            "Requirements' subsections ('Data Quality', 'Technical Metadata', "
            "'Administrative Metadata'): rules are often written as prose "
            "inside a single Description cell that covers several files at "
            "once — split them and attribute each rule to the feed whose file "
            "or table it names, even when nearby template rows read 'NA' or "
            "are blank. Examples: 'Process shall fail when file layout is not "
            "as per source dictionary', 'If the ZIP_CODE column is NULL, "
            "reject the record to the reject table', 'Member ID validation "
            "should be performed against CoreMember for existence', 'LOB_ID field "
            "should be mapped to Payer Area field'."
        ),
    )
    recycle_rule: Optional[str] = Field(
        default=None,
        description=(
            "Any stated recycle/retry handling for unmatched or rejected "
            "records, including a dedicated recycle table, a recycle flag, or "
            "a day window (e.g. 'Invalid records moved to Recycle table, "
            "retained 15 days', 'Recycle Flag enabled for 7 days: check "
            "SUBS_ID against PR_STD.COREMEMBER.CM_SUBS_MASTER for GRP_CK = 47'). "
            "Capture the window and any reference table/condition verbatim. "
            "Often stated in prose inside Data Quality sections rather than "
            "in a labeled row — a template row saying 'NA' does not override "
            "a prose statement that names this feed's file. Null only if the "
            "document truly states none for this feed."
        ),
    )
    history_backfill: Optional[str] = Field(
        default=None,
        description=(
            "Stated historical/backfill requirements, e.g. 'History files up "
            "to 3 years should be loaded; one-time movement from O drive to "
            "ADLS'. Null if none."
        ),
    )
    archive_retention: Optional[str] = Field(
        default=None,
        description=(
            "Stated archive/retention schedule, e.g. 'Files: 7 Years; Data: "
            "Corporate retention schedule'. Null if none."
        ),
    )
    phi_pii_notes: Optional[str] = Field(
        default=None,
        description="Any stated sensitivity/masking notes for this feed's data. Null if none.",
    )
    sttm_reference: Optional[str] = Field(
        default=None,
        description=(
            "How the document refers to the accompanying mapping/STTM document "
            "for this feed (e.g. 'Refer CVX STTM'). Copy the cited cell VALUE(S) "
            "verbatim and nothing else: never include the row label they sit "
            "under (e.g. 'Link to STTM'), never add words. If more than one cell "
            "cites it, join the values with '; '. Null if none."
        ),
    )
    requirement_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Requirement identifiers in the document that this feed's facts "
            "came from, verbatim (e.g. 'SRQ226433', 'MDST231070', 'SIR226434', "
            "'NFR226417'). Provenance for traceability."
        ),
    )


class FrdIngestionSpec(_Model):
    """Ingestion specification extracted from a Functional Requirements Document (FRD) for onboarding vendor/state source files into the data lake (source-to-stage-to-standard). Extract ONLY facts stated in the document. Use null (or an empty array) for anything the document does not state. Never invent file names, table names, schemas, schedules, or rules. Preserve identifiers, file name patterns, paths, and table names verbatim, including placeholders like YYYYMMDD."""

    project: Optional[Project] = Field(
        default=None,
        description="Document-level project identification.",
    )
    in_scope: List[str] = Field(
        default_factory=list,
        description=(
            "Scope items listed under 'In Scope', one string per substantive "
            "item, close to verbatim. Skip scaffolding lead-ins that carry no "
            "content (e.g. 'Below LOB's is in Scope.', 'Below are the file "
            "name & description...')."
        ),
    )
    out_of_scope: List[str] = Field(
        default_factory=list,
        description="Items listed under 'Out of Scope', one string per item.",
    )
    assumptions_constraints_dependencies: List[AcdItem] = Field(
        default_factory=list,
        description="Rows of the Assumptions, Constraints & Dependencies table.",
    )
    feeds: List[Feed] = Field(
        default_factory=list,
        description=(
            "One entry per distinct source file/feed the document requires to "
            "be ingested. A feed is one source file pattern (or one family of "
            "patterns sharing a layout) with its own target tables. If the "
            "document describes three files with three target tables, emit "
            "three feeds."
        ),
    )
    system_interfaces: List[str] = Field(
        default_factory=list,
        description=(
            "Stated system interface requirements not tied to a single feed "
            "(e.g. connectivity between the data lake and an on-prem "
            "application), one summary string per interface with its "
            "requirement id if stated."
        ),
    )
    open_items: List[str] = Field(
        default_factory=list,
        description=(
            "Anything the document marks as TBD, pending, or to-be-provided "
            "(e.g. 'SR# for SFG Template: TBD', 'History files start date to "
            "be provided by Source team')."
        ),
    )


# --------------------------------------------------------------------------- #
# Phase 4/5 provenance models — gated ambiguities + human resolutions.
#
# Not part of FrdIngestionSpec's tree, and never passed to
# `client.messages.parse(output_format=...)` in 02_extract: these describe
# `_provenance.ambiguities` / `_provenance.grounding.advisory_flagged` /
# `_provenance.human_resolutions`, which 03_contract_build.py and
# 04_sttm_render.py construct deterministically, not something the LLM
# emits. Kept here anyway (rather than a separate module) because both
# scripts and both review apps need one shared definition of this shape,
# same rationale as FrdIngestionSpec above.
# --------------------------------------------------------------------------- #
class AmbiguityContext(_Model):
    """Structured context captured at the point an ambiguity is constructed.
    Which fields are populated depends on `GatedAmbiguity.kind` — every field
    here is genuinely available at at least one construction site in
    03_contract_build.py; none is fabricated to fill out the shape. See that
    module's attribution_check() / enrich() / grounding_audit() for exactly
    which fields each kind sets."""

    feed_names: List[str] = Field(default_factory=list, description="attribution: feeds the duplicated rule was found on, in the same order as feed_indices.")
    feed_indices: List[int] = Field(default_factory=list, description="attribution: indices into contract['feeds'] for feed_names, same order.")
    rule: Optional[str] = Field(default=None, description="attribution: the full (untruncated) duplicated rule text -- `text` embeds only a 120-char truncation for display, this is what write-back matching uses.")
    field: Optional[str] = Field(default=None, description="disagreement/advisory_grounding: the dotted or bracketed field path this ambiguity concerns.")
    agent_value: Optional[str] = Field(default=None, description="disagreement: the value the LLM extracted.")
    content_value: Optional[str] = Field(default=None, description="disagreement: the value the deterministic regex found in the source content.")
    path: Optional[str] = Field(default=None, description="advisory_grounding: the full field path as shown in the report, e.g. 'feeds[0](CVX Coverage Inbound Files).phi_pii_notes'.")
    feed_index: Optional[int] = Field(default=None, description="advisory_grounding: index into contract['feeds'] when the flagged field is feed-scoped; null for top-level paths (in_scope, acd(name), etc).")
    original_value: Optional[str] = Field(default=None, description="advisory_grounding: the exact flagged string, captured directly rather than re-parsed from `text` later.")


class GatedAmbiguity(_Model):
    """One gated ambiguity or advisory flag, emitted directly by
    03_contract_build.py. Populates `_provenance.ambiguities` (kinds
    'attribution', 'disagreement') and `_provenance.grounding.
    advisory_flagged` (kind 'advisory_grounding').

    `id` is a stable hash of kind+text+context (see 03_contract_build.py's
    `_ambiguity_id()`) — unchanged across a re-run of the pipeline against
    unchanged inputs, so it survives as the human_resolutions join key even
    when unrelated parts of the document change.

    There is deliberately no 'other' kind: every construction site in
    03_contract_build.py declares its own kind at the point the ambiguity is
    built (kind is never inferred after the fact from the text), so no code
    path can produce a shape that doesn't fit one of the three literals
    below — a pipeline output that somehow did would fail pydantic
    validation loudly rather than being silently bucketed."""

    id: str
    kind: Literal["attribution", "disagreement", "advisory_grounding"]
    text: str = Field(description="Human-readable string, for display in the review apps' UI — unchanged from what 03_contract_build.py produced before this schema existed.")
    has_candidates: bool
    candidates: List[str] = Field(default_factory=list, description="[] when has_candidates is false.")
    context: AmbiguityContext = Field(default_factory=AmbiguityContext)


class HumanResolution(_Model):
    """One reviewer decision, written by either review app into
    `_provenance.human_resolutions` and consumed by
    `04_sttm_render.py::apply_human_resolutions()`.

    `resolution_type` is the authoritative signal of reviewer intent — not
    inferred from which of `chosen_candidate`/`rationale` happens to be
    populated. 'free_text' on a candidate-having ambiguity (has_candidates
    True) is a legal, representable resolution_type -- e.g. a reviewer who
    types a rationale without picking a candidate -- but apply_human_
    resolutions() treats it as unresolved/still-gated, never applies it
    structurally, and records why. `rationale` is never applied structurally
    for a candidate-having ambiguity; for a candidate-free one
    (advisory_grounding) it IS the resolution, exactly as today's
    override_value is applied, since there is nothing else it could be.
    """

    ambiguity_id: str
    kind: Literal["attribution", "disagreement", "advisory_grounding"]
    resolution_type: Literal["candidate_pick", "free_text", "none_of_these"]
    chosen_candidate: Optional[str] = Field(default=None, description="Set iff resolution_type == 'candidate_pick'.")
    rationale: Optional[str] = Field(default=None, description="Optional annotation; the actual resolution value only when has_candidates is false.")
    candidates_snapshot: List[str] = Field(default_factory=list, description="Copy of the ambiguity's candidates at the moment of resolution, for staleness detection if the contract's candidates later change.")
    resolved_at: str
    resolved_by: Optional[str] = None
