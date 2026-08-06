"""Applies tools/anonymization_mapping.py to the MIDS entry of
notebooks/_mock_extractions.py, so the hardcoded mock spec keeps matching
the anonymized demo fixtures.

**This script MUST be run against pristine, never-anonymized text.** It is
not idempotent: `shift_requirement_ids()` adds a fixed delta every time it
runs, so a second pass turns MDST235207 into MDST239344 -- a value that
appears nowhere in the FRD. `requirement_ids` is STRICT grounding-checked
(03_contract_build.py), so that silently gates the document FAIL: no
contract, no eval, no coverage tile.

`classify()` below decides which of three states the region is in, by
checking *every* requirement id in it against the pristine set recovered
from PRISTINE_COMMIT:

    pristine  -- all ids un-shifted        -> apply the mapping
    applied   -- all ids shifted by DELTA  -> nothing to do, exit 0
    mixed     -- anything else             -> refuse, exit 3, --force is
                                              not honoured

The correct workflow when the mapping changes is to restore the MIDS
region from the last pre-anonymization commit and run this once:

    git show <pre-anon-commit>:notebooks/_mock_extractions.py   # take the
    # _MIDS_RULE_ZIP .. def _caqh_spec() region, splice it in, then:
    python tools/apply_anonymization_to_mock_spec.py

Run from the repo root:  python tools/apply_anonymization_to_mock_spec.py [--dry-run] [--force]

Scoped deliberately to the MIDS region only (`_MIDS_RULE_ZIP` through the
end of `_mids_spec()`): the CAQH fixture is not being anonymized, and
several rules (`\\bSD\\b`, `\\bsd_`, the people names) would otherwise
rewrite it too. Rule order in the mapping module is load-bearing and is
applied exactly as written there -- nothing is hand-derived here.

Routing keys are NOT handled by the substitution rules: the installed
filename is demo_frd.docx, so the Tier 1 key has to become the loose form
of that stem rather than the mapped project name. That change is made
explicitly in the caller (see the report accompanying this commit).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from anonymization_mapping import ID_DELTA, ID_PREFIXES, RULES  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "notebooks" / "_mock_extractions.py"

START = "_MIDS_RULE_ZIP = ("
END = "def _caqh_spec()"

#: Last commit whose MIDS region is pristine (never anonymized). The
#: pristine id set is read back out of it at runtime rather than pasted
#: here as a literal, so it cannot drift from what git actually holds.
PRISTINE_COMMIT = "1c8b2dc"

_ID_RE = re.compile(rf"\b({'|'.join(ID_PREFIXES)})(\d+)\b")


def shift_requirement_ids(text: str) -> str:
    """Offset requirement ids by the mapping module's fixed delta, so they
    stay internally consistent but no longer match the client's tracker."""
    return _ID_RE.sub(lambda m: f"{m.group(1)}{int(m.group(2)) + ID_DELTA}", text)


def substitute(text: str) -> str:
    """The text substitutions alone -- everything except the id shift."""
    for pattern, replacement in RULES:
        text = re.sub(pattern, replacement, text)
    return text


def apply_rules(text: str) -> str:
    return shift_requirement_ids(substitute(text))


def _rel(path: Path) -> str:
    """`path` relative to the repo root, for display in messages.

    Falls back to the absolute path instead of raising: every caller is
    building a diagnostic, and a formatting detail must not be what turns a
    clear refusal (or a completed write) into a traceback.
    """
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def region_of(text: str) -> str:
    """The MIDS slice of a `_mock_extractions.py` source text."""
    return text[text.index(START) : text.index(END)]


def ids_in(text: str) -> set[str]:
    """Every requirement id in `text`, as a set."""
    return {f"{p}{n}" for p, n in _ID_RE.findall(text)}


