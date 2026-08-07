"""Grounding-audit and enrichment tests (frdsttm.contract_build) — pure
functions, no LLM, no network, no Spark."""

from frdsttm.contract_build import (
    _advisory_ok,
    _ambiguity_id,
    _strict_ok,
    _tokens,
    attribution_check,
    enrich,
    grounding_audit,
    norm,
)
from frdsttm.models import FrdIngestionSpec


def _spec(**kwargs) -> FrdIngestionSpec:
    return FrdIngestionSpec.model_validate(kwargs)


# --------------------------------------------------------------------------- #
# norm / tokens
# --------------------------------------------------------------------------- #

def test_norm_handles_word_unicode_and_markdown_chrome():
    # Smart quotes, non-breaking hyphen (U+2011), markdown chars — the exact
    # unicode Word and the parsed markdown emit.
    assert norm("“AS‑IS” **load**") == '"as-is" load'


def test_norm_collapses_whitespace_and_lowercases():
    assert norm("  Load\n\tAS   IS  ") == "load as is"


def test_tokens_drops_short_words():
    assert _tokens("the file must be loaded") == ["file", "must", "loaded"]


# --------------------------------------------------------------------------- #
# strict / advisory primitives
# --------------------------------------------------------------------------- #

def test_strict_ok_is_normalized_substring():
    content = norm("Pattern: demographic_extract_CCYY_MM.csv arrives monthly")
    assert _strict_ok("demographic_extract_CCYY_MM.csv", content)
    assert not _strict_ok("invented_file.csv", content)


def test_advisory_ok_threshold():
    content_tokens = set(_tokens("records must match member identifiers in coremember"))
    # All tokens present -> ok.
    assert _advisory_ok("member records match coremember", content_tokens)
    # Mostly absent tokens -> flagged.
    assert not _advisory_ok("completely unrelated invented prose", content_tokens)


def test_advisory_ok_empty_value_is_ok():
    assert _advisory_ok("a b c", set())  # no tokens >= 4 chars -> vacuously ok


# --------------------------------------------------------------------------- #
# grounding_audit
# --------------------------------------------------------------------------- #

def test_grounding_audit_strict_failure_on_invented_pattern():
    spec = _spec(feeds=[{"feed_name": "f1", "file_name_patterns": ["real_file.csv", "fake.csv"]}])
    audit = grounding_audit(spec, "The feed f1 delivers real_file.csv weekly.", [])
    assert audit["strict_checked"] == 2  # the two file_name_patterns
    assert any("fake.csv" in s for s in audit["strict_failed"])
    assert not any("real_file.csv" in s for s in audit["strict_failed"])


def test_grounding_audit_clean_spec_passes():
    content = "Project ID: 1005034. Feed cv_risk delivers cv_risk_file.csv."
    spec = _spec(
        project={"project_id": "1005034"},
        feeds=[{"feed_name": "cv_risk", "file_name_patterns": ["cv_risk_file.csv"]}],
    )
    audit = grounding_audit(spec, content, [])
    assert audit["strict_failed"] == []
    assert audit["advisory_flagged"] == []


def test_grounding_audit_advisory_flag_carries_feed_writeback_context():
    spec = _spec(feeds=[{"feed_name": "f1", "validation_rules": ["totally invented requirement nowhere stated"]}])
    audit = grounding_audit(spec, "content that shares no long tokens", [])
    assert len(audit["advisory_flagged"]) == 1
    flag = audit["advisory_flagged"][0]
    assert flag["kind"] == "advisory_grounding"
    assert flag["context"]["feed_index"] == 0
    assert flag["context"]["field"] == "validation_rules"
    assert flag["candidates"] == []


def test_grounding_audit_skips_empty_values():
    audit = grounding_audit(_spec(feeds=[{"feed_name": None}]), "anything", [])
    assert audit["strict_checked"] == 0
    assert audit["advisory_checked"] == 0


# --------------------------------------------------------------------------- #
# enrichment
# --------------------------------------------------------------------------- #

def test_enrich_fills_project_id_from_regex_when_agent_null():
    spec = _spec()
    spec, enrichments, disagreements = enrich(spec, "Project ID: 1005034")
    assert spec.project.project_id == "1005034"
    assert len(enrichments) == 1
    assert disagreements == []


def test_enrich_flags_disagreement_and_keeps_agent_value():
    spec = _spec(project={"project_id": "9999999"})
    spec, enrichments, disagreements = enrich(spec, "Project ID: 1005034")
    assert spec.project.project_id == "9999999"  # agent value kept
    assert len(disagreements) == 1
    d = disagreements[0]
    assert d["kind"] == "disagreement"
    assert set(d["candidates"]) == {"9999999", "1005034"}


def test_enrich_fills_lobs_from_region_pairs_only_when_empty():
    content = "Regions: REG#1 1234 and REG#2 5678 and REG#1 1234 again"
    spec = _spec(feeds=[{"feed_name": "a"}, {"feed_name": "b", "lobs": ["REG#9 0001"]}])
    spec, enrichments, _ = enrich(spec, content)
    assert spec.feeds[0].lobs == ["REG#1 1234", "REG#2 5678"]  # deduped, ordered
    assert spec.feeds[1].lobs == ["REG#9 0001"]  # non-empty lobs untouched


# --------------------------------------------------------------------------- #
# attribution check + ambiguity ids
# --------------------------------------------------------------------------- #

def test_attribution_check_gates_shared_rule_across_feeds():
    spec = _spec(
        feeds=[
            {"feed_name": "a", "validation_rules": ["ZIP_CODE must not be null"]},
            {"feed_name": "b", "validation_rules": ["zip_code  must not be NULL"]},  # same after norm()
            {"feed_name": "c", "validation_rules": ["some other rule"]},
        ]
    )
    ambiguities = attribution_check(spec)
    assert len(ambiguities) == 1
    amb = ambiguities[0]
    assert amb["kind"] == "attribution"
    assert amb["candidates"] == ["a", "b"]
    assert amb["context"]["feed_indices"] == [0, 1]


def test_attribution_check_single_feed_returns_nothing():
    spec = _spec(feeds=[{"feed_name": "only", "validation_rules": ["r1"]}])
    assert attribution_check(spec) == []


def test_ambiguity_id_is_deterministic_and_kind_prefixed():
    a = _ambiguity_id("attribution", "text", {"k": "v"})
    b = _ambiguity_id("attribution", "text", {"k": "v"})
    c = _ambiguity_id("attribution", "text", {"k": "other"})
    assert a == b
    assert a != c
    assert a.startswith("attribution-")
