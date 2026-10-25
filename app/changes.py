"""
What changed between two versions of the same submission?

This is the resubmission story in one structure: what the vendor changed, which checks
that fixed, which checks became possible, and whether anything got worse.
"""
from __future__ import annotations


def form_snapshot(sub: dict) -> dict:
    skip = {"documents", "version", "resubmission_of"}
    return {k: v for k, v in sub.items() if not k.startswith("_") and k not in skip}


def _flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def diff_form(old: dict, new: dict) -> list[dict]:
    a, b = _flatten(old), _flatten(new)
    return [{"field": k, "old": a.get(k), "new": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def compare_versions(prev: dict, new: dict) -> dict:
    p = {(r["rule_id"], r["name"]): r for r in prev["rules"]}
    n = {(r["rule_id"], r["name"]): r for r in new["rules"]}
    resolved, unlocked, regressed, still_failing = [], [], [], []
    for key, nr in n.items():
        pr = p.get(key)
        if not pr:
            continue
        pair = (pr["status"], nr["status"])
        row = {"rule_id": key[0], "name": key[1], "was": pr["status"], "now": nr["status"], "detail": nr["detail"]}
        if pair[0] == "FAIL" and pair[1] in ("PASS", "WARN"):
            resolved.append(row)
        elif pair[0] == "SKIP" and pair[1] != "SKIP":
            unlocked.append(row)
        elif pair[1] == "FAIL" and pair[0] != "FAIL":
            regressed.append(row)
        elif pair == ("FAIL", "FAIL"):
            still_failing.append(row)

    ph, nh = prev.get("doc_hashes", {}), new.get("doc_hashes", {})
    changed = sorted(s for s in nh if ph.get(s) != nh[s])
    unchanged = sorted(s for s in nh if ph.get(s) == nh[s])
    return {
        "from_version": prev.get("version", 1), "to_version": new.get("version", 1),
        "status_before": prev["status"], "status_after": new["status"],
        "documents_changed": changed, "documents_unchanged": unchanged,
        "form_fields_changed": diff_form(prev.get("form"), new.get("form")),
        "resolved": resolved, "newly_checkable": unlocked, "regressed": regressed,
        "still_failing": still_failing,
    }
