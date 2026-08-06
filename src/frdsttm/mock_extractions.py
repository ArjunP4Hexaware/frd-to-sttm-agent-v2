# Databricks notebook source
# MAGIC %md
# MAGIC # Hand-authored mock extractions for zero-cost local pipeline testing
# MAGIC
# MAGIC Used only by `02_extract.py`'s local-mode `STTM_MOCK_EXTRACTION=1` path
# MAGIC (see that notebook's extraction-loop cell) to skip the real Anthropic API
# MAGIC call entirely. **Not a quality benchmark** — these two `FrdIngestionSpec`
# MAGIC objects were hand-built by reading the real parsed markdown for the two
# MAGIC `local_dev_fixtures/frd_raw/` sample FRDs (Medicare Expansion/MIDS Socially
# MAGIC Determined, Payment Integrity/CAQH TPL 1005034) and copying real facts —
# MAGIC file patterns, schemas, table names, requirement ids, business rules —
# MAGIC verbatim or near-verbatim from that content. Real extraction-quality
# MAGIC numbers (grounding %, eval % against reference workbooks) live in
# MAGIC `docs/STANDUP_NOTES.md`; this module exists purely to exercise
# MAGIC `03_contract_build.py` and the review app's plumbing without spending API
# MAGIC calls.
# MAGIC
# MAGIC Each spec deliberately reproduces a genuine ambiguity from its source FRD
# MAGIC rather than a clean happy path:
# MAGIC - **MIDS**: the FRD's own Data Quality section states the ZIP_CODE-null
# MAGIC   rule, MEMBER_ID-null rule, and 7-day recycle rule once, then lists all
# MAGIC   three feed files as the target ("...from the below files") without
# MAGIC   saying which rule applies to which file — the exact vague-attribution
# MAGIC   pattern `docs/STANDUP_NOTES.md` describes for this document. Reproducing
# MAGIC   that same rule text identically across all three mock feeds makes
# MAGIC   `03_contract_build.py`'s `attribution_check()` gate all three as
# MAGIC   unconfirmed, same as the real pipeline run did.
# MAGIC - **CAQH**: this FRD only describes one feed, so the cross-feed
# MAGIC   attribution gate can't fire (`attribution_check()` returns early below
# MAGIC   2 feeds) — a structurally different gate is used instead:
# MAGIC   `phi_pii_notes` is phrased as a loose paraphrase (mirroring a plausible
# MAGIC   LLM summarization drift the system prompt explicitly warns against)
# MAGIC   rather than the source's terse "Refer CAQH STTM" / "Medicaid Id"
# MAGIC   wording, which trips `grounding_audit()`'s advisory token-overlap
# MAGIC   check.
# MAGIC - **CAQH** also deliberately leaves `lobs` empty even though the source
# MAGIC   content has fourteen literal `REG#<n> <code>` pairs, to exercise
# MAGIC   `enrich()`'s real regex auto-population path (not an ambiguity, just a
# MAGIC   demonstration that the deterministic-enrichment code fires on genuine
# MAGIC   content, unprompted).

# COMMAND ----------

from __future__ import annotations

import re

from frdsttm.models import AcdItem, Feed, FrdIngestionSpec, Project, TableTarget

_MIDS_RULE_ZIP = (
    "If the ZIP_CODE column is NULL, then we are rejecting the record and "
    "moving it to the reject table from the below files."
)
_MIDS_RULE_MEMBER = (
    "If the MEMBER_ID column is NULL, then we are rejecting the record and "
    "moving it to the reject table from the below file."
)
_MIDS_RECYCLE = (
    "Recycle Flag enabled for 7 days: check SUBS_ID against "
    "PR_STD.COREMEMBER.CM_SUBS_MASTER for GRP_CK = 47; if available, process it, "
    "else load to the Recycle table with Recycle Flag enabled for 7 days."
)


