"""Substitution rules for anonymizing the MIDS fixtures.

Rules whose *patterns* are personal data -- individuals' mailboxes and
names, a direct phone number, the client SharePoint host -- are not stored
here. They live in the gitignored sidecar
`tools/anonymization_pii_rules_local.py` and are spliced back in at import.

Order is load-bearing (longest / most-specific first), so each relocated
rule leaves a `PII("<key>")` placeholder occupying its exact ordinal slot.
`_resolve()` swaps each placeholder for the sidecar entry of the same key
without moving anything: position is structural here, never reconstructed
at load time. A missing sidecar or a missing key raises -- a partial rule
set would produce exactly the half-mapped region that
`apply_anonymization_to_mock_spec.classify()` exists to refuse.
"""


class PII:
    """Placeholder holding an ordinal slot for a rule kept out of git."""

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
    (r'sociallydetermined\.com', 'civicvantage.com'),
    (r'amerihealthcaritas\.com', 'meridianchp.com'),
    PII("phone_direct"),

    # Normalise any residual SharePoint URL tail (doc GUIDs, query strings).
    # The original URLs contain literal spaces, so a single non-greedy URL
    # pattern cannot consume them in one pass.
    (r'(meridianchp\.sharepoint\.com/sites/DataOffice/ProjectDocs)[^<"\']*', r'\1'),
    (r'STTM-Medicare Expansion-MIDS-Social Determine', 'STTM-Medicare Expansion-OHDS-Social Factors'),
    (r'Social Determine\b', 'Social Factors'),
    (r'Blueprint Software Systems, Inc', 'Meridian Community Health'),
    (r'\bBlueprint\b', 'Requirements Hub'),

    # ---- org / vendor names ----
    (r'AmeriHealth Caritas', 'Meridian Community Health'),
    (r'AmeriHealth', 'Meridian'),
    (r'\bACFC\b', 'MCHP'),
    (r'Socially Determined', 'Civic Vantage'),
    # snake_case form used in ADLS landing paths. Deliberately anchored to
    # 'socially_determined' so it cannot touch the SDOH domain columns
    # (social_connectedness_*, social_detachment_index, ...), which are
    # generic terminology and are eval-bearing.
    (r'socially_determined', 'civic_vantage'),
    (r"Socially Determined\u2019 ?s", "Civic Vantage's"),
    (r'\bSD\b', 'CV'),

    # ---- project identity (MUST precede the bare MIDS rule) ----
    (r'Medicare Expansion-MIDS-Social Determine', 'Medicare Expansion-OHDS-Social Factors'),
    (r'Medicare Expansion-MIDS - Socially Determined', 'Medicare Expansion-OHDS - Social Factors'),
    (r'\b1003866\b', '1007412'),

    # ---- program / geography ----
    (r'\bMIDS\b', 'OHDS'),
    (r'\bMichigan\b', 'Ohio'),
    (r'\bMI state\b', 'OH state'),
    (r'\bMI Risk\b', 'OH Risk'),
    (r'MI-Monthly', 'OH-Monthly'),
    (r'_amer_MI_', '_mrdn_OH_'),
    (r'_amer_mi_', '_mrdn_oh_'),

    # ---- product / brand names ----
    # Second-pass renames: these retire the first-pass stand-ins (which read
    # as a plausible real health plan) in favour of obviously-synthetic
    # names. Patterns here therefore match the *output* of earlier rules, not
    # the client source -- so this block MUST sit after everything that
    # produces them: 'amerihealthcaritas.com' -> 'meridianchp.com', the
    # SharePoint-tail normaliser, 'Blueprint Software Systems, Inc' /
    # 'AmeriHealth Caritas' -> 'Meridian Community Health', 'AmeriHealth' ->
    # 'Meridian', and '\bACFC\b' -> 'MCHP'. 'VIP Care MI DSNP' is the one
    # exception: it is verbatim client text no earlier rule ever touched.
    #
    # It must also sit after the '\bMI state\b' / '\bMI Risk\b' / 'MI-Monthly'
    # rules directly above -- the standalone-MI rule that closes this block
    # would otherwise rewrite their MI first and strand them.
    #
    # NOTE: the 2026-07-26 pass edited demo_frd.docx by hand (byte-level
    # substitution over the already-anonymized fixture). This mapping is NOT being
    # re-applied to the client original; these entries are recorded so the
    # rule set still describes the fixture's full provenance.
    (r'VIP Care MI DSNP', 'Sample Dual-Eligible Program'),
    (r'Meridian Community Health', 'Example Health Plan'),
    (r'meridianchp\.sharepoint\.com', 'examplehealthplan.example'),
    (r'meridianchp\.com', 'examplehealthplan.example'),
    (r'meridianchp', 'examplehealthplan.example'),
    (r'\bMCHP\b', 'EX-HP'),
    (r'\bMeridian\b', 'Example Health Plan'),
    # Last in the block: only the MI occurrences none of the above consumed.
    (r'(?<![A-Za-z0-9_])MI(?![A-Za-z0-9_])', 'XX'),

    # ---- project identity ----

    # ---- schemas: longest first ----
    (r'\bstg_sdoh\b', 'stg_sdh'),
    (r'\bstg_cm\b', 'stg_care'),
    (r'\bsdoh\b', 'sdh'),

    (r'stg_care/cm\b', 'stg_care/care'),
    (r'\b33732\b', '48119'),
    (r'\u2019 s benefit', "'s benefit"),

    # ---- table / column prefix (vendor fingerprint) ----
    (r'\bsd_', 'cv_'),

    # ---- source file names ----
    (r'demographics_package', 'demographic_extract'),
    (r'analytics_package', 'community_metrics'),

    # ---- upstream system ----
    (r'PR_STD\.FACETS\.CMC_SBSB_SUBSC', 'PR_STD.COREMEMBER.CM_SUBS_MASTER'),
    (r'\bFACETS\b', 'COREMEMBER'),
    (r'\bFacets\b', 'CoreMember'),
    (r'\bfacets_member\b', 'coremember_member'),
    (r'\bSBSB_ID\b', 'SUBS_ID'),
    (r'GRGR_CK = 31', 'GRP_CK = 47'),
    (r'\bGRGR_CK\b', 'GRP_CK'),

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
]


