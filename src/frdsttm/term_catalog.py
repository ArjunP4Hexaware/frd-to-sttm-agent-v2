"""
frdsttm.term_catalog — the warehouse's own column vocabulary, harvested once.

WHY THIS EXISTS, AND WHY IT IS NOT "READING AN STTM"
---------------------------------------------------
Measured 2026-08-27 on the CAQH feed's 115 columns, `databricks-claude-opus-5`:

    condition                                accuracy    high-confidence band
    name alone                                0 / 115     0 of 71 correct
    + told the warehouse is Facets            0 / 115     0 of 6
    + few-shot from the 8 header rows        16 / 101     1 of 1
    + A REAL CATALOG TO CHOOSE FROM          95 / 101    92 of 92  (100%)

Generating a target column name is not merely weak, it is ZERO — and in the
blind condition 71 of 115 wrong answers came back marked HIGH confidence.
Matching against a catalog that actually exists is 94%, with a high band that
was right 92 times out of 92. The model cannot invent the vocabulary; it is
excellent at matching to one.

So the vocabulary has to come from somewhere, and until someone grants
`information_schema` read over PR_STD / PR_DLK, the only source in existence
is the approved STTMs. CAQH's 115 source→target pairs ARE a catalog.

The restructure (Arjun, 2026-08-27) is what makes using them legitimate. A RUN
must ingest exactly two documents: the FRD and the VDD. It must never open an
STTM, because a workbook that contains a feed's mapping can hand that feed its
own answers back — the self-circling loop that made every accuracy figure
meaningless. So the pairs are harvested AT SYNC TIME into a vocabulary, and a
run reads the VOCABULARY, not a workbook:

    sync   approved STTMs  ──harvest──▶  term catalog (in the corpus index)
    run    FRD + VDD + the catalog  ──▶  draft STTM

The catalog has the same standing as `contracts/naming_standards.json`:
config the agent carries, not a document anyone hands over. And it is
structurally incapable of leaking a feed its own mapping, because a vocabulary
is `source term → target term`, not `feed → mapping`. It cannot contain the
answer for a feed it has never seen.

TWO RULES THAT ARE NOT OPTIONAL
-------------------------------
* **A feed's own workbook is excluded when that feed is scored.** Not a flag —
  `EXCLUDE_OWN_REFERENCE` was a flag and it got set to 0 for a demo, which is
  exactly how the self-referential accuracy figure happened. `lookup` takes
  `exclude` and 04 always passes the feed's own paired workbook.
* **A conflicted term is never resolved silently.** Where two workbooks map
  the same source term to different targets, `lookup` returns BOTH and the
  caller gates. Picking the more frequent one would be inventing a convention
  the client never stated.
"""

from __future__ import annotations

import re
from collections import Counter

#: Layers whose column names are worth harvesting. Both are the client's own
#: naming; keeping them apart matters because a feed can rename between them.
LAYERS = ("stage", "standard")

#: Source names that are not vendor fields. Audit rows carry these — they are
#: the CLIENT's columns (SRC_FILE_NAME, REC_CREATION_TIME, ...) derived by the
#: pipeline, and the standards contract already owns them. Harvesting them
#: would put a client convention into a vocabulary meant for vendor terms.
_NOT_A_SOURCE = {"", "na", "n/a", "-", "none"}


def normalise(term: str | None) -> str:
    """Matching identity for a source column name.

    Lower-cased, punctuation collapsed to single spaces. "Member ID",
    "member_id" and "MEMBER  ID" are the same term; "Previous Member ID" is
    not. Deliberately conservative — a looser key (dropping stop-words, say)
    would merge "Payer ID" with "National Payer ID", which really do map to
    different targets on the CAQH feed.
    """
    if not term:
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(term).lower()).split())


def harvest_workbook(parsed: dict, workbook: str) -> list[dict]:
    """Every (source term → target column) pair one approved STTM asserts.

    ``parsed`` is a `frdsttm.reference_workbooks.parse_reference_workbook`
    result. Audit rows and identity-less rows are skipped; everything else
    becomes one entry per layer that names a column.
    """
    out: list[dict] = []
    for feed_key, feed in (parsed.get("feeds") or {}).items():
        fields = feed.get("fields") or []
        targets = feed.get("ref_targets") or []
        for field, target in zip(fields, targets):
            source = field.get("source_column")
            if field.get("audit") or normalise(source) in _NOT_A_SOURCE:
                continue
            for layer in LAYERS:
                column = ((target or {}).get(layer) or {}).get("column")
                if not column or normalise(column) in _NOT_A_SOURCE:
                    continue
                out.append({
                    "term": normalise(source),
                    "source": str(source).strip(),
                    "target": str(column).strip(),
                    "layer": layer,
                    "table": ((target or {}).get(layer) or {}).get("table"),
                    "workbook": workbook,
                    "feed": feed_key,
                })
    return out