def _mids_spec() -> FrdIngestionSpec:
    common_rules = [_MIDS_RULE_ZIP, _MIDS_RULE_MEMBER]
    return FrdIngestionSpec(
        project=Project(
            project_id="1007412",
            project_name="Medicare Expansion-OHDS-Social Factors",
            business_context_summary=(
                "Ingest Community Demographic, Community Risk, and Individual "
                "Risk files from Civic Vantage (CV) into the Lakehouse "
                "for community and member risk analytics reporting."
            ),
        ),
        in_scope=[
            "Transfer of OH Risk files (Community Demographic, Community Risk "
            "& Individual Risk) from Civic Vantage to Lakehouse.",
            "Inclusion of requested Line of Business (LOBs) in the scope.",
        ],
        out_of_scope=[
            "Any transformation or restricting of data beyond what is required "
            "for migration is not included.",
            "Enhancements to data quality or data cleansing beyond the "
            "accuracy required for migration are not included.",
        ],
        assumptions_constraints_dependencies=[
            AcdItem(
                name="Data Accessibility",
                description=(
                    "All necessary data needed for Risk report as part of "
                    "this FRD are accessible to the reporting team"
                ),
                acd_type="Assumption",
            ),
            AcdItem(
                name="Data Governance",
                description="Data transformation rules must comply with existing data governance policies.",
                acd_type="Constraint",
            ),
        ],
        feeds=[
            Feed(
                feed_name="cv_community_demographic_risk",
                source_system="Civic Vantage (CV)",
                file_name_patterns=["demographic_extract_CCYY_MM.csv"],
                file_format="csv",
                frequency="Yearly twice (January/February, August/September)",
                lobs=["OHDS"],
                domain="Social Determinants of Health",
                sub_domain="Public",
                landing_location=r"mftlanding\inbound\sdh\public\civic_vantage",
                stage_target=TableTarget(schema="stg_sdh", tables=["cv_community_demographic_risk"], load_strategy="Truncate and Load"),
                standard_target=TableTarget(schema="sdh", tables=["cv_community_demographic_risk"], load_strategy="Append"),
                validation_rules=list(common_rules),
                recycle_rule=_MIDS_RECYCLE,
                sttm_reference="STTM-Medicare Expansion-OHDS-Social Factors.xlsx",
                requirement_ids=["MDST235207"],
            ),
            Feed(
                feed_name="cv_community_risk",
                source_system="Civic Vantage (CV)",
                file_name_patterns=["community_metrics_CCYY_MM.csv"],
                file_format="csv",
                frequency="Yearly twice (January/February, August/September)",
                lobs=["OHDS"],
                domain="Social Determinants of Health",
                sub_domain="Public",
                landing_location=r"mftlanding\inbound\sdh\public\civic_vantage",
                stage_target=TableTarget(schema="stg_sdh", tables=["cv_community_risk"], load_strategy="Truncate and Load"),
                standard_target=TableTarget(schema="sdh", tables=["cv_community_risk"], load_strategy="Append"),
                validation_rules=list(common_rules),
                recycle_rule=_MIDS_RECYCLE,
                sttm_reference="STTM-Medicare Expansion-OHDS-Social Factors.xlsx",
                requirement_ids=["MDST235207"],
            ),
            Feed(
                feed_name="cv_individual_risk",
                source_system="Civic Vantage (CV)",
                file_name_patterns=["cv_ind_risk_data_package_mrdn_OH_CCYYMMDD_HHMM.psv"],
                file_format="psv",
                frequency="Monthly, received between the 11th-15th before 5 PM EST",
                lobs=["OHDS"],
                domain="Care Management",
                sub_domain="Social Determinants of Health",
                landing_location=r"mftlanding\inbound\care_management\sdh\civic_vantage",
                stage_target=TableTarget(schema="stg_care", tables=["cv_individual_risk"], load_strategy="Truncate and Load"),
                standard_target=TableTarget(schema="care", tables=["cv_individual_risk"], load_strategy="Append"),
                validation_rules=list(common_rules),
                recycle_rule=_MIDS_RECYCLE,
                sttm_reference="STTM-Medicare Expansion-OHDS-Social Factors.xlsx",
                requirement_ids=["MDST235207"],
            ),
        ],
        system_interfaces=[],
        open_items=["SR# for SFG Template: TBD", "Data Catalog Entry: TBD"],
    )