def pristine_ids() -> set[str]:
    """The MIDS region's requirement ids as of PRISTINE_COMMIT.

    Read from git rather than hardcoded: the whole point of the check below
    is to compare against ground truth, and a literal list in this file is
    not ground truth -- it is a second copy that can silently disagree with
    the commit it claims to describe.
    """
    out = subprocess.run(
        ["git", "show", f"{PRISTINE_COMMIT}:notebooks/_mock_extractions.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"cannot read {PRISTINE_COMMIT}:notebooks/_mock_extractions.py "
            f"({out.stderr.strip()}). The pristine id set is not recoverable, "
            "so the region cannot be classified -- refusing rather than guessing."
        )
    return ids_in(region_of(out.stdout))


def classify(region: str) -> tuple[str, dict[str, set[str]]]:
    """Classify the MIDS region as `pristine`, `applied`, or `mixed`.

    This replaces an earlier guard that asked "do the substitution rules
    still bite?". That question is existential, but the question that
    actually matters -- "is this region pristine?" -- is universal, and the
    two come apart on exactly the case the guard exists for. A half-mapped
    region still contains enough unmapped text for some rule to bite, so
    the old check called it pristine and re-shifted the ids that had already
    been shifted (MDST235207 -> MDST239344).

    So classify every id in the region, not a sample, against two known
    sets: the pristine ids from PRISTINE_COMMIT, and those same ids plus
    ID_DELTA. A region is safe to process only if all of its ids sit in the
    first set, and safe to skip only if all of them sit in the second. Any
    other shape -- ids in both sets, or ids in neither (a double shift lands
    here) -- is a partially anonymized region, which no amount of forcing
    should push further out of shape.
    """
    pristine = pristine_ids()
    shifted = {f"{p}{int(n) + ID_DELTA}" for p, n in (_ID_RE.match(i).groups() for i in pristine)}
    if pristine & shifted:
        raise RuntimeError(
            f"ID_DELTA={ID_DELTA} maps some pristine ids onto other pristine "
            "ids; pristine and shifted states are indistinguishable."
        )

    found = ids_in(region)
    buckets = {
        "pristine": found & pristine,
        "shifted": found & shifted,
        "unknown": found - pristine - shifted,
    }

    if not found:
        raise RuntimeError(
            f"no requirement ids found between {START!r} and {END!r} -- the "
            "region markers are probably wrong. Refusing to rewrite a region "
            "this check cannot classify."
        )
    if buckets["unknown"]:
        return "mixed", buckets
    if buckets["pristine"] and buckets["shifted"]:
        return "mixed", buckets
    return ("pristine" if buckets["pristine"] else "applied"), buckets


def _mixed_report(buckets: dict[str, set[str]]) -> str:
    def show(key: str) -> str:
        return ", ".join(sorted(buckets[key])) or "(none)"

    if buckets["pristine"] and buckets["shifted"]:
        headline = (
            "the MIDS region is PARTIALLY anonymized -- some requirement ids "
            "have been shifted and some have not, so the region is in neither "
            "a pristine nor a fully applied state."
        )
        consequence = (
            "Applying the mapping now would shift the pristine ids a first "
            "time and the already-shifted ids a second time, and re-running "
            "it later would not undo either."
        )
    else:
        headline = (
            "the MIDS region contains requirement ids that are neither "
            "pristine nor pristine+ID_DELTA, so its state cannot be "
            f"established against {PRISTINE_COMMIT}."
        )
        consequence = (
            f"Ids this far out are usually a shift that already ran twice "
            f"(each pass adds {ID_DELTA}), which leaves values appearing "
            "nowhere in the FRD. requirement_ids is STRICT grounding-checked, "
            "so those gate the document FAIL."
        )

    return (
        f"REFUSING: {headline}\n"
        f"  still pristine : {show('pristine')}\n"
        f"  already shifted: {show('shifted')}\n"
        f"  unrecognized   : {show('unknown')}\n"
        f"{consequence}\n"
        f"Restore the region from git and run this once:\n"
        f"    git show {PRISTINE_COMMIT}:notebooks/_mock_extractions.py\n"
        f"    # splice the {START!r} .. {END!r} region into "
        f"{_rel(TARGET)}, then:\n"
        "    python tools/apply_anonymization_to_mock_spec.py\n"
        "--force does not apply here: it exists to authorize a second shift "
        "of a consistent region, not to wave through a corrupted one."
    )


def main() -> int:
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    src = TARGET.read_text(encoding="utf-8")

    i = src.index(START)
    j = src.index(END)
    region = src[i:j]

    # Classified before --dry-run as well as before the write: a dry run on
    # already-mapped or half-mapped text would print a plausible-looking
    # id-shift diff and invite someone to apply it.
    try:
        state, buckets = classify(region)
    except RuntimeError as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 3

    if state == "mixed":
        # Deliberately not overridable by --force. Every other refusal here
        # describes a region in a known state, where forcing means choosing a
        # different valid action; this one describes a region whose state is
        # already wrong, where forcing only means damaging it further.
        print(_mixed_report(buckets), file=sys.stderr)
        return 3

    if state == "applied" and not force:
        print(
            "The MIDS region has already been anonymized -- every requirement "
            f"id in it ({', '.join(sorted(buckets['shifted']))}) is a pristine "
            f"id plus ID_DELTA={ID_DELTA}. Nothing to do.\n"
            "Re-running the shift would produce ids that appear nowhere in the "
            "FRD, and requirement_ids is STRICT grounding-checked, so that "
            "would gate the document FAIL. Pass --force only if you genuinely "
            "intend a second shift."
        )
        return 0

    new_region = apply_rules(region)

    if new_region == region:
        print("no change -- mapping already applied?")
        return 0

    if dry:
        for a, b in zip(region.splitlines(), new_region.splitlines()):
            if a != b:
                print(f"- {a.strip()}\n+ {b.strip()}\n")
        return 0

    TARGET.write_text(src[:i] + new_region + src[j:], encoding="utf-8")
    print(f"applied mapping to MIDS region of {_rel(TARGET)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
