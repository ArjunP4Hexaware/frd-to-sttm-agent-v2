from frdsttm import standards as std


def test_versions_and_hash():
    assert std.NAMING_VERSION and std.ENGINEERING_VERSION
    assert std.standards_sha256() == std.standards_sha256()


def test_schema_for_known_and_unknown_domain():
    assert std.schema_for("stage", "MEMBER") == "STG_MBR"
    assert std.schema_for("standard", "claims") == "CLM"
    assert std.schema_for("stage", "sdoh") is None       # not in the client's table → ask
    assert std.schema_for("stage", None) is None


def test_catalog_for():
    assert std.catalog_for("stage") == "PR_DLK"
    assert std.catalog_for("standard") == "PR_STD"
    assert std.catalog_for("gold") is None


def test_load_strategy_aliases():
    assert std.normalize_load_strategy("Truncate and Load") == "TRUNC"
    assert std.normalize_load_strategy("Upsert") == "UPSRT"
    assert std.normalize_load_strategy("Nonsense") is None


def test_infer_from_example_is_a_switch_in_the_config():
    assert std.ENGINEERING_VERSION == "1.1.0" and std.infer_from_example_enabled() is True


def test_type_promotion_from_source_type():
    assert std.stage_default_type() == "String"
    assert std.promote_type("int") == "Int"
    assert std.promote_type("Decimal") == "Decimal(10,2)"
    assert std.promote_type("Date/Numeric") is None


def test_audit_columns_lob_only_on_data_bearing_tables():
    names = [c["name"] for c in std.audit_columns("stage", data_bearing=True)]
    assert names == ["SRC_FILE_NAME", "REC_CREATION_TIME", "REC_UPDATED_TIME", "LOB"]
    assert "LOB" not in [c["name"] for c in std.audit_columns("stage", data_bearing=False)]
