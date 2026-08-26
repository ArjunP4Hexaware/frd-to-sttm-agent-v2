"""Offline tests for frdsttm.standards — the client standards contracts.

The point of these contracts is that a target-side rule is WRITTEN DOWN and
VERSIONED rather than baked into Python, and that anything the client did
not actually state cannot be applied silently. The tests below pin both
halves: the vocabulary lookups against values taken from the real
documents, and the refusals that keep an unsourced rule from leaking into a
rendered workbook.
"""

from __future__ import annotations

import json

import pytest

from frdsttm import standards as S


# --------------------------------------------------------------------------
# Loading / versioning doctrine
# --------------------------------------------------------------------------

def test_both_contracts_load_and_declare_a_version():
    assert S.NAMING_STANDARDS["kind"] == "naming_standards"
    assert S.ENGINEERING_STANDARDS["kind"] == "engineering_standards"
    assert S.NAMING_VERSION and S.ENGINEERING_VERSION


def test_missing_contract_raises_naming_the_path(tmp_path):
    missing = tmp_path / "naming_standards.json"
    with pytest.raises(S.StandardsError) as exc:
        S.load_naming_standards(missing)
    assert str(missing) in str(exc.value)
    assert "no hardcoded fallback" in str(exc.value)


def test_unversioned_contract_is_refused(tmp_path):
    p = tmp_path / "naming_standards.json"
    p.write_text(json.dumps({"kind": "naming_standards"}), encoding="utf-8")
    with pytest.raises(S.StandardsError, match="no 'version'"):
        S.load_naming_standards(p)


def test_wrong_kind_is_refused(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"version": "1.0.0", "kind": "engineering_standards"}), encoding="utf-8")
    with pytest.raises(S.StandardsError, match="expected 'naming_standards'"):
        S.load_naming_standards(p)


def test_standards_sha256_is_stable_and_content_sensitive():
    first = S.standards_sha256()
    assert first == S.standards_sha256()
    bumped = json.loads(json.dumps(S.NAMING_STANDARDS))
    bumped["version"] = "9.9.9"
    assert S.standards_sha256(naming=bumped) != first


def test_source_documents_are_recorded_with_hashes():
    for contract in (S.NAMING_STANDARDS, S.ENGINEERING_STANDARDS):
        docs = contract["source_documents"]
        assert docs, "a standards contract must name the document it transcribes"
        for d in docs:
            assert d["name"].endswith(".docx")
            assert len(d["sha256"]) == 64


# --------------------------------------------------------------------------
# Vocabularies — values taken verbatim from the client's tables
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "vocab,term,expected",
    [
        ("data_layers", "STAGE", "STG"),
        ("data_layers", "Standard", "STD"),
        ("data_layers", "consumption", "CMP"),
        ("domains", "MEMBER", "MBR"),
        ("domains", "claims", "CLM"),
        ("domains", "  Pharmacy  ", "RX"),
        ("product_codes", "Data Lake", "DLK"),
        ("frequencies", "Weekly", "WKL"),
        ("region_lob", "Exchange", "REG_EXCH"),
    ],
)
def test_abbreviate_is_case_and_whitespace_insensitive(vocab, term, expected):
    assert S.abbreviate(vocab, term) == expected


def test_abbreviate_returns_none_for_a_term_the_client_does_not_list():
    # The SD FRD's own domain. A real gap in the client's table, not a typo:
    # the caller must gate rather than manufacture an abbreviation.
    assert S.abbreviate("domains", "sdoh") is None
    assert S.abbreviate("domains", None) is None
    assert S.abbreviate("domains", "   ") is None


def test_unknown_vocabulary_raises_listing_what_exists():
    with pytest.raises(S.StandardsError, match="data_layers"):
        S.abbreviate("nonexistent_vocab", "x")


def test_known_terms_supports_a_gating_message():
    assert "MEMBER" in S.known_terms("domains")
    assert S.known_terms("domains") == tuple(sorted(S.known_terms("domains")))


# --------------------------------------------------------------------------
# Load strategy — the CodeGen enum defect
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "stated,expected",
    [
        ("Truncate and Load", "TRUNC"),   # CAQH + SD spelling
        ("Truncate & Load", "TRUNC"),     # the document's spelling
        ("Append", "INSRT"),
        ("Upsert", "UPSRT"),              # SFMC spelling
        ("Update Else Insert", "UPSRT"),  # the document's spelling
        ("Extracts", "EXTR"),
    ],
)
def test_every_sanctioned_load_strategy_including_the_spellings_frds_use(stated, expected):
    assert S.normalize_load_strategy(stated) == expected


def test_upsert_is_sanctioned_even_though_codegen_rejects_it():
    # The SFMC FRD states 'Upsert'. CodeGen's FrdContract enum accepts only
    # {'Truncate and Load','Append'}, so it rejects a valid document. This
    # test pins which side is wrong.
    assert S.normalize_load_strategy("Upsert") is not None


def test_an_unsanctioned_strategy_is_none():
    assert S.normalize_load_strategy("Merge Into") is None


# --------------------------------------------------------------------------
# Derivations — schema and catalog per layer
# --------------------------------------------------------------------------

