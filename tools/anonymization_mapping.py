"""Substitution rules for anonymizing the demo fixtures.

REAL-SIDE STRINGS ARE NOT STORED IN THIS FILE — LOUD NOTE, READ THIS.
Every rule whose *pattern* is a real client-side string (people, mailboxes,
a phone number, the client SharePoint host, organization/vendor/program
names, geography, project identifiers, schema/system identifiers, source
file names) lives in the gitignored sidecar
`tools/anonymization_pii_rules_local.py` and is spliced back in at import.
This committed file records only:

  - rule ORDER (load-bearing — longest / most-specific first; each
    relocated rule leaves a `PII("<key>")` placeholder occupying its exact
    ordinal slot),
  - the FAKE replacement side (in each placeholder's trailing comment),
  - the second-pass "retirement" rules whose patterns match the OUTPUT of
    earlier rules (first-pass stand-ins) — those patterns contain no real
    strings and stay committed.

`_resolve()` swaps each placeholder for the sidecar entry of the same key
without moving anything: position is structural here, never reconstructed
at load time. A missing sidecar or a missing key raises — a partial rule
set would produce exactly the half-mapped region that
`apply_anonymization_to_mock_spec.classify()` exists to refuse.

The sidecar is NOT recoverable from git. The personal-data keys
(person_* / email_* / phone_direct / sharepoint_url) are restorable only
from the pii_purge backup noted in `_sidecar()`'s error message; the
relocated real-side rule keys (r01…) were written to the machine-local
sidecar when they were moved out of this file on 2026-08-06.
"""


class PII:
    """Placeholder holding an ordinal slot for a rule whose real-side
    pattern is kept out of git (personal data or client-identifying
    strings). The trailing comment on each entry records the fake side."""

    __slots__ = ("key",)

    def __init__(self, key: str) -> None:
        self.key = key


