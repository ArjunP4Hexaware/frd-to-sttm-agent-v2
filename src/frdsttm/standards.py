"""
frdsttm.standards — loader for the versioned client standards contracts.

``contracts/naming_standards.json`` and ``contracts/engineering_standards.json``
are transcriptions of the client's two standards documents (EDO Data
Engineering Naming Standards / Coding Standards, read 2026-08-26). They
decide the TARGET side of an STTM — catalog and schema per layer, load
strategy vocabulary, type promotion, DQ conventions — which 04 previously
borrowed implicitly from whichever reference workbook happened to match.
That borrow is why a render only looked right when regenerating an
already-mapped FRD; see docs/THREE_INPUT_ARCHITECTURE.md §5 and §11.

Doctrine, identical to :mod:`frdsttm.label_contract`:

* the files are VERSIONED and loaded at import;
* a missing or unversioned file raises :class:`StandardsError` naming the
  path — there is deliberately NO hardcoded fallback, because a rule baked
  into Python is invisible to the reviewer, silently stale when the client
  revises the document, and not re-derivable in the ACFC rebuild;
* :func:`standards_sha256` goes into every run's provenance beside
  ``system_prompt_sha256``.

The honesty rules the contracts encode are enforced here, not just
documented:

* :func:`abbreviate` returns ``None`` for a term the client's vocabulary
  does not list (the SD FRD's domain ``sdoh`` is a real example) — the
  caller gates; it never invents an abbreviation.
* :func:`column_convention` refuses to hand back an UNSOURCED convention
  unless the caller explicitly opts in, because neither standards document
  contains a column naming rule or the audit-column set.

Path resolution is relative to this module's own file, matching
``label_contract`` so it holds for local scripts, the editable install the
tests use, and Databricks Git folders.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = _REPO_ROOT / "contracts"
NAMING_PATH = CONTRACTS_DIR / "naming_standards.json"
ENGINEERING_PATH = CONTRACTS_DIR / "engineering_standards.json"


class StandardsError(RuntimeError):
    """A standards contract is missing, unversioned or malformed."""


def _load(path: Path, kind: str) -> dict:
    if not path.is_file():
        raise StandardsError(
            f"Standards contract not found: {path} — "
            f"contracts/{path.name} must exist at the repo root. It is the "
            "transcription of the client's standards document; there is "
            "deliberately no hardcoded fallback, because an unwritten rule "
            "does not survive the hand-off."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise StandardsError(f"Standards contract at {path} is not valid JSON: {exc}") from exc
    if not data.get("version"):
        raise StandardsError(
            f"Standards contract at {path} declares no 'version' — "
            "refusing to use an unversioned contract."
        )
    if data.get("kind") != kind:
        raise StandardsError(
            f"Standards contract at {path} declares kind={data.get('kind')!r}, expected {kind!r}."
        )
    return data


def load_naming_standards(path: str | Path | None = None) -> dict:
    """Load and minimally validate the naming standards contract."""
    return _load(Path(path) if path is not None else NAMING_PATH, "naming_standards")


def load_engineering_standards(path: str | Path | None = None) -> dict:
    """Load and minimally validate the engineering standards contract."""
    return _load(Path(path) if path is not None else ENGINEERING_PATH, "engineering_standards")


NAMING_STANDARDS: dict = load_naming_standards()
ENGINEERING_STANDARDS: dict = load_engineering_standards()

NAMING_VERSION: str = NAMING_STANDARDS["version"]
ENGINEERING_VERSION: str = ENGINEERING_STANDARDS["version"]


def standards_sha256(
    naming: Optional[dict] = None, engineering: Optional[dict] = None
) -> str:
    """Provenance hash over BOTH contracts, for the run manifest.

    Computed over canonical JSON rather than raw bytes so that reformatting
    a contract without changing a rule does not invalidate a run's
    provenance, exactly as ``schema_sha256`` is computed in 02.
    """
    payload = json.dumps(
        {
            "naming": naming if naming is not None else NAMING_STANDARDS,
            "engineering": engineering if engineering is not None else ENGINEERING_STANDARDS,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Vocabulary lookup
# --------------------------------------------------------------------------

def _vocab(name: str, naming: Optional[dict] = None) -> dict:
    src = naming if naming is not None else NAMING_STANDARDS
    try:
        return src["vocabularies"][name]["values"]
    except KeyError as exc:
        raise StandardsError(
            f"Naming standards declares no vocabulary {name!r}. "
            f"Available: {sorted(src.get('vocabularies', {}))}"
        ) from exc


def abbreviate(vocabulary: str, term: str | None, naming: Optional[dict] = None) -> Optional[str]:
    """Return the client's abbreviation for ``term``, or ``None`` if unlisted.

    ``None`` is a real answer, not a failure: the SD FRD's domain ``sdoh``
    is absent from the client's own domain table. The caller must then fall
    through to the FRD's stated value and raise a gated ambiguity — never
    manufacture an abbreviation.
    """
    if term is None:
        return None
    key = " ".join(str(term).split()).upper()
    if not key:
        return None
    return _vocab(vocabulary, naming).get(key)


def known_terms(vocabulary: str, naming: Optional[dict] = None) -> tuple[str, ...]:
    """Every term the client's vocabulary lists, for gating messages."""
    return tuple(sorted(_vocab(vocabulary, naming)))


def normalize_load_strategy(term: str | None, naming: Optional[dict] = None) -> Optional[str]:
    """Abbreviation for a load strategy, accepting the spellings real FRDs use.

    ``Upsert`` (SFMC) and ``Truncate and Load`` (CAQH, SD) are aliases of
    the sanctioned ``Update Else Insert`` and ``Truncate & Load``.
    """
    return abbreviate("load_strategies", term, naming)


