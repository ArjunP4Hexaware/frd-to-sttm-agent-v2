"""
frdsttm.label_contract — loader for the shared FRD label contract.

``contracts/frd_label_contract.json`` is the versioned artifact that
replaced the hardcoded IS-Methodology label prose previously mirrored
between this repo's ingest/contract-build path and the upstream
brd-to-frd-agent's IS-Methodology renderer. The SAME file is committed
byte-identically to BOTH repos; the upstream repo's round-trip suite
fails loudly if the two copies drift. Any contract change bumps
``version`` and must land as identical files in both repos in the same
change set — never edit one side alone.

This module fails LOUDLY (LabelContractError naming the path) when the
file is missing or lacks a ``version`` — a silent fallback to hardcoded
values would resurrect exactly the drift problem the artifact kills.

Path resolution is relative to this module's own file
(``…/src/frdsttm/`` → repo root → ``contracts/``), which holds in every
supported execution mode: plain local scripts, the editable install the
tests use, and Databricks Git folders (where the ``notebooks/_*.py``
shims' sys.path bootstrap imports ``frdsttm`` from ``<repo>/src``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = _REPO_ROOT / "contracts" / "frd_label_contract.json"


class LabelContractError(RuntimeError):
    """The shared label contract is missing or malformed."""


def load_label_contract(path: str | Path | None = None) -> dict:
    """Load and minimally validate the shared label contract. No fallback:
    a missing file or absent version raises rather than resurrecting
    hardcoded values."""
    p = Path(path) if path is not None else CONTRACT_PATH
    if not p.is_file():
        raise LabelContractError(
            f"Shared FRD label contract not found: {p} — "
            "contracts/frd_label_contract.json must exist at the repo root "
            "(committed identically to brd-to-frd-agent and frd-to-sttm-agent; "
            "there is deliberately no hardcoded fallback)."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    if not data.get("version"):
        raise LabelContractError(
            f"Shared FRD label contract at {p} declares no 'version' — "
            "refusing to use an unversioned contract."
        )
    return data


LABEL_CONTRACT = load_label_contract()

SECTION_LABELS: dict[str, str] = LABEL_CONTRACT["section_labels"]
REQ_ID_FAMILIES: tuple[str, ...] = tuple(LABEL_CONTRACT["req_id_families"])

# Compiled from the contract's stored pattern strings — byte-for-byte the
# regexes this parser previously hardcoded.
PROJECT_ID_LINE_RE = re.compile(LABEL_CONTRACT["project_id"]["line_pattern"])
PROJECT_ID_DIGITS_RE = re.compile("(" + LABEL_CONTRACT["project_id"]["digits_pattern"] + ")")