def _resolve(rules: list) -> list:
    """Splice sidecar rules into their placeholders, in place, or raise.

    Never drops a placeholder it cannot fill: a rule set that is silently
    short by one is indistinguishable from a correct one at the call site,
    and produces a half-anonymized region that is not automatically
    recoverable.
    """
    keys = [r.key for r in rules if isinstance(r, PII)]
    if not keys:
        return rules

    sidecar = "tools/anonymization_pii_rules_local.py"
    # The sidecar sits beside this file; put that directory on the path so
    # the import works however this module was reached.
    import os
    import sys

    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)

    try:
        from anonymization_pii_rules_local import PII_RULES
    except ImportError as exc:
        raise ImportError(
            f"{sidecar} is missing, so {len(keys)} PII-bearing substitution "
            f"rules cannot be loaded: {', '.join(keys)}.\n"
            "It is deliberately gitignored (it holds real names, mailboxes "
            "and a phone number) and is therefore not restorable from git.\n"
            "Restore it from ~/Desktop/pii_purge_backup_2026-07-26/"
            f"{sidecar}\n"
            "Refusing to run with an incomplete rule set -- a partial pass "
            "leaves the region half-anonymized."
        ) from exc

    missing = [k for k in keys if k not in PII_RULES]
    if missing:
        raise KeyError(
            f"{sidecar} is present but does not define: {', '.join(missing)}. "
            f"Every PII(...) placeholder in RULES must have a matching key. "
            "Refusing to run with an incomplete rule set."
        )

    return [PII_RULES[r.key] if isinstance(r, PII) else r for r in rules]


RULES = _resolve(RULES)

# Requirement IDs are offset by a fixed delta so they stay internally
# consistent but no longer match the client's issue tracker.
ID_PREFIXES = ('BR', 'NFR', 'SIR', 'SRQ', 'MDST', 'MDA', 'MDT', 'MDDQ', 'MDV')
ID_DELTA = 4137

# Columns holding DataType, per MAPPING sheet. NEVER perturbed: the
# derived-vs-reference datatype mismatches are what produce the 194
# non-matching cells, so touching these changes the eval result.
DATATYPE_COLS = {
    'MAPPING-SD_COMMUNITY_DEMOGRAPHI': (10, 15),
    'MAPPING-SD_COMMUNITY_RISK':       (10, 15),
    'MAPPING-SD_INDIV_RISK':           (11, 16),
}

# Sample/example-value columns — safe to perturb (source-layout only,
# never read by evaluate_against_reference).
SAMPLE_COLS = {
    'MAPPING-SD_COMMUNITY_DEMOGRAPHI': (3,),
    'MAPPING-SD_COMMUNITY_RISK':       (2,),
    'MAPPING-SD_INDIV_RISK':           (3,),
}

SHEET_RENAME = {
    'MAPPING-SD_COMMUNITY_DEMOGRAPHI': 'MAPPING-CV_COMMUNITY_DEMOGRAPHI',
    'MAPPING-SD_COMMUNITY_RISK':       'MAPPING-CV_COMMUNITY_RISK',
    'MAPPING-SD_INDIV_RISK':           'MAPPING-CV_INDIV_RISK',
}
