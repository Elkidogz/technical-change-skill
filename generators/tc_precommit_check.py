#!/usr/bin/env python3
"""TC Pre-Commit Advisory — Warns if staged files aren't in any active TC.

Designed to run as a Claude Code PreToolUse hook on Bash commands matching
'git commit'. Never blocks the commit — advisory only.

Behavior:
  - Reads staged files via `git diff --cached --name-only`
  - Checks if any staged file appears in an in_progress TC's files_affected[]
  - If none match: prints a suggestion to create or link a TC
  - Always exits 0 (never blocks)

Usage:
    python tc_precommit_check.py --if-commit [<tc_root_dir>]

Exit codes:
    Always 0 (advisory only, never blocks)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_git(args: list[str], repo_path: str = ".") -> str | None:
    """Run git command, return stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path] + args,
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _find_tc_root(start: str = ".") -> Path | None:
    """Walk up from start to find docs/TC/tc_registry.json."""
    current = Path(start).resolve()
    for parent in [current] + list(current.parents):
        candidate = parent / "docs" / "TC" / "tc_registry.json"
        if candidate.exists():
            return candidate.parent
    return None


def check(tc_root_path: str | None = None) -> None:
    """Advisory check. Prints a message if staged files aren't tracked by any TC."""
    try:
        # Find TC root
        if tc_root_path:
            tc_root = Path(tc_root_path)
        else:
            tc_root = _find_tc_root()
        if not tc_root:
            return  # No TC tracking in this project

        # Detect repo root
        repo_path = "."
        for parent in tc_root.resolve().parents:
            if (parent / ".git").exists():
                repo_path = str(parent)
                break

        # Get staged files
        raw = _run_git(["diff", "--cached", "--name-only"], repo_path)
        if not raw:
            return
        staged = {f.strip().replace("\\", "/") for f in raw.splitlines() if f.strip()}
        if not staged:
            return

        # Load registry
        registry_path = tc_root / "tc_registry.json"
        if not registry_path.exists():
            return
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)

        # Find in_progress TCs and their tracked files
        tracked_files: set[str] = set()
        active_tc_ids: list[str] = []
        for entry in registry.get("records", []):
            if entry.get("status") == "in_progress":
                active_tc_ids.append(entry["tc_id"])
                # Read the full record to get files_affected
                record_path = tc_root / entry.get("path", "") / "tc_record.json"
                if record_path.exists():
                    try:
                        with open(record_path, "r", encoding="utf-8") as f:
                            record = json.load(f)
                        for fa in record.get("files_affected", []):
                            if isinstance(fa, dict) and "path" in fa:
                                tracked_files.add(fa["path"])
                    except (json.JSONDecodeError, OSError):
                        pass

        if not active_tc_ids:
            # No active TCs — suggest creating one
            print(f"[TC advisory] No in-progress TC found. Consider /tc create before committing.")
            return

        # Check if any staged file is tracked
        overlap = staged & tracked_files
        untracked = staged - tracked_files
        if untracked and not overlap:
            print(f"[TC advisory] Staged files not in any active TC ({', '.join(active_tc_ids)}):")
            for f in sorted(list(untracked)[:5]):
                print(f"  {f}")
            if len(untracked) > 5:
                print(f"  ... and {len(untracked) - 5} more")
            print(f"  Consider: /tc link {active_tc_ids[0]} HEAD (after commit)")

    except Exception:
        pass  # Never fail


def main() -> int:
    if "--if-commit" not in sys.argv:
        print("Usage: python tc_precommit_check.py --if-commit [<tc_root_dir>]")
        return 0

    tc_root_path = None
    for i, arg in enumerate(sys.argv):
        if arg not in ("--if-commit",) and not arg.endswith(".py") and i > 0:
            tc_root_path = arg
            break

    check(tc_root_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
