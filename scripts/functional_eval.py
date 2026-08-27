"""Functional correctness of a rendered STTM vs the approved one — by MEANING, not by cell.

Usage:  ~/.virtualenvs/frdsttm/bin/python scripts/functional_eval.py <dir>
  <dir> holds SD_output.xlsx / SD_reference.xlsx / SD_contract.v2.json and the CAQH
  triple: the rendered workbook from a run, the approved STTM, the v2 contract.
  Written 2026-08-27 to answer Arjun's "is it CORRECT, not just similar" — the
  golden-pair eval in 04 is a positional string match on target cells, so a synonym
  and a wrong table cost the same. Not wired into 04; if it earns a place it belongs
  beside evaluate_against_reference. Two of its buckets are parser artifacts on
  purpose-built input (audit-row order; the reference's audit rows are not marked
  in the single-sheet dialect) — read the report section before quoting numbers.

Joins output rows to reference rows on (table, source column, k-th occurrence) and
classifies every difference:

  STRUCTURAL   a column missing / extra, a different target TABLE, a different type
               FAMILY (string vs number vs date), different nullability, a rule on the
               wrong row or missing  -> the STTM would build the wrong thing
  NAMING       a different target COLUMN NAME that is not a pure case/underscore
               variant                 -> the STTM builds the right column under a name
                                          the warehouse may not expect (a rename)
  COSMETIC     description / sample wording, type spelling within a family, case
               and whitespace           -> a reviewer would not change the mapping
Plus: does every FRD rule the contract carries land somewhere in the workbook.
"""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from frdsttm.reference_workbooks import parse_reference_workbook  # noqa: E402

HERE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent  # dir with <NAME>_output.xlsx, <NAME>_reference.xlsx, <NAME>_contract.v2.json

def norm(s):  return re.sub(r"[^a-z0-9]", "", str(s or "").lower())
def nl(s):    return re.sub(r"\s+", " ", str(s or "").strip().lower())

def family(t):
    t = nl(t)
    if not t: return ""
    if re.match(r"(decimal|numeric\s*\(|float|double|money|number\s*\()", t): return "decimal"
    if re.match(r"(int|bigint|smallint|tinyint|numeric$|number$|integer)", t): return "int"
    if re.match(r"(date|datetime|timestamp|time)", t): return "date"
    if re.match(r"(bool|bit)", t): return "bool"
    return "string"   # string, varchar, char, text, nvarchar, alpha numeric ...

def index(feed):
    """{(norm source col, k): field} with k = occurrence index (HDR/DTL/TRL repeat names)."""
    seen, out = Counter(), {}
    for f, t in zip(feed["fields"], feed["ref_targets"]):   # targets are a parallel list
        f = dict(f, stage=t.get("stage") or {}, standard=t.get("standard") or {})
        key = norm(f["source_column"]); k = seen[key]; seen[key] += 1
        out[(key, k)] = f
    return out