def _caqh_spec() -> FrdIngestionSpec:
    return FrdIngestionSpec(
        project=Project(
            project_id="1005034",
            project_name="Payment Integrity - TPL Inbound files ingestion to DL",
            business_context_summary=(
                "Ingest CAQH Third Party Liability (TPL) coordination-of-benefits "
                "files into the data lake to give business users traceability "
                "back to source files and lineage, replacing manual retrieval "
                "from archived folders."
            ),
        ),
        in_scope=[
            "CAQH TPL Inbound Files ingestion into DL",
            "CAQH (Council for Affordable Quality Healthcare) Vendor",
        ],
        out_of_scope=[
            "Acknowledge Files",
            "State/ CAQH / Syrtis Error Files",
            "TPL Outbound Files",
            "Retro TPL Files",
            "HMS/ DHP Recovery Claims",
        ],
        assumptions_constraints_dependencies=[
            AcdItem(
                name="Connectivity",
                description="SFG connectivity between vendors and ACFC are already established.",
                acd_type="Assumption",
            ),
            AcdItem(
                name="Masking",
                description=(
                    "Files should be masked prior to performing Dev & QA testing. "
                    "ETA for masking is 2 weeks."
                ),
                acd_type="Dependency",
            ),
        ],
        feeds=[
            Feed(
                feed_name="CAQH TPL Inbound Files",
                source_system="CAQH (Council for Affordable Quality Healthcare)",
                file_name_patterns=[
                    "YYYYMMDD_1022_1340_COBReport_*_P_I_1_T.txt",
                    "YYYYMMDD_1022_1341_COBReport_*_P_I_1_T.txt",
                    "YYYYMMDD_1022_1342_COBReport_*_P_I_1_T.txt",
                ],
                file_format="txt",
                delimiter="|",
                record_segments=["Header", "Detail", "Trailer"],
                frequency="Weekly Monday 8 PM",
                load_windows_sla=[
                    "File should be loaded before business hours 8 AM.",
                    "Files received during business hours should be loaded after 5 PM.",
                ],
                lobs=[],  # deliberately empty -- see module docstring: exercises enrich()'s regex auto-population
                domain="Member",
                sub_domain="Third Party Liability",
                landing_location="mftlanding/inbound/member/tpl/caqh",
                stage_target=TableTarget(
                    catalog="pr_dlk",
                    schema="stg_mbr",
                    tables=["EXT_TPL_CAQH_HDR", "EXT_TPL_CAQH_DTL", "EXT_TPL_CAQH_TRL", "EXT_TPL_CAQH_DTL_RECYCLE"],
                    load_strategy="Truncate and Load",
                ),
                standard_target=TableTarget(load_strategy="Append"),
                validation_rules=[
                    "Process shall fail when file layout is not as per source dictionary.",
                    "LOB_ID field should be mapped to Payer Area field.",
                    "Member ID validation should be performed against Facets for existence.",
                    "Duplicate file name check shall be turned off as vendor may re-send the corrected file with same file name.",
                ],
                recycle_rule="Invalid member records are moved to the Recycle table, recycled up to 15 days.",
                history_backfill=(
                    "History files up to 3 years should be loaded into Data Lake; "
                    "start date and file location to be provided by Source team; "
                    "one-time movement from O drive to ADLS."
                ),
                archive_retention="Files: 7 Years; Data: Corporate retention schedule",
                # Deliberately loose paraphrase of the source's terse "PII Fields: Refer
                # CAQH STTM" / "Critical Data Elements: Medicaid Id" -- see module docstring.
                phi_pii_notes=(
                    "Medicaid identifier and coverage details are considered "
                    "protected member information per the STTM reference"
                ),
                sttm_reference="STTM_STG_STD_PaymentIntegrity_TPL_CAQH_To_DL_Mapping_Document_1005034__.xlsx",
                requirement_ids=[
                    "SRQ226433", "BR226408", "SIR226434", "MDST231070",
                    "MDA231068", "MDT231141", "MDDQ231071", "MDV231142",
                ],
            ),
        ],
        system_interfaces=[
            "Connectivity with on Prem TPL Database: TPL team developed "
            "Application will connect to DL and query the tables based on "
            "user inputs."
        ],
        open_items=["SR# for SFG Template: TBD"],
    )


# The demo key is the *installed filename stem* (demo_frd.docx), not the
# anonymized project name: tier 1 matches the uploaded doc_id, which is that
# stem. Keying on "Medicare Expansion-OHDS - Social Factors" would share no
# loose-form substring with "demo_frd" and every upload would 422.
_MOCKS = {
    "demo_frd": _mids_spec,
    "FRD_STG_STD_PaymentIntegrity_TPL_CAQH_To_DL_Ingestion_1005034": _caqh_spec,
}


class MockSpecNotFoundError(Exception):
    """Raised when neither routing tier below can confidently match a
    document to one of the two hand-authored mock specs. A distinct type
    (not a bare AssertionError) so a caller across a process boundary --
    e.g. the orchestration backend's subprocess-invoked pipeline stages --
    can tell "no confident mock match" apart from an arbitrary crash."""