def build_catalog(parsed_by_workbook: dict[str, dict]) -> dict:
    """{normalised term: [entry, ...]} over every approved workbook.

    Entries keep their `workbook`, which is what makes per-run exclusion
    possible. Deliberately NOT deduplicated across workbooks: two workbooks
    agreeing is evidence, and `lookup` counts it.
    """
    catalog: dict[str, list[dict]] = {}
    for workbook, parsed in sorted(parsed_by_workbook.items()):
        for entry in harvest_workbook(parsed, workbook):
            catalog.setdefault(entry["term"], []).append(entry)
    return catalog


def catalog_stats(catalog: dict) -> dict:
    """Summary for the corpus index and the app — never the entries."""
    workbooks = {e["workbook"] for v in catalog.values() for e in v}
    identity = sum(1 for v in catalog.values() for e in v
                   if normalise(e["source"]) == normalise(e["target"]))
    total = sum(len(v) for v in catalog.values())
    return {
        "n_terms": len(catalog),
        "n_entries": total,
        "n_workbooks": len(workbooks),
        "n_identity_pairs": identity,
        "workbooks": sorted(workbooks),
    }


def lookup(source: str, catalog: dict, layer: str = "stage",
           *, exclude: tuple[str, ...] | set[str] = ()) -> dict:
    """What the vocabulary says this source term maps to, for one layer.

    Returns::

        {"verdict": "hit" | "conflict" | "miss",
         "target": str | None,          # only on "hit"
         "candidates": [{"target", "n", "workbooks"}...],
         "excluded": int}               # entries dropped by `exclude`

    * ``hit`` — every surviving entry agrees. Safe to fill; the caller records
      provenance ``term_catalog``.
    * ``conflict`` — the workbooks disagree. The caller GATES with the
      candidates. Choosing the most frequent would be inventing a rule.
    * ``identity_only`` — every workbook that knows this term maps it to
      ITSELF. That is the `as_is` convention showing through, not a
      vocabulary, and it must not be reported as a catalog hit: a feed that
      renames its columns would be handed the source name with a provenance
      claiming the warehouse asked for it.
    * ``miss`` — the vocabulary has never seen this term. The caller falls
      back to the source name (the as_is convention, measured 399/399 on the
      one feed that uses it) and records that it did.

    ``exclude`` is the run's own paired workbook, and passing it is not
    optional — see the module docstring.
    """
    excluded_names = {str(x) for x in exclude}
    entries = catalog.get(normalise(source), [])
    kept = [e for e in entries
            if e["layer"] == layer and e["workbook"] not in excluded_names]
    dropped = sum(1 for e in entries
                  if e["layer"] == layer and e["workbook"] in excluded_names)

    if not kept:
        return {"verdict": "miss", "target": None, "candidates": [], "excluded": dropped}

    # An IDENTITY pair (source == target) is evidence of the `as_is`
    # convention, not of a vocabulary. Treating it as a catalog HIT was
    # actively harmful, found 2026-08-27 while regenerating CAQH with its own
    # workbook excluded: SD maps "Member ID" -> member_id, so the catalog
    # confidently filled `member_id` for a feed whose real answer is
    # TPL_MEME_ID. Two fills, both wrong, both marked as catalog-sourced.
    # Identity pairs now fall through to the same as_is fallback a miss takes
    # — the OUTPUT is identical, but the provenance stops claiming a
    # vocabulary said so, and nothing is gated on a non-fact.
    if all(normalise(e["target"]) == normalise(source) for e in kept):
        return {"verdict": "identity_only", "target": None,
                "candidates": [], "excluded": dropped}

    counts = Counter(e["target"] for e in kept)
    candidates = [
        {"target": t, "n": n,
         "workbooks": sorted({e["workbook"] for e in kept if e["target"] == t})}
        for t, n in counts.most_common()
    ]
    if len(counts) == 1:
        return {"verdict": "hit", "target": candidates[0]["target"],
                "candidates": candidates, "excluded": dropped}
    return {"verdict": "conflict", "target": None,
            "candidates": candidates, "excluded": dropped}


def serialize(catalog: dict) -> dict:
    """The catalog as it goes into corpus_index.json.

    Stored as `{term: [[target, layer, workbook, table], ...]}` — positional
    rather than per-key dicts, because the index is loaded on every request
    and one real workbook already contributes 230 entries.
    """
    return {term: [[e["target"], e["layer"], e["workbook"], e["table"]] for e in entries]
            for term, entries in sorted(catalog.items())}


def deserialize(raw: dict) -> dict:
    """Inverse of :func:`serialize`. `source` is not round-tripped — matching
    only ever uses the normalised term, and the display name is on the FRD."""
    return {
        term: [{"term": term, "source": term, "target": t, "layer": layer,
                "workbook": wb, "table": table, "feed": None}
               for t, layer, wb, table in entries]
        for term, entries in (raw or {}).items()
    }