def compare(name, out_path, ref_path, contract_path):
    out, ref = parse_reference_workbook(out_path), parse_reference_workbook(ref_path)
    contract = json.loads(Path(contract_path).read_text())
    # table alignment: by normalised table key, else by order (CAQH output/ref use different keys)
    ok, rk = list(out["feeds"]), list(ref["feeds"])
    pairs = [(k, k) for k in ok if k in ref["feeds"]] or list(zip(ok, rk))
    buckets = defaultdict(list); n_rows = 0
    for ko, kr in pairs:
        O, R = out["feeds"][ko], ref["feeds"][kr]
        oi, ri = index(O), index(R)
        for key, rf in ri.items():
            n_rows += 1
            of = oi.get(key)
            if of is None:
                if rf.get("audit"): buckets["structural: audit row missing"].append(rf["source_column"]); continue
                buckets["structural: column missing"].append(rf["source_column"]); continue
            src = rf["source_column"]
            # source-side type/description/sample/flags
            if family(of["datatype"]) != family(rf["datatype"]):
                buckets["structural: source type family"].append(f"{src}: {of['datatype']!r} vs {rf['datatype']!r}")
            elif nl(of["datatype"]) != nl(rf["datatype"]):
                buckets["cosmetic: type spelling"].append(f"{src}: {of['datatype']!r} vs {rf['datatype']!r}")
            if bool(of.get("nullable")) != bool(rf.get("nullable")) or bool(of.get("mandatory")) != bool(rf.get("mandatory")):
                buckets["structural: nullability / mandatory"].append(
                    f"{src}: out nullable={of.get('nullable')} mand={of.get('mandatory')} | ref nullable={rf.get('nullable')} mand={rf.get('mandatory')}")
            if bool(of.get("phi")) != bool(rf.get("phi")):
                buckets["governance: PHI flag"].append(f"{src}: out={of.get('phi')} ref={rf.get('phi')}")
            if nl(of.get("description")) != nl(rf.get("description")):
                (buckets["cosmetic: description missing"] if not nl(of.get("description")) else buckets["cosmetic: description wording"]).append(src)
            if nl(of.get("sample")) != nl(rf.get("sample")):
                buckets["cosmetic: sample value"].append(src)
            oc, rc = nl(of.get("comment")), nl(rf.get("comment"))
            if bool(oc) != bool(rc):
                buckets["structural: rule on row (present in one only)"].append(f"{src}: out={oc[:50]!r} ref={rc[:50]!r}")
            elif oc != rc:
                buckets["cosmetic: rule wording"].append(src)
            # target side
            for layer in ("stage", "standard"):
                ot, rt = of[layer], rf[layer]
                for attr in ("schema", "table"):
                    if nl(rt.get(attr)) and nl(ot.get(attr)) != nl(rt.get(attr)):
                        buckets[f"structural: {layer} {attr}"].append(f"{src}: {ot.get(attr)!r} vs {rt.get(attr)!r}")
                if nl(rt.get("column")):
                    if not nl(ot.get("column")):
                        buckets[f"structural: {layer} column blank"].append(src)
                    elif norm(ot["column"]) == norm(rt["column"]):
                        if nl(ot["column"]) != nl(rt["column"]): buckets[f"cosmetic: {layer} column case/underscore"].append(src)
                    else:
                        buckets[f"naming: {layer} column differs"].append(f"{src}: {ot['column']!r} vs {rt['column']!r}")
                if nl(rt.get("datatype")):
                    if family(ot.get("datatype")) != family(rt.get("datatype")):
                        buckets[f"structural: {layer} type family"].append(f"{src}: {ot.get('datatype')!r} vs {rt.get('datatype')!r}")
                    elif nl(ot.get("datatype")) != nl(rt.get("datatype")):
                        buckets[f"cosmetic: {layer} type spelling"].append(f"{src}: {ot.get('datatype')!r} vs {rt.get('datatype')!r}")
        for key, of in oi.items():
            if key not in ri:
                buckets["structural: audit row extra" if of.get("audit") else "structural: column extra"].append(of["source_column"])
    # FRD rules: does every rule the contract carries land in the workbook?
    placed = contract.get("_provenance", {}).get("rule_placement") or []
    rules = [(f["feed_name"], r) for f in contract["feeds"] for r in (f.get("validation_rules") or []) + ([f["recycle_rule"]] if f.get("recycle_rule") else [])]
    workbook_text = " ".join(nl(f.get("comment")) for fd in out["feeds"].values() for f in fd["fields"])
    meta_text = nl(json.dumps(out.get("meta", {})))
    missing_rules = [r[:80] for _, r in rules if nl(r)[:40] not in workbook_text and nl(r)[:40] not in meta_text]
    return n_rows, buckets, rules, missing_rules, pairs

def report(name, out_path, ref_path, contract_path):
    n, b, rules, missing_rules, pairs = compare(name, out_path, ref_path, contract_path)
    print(f"\n{'='*78}\n{name}: {n} reference rows across {len(pairs)} table(s)")
    def total(prefix): return sum(len(v) for k, v in b.items() if k.startswith(prefix))
    print(f"  STRUCTURAL differences : {total('structural')}")
    print(f"  GOVERNANCE (PHI flag)  : {total('governance')}")
    print(f"  NAMING (target column) : {total('naming')}")
    print(f"  COSMETIC               : {total('cosmetic')}")
    print(f"  FRD rules in contract  : {len(rules)}; not found anywhere in the workbook: {len(missing_rules)}")
    for k in sorted(b, key=lambda k: (not k.startswith('structural'), not k.startswith('governance'), not k.startswith('naming'), k)):
        v = b[k]; print(f"    {k:48} {len(v):4}   e.g. {v[0][:90]}" + (f" | {v[1][:60]}" if len(v) > 1 and len(v[0]) < 40 else ""))
    for r in missing_rules[:6]: print("    missing rule:", r)
    return {"rows": n, "structural": total("structural"), "governance": total("governance"), "naming": total("naming"), "cosmetic": total("cosmetic"), "rules": len(rules), "rules_missing": len(missing_rules), "buckets": {k: v for k, v in b.items()}}

if __name__ == "__main__":
    res = {}
    for name in ("SD", "CAQH"):
        res[name] = report(name, HERE / f"{name}_output.xlsx", HERE / f"{name}_reference.xlsx", HERE / f"{name}_contract.v2.json")
    (HERE / "functional_eval.json").write_text(json.dumps(res, indent=1, default=str))