# Ordered: longest / most-specific first. Order is load-bearing.
RULES = [
    # ---- URLs & emails (before bare domain/name rules) ----
    PII("sharepoint_url"),
    PII("email_vendor_1"),
    PII("email_vendor_2"),
    PII("email_dl_support"),
    PII("r01"),  # vendor domain -> 'civicvantage.example'
    PII("r02"),  # client domain -> 'meridianchp.com' (retired below)
    PII("phone_direct"),

    # Normalise any residual SharePoint URL tail (doc GUIDs, query strings)
    # on the first-pass stand-in host. The original URLs contain literal
    # spaces, so a single non-greedy URL pattern cannot consume them in one
    # pass. Pattern matches earlier rules' output only — no real strings.
    (r'(meridianchp\.sharepoint\.com/sites/DataOffice/ProjectDocs)[^<"\']*', r'\1'),
    PII("r03"),  # STTM workbook title -> 'STTM-Medicare Expansion-OHDS-Social Factors'
    PII("r04"),  # truncated vendor-name form -> 'Social Factors'
    PII("r05"),  # requirements-tool vendor (full legal name) -> 'Meridian Community Health'
    PII("r06"),  # requirements-tool product name -> 'Requirements Hub'

    # ---- org / vendor names ----
    PII("r07"),  # client org (full) -> 'Meridian Community Health' (retired below)
    PII("r08"),  # client org (short) -> 'Meridian' (retired below)
    PII("r09"),  # client org abbreviation -> 'MCHP' (retired below)
    PII("r10"),  # data vendor name -> 'Civic Vantage'
    # snake_case form used in ADLS landing paths. Deliberately anchored to
    # the full vendor snake_case name so it cannot touch the SDOH domain
    # columns (social_connectedness_*, social_detachment_index, ...), which
    # are generic terminology and are eval-bearing.
    PII("r11"),  # vendor snake_case path token -> 'civic_vantage'
    PII("r12"),  # vendor possessive (smart-quote form) -> "Civic Vantage's"
    PII("r13"),  # vendor two-letter abbreviation -> 'CV'

    # ---- project identity (MUST precede the bare program-acronym rule) ----
    PII("r14"),  # project title, hyphenated form -> 'Medicare Expansion-OHDS-Social Factors'
    PII("r15"),  # project title, spaced form -> 'Medicare Expansion-OHDS - Social Factors'
    PII("r16"),  # real project number -> '1007412'

    # ---- program / geography ----
    PII("r17"),  # program acronym -> 'OHDS'
    PII("r18"),  # state name -> 'Ohio'
    PII("r19"),  # state-abbrev phrase 1 -> 'OH state'
    PII("r20"),  # state-abbrev phrase 2 -> 'OH Risk'
    PII("r21"),  # state-abbrev phrase 3 -> 'OH-Monthly'
    PII("r22"),  # client/state file-name token (upper) -> '_mrdn_OH_'
    PII("r23"),  # client/state file-name token (lower) -> '_mrdn_oh_'

    # ---- product / brand names ----
    # Second-pass renames: these retire the first-pass stand-ins (which read
    # as a plausible real health plan) in favour of obviously-synthetic
    # names. The committed patterns here match the *output* of earlier
    # rules, not the client source — so this block MUST sit after everything
    # that produces them. The one exception is r24: verbatim client text no
    # earlier rule ever touched, so its pattern lives in the sidecar.
    #
    # It must also sit after the state-abbrev phrase rules directly above —
    # the standalone state-abbrev rule that closes this block (r25) would
    # otherwise rewrite their abbreviation first and strand them.
    #
    # NOTE: the 2026-07-26 pass edited demo_frd.docx by hand (byte-level
    # substitution over the already-anonymized fixture). This mapping is NOT
    # being re-applied to the client original; these entries are recorded so
    # the rule set still describes the fixture's full provenance.
    PII("r24"),  # client dual-eligible program name -> 'Sample Dual-Eligible Program'
    (r'Meridian Community Health', 'Example Health Plan'),
    (r'meridianchp\.sharepoint\.com', 'examplehealthplan.example'),
    (r'meridianchp\.com', 'examplehealthplan.example'),
    (r'meridianchp', 'examplehealthplan.example'),
    (r'\bMCHP\b', 'EX-HP'),
    (r'\bMeridian\b', 'Example Health Plan'),
    # Last in the block: only the standalone state-abbrev occurrences none
    # of the above consumed.
    PII("r25"),  # standalone state abbreviation -> 'XX'

    # ---- schemas: longest first ----
    PII("r26"),  # real stage schema 1 -> 'stg_sdh'
    PII("r27"),  # real stage schema 2 -> 'stg_care'
    PII("r28"),  # real standard schema 1 -> 'sdh'

    PII("r29"),  # stage/standard path with real standard schema 2 -> 'stg_care/care'
    PII("r30"),  # real ZIP code -> '48119'
    (r'’ s benefit', "'s benefit"),

    # ---- table / column prefix (vendor fingerprint) ----
    PII("r31"),  # vendor table/column prefix -> 'cv_'

    # ---- source file names ----
    PII("r32"),  # real source file name 1 -> 'demographic_extract'
    PII("r33"),  # real source file name 2 -> 'community_metrics'

    # ---- upstream system ----
    PII("r34"),  # membership-system three-part table -> 'PR_STD.COREMEMBER.CM_SUBS_MASTER'
    PII("r35"),  # membership system (upper) -> 'COREMEMBER'
    PII("r36"),  # membership system (title) -> 'CoreMember'
    PII("r37"),  # membership-system table token -> 'coremember_member'
    PII("r38"),  # subscriber-id column -> 'SUBS_ID'
    PII("r39"),  # group-key predicate (with real value) -> 'GRP_CK = 47'
    PII("r40"),  # group-key column -> 'GRP_CK'

    # ---- people ----
    PII("person_1_full"),
    PII("person_1_bare"),
    PII("person_2_first_last"),
    PII("person_2_last_first"),
    PII("person_3"),
    PII("person_4"),
    PII("person_5"),
    PII("person_6"),
    PII("person_7"),
    PII("person_8"),
    PII("person_9"),

    # ---- 2026-08-06 residual pass ----
    # Fixes from the anonymization audit of the demo pair; recorded here so
    # the rule set keeps describing the fixtures' full provenance. The
    # fixtures were edited directly (xlsx cells / docx byte substitution);
    # the standalone standard-layer schema CELL value ('care' now) cannot be
    # a safe global regex and was a cell-scoped edit only.
    PII("r41"),  # botched surname find-and-replace residue -> 'S. Kavanagh'
    PII("r42"),  # state-program report name -> 'Wellness Report'
    PII("r43"),  # vendor marketing phrase -> 'community and member risk analytics database'
    (r'civicvantage\.com', 'civicvantage.example'),  # retire first-pass vendor domain
    PII("r44"),  # 'Schema Name: <real standard schema 2>' -> 'Schema Name: care'
]


