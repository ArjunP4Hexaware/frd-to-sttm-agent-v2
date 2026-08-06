"""Shared FRD label contract (contracts/frd_label_contract.json) — the
versioned artifact both this repo's parser and the upstream
brd-to-frd-agent's renderer load (byte-identical copies in both repos;
the upstream round-trip suite is the cross-repo drift tripwire). These
tests pin that the file exists, parses, declares its version, and carries
every group/key/value the code consumes — deleting or rewording any of
them fails the suite instead of silently breaking the pipeline."""

import json
import re
from pathlib import Path

import pytest

from frdsttm.contract_build import norm
from frdsttm.label_contract import (
    CONTRACT_PATH,
    LABEL_CONTRACT,
    PROJECT_ID_DIGITS_RE,
    PROJECT_ID_LINE_RE,
    REQ_ID_FAMILIES,
    SECTION_LABELS,
    LabelContractError,
    load_label_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_contract_file_exists_parses_and_declares_version():
    assert CONTRACT_PATH == REPO_ROOT / "contracts" / "frd_label_contract.json"
    assert CONTRACT_PATH.is_file()
    data = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert data["version"] == "1.0.0"
    assert data == LABEL_CONTRACT  # module-level load saw the same bytes


def test_contract_carries_every_group_and_key_the_code_consumes():
    # v1.0.0 codifies exactly what the code used before the contract
    # existed — these are pins, not preferences. A deliberate contract
    # change bumps `version` and updates both repos in the same change set.
    assert set(SECTION_LABELS) == {
        "in_scope", "out_of_scope", "assumptions_constraints_dependencies",
        "data_ingestion_requirements", "data_quality",
        "technical_metadata", "administrative_metadata",
    }
    assert SECTION_LABELS["in_scope"] == "In Scope"
    assert SECTION_LABELS["assumptions_constraints_dependencies"] == (
        "Assumptions, Constraints & Dependencies"
    )
    assert all(isinstance(v, str) and v for v in SECTION_LABELS.values())

    assert REQ_ID_FAMILIES == ("BR", "REQ", "FR", "SRQ", "SIR", "NFR", "MDST")

    project_id = LABEL_CONTRACT["project_id"]
    assert project_id["label"] == "Project ID:"
    re.compile(project_id["digits_pattern"])
    re.compile(project_id["line_pattern"])

    tbd = LABEL_CONTRACT["placeholders"]["tbd_pending"]
    assert tbd == "TBD — pending client input (not stated in the source BRD)"


def test_project_id_patterns_match_the_rendered_body_line():
    # line_pattern applies to norm()ed (lowercased, space-collapsed) content,
    # exactly as enrich() uses it.
    m = PROJECT_ID_LINE_RE.search(norm("Project ID: 1005034"))
    assert m and m.group(1) == "1005034"
    assert PROJECT_ID_LINE_RE.search(norm("Project ID: 12345")) is None  # <6 digits
    # digits_pattern drives filename inference in 01_frd_ingest.
    m = PROJECT_ID_DIGITS_RE.search("FRD_IS_Methodology_1005034.docx")
    assert m and m.group(1) == "1005034"


def test_requirement_marker_built_from_families_matches_every_family():
    # The exact skeleton 01_frd_ingest builds around the shared families.
    marker = re.compile(
        r"^\s*((?:" + "|".join(REQ_ID_FAMILIES) + r")[-\s]?\d+)\b[\s:.—–-]*(.*)$", re.I
    )
    for family in REQ_ID_FAMILIES:
        m = marker.match(f"{family}226433: some requirement text")
        assert m and m.group(1) == f"{family}226433"
    assert marker.match("UNKNOWN226433: text") is None


def test_extraction_schema_still_names_every_contract_label():
    # The Agent Bricks extraction schema's prose descriptions steer the LLM
    # by these labels; a contract label the schema stops mentioning is
    # drift on the LLM-instruction side.
    schema_text = (REPO_ROOT / "schema" / "sttm_extraction_schema.json").read_text(
        encoding="utf-8"
    )
    for label in SECTION_LABELS.values():
        assert label in schema_text, f"schema no longer mentions contract label {label!r}"


def test_missing_file_and_missing_version_fail_loudly(tmp_path):
    with pytest.raises(LabelContractError, match="not found"):
        load_label_contract(tmp_path / "nope.json")
    unversioned = tmp_path / "unversioned.json"
    unversioned.write_text('{"section_labels": {}}', encoding="utf-8")
    with pytest.raises(LabelContractError, match="version"):
        load_label_contract(unversioned)
