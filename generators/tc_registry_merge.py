#!/usr/bin/env python3
"""TC Registry 3-Way Merge Driver — Resolves tc_registry.json conflicts.

Custom git merge driver that merges tc_registry.json intelligently:
  - records[]: union by tc_id (latest `updated` wins for duplicates)
  - next_tc_number: max of both sides
  - statistics: recomputed from merged records
  - project_name, created: taken from "ours" (left)

Register with:
    git config merge.tc-registry.driver 'python "<path>/tc_registry_merge.py" %O %A %B'

And add to .gitattributes:
    docs/TC/tc_registry.json merge=tc-registry

Usage:
    python tc_registry_merge.py <base> <ours> <theirs>

    base:   Common ancestor (git %O)
    ours:   Current branch version (git %A) — output is written here
    theirs: Other branch version (git %B)

Exit codes:
    0 = MERGE SUCCEEDED (result written to %A)
    1 = MERGE CONFLICT (could not auto-resolve, manual resolution needed)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT / "validators"))

from validate_tc import compute_registry_statistics  # noqa: E402


def _load_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"tc-registry merge: cannot read {path}: {e}", file=sys.stderr)
        return None


def _newer(a: str | None, b: str | None) -> str:
    """Return the more recent ISO 8601 timestamp."""
    if not a:
        return b or ""
    if not b:
        return a
    try:
        da = datetime.fromisoformat(a)
        db = datetime.fromisoformat(b)
        return a if da >= db else b
    except ValueError:
        return a


def merge_registries(base: dict, ours: dict, theirs: dict) -> dict:
    """3-way merge of tc_registry.json data.

    Strategy:
      - Scalars: ours wins (project_name, created)
      - next_tc_number: max(ours, theirs)
      - records[]: union by tc_id; for duplicates, latest `updated` wins
      - statistics: recomputed from merged records
    """
    # Build record indexes
    base_records = {r["tc_id"]: r for r in base.get("records", []) if "tc_id" in r}
    ours_records = {r["tc_id"]: r for r in ours.get("records", []) if "tc_id" in r}
    theirs_records = {r["tc_id"]: r for r in theirs.get("records", []) if "tc_id" in r}

    # Union all tc_ids
    all_ids = set(ours_records.keys()) | set(theirs_records.keys())

    merged_records: list[dict] = []
    for tc_id in sorted(all_ids):
        ours_rec = ours_records.get(tc_id)
        theirs_rec = theirs_records.get(tc_id)

        if ours_rec and theirs_rec:
            # Both sides have this TC — pick the more recently updated one
            ours_updated = ours_rec.get("updated", "")
            theirs_updated = theirs_rec.get("updated", "")
            winner = ours_rec if _newer(ours_updated, theirs_updated) == ours_updated else theirs_rec
            merged_records.append(winner)
        elif ours_rec:
            merged_records.append(ours_rec)
        elif theirs_rec:
            merged_records.append(theirs_rec)

    # next_tc_number: max of both
    next_num = max(
        ours.get("next_tc_number", 1),
        theirs.get("next_tc_number", 1),
    )

    # Recompute statistics
    stats = compute_registry_statistics(merged_records)

    # Build merged registry (scalars from ours)
    now = datetime.now(timezone.utc).astimezone().isoformat()
    merged = {
        "project_name": ours.get("project_name", theirs.get("project_name", "")),
        "created": ours.get("created", theirs.get("created", now)),
        "updated": now,
        "next_tc_number": next_num,
        "records": merged_records,
        "statistics": stats,
    }

    return merged


def main() -> int:
    if len(sys.argv) < 4:
        print("Usage: python tc_registry_merge.py <base> <ours> <theirs>")
        print()
        print("Register as git merge driver:")
        print('  git config merge.tc-registry.driver \'python "<path>/tc_registry_merge.py" %O %A %B\'')
        print("  echo 'docs/TC/tc_registry.json merge=tc-registry' >> .gitattributes")
        return 1

    base_path, ours_path, theirs_path = sys.argv[1], sys.argv[2], sys.argv[3]

    base = _load_json(base_path)
    ours = _load_json(ours_path)
    theirs = _load_json(theirs_path)

    if base is None or ours is None or theirs is None:
        print("tc-registry merge: cannot load one or more inputs", file=sys.stderr)
        return 1

    try:
        merged = merge_registries(base, ours, theirs)
    except Exception as e:
        print(f"tc-registry merge: error during merge: {e}", file=sys.stderr)
        return 1

    # Write result to %A (ours)
    try:
        with open(ours_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"tc-registry merge: cannot write result: {e}", file=sys.stderr)
        return 1

    # Summary
    ours_count = len({r["tc_id"] for r in ours.get("records", []) if "tc_id" in r})
    theirs_count = len({r["tc_id"] for r in theirs.get("records", []) if "tc_id" in r})
    merged_count = len(merged["records"])
    print(f"tc-registry merge: {ours_count} (ours) + {theirs_count} (theirs) → {merged_count} records", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