def test_schema_derivation_reproduces_the_real_documents():
    assert S.schema_for("stage", "MEMBER") == "STG_MBR"      # CAQH STTM
    assert S.schema_for("standard", "MEMBER") == "MBR"       # CAQH STTM
    assert S.schema_for("standard", "member") == "MBR"       # SFMC FRD, lower


def test_schema_returns_none_when_the_domain_is_unlisted():
    # SD's 'sdoh': the pattern is known but the domain is not, so the caller
    # gates rather than rendering 'STG_None'.
    assert S.schema_for("stage", "sdoh") is None
    assert S.schema_for("stage", None) is None


def test_schema_returns_none_for_a_layer_the_contract_has_no_pattern_for():
    assert S.schema_for("gold", "MEMBER") is None


def test_catalog_only_answers_for_the_two_observed_layers():
    assert S.catalog_for("stage") == "PR_DLK"
    assert S.catalog_for("standard") == "PR_STD"
    # Never generalise a single pair's convention to another layer.
    assert S.catalog_for("gold") is None
    assert S.catalog_for("raw") is None


def test_derivations_carry_their_confidence_for_provenance():
    assert S.derivation_status("catalog") == "OBSERVED_SINGLE_PAIR"
    assert S.derivation_status("schema") == "OBSERVED"


# --------------------------------------------------------------------------
# Column rules — unsourced, and must stay hard to use by accident
# --------------------------------------------------------------------------

def test_column_rules_are_marked_unsourced():
    # Neither standards document contains a column naming rule or the audit
    # columns. Until the client confirms, this repo's file is the only record.
    assert S.column_rules_are_sourced() is False
    assert S.NAMING_STANDARDS["column_rules"]["_status"] == "UNSOURCED"
    assert S.NAMING_STANDARDS["column_rules"]["confirmed_by"] is None


def test_column_convention_refuses_without_an_explicit_opt_in():
    with pytest.raises(S.StandardsError, match="UNSOURCED"):
        S.column_convention("prefixed_upper_snake")


def test_column_convention_opt_in_returns_the_observed_convention():
    conv = S.column_convention("prefixed_upper_snake", allow_unsourced=True)
    assert conv["target_column"] == "upper_snake"
    assert conv["prefix"] == "TPL_"
    as_is = S.column_convention("as_is", allow_unsourced=True)
    assert as_is["target_column"] == "as_is"
    assert as_is["prefix"] == ""
    assert as_is["audit_columns"] == []


def test_unknown_convention_raises_listing_what_exists():
    with pytest.raises(S.StandardsError, match="as_is"):
        S.column_convention("nope", allow_unsourced=True)


def test_audit_columns_match_the_caqh_workbook():
    names = [c["name"] for c in S.audit_columns("prefixed_upper_snake", "stage", allow_unsourced=True)]
    assert names == ["SRC_FILE_NAME", "REC_CREATION_TIME", "REC_UPDATED_TIME"]
    types = {c["name"]: c["datatype"] for c in
             S.audit_columns("prefixed_upper_snake", "standard", allow_unsourced=True)}
    assert types["SRC_FILE_NAME"] == "String"
    assert types["REC_CREATION_TIME"] == "timestamp"


def test_no_default_convention_is_declared():
    # Picking one by default would reintroduce the implicit borrow from the
    # matched template that the whole contract exists to remove.
    assert S.NAMING_STANDARDS["column_rules"]["default_convention"] is None


# --------------------------------------------------------------------------
# Engineering standards
# --------------------------------------------------------------------------

def test_type_promotion_is_from_the_source_datatype_never_the_sample():
    assert S.type_promotion_basis() == "source_datatype"
    assert S.ENGINEERING_STANDARDS["type_promotion"]["_never"] == "sample_value"
    assert S.ENGINEERING_STANDARDS["type_promotion"]["_status"] == "STATED"


def test_stage_defaults_to_string():
    assert S.stage_default_type() == "String"


@pytest.mark.parametrize(
    "source_type,expected",
    [
        ("int", "Int"),
        ("Integer", "Int"),
        ("numeric", "Decimal(10,2)"),
        ("decimal", "Decimal(10,2)"),
        ("varchar", "String"),
        ("Alpha Numeric", "String"),
    ],
)
def test_promote_type_covers_the_vendor_types_seen_in_real_documents(source_type, expected):
    assert S.promote_type(source_type) == expected


def test_promote_type_returns_none_for_an_uncovered_type():
    assert S.promote_type("blob") is None
    assert S.promote_type(None) is None


def test_promotions_are_marked_observed_not_stated():
    assert S.ENGINEERING_STANDARDS["type_promotion"]["promotions"]["_status"] == "UNSOURCED"


def test_reject_table_name_is_deliberately_absent():
    # The document mandates a reject table without naming one.
    assert S.ENGINEERING_STANDARDS["data_quality"]["rejects"]["reject_table"] is None


def test_run_control_columns_are_verbatim():
    assert S.run_control_columns() == (
        "OBJECT_NAME", "SRC_REC_COUNT", "TGT_REC_COUNT", "REJECTED_REC_COUNT",
        "EXEC_STATUS", "ERROR_MESSAGE", "BATCH_DATE",
    )
