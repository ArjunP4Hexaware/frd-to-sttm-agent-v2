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
import re
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
    """True once the client has CONFIRMED the column-level conventions.

    Neither standards document contains a target column naming rule or the
    audit-column set (both grepped 2026-08-26). ``confirmed_by`` stays null
    until the client says otherwise. Note this is about CONFIRMATION, not
    about whether a rule is derivable: :func:`column_convention` lets a
    measured-100% convention through on its own evidence.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    return bool(src.get("column_rules", {}).get("confirmed_by"))


def convention_status(name: str, naming: Optional[dict] = None) -> str:
    """``DERIVABLE`` or ``NOT_DERIVABLE`` for one naming convention.

    Measured against the mapped STTMs, not asserted: ``as_is`` reproduces
    399/399 rows, ``prefixed_upper_snake`` 22/115. The contract carries the
    numbers.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    conventions = src.get("column_rules", {}).get("conventions", {})
    if name not in conventions:
        raise StandardsError(
            f"Naming standards declares no column convention {name!r}. "
            f"Available: {sorted(conventions)}"
        )
    return conventions[name].get("_status", "NOT_DERIVABLE")


def column_convention(
    name: str, naming: Optional[dict] = None, allow_unsourced: bool = False
) -> dict:
    """Return one column naming convention (target case, prefix).

    Allowed without an opt-in when the convention is DERIVABLE — it
    reproduces the mapped workbook exactly, so applying it is a measurement,
    not a guess — or once the client has confirmed the rules. Otherwise the
    caller must pass ``allow_unsourced=True``, committing it to recording
    those cells as provenance ``standards:unsourced`` with a gated
    ambiguity.

    Audit columns are NOT part of a convention any more (v1.1.0): they are
    consistent across both mapped pairs once segment role is accounted for.
    See :func:`audit_columns`.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    conventions = src.get("column_rules", {}).get("conventions", {})
    status = convention_status(name, src)
    if status != "DERIVABLE" and not column_rules_are_sourced(src) and not allow_unsourced:
        raise StandardsError(
            f"Column convention {name!r} is {status}: neither client standards "
            "document states a target column naming rule, and this one does not "
            "reproduce the mapped workbook "
            f"({conventions[name].get('_evidence', 'see the contract')}). "
            "Pass allow_unsourced=True and record the cells as provenance "
            "'standards:unsourced' with a gated ambiguity, or get the client to "
            "confirm the convention and set confirmed_by."
        )
    return dict(conventions[name])


def upper_snake(name: str) -> str:
    """``Member ID`` -> ``MEMBER_ID``. The half of a target column name that
    IS a function of the source name."""
    return "_".join(w.upper() for w in re.split(r"[^A-Za-z0-9]+", str(name or "")) if w)


def infer_column_convention(sub_domain: str | None, target_tables) -> dict:
    """Does this feed RENAME its columns? Answered from the FRD alone.

    Both inputs are Structural Metadata rows the FRD already carries:
    ``Domain and Sub-domain`` and ``Target Table Name``. The signal is simple
    and it holds on every real feed on hand — **if the sub-domain token also
    appears in the target table names, the columns carry it too**:

        CAQH  sub-domain TPL   tables EXT_TPL_CAQH_HDR/DTL/TRL  -> renames 115/115
        SD    sub-domain Public  tables sd_community_risk, ...  -> renames   4/411

    Returns ``{"convention", "prefix", "why"}`` where convention is
    ``prefixed_upper_snake`` or ``as_is``.

    This is a DERIVED GUESS, not a client rule — neither standards document
    states a column-naming convention (both grepped 2026-08-26). It exists
    because the alternative was emitting the bare source name for a feed that
    renames, which is further from the truth and gives a reviewer nothing to
    correct. Measured on CAQH: the bare source name scores 0/115 exact at mean
    similarity 0.59; this convention scores 22/115 exact at mean 0.79, with 63
    of 115 either exact or within 0.80 — a rename a BSA finishes, not a
    rebuild. The caller must record it as derived and flag it for review.
    """
    raw = str(sub_domain or "")
    words = re.findall(r"[A-Za-z0-9]+", raw)
    # A real FRD spells the sub-domain out ("Third Party Liability") while the
    # tables carry its ACRONYM (EXT_TPL_CAQH_*). Testing only the collapsed
    # phrase missed that on the one feed we know renames, so the acronym of a
    # multi-word sub-domain is a candidate too — longest candidate first, so
    # an exact phrase match still wins over a coincidental acronym.
    candidates = [re.sub(r"[^a-z0-9]+", "", raw.lower())]
    if len(words) > 1:
        candidates.append("".join(w[0] for w in words).lower())
    candidates = [c for c in dict.fromkeys(candidates) if c]
    tables = [re.sub(r"[^a-z0-9]+", "", str(t or "").lower()) for t in (target_tables or [])]
    for token in sorted(candidates, key=len, reverse=True):
        if tables and any(token in t for t in tables):
            how = ("appears in" if token == candidates[0]
                   else f"abbreviates to {token.upper()!r}, which appears in")
            return {"convention": "prefixed_upper_snake",
                    "prefix": f"{token.upper()}_",
                    "why": f"the FRD's sub-domain {sub_domain!r} {how} its target table "
                           f"names, so the columns are assumed to carry it too"}
    return {"convention": "as_is", "prefix": "",
            "why": "the FRD's sub-domain does not appear in the target table names, so the "
                   "source column names are assumed to carry through unchanged "
                   "(measured 399/399 on the one approved feed that works this way)"}


def audit_columns(
    layer: str,
    naming: Optional[dict] = None,
    data_bearing: bool = True,
    include_feed_specific: bool = False,
) -> tuple[dict, ...]:
    """Target columns with no source field, for ``layer``.

    The core three appear on every table of both mapped workbooks in both
    layers. ``LOB`` appears on data-bearing tables only — it is absent from
    CAQH's header and trailer control tables, which is coherent: a line of
    business is a per-record business attribute, meaningless on a file
    control record. Pass ``data_bearing=False`` for a header/trailer table.

    ``include_feed_specific`` adds columns seen in ONE workbook only
    (``FILE_TYPE``). Off by default: emitting a one-feed column for a new
    feed would be an invention.
    """
    src = naming if naming is not None else NAMING_STANDARDS
    blocks = src.get("column_rules", {}).get("audit_columns", {})
    groups = ["core"]
    if data_bearing:
        groups.append("data_bearing_only")
    if include_feed_specific:
        groups.append("feed_specific")
    out = []
    lay = str(layer).lower()
    for g in groups:
        for col in blocks.get(g, {}).get("columns", []):
            if lay in [str(x).lower() for x in col.get("layers", [])]:
                out.append({**col, "audit_group": g,
                            "confidence": blocks[g].get("_status")})
    return tuple(out)


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
