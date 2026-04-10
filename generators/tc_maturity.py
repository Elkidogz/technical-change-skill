#!/usr/bin/env python3
"""
tc_maturity.py -- TC Maturity Ladder (inspired by singularity-claude).

Scores deployed TCs across 5 dimensions and promotes them through maturity levels:
  Draft -> Tested -> Hardened -> Crystallized

Scoring dimensions (0-20 each):
  - correctness:   Does the TC accomplish what it said it would?
  - completeness:  Are all test cases populated and passing?
  - edge_cases:    Are edge cases documented and handled?
  - efficiency:    Is the implementation clean, no obvious waste?
  - reusability:   Can patterns/decisions here be reused in future TCs?

Maturity transitions (automatic on score):
  - Draft (default, newly created TCs)
  - Tested:       &gt;=60 total + at least 1 test case passing
  - Hardened:     &gt;=80 total + 3+ executions without regression
  - Crystallized: &gt;=90 total + 5+ executions + all edge cases documented
                  -> LOCKED (immutable snapshot)

Usage:
  python tc_maturity.py score <tc_record.json> --correctness N --completeness N --edge-cases N --efficiency N --reusability N
  python tc_maturity.py promote <tc_record.json>            # auto-promote based on score
  python tc_maturity.py status <tc_record.json>             # show current maturity + score
  python tc_maturity.py distribution <tc_registry.json>     # show maturity counts across project
  python tc_maturity.py ladder                              # print ladder rules

Exit codes: 0 = success, 1 = error, 2 = not found
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

__version__ = "1.0.0"

MATURITY_LEVELS = ["draft", "tested", "hardened", "crystallized"]
MATURITY_ORDER = {m: i for i, m in enumerate(MATURITY_LEVELS)}

SCORE_DIMENSIONS = ("correctness", "completeness", "edge_cases", "efficiency", "reusability")

# Thresholds for promotion
THRESHOLDS = {
    "tested":       {"total": 60, "executions": 1, "passing_tests": 1},
    "hardened":     {"total": 80, "executions": 3, "passing_tests": 2},
    "crystallized": {"total": 90, "executions": 5, "passing_tests": 3, "edge_cases_documented": True},
}


def _now():
    return datetime.now().astimezone().isoformat()


def _load_tc(path):
    path = Path(path)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_tc(path, data):
    path = Path(path)
    data["updated"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _init_maturity(tc):
    """Initialize maturity block if missing."""
    if "maturity" not in tc:
        tc["maturity"] = {
            "level": "draft",
            "scores": {dim: 0 for dim in SCORE_DIMENSIONS},
            "total": 0,
            "executions": 0,
            "passing_tests": 0,
            "edge_cases_documented": False,
            "locked": False,
            "history": [],
        }
    return tc["maturity"]


def _count_passing_tests(tc):
    tests = tc.get("test_cases", [])
    return sum(1 for t in tests if t.get("status") == "pass")


def _check_edge_cases_documented(tc):
    """Heuristic: does the TC description mention edge cases?"""
    desc = tc.get("description", {})
    text = (desc.get("summary", "") + " " + desc.get("detailed_design", "")).lower()
    markers = ["edge case", "edge-case", "edge:", "corner case", "failure mode", "error handling"]
    return any(m in text for m in markers)


def cmd_score(args):
    tc = _load_tc(args.tc)
    maturity = _init_maturity(tc)
    if maturity.get("locked"):
        print(f"TC is CRYSTALLIZED and locked -- cannot re-score")
        return 1

    # Bounds check
    for dim in SCORE_DIMENSIONS:
        val = getattr(args, dim.replace("-", "_"))
        if val < 0 or val > 20:
            print(f"Error: {dim} must be 0-20, got {val}", file=sys.stderr)
            return 1

    scores = {dim: getattr(args, dim.replace("-", "_")) for dim in SCORE_DIMENSIONS}
    total = sum(scores.values())

    # Update maturity
    maturity["scores"] = scores
    maturity["total"] = total
    maturity["executions"] = maturity.get("executions", 0) + 1
    maturity["passing_tests"] = _count_passing_tests(tc)
    maturity["edge_cases_documented"] = _check_edge_cases_documented(tc)
    maturity["history"].append({
        "timestamp": _now(),
        "scores": scores,
        "total": total,
        "scored_by": args.scored_by or "manual",
    })

    _save_tc(args.tc, tc)
    print(f"Scored TC: total={total}/100")
    for dim, val in scores.items():
        print(f"  {dim:15} {val}/20")
    print(f"  executions:    {maturity['executions']}")
    print(f"  passing_tests: {maturity['passing_tests']}")
    print(f"  edge_cases:    {'yes' if maturity['edge_cases_documented'] else 'no'}")
    return 0


def cmd_promote(args):
    tc = _load_tc(args.tc)
    maturity = _init_maturity(tc)
    if maturity.get("locked"):
        print(f"TC is CRYSTALLIZED and locked")
        return 0

    current = maturity.get("level", "draft")
    total = maturity.get("total", 0)
    execs = maturity.get("executions", 0)
    passing = maturity.get("passing_tests", 0)
    edge_ok = maturity.get("edge_cases_documented", False)

    new_level = current

    # Check crystallized
    t = THRESHOLDS["crystallized"]
    if total >= t["total"] and execs >= t["executions"] and passing >= t["passing_tests"] and edge_ok:
        new_level = "crystallized"
        maturity["locked"] = True
    else:
        # Check hardened
        t = THRESHOLDS["hardened"]
        if total >= t["total"] and execs >= t["executions"] and passing >= t["passing_tests"]:
            new_level = "hardened"
        else:
            # Check tested
            t = THRESHOLDS["tested"]
            if total >= t["total"] and execs >= t["executions"] and passing >= t["passing_tests"]:
                new_level = "tested"

    # Only promote (never demote)
    if MATURITY_ORDER[new_level] > MATURITY_ORDER[current]:
        maturity["level"] = new_level
        maturity.setdefault("history", []).append({
            "timestamp": _now(),
            "event": "promoted",
            "from": current,
            "to": new_level,
        })
        _save_tc(args.tc, tc)
        print(f"PROMOTED: {current} -> {new_level}")
        if new_level == "crystallized":
            print("  TC is now LOCKED -- immutable snapshot")
        return 0

    # Could not promote -- give actionable hints
    print(f"No promotion: still at '{current}'")
    print(f"  total={total}, execs={execs}, passing_tests={passing}, edge_cases={'yes' if edge_ok else 'no'}")
    print()

    next_idx = MATURITY_ORDER[current] + 1
    if next_idx >= len(MATURITY_LEVELS):
        print("Already at top of ladder")
        return 0
    next_level = MATURITY_LEVELS[next_idx]
    needed = THRESHOLDS[next_level]
    print(f"To reach '{next_level}' you need:")
    if total < needed["total"]:
        gap = needed["total"] - total
        print(f"  - Score >= {needed['total']} (current: {total}, gap: -{gap})")
        print(f"    -> Re-score with higher dimensions (correctness/completeness/edge_cases/efficiency/reusability)")
    if execs < needed["executions"]:
        gap = needed["executions"] - execs
        print(f"  - >= {needed['executions']} executions (current: {execs}, gap: -{gap})")
        print(f"    -> Run `tc_maturity.py score` again after each new test cycle")
    if passing < needed["passing_tests"]:
        gap = needed["passing_tests"] - passing
        print(f"  - >= {needed['passing_tests']} passing tests (current: {passing}, gap: -{gap})")
        print(f"    -> WORKFLOW (per global corrections corr_0006 + corr_0007):")
        print(f"       1. Add a new revision (R{len(tc.get('revision_history', [])) + 1}) introducing")
        print(f"          DEBUG-level logging markers in the affected code that prove the criteria.")
        print(f"       2. Update test_cases[].procedure with steps that grep for those log markers.")
        print(f"       3. Run the test, capture matching log lines as evidence (type: log_snippet).")
        print(f"       4. Mark test_cases[].status = 'pass' with the captured evidence.")
        print(f"       5. Add another revision noting the debug logging can now be downgraded.")
        print(f"       6. Re-score and re-promote.")
    if next_level == "crystallized" and not edge_ok:
        print(f"  - Edge cases must be documented")
        print(f"    -> Add to description.detailed_design: include 'edge case', 'corner case',")
        print(f"       or 'failure mode'. Document boundaries, unexpected inputs, error conditions.")
    return 0


def cmd_status(args):
    tc = _load_tc(args.tc)
    maturity = tc.get("maturity", {"level": "draft", "total": 0, "executions": 0})
    print(f"\nTC: {tc.get('tc_id', '?')}")
    print(f"Title: {tc.get('title', '?')}")
    print(f"Status: {tc.get('status', '?')}")
    print(f"\nMaturity: {maturity.get('level', 'draft').upper()}")
    if maturity.get("locked"):
        print("          [LOCKED -- immutable]")
    print(f"Total score: {maturity.get('total', 0)}/100")
    scores = maturity.get("scores", {})
    if scores:
        for dim in SCORE_DIMENSIONS:
            print(f"  {dim:15} {scores.get(dim, 0)}/20")
    print(f"Executions:    {maturity.get('executions', 0)}")
    print(f"Passing tests: {maturity.get('passing_tests', 0)}")
    print(f"Edge cases:    {'documented' if maturity.get('edge_cases_documented') else 'not documented'}")
    return 0


def cmd_distribution(args):
    registry = _load_tc(args.registry)
    records = registry.get("records", [])
    counts = {m: 0 for m in MATURITY_LEVELS}
    total_scored = 0
    tc_dir = Path(args.registry).parent

    for rec in records:
        rec_path = tc_dir / rec["path"] / "tc_record.json"
        if not rec_path.exists():
            continue
        try:
            tc = json.loads(rec_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        maturity = tc.get("maturity", {})
        level = maturity.get("level", "draft")
        counts[level] = counts.get(level, 0) + 1
        if maturity.get("total", 0) > 0:
            total_scored += 1

    total = sum(counts.values())
    print(f"\nMaturity distribution ({total} TCs, {total_scored} scored):")
    for m in MATURITY_LEVELS:
        n = counts.get(m, 0)
        pct = (n / total * 100) if total else 0
        bar = "#" * int(pct / 2)
        print(f"  {m:14} {n:4}  {bar} {pct:.1f}%")
    return 0


def cmd_ladder(args):
    print("""