def _sidecar():
    """Import the gitignored sidecar module holding every real-side value,
    or raise loudly. Never partial: a rule set that is silently short by
    one is indistinguishable from a correct one at the call site, and
    produces a half-anonymized region that is not automatically
    recoverable."""
    sidecar = "tools/anonymization_pii_rules_local.py"
    # The sidecar sits beside this file; put that directory on the path so
    # the import works however this module was reached.
    import os
    import sys

    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)

    try:
        import anonymization_pii_rules_local
    except ImportError as exc:
        raise ImportError(
            f"{sidecar} is missing, so the real-side substitution rules "
            "cannot be loaded.\n"
            "It is deliberately gitignored (it holds real names, mailboxes, "
            "a phone number, and client-identifying strings) and is "
            "therefore not restorable from git.\n"
            "Restore it from ~/Desktop/pii_purge_backup_2026-07-26/"
            f"{sidecar} (person/email/phone keys) and re-add the relocated "
            "r01… keys if absent.\n"
            "Refusing to run with an incomplete rule set — a partial pass "
            "leaves the region half-anonymized."
        ) from exc
    return anonymization_pii_rules_local


def _resolve(rules: list) -> list:
    """Splice sidecar rules into their placeholders, in place, or raise."""
    keys = [r.key for r in rules if isinstance(r, PII)]
    if not keys:
        return rules

    PII_RULES = _sidecar().PII_RULES
    missing = [k for k in keys if k not in PII_RULES]
    if missing:
        raise KeyError(
            f"tools/anonymization_pii_rules_local.py is present but does not "
            f"define: {', '.join(missing)}. Every PII(...) placeholder in "
            "RULES must have a matching key. Refusing to run with an "
            "incomplete rule set."
        )

    return [PII_RULES[r.key] if isinstance(r, PII) else r for r in rules]


RULES = _resolve(RULES)

# Requirement IDs are offset by a fixed delta so they stay internally
# consistent but no longer match the client's issue tracker. The delta
# itself lives in the sidecar: together with the committed fake ids it
# would reconstruct the client's real tracker ids.
ID_PREFIXES = ('BR', 'NFR', 'SIR', 'SRQ', 'MDST', 'MDA', 'MDT', 'MDDQ', 'MDV')
ID_DELTA = _sidecar().ID_DELTA

# Workbook-anonymizer column maps, keyed by the REAL source workbook's
# sheet names (a vendor fingerprint), so the dicts live in the sidecar and
# are re-exported here. Semantics unchanged:
#  - DATATYPE_COLS: columns holding DataType, per MAPPING sheet. NEVER
#    perturbed: the derived-vs-reference datatype mismatches are what
#    produce the 194 non-matching cells, so touching these changes the
#    eval result.
#  - SAMPLE_COLS: sample/example-value columns — safe to perturb
#    (source-layout only, never read by evaluate_against_reference).
#  - SHEET_RENAME: real sheet name -> anonymized sheet name.
DATATYPE_COLS = _sidecar().DATATYPE_COLS
SAMPLE_COLS = _sidecar().SAMPLE_COLS
SHEET_RENAME = _sidecar().SHEET_RENAME
