"""
frdsttm.models — the extraction schema (what Claude reads out of an FRD).

`FrdIngestionSpec` is rendered into the extraction prompt as a JSON schema —
the per-field descriptions ARE the extraction guidance — and the model's
answer is validated back into it (`extra="forbid"` is the drift guard).
No Databricks or Spark dependency.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Project(_Model):
    """Document-level project identification."""

    project_id: Optional[str] = Field(default=None, description="Project identifier exactly as stated, e.g. '1007412'. Null if not stated.")
    project_name: Optional[str] = Field(default=None, description="Project name as stated on the title page or header.")
    business_context_summary: Optional[str] = Field(default=None, description="1-3 sentence summary of the business goal, using the document's own terms.")


class AcdItem(_Model):
    """Row of the Assumptions, Constraints & Dependencies table."""

    name: Optional[str] = Field(default=None, description="The 'Name' cell.")
    description: Optional[str] = Field(default=None, description="The 'Description' cell.")
    acd_type: Optional[str] = Field(default=None, description="The stated type: Assumption, Constraint, or Dependency (normalize singular/plural to singular).")


class TableTarget(_Model):
    """Stage (silver) or Standard (gold) layer target as stated in the document."""

    catalog: Optional[str] = Field(default=None, description="Catalog/workspace if stated (e.g. 'PR_DLK' for the stage layer, 'PR_STD' for the standard layer). Null otherwise.")
    schema_: Optional[str] = Field(default=None, alias="schema", description="Schema name (e.g. 'stg_mbr' or 'stg_sdh' for the stage layer; 'MBR', 'sdh', or 'care' for the standard layer).")
    tables: List[str] = Field(default_factory=list, description="All table names for this feed at this layer, verbatim.")
    load_strategy: Optional[str] = Field(default=None, description="As stated, e.g. 'Truncate and Load' for the stage layer or 'Append' for the standard layer.")


class Feed(_Model):
    """One distinct source file/feed the document requires to be ingested.

    A feed is one source file pattern (or one family of patterns sharing a
    layout) with its own target tables. If the document describes three files
    with three target tables, emit three feeds.
    """

    feed_name: Optional[str] = Field(default=None, description="Short name for the feed, preferring the document's own naming (e.g. a target table name like 'cv_community_risk', or 'CVX Coverage Inbound Files').")
    source_system: Optional[str] = Field(default=None, description="The vendor/source that produces the file, e.g. 'CVX', 'Civic Vantage (CV)', a state system. Null if not stated.")
    file_name_patterns: List[str] = Field(default_factory=list, description="Every file name pattern stated for this feed, verbatim including date placeholders and wildcards (e.g. 'demographic_extract_CCYY_MM.csv', 'YYYYMMDD_2044_1540_CoverageReport_*_P_I_1_T.txt').")
    file_format: Optional[str] = Field(default=None, description="File type as stated: csv, psv, txt, fixed-width, etc. Null if not stated.")
    delimiter: Optional[str] = Field(default=None, description="Field delimiter if stated (e.g. ',', '|'). Null if not stated.")
    record_segments: List[str] = Field(default_factory=list, description="Record segments if the file is multi-record (e.g. ['Header','Detail','Trailer']). Empty array if the document does not describe segments.")
    frequency: Optional[str] = Field(default=None, description="Delivery frequency as stated, e.g. 'Weekly Monday 8 PM', 'Monthly', 'Yearly twice (Jan-Feb and Aug-Sep)'.")
    load_windows_sla: List[str] = Field(default_factory=list, description="Stated load-timing/SLA rules, close to verbatim (e.g. 'File should be loaded before business hours 8 AM', 'received between 11-15th of every month before 5 PM EST').")
    lobs: List[str] = Field(default_factory=list, description="Lines of business in scope for this feed, as stated: names like 'OHDS', bare codes like '0100', or Region/LOB code pairs listed in metadata tables (e.g. 'REG#1 0100') - capture every code listed.")
    domain: Optional[str] = Field(default=None, description="Data domain if stated, e.g. 'Member'.")
    sub_domain: Optional[str] = Field(default=None, description="Sub-domain if stated, e.g. 'Third Party Liability', 'Social Determinants of Health'.")
    landing_location: Optional[str] = Field(default=None, description="ADLS/landing path verbatim if stated, e.g. 'mftlanding/inbound/member/coverage/cvx'. Null if not stated.")
    stage_target: Optional[TableTarget] = Field(default=None, description="Stage (silver) layer target as stated.")
    standard_target: Optional[TableTarget] = Field(default=None, description="Standard (gold) layer target as stated.")
    validation_rules: List[str] = Field(default_factory=list, description="Stated processing/validation rules for this feed, one per rule, close to verbatim. Check ALL sections, especially 'Data Ingestion Requirements' subsections ('Data Quality', 'Technical Metadata', 'Administrative Metadata'): rules are often written as prose inside a single Description cell that covers several files at once — split them and attribute each rule to the feed whose file or table it names, even when nearby template rows read 'NA' or are blank. Examples: 'Process shall fail when file layout is not as per source dictionary', 'If the ZIP_CODE column is NULL, reject the record to the reject table', 'Member ID validation should be performed against CoreMember for existence', 'LOB_ID field should be mapped to Payer Area field'.")
    recycle_rule: Optional[str] = Field(default=None, description="Any stated recycle/retry handling for unmatched or rejected records, including a dedicated recycle table, a recycle flag, or a day window (e.g. 'Invalid records moved to Recycle table, retained 15 days', 'Recycle Flag enabled for 7 days: check SUBS_ID against PR_STD.COREMEMBER.CM_SUBS_MASTER for GRP_CK = 47'). Capture the window and any reference table/condition verbatim. Often stated in prose inside Data Quality sections rather than in a labeled row — a template row saying 'NA' does not override a prose statement that names this feed's file. Null only if the document truly states none for this feed.")
    history_backfill: Optional[str] = Field(default=None, description="Stated historical/backfill requirements, e.g. 'History files up to 3 years should be loaded; one-time movement from O drive to ADLS'. Null if none.")
    archive_retention: Optional[str] = Field(default=None, description="Stated archive/retention schedule, e.g. 'Files: 7 Years; Data: Corporate retention schedule'. Null if none.")
    phi_pii_notes: Optional[str] = Field(default=None, description="Any stated sensitivity/masking notes for this feed's data. Null if none.")
    sttm_reference: Optional[str] = Field(default=None, description="How the document refers to the accompanying mapping/STTM document for this feed (e.g. 'Refer CVX STTM'). Copy the cited cell VALUE(S) verbatim and nothing else: never include the row label they sit under (e.g. 'Link to STTM'), never add words. If more than one cell cites it, join the values with '; '. Null if none.")
    requirement_ids: List[str] = Field(default_factory=list, description="Requirement identifiers in the document that this feed's facts came from, verbatim (e.g. 'SRQ226433', 'MDST231070', 'SIR226434', 'NFR226417'). Provenance for traceability.")


class FrdIngestionSpec(_Model):
    """Ingestion specification extracted from a Functional Requirements Document (FRD) for onboarding vendor/state source files into the data lake (source-to-stage-to-standard). Extract ONLY facts stated in the document. Use null (or an empty array) for anything the document does not state. Never invent file names, table names, schemas, schedules, or rules. Preserve identifiers, file name patterns, paths, and table names verbatim, including placeholders like YYYYMMDD."""

    project: Optional[Project] = Field(default=None, description="Document-level project identification.")
    in_scope: List[str] = Field(default_factory=list, description="Scope items listed under 'In Scope', one string per substantive item, close to verbatim. Skip scaffolding lead-ins that carry no content (e.g. 'Below LOB's is in Scope.', 'Below are the file name & description...').")
    out_of_scope: List[str] = Field(default_factory=list, description="Items listed under 'Out of Scope', one string per item.")
    assumptions_constraints_dependencies: List[AcdItem] = Field(default_factory=list, description="Rows of the Assumptions, Constraints & Dependencies table.")
    feeds: List[Feed] = Field(default_factory=list, description="One entry per distinct source file/feed the document requires to be ingested. A feed is one source file pattern (or one family of patterns sharing a layout) with its own target tables. If the document describes three files with three target tables, emit three feeds.")
    system_interfaces: List[str] = Field(default_factory=list, description="Stated system interface requirements not tied to a single feed (e.g. connectivity between the data lake and an on-prem application), one summary string per interface with its requirement id if stated.")
    open_items: List[str] = Field(default_factory=list, description="Anything the document marks as TBD, pending, or to-be-provided (e.g. 'SR# for SFG Template: TBD', 'History files start date to be provided by Source team').")
