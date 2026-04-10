#!/usr/bin/env python3
"""TC Git Link — Link git commits to TC records.

Resolves a commit SHA (or HEAD), reads its metadata via git show, and appends
it to the TC record's git.commits[] array. Also merges files_changed into
files_affected[] and adds a revision_history entry.

Usage:
    python tc_git_link.py <tc_record.json> [<sha>|HEAD] [--range A..B]

    tc_record.json:  Path to the TC record to update
    sha:             Commit to link (default: HEAD)
    --range A..B:    Link all commits in the range (e.g., main..feature-branch)

Exit codes:
    0 = SUCCESS
    1 = ALREADY LINKED (no-op, not an error)
    2 = GIT ERROR or FILE ERROR
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


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], repo_path: str = ".") -> str:
    """Run a git command and return stdout."""
    cmd = ["git", "-C", repo_path] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git command timed out: {' '.join(cmd)}")
    if result.returncode != 0:
        raise RuntimeError(f"git error: {result.stderr.strip()}")
    return result.stdout.strip()


def _resolve_commits(ref: str, repo_path: str = ".") -> list[str]:
    """Resolve a ref or range into a list of full SHAs."""
    if ".." in ref:
        raw = _run_git(["rev-list", ref], repo_path)
        return [line.strip() for line in raw.splitlines() if line.strip()]
    else:
        sha = _run_git(["rev-parse", "--verify", ref], repo_path)
        return [sha.strip()]


def _get_commit_info(sha: str, repo_path: str = ".") -> dict:
    """Get commit metadata from git show."""
    sep = "<<|>>"
    fmt = sep.join(["%H", "%h", "%an", "%aI", "%s", "%P"])
    raw = _run_git(["show", f"--format={fmt}", "--name-only", sha], repo_path)
    lines = raw.split("\n")
    parts = lines[0].split(sep)
    if len(parts) < 6:
        raise RuntimeError(f"Could not parse git show output for {sha}")

    parents = parts[5].strip().split() if parts[5].strip() else []

    # Extract files (after the blank line separator)
    files: list[str] = []
    in_files = False
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            in_files = True
            continue
        if in_files and stripped and not stripped.startswith("diff "):
            files.append(stripped.replace("\\", "/"))

    return {
        "sha": parts[0].strip(),
        "short_sha": parts[1].strip(),
        "author": parts[2].strip(),
        "authored_date": parts[3].strip(),
        "subject": parts[4].strip(),
        "parent_count": len(parents),
        "files_changed": files,
    }


def _get_current_branch(repo_path: str = ".") -> str | None:
    """Get the current branch name, or None if detached."""
    try:
        return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path) or None
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Link logic
# ---------------------------------------------------------------------------

def link_commits_to_tc(
    record_path: str,
    ref: str = "HEAD",
    repo_path: str = ".",
    link_source: str = "manual",
) -> tuple[int, int, list[str]]:
    """Link commit(s) to a TC record.

    Returns: (linked_count, skipped_count, messages)
    """
    path = Path(record_path)
    if not path.exists():
        return 0, 0, [f"TC record not found: {path}"]

    with open(path, "r", encoding="utf-8") as f:
        record = json.load(f)

    # Initialize git block if absent
    if not record.get("git") or record["git"] is None:
        record["git"] = {
            "repo_root": None,
            "initial_branch": _get_current_branch(repo_path),
            "commits": [],
            "remotes": [],
            "release_tags": [],
        }

    git = record["git"]
    existing_shas = {c["sha"] for c in git.get("commits", [])}
    now = datetime.now(timezone.utc).astimezone().isoformat()
    branch = _get_current_branch(repo_path)

    # Set initial_branch if not yet set
    if not git.get("initial_branch") and branch:
        git["initial_branch"] = branch

    # Resolve commits
    try:
        shas = _resolve_commits(ref, repo_path)
    except RuntimeError as e:
        return 0, 0, [str(e)]

    linked = 0
    skipped = 0
    messages: list[str] = []

    for sha in shas:
        if sha in existing_shas:
            skipped += 1
            messages.append(f"  skip: {sha[:7]} (already linked)")
            continue

        try:
            info = _get_commit_info(sha, repo_path)
        except RuntimeError as e:
            messages.append(f"  error: {sha[:7]}: {e}")
            continue

        git["commits"].append({
            "sha": info["sha"],
            "short_sha": info["short_sha"],
            "author": info["author"],
            "authored_date": info["authored_date"],
            "subject": info["subject"],
            "branch": branch,
            "parent_count": info["parent_count"],
            "files_changed": info["files_changed"],
            "linked_at": now,
            "link_source": link_source,
        })
        existing_shas.add(info["sha"])

        # Merge files_changed into files_affected
        existing_paths = {fa["path"] for fa in record.get("files_affected", [])}
        for fpath in info["files_changed"]:
            if fpath not in existing_paths:
                record.setdefault("files_affected", []).append({
                    "path": fpath,
                    "action": "modified",
                    "description": None,
                    "lines_added": None,
                    "lines_removed": None,
                })
                existing_paths.add(fpath)

        linked += 1
        messages.append(f"  linked: {info['short_sha']} {info['subject'][:60]}")

    if linked > 0:
        # Append revision history entry
        revisions = record.get("revision_history", [])
        next_r = len(revisions) + 1
        sha_list = ", ".join(m.split()[1] for m in messages if m.strip().startswith("linked:"))
        revisions.append({
            "revision_id": f"R{next_r}",
            "timestamp": now,
            "author": "tc-git-link",
            "summary": f"Linked {linked} commit(s) via /tc link: {sha_list}",
            "field_changes": [
                {"field": "git.commits", "action": "added", "new_value": f"{linked} commit(s)"}
            ],
        })

        record["updated"] = now
        record["metadata"]["last_modified"] = now
        record["metadata"]["last_modified_by"] = "tc-git-link"

    # Validate before writing
    errors = validate_tc_record(record)
    if errors:
        messages.append(f"  WARNING: record has {len(errors)} validation error(s) after linking:")
        for err in errors[:5]:
            messages.append(f"    - {err}")

    # Atomic write
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

    return linked, skipped, messages


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tc_git_link.py <tc_record.json> [<sha>|HEAD] [--range A..B]")
        return 2

    record_path = sys.argv[1]
    ref = "HEAD"
    link_source = "manual"

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--range" and i + 1 < len(sys.argv):
            i += 1
            ref = sys.argv[i]
        elif arg == "--source" and i + 1 < len(sys.argv):
            i += 1
            link_source = sys.argv[i]
        else:
            ref = arg
        i += 1

    # Detect repo root from record path
    record_dir = Path(record_path).resolve().parent
    repo_path = "."
    for parent in record_dir.parents:
        if (parent / ".git").exists():
            repo_path = str(parent)
            break

    print(f"Linking {ref} to {Path(record_path).stem}...")
    linked, skipped, messages = link_commits_to_tc(
        record_path, ref=ref, repo_path=repo_path, link_source=link_source,
    )

    for msg in messages:
        print(msg)

    print()
    if linked > 0:
        print(f"Linked {linked} commit(s), skipped {skipped}.")
        return 0
    elif skipped > 0:
        print(f"All {skipped} commit(s) already linked.")
        return 1
    else:
        print("No commits linked.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