# --------------------------------------------------------------------------- #
# Tier 1 -- loose filename match. Same normalization 03_contract_build.py's
# extraction<->doc_id matcher uses (_loose()): strip everything but
# a-z0-9, lowercase, then a bidirectional substring check. This is what
# lets a re-exported/renamed copy of a known fixture (e.g. appending
# " (1)" for a second upload of the same file) still match: the renamed
# doc_id's loose form contains the known key's loose form as a substring.
# --------------------------------------------------------------------------- #
def _loose(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _tier1_filename_match(doc_id: str) -> str | None:
    doc_loose = _loose(doc_id)
    if not doc_loose:
        return None
    matches = [key for key in _MOCKS if _loose(key) in doc_loose or doc_loose in _loose(key)]
    # Only trust this tier when exactly one known key matches -- an empty
    # or ambiguous (matches more than one) result falls through to tier 2
    # rather than guessing.
    return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------- #
# Tier 2 -- content-based fallback, for a re-exported document whose
# filename no longer resembles either fixture's doc_id at all. Reuses the
# same token-overlap shape 04_sttm_render.py's _loose_tokens() /
# grounding_audit()'s advisory check already use: lowercase alphanumeric
# tokens of length >= 4. Fingerprints below are hand-picked, distinctive
# strings already present verbatim in each mock spec above (project ids,
# feed/table names, source systems) -- i.e. facts already verified real for
# that fixture, not arbitrary keywords -- so a fingerprint hit means the
# uploaded content actually names things unique to that FRD.
# --------------------------------------------------------------------------- #
def _loose_tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{4,}", s.lower())}


_FINGERPRINTS: dict[str, set[str]] = {
    "demo_frd": _loose_tokens(
        "1007412 Medicare Expansion OHDS Civic Vantage "
        "cv_community_demographic_risk cv_community_risk cv_individual_risk "
        "demographic_extract_CCYY_MM community_metrics_CCYY_MM "
        "cv_ind_risk_data_package_mrdn_OH MDST235207"
    ),
    "FRD_STG_STD_PaymentIntegrity_TPL_CAQH_To_DL_Ingestion_1005034": _loose_tokens(
        "1005034 CAQH Payment Integrity Third Party Liability TPL "
        "COBReport EXT_TPL_CAQH_HDR EXT_TPL_CAQH_DTL EXT_TPL_CAQH_TRL "
        "SRQ226433 BR226408 SIR226434"
    ),
}

# Confidence bar for tier 2: the winning fixture must cover a healthy
# fraction of its own fingerprint AND clearly beat every other fixture's
# score -- either condition failing means "not confident", not "pick the
# best of a bad lot".
_TIER2_MIN_SCORE = 0.5
_TIER2_MIN_MARGIN = 0.25


def _tier2_content_match(content: str) -> str | None:
    if not content:
        return None
    doc_tokens = _loose_tokens(content)
    scores = {
        key: len(doc_tokens & fp) / len(fp)
        for key, fp in _FINGERPRINTS.items()
        if fp
    }
    if not scores:
        return None
    best_key, best_score = max(scores.items(), key=lambda kv: kv[1])
    runner_up = max((s for k, s in scores.items() if k != best_key), default=0.0)
    if best_score >= _TIER2_MIN_SCORE and (best_score - runner_up) >= _TIER2_MIN_MARGIN:
        return best_key
    return None


def mock_spec_for(doc_id: str, content: str | None = None) -> FrdIngestionSpec:
    """Routes a doc_id (and, for tier 2, its ingested content) to one of the
    two hand-authored mock specs above. Tries the cheap/precise filename
    match first; falls back to a content fingerprint only when the filename
    is inconclusive; refuses loudly (MockSpecNotFoundError) rather than
    guessing when neither tier is confident -- this module is scoped to the
    two known fixtures only, so silently matching an unrelated upload to
    the wrong mock spec would be worse than failing the run."""
    match = _tier1_filename_match(doc_id) or _tier2_content_match(content or "")
    if match is None:
        raise MockSpecNotFoundError(
            f"STTM_MOCK_EXTRACTION=1 but no hand-authored mock spec could be "
            f"confidently matched for doc_id={doc_id!r} -- neither the filename "
            f"nor the document content matched a known fixture (MIDS, CAQH) with "
            f"enough confidence. Add a mock spec to notebooks/_mock_extractions.py "
            f"for this document, or unset STTM_MOCK_EXTRACTION to call the real API."
        )
    return _MOCKS[match]()