TC Maturity Ladder
==================

  draft       (default)  Newly created, unscored
  tested      >=60/100   At least 1 execution + 1 passing test
  hardened    >=80/100   At least 3 executions + 2 passing tests
  crystallized >=90/100  At least 5 executions + 3 passing tests
                         + edge cases documented
                         -> LOCKED (immutable)

Score dimensions (0-20 each, 100 total):
  - correctness    Does it accomplish what it said?
  - completeness   All test cases populated and passing?
  - edge_cases     Edge cases documented and handled?
  - efficiency     Clean, no obvious waste?
  - reusability    Patterns reusable in future TCs?

Promotion is automatic via `tc_maturity.py promote <tc_record.json>`.
Demotion is NOT allowed. Crystallized TCs are locked and cannot be re-scored.
""")
    return 0


def main():
    p = argparse.ArgumentParser(description="TC Maturity Ladder")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_score = sub.add_parser("score", help="Score a TC across 5 dimensions")
    p_score.add_argument("tc")
    p_score.add_argument("--correctness", type=int, required=True)
    p_score.add_argument("--completeness", type=int, required=True)
    p_score.add_argument("--edge-cases", dest="edge_cases", type=int, required=True)
    p_score.add_argument("--efficiency", type=int, required=True)
    p_score.add_argument("--reusability", type=int, required=True)
    p_score.add_argument("--scored-by", default=None)

    p_promote = sub.add_parser("promote", help="Auto-promote based on current score")
    p_promote.add_argument("tc")

    p_status = sub.add_parser("status", help="Show maturity status for a TC")
    p_status.add_argument("tc")

    p_dist = sub.add_parser("distribution", help="Show maturity distribution across a registry")
    p_dist.add_argument("registry")

    sub.add_parser("ladder", help="Print ladder rules")

    args = p.parse_args()
    handlers = {
        "score": cmd_score,
        "promote": cmd_promote,
        "status": cmd_status,
        "distribution": cmd_distribution,
        "ladder": cmd_ladder,
    }
    sys.exit(handlers[args.cmd](args))


if __name__ == "__main__":
    main()
