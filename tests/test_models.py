"""Round-trip and schema-guard tests for the pydantic models
(frdsttm.models) — pure validation, no LLM, no network, no Spark."""

import json

import pytest
from pydantic import ValidationError

from frdsttm.mock_extractions import mock_spec_for
from frdsttm.models import (
    Feed,
    FrdIngestionSpec,
    GatedAmbiguity,
    HumanResolution,
    TableTarget,
)


def test_frd_ingestion_spec_round_trip_mock_mids():
    spec = mock_spec_for("demo_frd", "")
    dumped = json.loads(spec.model_dump_json(by_alias=True))
    revalidated = FrdIngestionSpec.model_validate(dumped)
    assert revalidated.model_dump_json(by_alias=True) == spec.model_dump_json(by_alias=True)


def test_frd_ingestion_spec_mock_has_three_feeds():
    spec = mock_spec_for("demo_frd", "")
    assert [f.feed_name for f in spec.feeds] == [
        "cv_community_demographic_risk",
        "cv_community_risk",
        "cv_individual_risk",
    ]


def test_empty_spec_is_structurally_valid():
    # The schema deliberately makes every field optional — {} validates.
    spec = FrdIngestionSpec.model_validate({})
    assert spec.feeds == []


def test_extra_field_is_rejected():
    # extra="forbid" is the pipeline's schema-drift guard.
    with pytest.raises(ValidationError):
        FrdIngestionSpec.model_validate({"unexpected_field": 1})


def test_feed_extra_field_is_rejected_nested():
    with pytest.raises(ValidationError):
        FrdIngestionSpec.model_validate({"feeds": [{"feed_name": "x", "nope": True}]})


def test_table_target_schema_alias_round_trip():
    tgt = TableTarget.model_validate({"schema": "stg_sdh", "tables": ["t1"]})
    assert tgt.schema_ == "stg_sdh"
    assert json.loads(tgt.model_dump_json(by_alias=True))["schema"] == "stg_sdh"


def test_gated_ambiguity_round_trip():
    raw = {
        "id": "attribution-abc123def456",
        "kind": "attribution",
        "text": "rule applied to 2 feeds",
        "has_candidates": True,
        "candidates": ["feed_a", "feed_b"],
        "context": {"feed_names": ["feed_a", "feed_b"]},
    }
    amb = GatedAmbiguity.model_validate(raw)
    assert json.loads(amb.model_dump_json())["kind"] == "attribution"


def test_gated_ambiguity_rejects_unknown_kind():
    # There is deliberately no 'other' kind.
    with pytest.raises(ValidationError):
        GatedAmbiguity.model_validate(
            {"id": "x", "kind": "other", "text": "t", "has_candidates": False}
        )


def test_human_resolution_round_trip():
    raw = {
        "ambiguity_id": "advisory_grounding-0123456789ab",
        "kind": "advisory_grounding",
        "resolution_type": "free_text",
        "rationale": "reviewer override text",
        "resolved_at": "2026-08-06T00:00:00+00:00",
        "resolved_by": "reviewer",
    }
    res = HumanResolution.model_validate(raw)
    assert res.chosen_candidate is None
    assert HumanResolution.model_validate(json.loads(res.model_dump_json())) == res


def test_human_resolution_rejects_unknown_resolution_type():
    with pytest.raises(ValidationError):
        HumanResolution.model_validate(
            {
                "ambiguity_id": "x",
                "kind": "attribution",
                "resolution_type": "maybe",
                "resolved_at": "2026-08-06T00:00:00+00:00",
            }
        )


def test_feed_defaults_are_empty_not_invented():
    feed = Feed()
    assert feed.feed_name is None
    assert feed.file_name_patterns == []
    assert feed.validation_rules == []
