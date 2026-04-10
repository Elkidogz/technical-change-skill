#!/usr/bin/env python3
"""TC PR Link — Link a pull request to a TC record (online opt-in).

Detects the remote provider (GitHub/GitLab/Bitbucket) from `git remote -v`,
queries PR metadata via `gh` or `glab` CLI, and populates the TC's
git.remotes[] block.

Graceful degradation: if CLIs are missing or unauthenticated, reports the
issue and exits cleanly. Never blocks offline workflow.

Usage:
    python tc_pr_link.py <tc_record.json> [<pr_number>]

    If pr_number is omitted, auto-detects from current branch.

Exit codes:
    0 = SUCCESS
    1 = CLI NOT AVAILABLE or PR NOT FOUND (graceful degradation)
    2 = FILE ERROR
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT / "validators"))

from validate_tc import validate_tc_record  # noqa: E402


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def _run_cmd(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "Command timed out"


def _run_git(args: list[str], repo_path: str = ".") -> str | None:
    """Run git command, return stdout or None."""
    rc, out, _ = _run_cmd(["git", "-C", repo_path] + args)
    return out if rc == 0 else None


def detect_provider(repo_path: str = ".") -> tuple[str, str, str]:
    """Detect remote provider from git remote -v.

    Returns: (provider, remote_name, remote_url)
    provider: github|gitlab|bitbucket|other
    """
    raw = _run_git(["remote", "-v"], repo_path)
    if not raw:
        return "other", "", ""

    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        if "(fetch)" not in line and "(push)" not in line:
            continue

        url_lower = url.lower()
        if "github.com" in url_lower:
            return "github", name, url
        elif "gitlab" in url_lower:
            return "gitlab", name, url
        elif "bitbucket" in url_lower:
            return "bitbucket", name, url

    # Take first remote as "other"
    first = raw.splitlines()[0].split()
    if len(first) >= 2:
        return "other", first[0], first[1]
    return "other", "", ""


def _get_current_branch(repo_path: str = ".") -> str | None:
    raw = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    return raw if raw and raw != "HEAD" else None


# ---------------------------------------------------------------------------
# CLI adapters
# ---------------------------------------------------------------------------

def _gh_pr_info(pr_number: int | None, repo_path: str = ".") -> dict | None:
    """Query PR info via GitHub CLI (gh)."""
    if pr_number:
        cmd = ["gh", "pr", "view", str(pr_number), "--json",
               "number,url,state,mergeCommit,reviewDecision,headRefName"]
    else:
        branch = _get_current_branch(repo_path)
        if not branch:
            return None
        cmd = ["gh", "pr", "view", branch, "--json",
               "number,url,state,mergeCommit,reviewDecision,headRefName"]

    rc, out, err = _run_cmd(cmd)
    if rc != 0:
        if rc == -1:
            print("  gh CLI not installed. Install: https://cli.github.com/")
        elif "no pull requests found" in err.lower():
            print(f"  No PR found for current branch.")
        else:
            print(f"  gh error: {err[:100]}")
        return None

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None

    state_map = {"OPEN": "open", "CLOSED": "closed", "MERGED": "merged"}
    raw_state = data.get("state", "").upper()

    # Check for draft
    if raw_state == "OPEN" and data.get("isDraft"):
        pr_state = "draft"
    else:
        pr_state = state_map.get(raw_state, "open")

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


def _glab_mr_info(mr_number: int | None, repo_path: str = ".") -> dict | None:
    """Query MR info via GitLab CLI (glab)."""
    if mr_number:
        cmd = ["glab", "mr", "view", str(mr_number), "--output", "json"]
    else:
        branch = _get_current_branch(repo_path)
        if not branch:
            return None
        cmd = ["glab", "mr", "view", branch, "--output", "json"]

    rc, out, err = _run_cmd(cmd)
    if rc != 0:
        if rc == -1:
            print("  glab CLI not installed. Install: https://gitlab.com/gitlab-org/cli")
        else:
            print(f"  glab error: {err[:100]}")
        return None

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None

    state_map = {"opened": "open", "closed": "closed", "merged": "merged"}
    pr_state = state_map.get(data.get("state", ""), "open")

    return {
        "number": data.get("iid"),
        "url": data.get("web_url"),
        "state": pr_state,
        "merged_sha": data.get("merge_commit_sha"),
        "review_decision": None,
        "last_synced": datetime.now(timezone.utc).astimezone().isoformat(),
    }


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def link_pr_to_tc(
    record_path: str,
    pr_number: int | None = None,
    repo_path: str = ".",
) -> bool:
    """Link PR metadata to a TC record. Returns True on success."""
    path = Path(record_path)
    if not path.exists():
        print(f"ERROR: TC record not found: {path}")
        return False

    with open(path, "r", encoding="utf-8") as f:
        record = json.load(f)

    # Initialize git block if needed
    if not record.get("git") or record["git"] is None:
        record["git"] = {
            "repo_root": None,
            "initial_branch": _get_current_branch(repo_path),
            "commits": [],
            "remotes": [],
            "release_tags": [],
        }

    # Detect provider
    provider, remote_name, remote_url = detect_provider(repo_path)
    print(f"Provider: {provider} ({remote_name}: {remote_url[:60]})")

    # Query PR info
    pr_info: dict | None = None
    if provider == "github":
        pr_info = _gh_pr_info(pr_number, repo_path)
    elif provider == "gitlab":
        pr_info = _glab_mr_info(pr_number, repo_path)
    elif provider == "bitbucket":
        print("  Bitbucket CLI integration not yet supported. Use manual /tc update.")
        return False
    else:
        print("  Unknown provider. Cannot auto-detect PR. Use manual /tc update.")
        return False

    if not pr_info:
        return False

    # Update remotes
    git = record["git"]
    existing_remotes = {r["name"]: i for i, r in enumerate(git.get("remotes", []))}
    remote_entry = {
        "name": remote_name or "origin",
        "url": remote_url,
        "provider": provider,
        "pr": pr_info,
    }

    if remote_name in existing_remotes:
        git["remotes"][existing_remotes[remote_name]] = remote_entry
    else:
        git.setdefault("remotes", []).append(remote_entry)

    # Add revision history entry
    now = datetime.now(timezone.utc).astimezone().isoformat()
    revisions = record.get("revision_history", [])
    next_r = len(revisions) + 1
    revisions.append({
        "revision_id": f"R{next_r}",
        "timestamp": now,
        "author": "tc-pr-link",
        "summary": f"Linked PR #{pr_info['number']} ({pr_info['state']}) from {provider}",
        "field_changes": [
            {"field": "git.remotes", "action": "added", "new_value": f"PR #{pr_info['number']}"}
        ],
    })

    record["updated"] = now
    record["metadata"]["last_modified"] = now
    record["metadata"]["last_modified_by"] = "tc-pr-link"

    # Write
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

    print(f"Linked PR #{pr_info['number']} ({pr_info['state']}) to {record['tc_id']}")
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tc_pr_link.py <tc_record.json> [<pr_number>]")
        return 2

    record_path = sys.argv[1]
    pr_number = None
    if len(sys.argv) >= 3:
        try:
            pr_number = int(sys.argv[2])
        except ValueError:
            print(f"ERROR: PR number must be an integer, got '{sys.argv[2]}'")
            return 2

    # Detect repo root
    repo_path = "."
    for parent in Path(record_path).resolve().parents:
        if (parent / ".git").exists():
            repo_path = str(parent)
            break

    success = link_pr_to_tc(record_path, pr_number=pr_number, repo_path=repo_path)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