# --------------------------------------------------------------------------
# Derivations
# --------------------------------------------------------------------------

def schema_for(layer: str, domain: str | None, naming: Optional[dict] = None) -> Optional[str]:
    """Target schema for ``layer`` given the FRD's domain, or ``None`` to gate.

    Patterns come from the contract (``derivations.schema``), not from code:
    stage is ``STG_{domain}``, standard is ``{domain}``. Returns ``None``
    when the domain is not in the client's vocabulary, so the caller gates
    instead of emitting a guessed schema.

    Case is UNSOURCED — the client's documents state none, and the three
    real documents disagree (``STG_MBR`` vs ``stg_mbr`` vs ``stg_sdoh``).
    The returned value follows the contract's pattern casing; the caller
    applies the matched template's case.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    spec = src.get("derivations", {}).get("schema", {}).get(str(layer).lower())
    if not spec:
        return None
    abbrev = abbreviate("domains", domain, src)
    if abbrev is None:
        return None
    return str(spec["pattern"]).format(domain=abbrev)


def catalog_for(layer: str, naming: Optional[dict] = None) -> Optional[str]:
    """Target catalog for ``layer``, or ``None`` when the contract has none.

    Observed from a single mapped pair — the contract says so, and the two
    values are not built the same way (``PR_DLK`` is PR_ + a PRODUCT code,
    ``PR_STD`` is PR_ + a LAYER abbreviation). Do not generalise to a layer
    the contract does not name; ``None`` means gate.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    value = src.get("derivations", {}).get("catalog", {}).get(str(layer).lower())
    return value if isinstance(value, str) else None


def derivation_status(name: str, naming: Optional[dict] = None) -> Optional[str]:
    """``STATED`` / ``OBSERVED`` / ``OBSERVED_SINGLE_PAIR`` for a derivation.

    Callers record this in ``_provenance`` so a reviewer can tell a client
    rule from an inference this repo made.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    node = src.get("derivations", {}).get(name, {})
    status = node.get("_status")
    if status is None:
        for sub in node.values():
            if isinstance(sub, dict) and sub.get("_status"):
                return sub["_status"]
    return status


# --------------------------------------------------------------------------
# Column rules — deliberately hard to use by accident
# --------------------------------------------------------------------------

def column_rules_are_sourced(naming: Optional[dict] = None) -> bool:
    """True once the client has confirmed the column-level conventions.

    Neither standards document contains a target column naming rule or the
    audit-column set (both grepped 2026-08-26). Until ``confirmed_by`` is
    set in the contract, every column-level value is this repo's
    observation, not the client's rule.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    return bool(src.get("column_rules", {}).get("confirmed_by"))


def column_convention(
    name: str, naming: Optional[dict] = None, allow_unsourced: bool = False
) -> dict:
    """Return one column convention (target case, prefix, audit columns).

    Raises unless the convention is confirmed by the client or the caller
    passes ``allow_unsourced=True`` — which commits it to recording the
    cells as ``standards:unsourced`` and raising a gated ambiguity, per
    ``column_rules._about`` in the contract.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    rules = src.get("column_rules", {})
    conventions = rules.get("conventions", {})
    if name not in conventions:
        raise StandardsError(
            f"Naming standards declares no column convention {name!r}. "
            f"Available: {sorted(conventions)}"
        )
    if not column_rules_are_sourced(src) and not allow_unsourced:
        raise StandardsError(
            "Column-level rules are UNSOURCED: neither client standards document "
            "states a target column naming rule or the audit-column set, and "
            "contracts/naming_standards.json has confirmed_by=null. Pass "
            "allow_unsourced=True and record the cells as provenance "
            "'standards:unsourced' with a gated ambiguity, or get the client to "
            "confirm the convention and set confirmed_by."
        )
    return dict(conventions[name])


def audit_columns(
    convention: str, layer: str, naming: Optional[dict] = None, allow_unsourced: bool = False
) -> tuple[dict, ...]:
    """The trailing audit columns a convention adds at ``layer``."""
    spec = column_convention(convention, naming, allow_unsourced=allow_unsourced)
    return tuple(
        dict(col)
        for col in spec.get("audit_columns", [])
        if str(layer).lower() in [str(x).lower() for x in col.get("layers", [])]
    )


# --------------------------------------------------------------------------
# Engineering standards
# --------------------------------------------------------------------------

def type_promotion_basis(engineering: Optional[dict] = None) -> str:
    """``source_datatype`` — the client's stated rule.

    Named rather than inlined because the golden SD workbook is
    reproducible by typing from the SAMPLE instead, and that agreement is a
    coincidence the eval must not learn from.
    """
    src = engineering if engineering is not None else ENGINEERING_STANDARDS
    return src["type_promotion"]["basis"]


def stage_default_type(engineering: Optional[dict] = None) -> str:
    src = engineering if engineering is not None else ENGINEERING_STANDARDS
    return src["type_promotion"]["stage_default"]


def promote_type(source_type: str | None, engineering: Optional[dict] = None) -> Optional[str]:
    """Standard-layer datatype for a vendor ``source_type``, or ``None`` to gate.

    Every mapping here is OBSERVED, not stated — the contract says so. A
    source type the observed table does not cover returns ``None``.
    """
    if source_type is None:
        return None
    src = engineering if engineering is not None else ENGINEERING_STANDARDS
    key = " ".join(str(source_type).split()).lower()
    if not key:
        return None
    for rule in src["type_promotion"]["promotions"]["observed"]:
        if key in [str(x).lower() for x in rule["source_type"]]:
            return rule["standard"]
    return None


def run_control_columns(engineering: Optional[dict] = None) -> tuple[str, ...]:
    src = engineering if engineering is not None else ENGINEERING_STANDARDS
    return tuple(src["run_control_table"]["columns"])
