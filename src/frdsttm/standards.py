"""
frdsttm.standards — ACFC's naming + engineering standards, as config.

The two client documents in `standards/` are transcribed into
`standards/naming_standards.json` and `standards/engineering_standards.json`.
Both are loaded once at import, versioned, and hashed into every run
(`standards_sha256`). A missing or unversioned file raises — there is no
hardcoded fallback, because a rule baked into Python is invisible to a
reviewer and silently stale when the client revises the document.

What the standards decide, and what they do not:

* catalog + schema per layer, load-strategy vocabulary, stage default type,
  type promotion from the SOURCE type, the audit columns every table carries;
* NOT the target column names — neither document states a column naming
  rule, so column names are carried AS-IS from the vendor data dictionary
  (Arjun, 2026-08-27). ``None`` from any lookup here means "ask", never
  "invent".
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
STANDARDS_DIR = REPO_ROOT / "standards"
NAMING_PATH = STANDARDS_DIR / "naming_standards.json"
ENGINEERING_PATH = STANDARDS_DIR / "engineering_standards.json"


class StandardsError(RuntimeError):
    """A standards file is missing, unversioned or malformed."""


def _load(path: Path, kind: str) -> dict:
    if not path.is_file():
        raise StandardsError(
            f"Standards file not found: {path}. It is the transcription of the "
            "client's standards document; there is deliberately no fallback."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise StandardsError(f"{path} is not valid JSON: {exc}") from exc
    if not data.get("version"):
        raise StandardsError(f"{path} declares no 'version' — refusing an unversioned standard.")
    if data.get("kind") != kind:
        raise StandardsError(f"{path} declares kind={data.get('kind')!r}, expected {kind!r}.")
    return data


def load_naming_standards(path: str | Path | None = None) -> dict:
    return _load(Path(path) if path is not None else NAMING_PATH, "naming_standards")


def load_engineering_standards(path: str | Path | None = None) -> dict:
    return _load(Path(path) if path is not None else ENGINEERING_PATH, "engineering_standards")


NAMING: dict = load_naming_standards()
ENGINEERING: dict = load_engineering_standards()
NAMING_VERSION: str = NAMING["version"]
ENGINEERING_VERSION: str = ENGINEERING["version"]


def standards_sha256() -> str:
    """One hash over both files (canonical JSON), stamped on every run."""
    payload = json.dumps({"naming": NAMING, "engineering": ENGINEERING},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #
def _vocab(name: str) -> dict:
    try:
        return NAMING["vocabularies"][name]["values"]
    except KeyError as exc:
        raise StandardsError(f"naming standards declare no vocabulary {name!r}") from exc


def abbreviate(vocabulary: str, term: str | None) -> Optional[str]:
    """The client's abbreviation for ``term``, or None when unlisted (= ask)."""
    if term is None:
        return None
    key = " ".join(str(term).split()).upper()
    return _vocab(vocabulary).get(key) if key else None


def known_terms(vocabulary: str) -> tuple[str, ...]:
    return tuple(sorted(_vocab(vocabulary)))


def normalize_load_strategy(term: str | None) -> Optional[str]:
    return abbreviate("load_strategies", term)


# --------------------------------------------------------------------------- #
# derivations
# --------------------------------------------------------------------------- #
def schema_for(layer: str, domain: str | None) -> Optional[str]:
    """``STG_{domain}`` / ``{domain}`` from the client's domain table, or None."""
    spec = NAMING.get("derivations", {}).get("schema", {}).get(str(layer).lower())
    if not spec:
        return None
    abbrev = abbreviate("domains", domain)
    return str(spec["pattern"]).format(domain=abbrev) if abbrev else None


def catalog_for(layer: str) -> Optional[str]:
    value = NAMING.get("derivations", {}).get("catalog", {}).get(str(layer).lower())
    return value if isinstance(value, str) else None


def audit_columns(layer: str, data_bearing: bool = True) -> tuple[dict, ...]:
    """The client's audit columns (no source field) for one layer. ``LOB``
    only on data-bearing tables — absent from header/trailer control records."""
    blocks = NAMING.get("column_rules", {}).get("audit_columns", {})
    groups = ["core"] + (["data_bearing_only"] if data_bearing else [])
    out = []
    lay = str(layer).lower()
    for g in groups:
        for col in blocks.get(g, {}).get("columns", []):
            if lay in [str(x).lower() for x in col.get("layers", [])]:
                out.append(dict(col))
    return tuple(out)


# --------------------------------------------------------------------------- #
# engineering
# --------------------------------------------------------------------------- #
def stage_default_type() -> str:
    return ENGINEERING["type_promotion"]["stage_default"]


def promote_type(source_type: str | None) -> Optional[str]:
    """Standard-layer type for a vendor ``source_type`` (from the SOURCE type,
    as the coding standard states), or None when the observed table has no
    entry — the caller keeps the stage type and records it."""
    if source_type is None:
        return None
    key = " ".join(str(source_type).split()).lower()
    if not key:
        return None
    for rule in ENGINEERING["type_promotion"]["promotions"]["observed"]:
        if key in [str(x).lower() for x in rule["source_type"]]:
            return rule["standard"]
    return None


# --------------------------------------------------------------------------- #
# standard-layer type from the vendor's EXAMPLE value (config-switched)
# --------------------------------------------------------------------------- #
_DECIMAL_EXAMPLE = re.compile(r"^[-+]?\d{1,3}(,\d{3})*\.\d+%?$|^[-+]?\d*\.\d+%?$")


def infer_from_example_enabled() -> bool:
    return bool(ENGINEERING["type_promotion"].get("infer_from_example", {}).get("enabled"))


def type_from_example(example) -> Optional[str]:
    """Standard-layer type inferred from the vendor's EXAMPLE VALUE, or None.

    The analyst's own rule, measured on the approved SD workbook: an example
    with a decimal point is Decimal(10,2) (139/139); anything else is left
    alone (String). The column NAME is deliberately not consulted — it does
    not predict the type. Off unless the standards config enables it."""
    if not infer_from_example_enabled() or example is None:
        return None
    s = str(example).strip()
    if _DECIMAL_EXAMPLE.match(s):
        return ENGINEERING["type_promotion"]["infer_from_example"].get("decimal_point_example", "Decimal(10,2)")
    return None
