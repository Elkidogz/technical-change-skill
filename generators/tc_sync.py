#!/usr/bin/env python3
"""TC Sync — Refresh PR metadata for all TCs with linked PRs (online opt-in).

Iterates over all TC records, finds those with git.remotes[].pr populated,
and refreshes PR state, review decision, and merge commit SHA via gh/glab CLI.

Graceful degradation: network failures or missing CLIs produce warnings, not
errors. TC records are unchanged when sync fails.

Usage:
    python tc_sync.py <tc_root_dir> [<tc_id>]

    tc_root_dir:  Path to docs/TC/
    tc_id:        Optional — sync only this TC (default: all TCs with PRs)

Exit codes:
    0 = SUCCESS (at least one TC synced)
    1 = NO TCS TO SYNC or CLI UNAVAILABLE
    2 = FILE ERROR
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT / "validators"))

from validate_tc import validate_tc_record  # noqa: E402


def _run_cmd(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "Command timed out"


def _sync_github_pr(pr_number: int) -> dict | None:
    """Refresh PR info from GitHub via gh CLI."""
    rc, out, err = _run_cmd([
        "gh", "pr", "view", str(pr_number), "--json",
        "number,url,state,mergeCommit,reviewDecision,isDraft",
    ])
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None

    state_map = {"OPEN": "open", "CLOSED": "closed", "MERGED": "merged"}
    raw_state = data.get("state", "").upper()
    pr_state = "draft" if raw_state == "OPEN" and data.get("isDraft") else state_map.get(raw_state, "open")

    review_map = {
        "APPROVED": "approved",
        "CHANGES_REQUESTED": "changes_requested",
        "REVIEW_REQUIRED": "review_required",
    }
    review = review_map.get(data.get("reviewDecision", ""), None)
    merge_commit = data.get("mergeCommit")
    merged_sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None

    return {
        "number": data.get("number"),
        "url": data.get("url"),
        "state": pr_state,
        "merged_sha": merged_sha,
        "review_decision": review,
        "last_synced": datetime.now(timezone.utc).astimezone().isoformat(),
    }


def _sync_gitlab_mr(mr_number: int) -> dict | None:
    """Refresh MR info from GitLab via glab CLI."""
    rc, out, err = _run_cmd(["glab", "mr", "view", str(mr_number), "--output", "json"])
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None

    state_map = {"opened": "open", "closed": "closed", "merged": "merged"}
    return {
        "number": data.get("iid"),
        "url": data.get("web_url"),
        "state": state_map.get(data.get("state", ""), "open"),
        "merged_sha": data.get("merge_commit_sha"),
        "review_decision": None,
        "last_synced": datetime.now(timezone.utc).astimezone().isoformat(),
    }


def sync_tc(record_path: Path) -> tuple[bool, str]:
    """Sync a single TC's PR metadata. Returns (changed, message)."""
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            record = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return False, f"Cannot read: {e}"

    git = record.get("git")
    if not git or not isinstance(git, dict):
        return False, "No git block"

    remotes = git.get("remotes", [])
    if not remotes:
        return False, "No remotes"

    changed = False
    tc_id = record.get("tc_id", "?")

    for remote in remotes:
        pr = remote.get("pr")
        if not pr or not isinstance(pr, dict):
            continue
        pr_number = pr.get("number")
        if not pr_number:
            continue

        provider = remote.get("provider", "other")
        new_pr: dict | None = None

        if provider == "github":
            new_pr = _sync_github_pr(pr_number)
        elif provider == "gitlab":
            new_pr = _sync_gitlab_mr(pr_number)

        if new_pr:
            old_state = pr.get("state")
            remote["pr"] = new_pr
            if new_pr.get("state") != old_state:
                changed = True

    if changed:
        now = datetime.now(timezone.utc).astimezone().isoformat()
        revisions = record.get("revision_history", [])
        next_r = len(revisions) + 1
        revisions.append({
            "revision_id": f"R{next_r}",
            "timestamp": now,
            "author": "tc-sync",
            "summary": f"PR metadata refreshed via /tc sync",
            "field_changes": [
                {"field": "git.remotes", "action": "changed", "new_value": "PR state updated"}
            ],
        })
        record["updated"] = now
        record["metadata"]["last_modified"] = now
        record["metadata"]["last_modified_by"] = "tc-sync"

        # Atomic write
        tmp = record_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        tmp.replace(record_path)

        return True, f"{tc_id}: PR state updated"
    else:
        return False, f"{tc_id}: no changes"


def sync_all(tc_root: Path, filter_tc_id: str | None = None) -> int:
    """Sync all TCs (or a specific one) that have PR metadata.

    Returns count of TCs with updated PR state.
    """
    records_dir = tc_root / "records"
    if not records_dir.exists():
        print("No records directory found.")
        return 0

    synced = 0
    print("=== TC Sync ===")
    print()

    for tc_dir in sorted(records_dir.iterdir()):
        record_path = tc_dir / "tc_record.json"
        if not record_path.exists():
            continue

        if filter_tc_id and tc_dir.name != filter_tc_id and not tc_dir.name.startswith(filter_tc_id):
            continue

        changed, message = sync_tc(record_path)
        icon = "+" if changed else "."
        print(f"  [{icon}] {message}")
        if changed:
            synced += 1

    print()
    if synced > 0:
        print(f"Updated {synced} TC(s).")
    else:
        print("No PR state changes detected (offline or all up-to-date).")

    return synced


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tc_sync.py <tc_root_dir> [<tc_id>]")
        return 2

    tc_root = Path(sys.argv[1])
    if not tc_root.exists():
        print(f"ERROR: TC root not found: {tc_root}")
        return 2

    filter_tc_id = sys.argv[2] if len(sys.argv) >= 3 else None

    synced = sync_all(tc_root, filter_tc_id)
    return 0 if synced > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
