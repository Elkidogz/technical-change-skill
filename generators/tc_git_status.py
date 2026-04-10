#!/usr/bin/env python3
"""TC Git Status — Show git integration state for all TC records.

Reports:
  - TCs with no git block (unlinked)
  - TCs with git.commits[] populated (linked)
  - TCs with PR metadata (online-enriched)
  - Commits on current branch not linked to any TC (candidates for /tc link)
  - Uncommitted files matching in-progress TC files_affected[]

Usage:
    python tc_git_status.py <tc_root_dir> [--unlinked-only] [--show-candidates]

Exit codes:
    0 = SUCCESS (report printed)
    2 = FILE ERROR
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], repo_path: str = ".") -> str | None:
    """Run a git command, return stdout or None on failure."""
    cmd = ["git", "-C", repo_path] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _get_current_branch(repo_path: str = ".") -> str | None:
    raw = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    return raw if raw and raw != "HEAD" else None


def _get_recent_commits(repo_path: str = ".", count: int = 50) -> list[dict]:
    """Get recent commits on the current branch."""
    sep = "<<|>>"
    fmt = sep.join(["%H", "%h", "%s", "%an", "%aI"])
    raw = _run_git(["log", f"-{count}", f"--format={fmt}"], repo_path)
    if not raw:
        return []
    commits = []
    for line in raw.splitlines():
        parts = line.split(sep)
        if len(parts) >= 5:
            commits.append({
                "sha": parts[0].strip(),
                "short_sha": parts[1].strip(),
                "subject": parts[2].strip(),
                "author": parts[3].strip(),
                "date": parts[4].strip(),
            })
    return commits


def _get_uncommitted_files(repo_path: str = ".") -> list[str]:
    """Get list of uncommitted (staged + unstaged) file paths."""
    raw = _run_git(["diff", "--name-only", "HEAD"], repo_path)
    files = raw.splitlines() if raw else []
    # Also include untracked
    untracked = _run_git(["ls-files", "--others", "--exclude-standard"], repo_path)
    if untracked:
        files.extend(untracked.splitlines())
    return [f.strip().replace("\\", "/") for f in files if f.strip()]


# ---------------------------------------------------------------------------
# TC scanning
# ---------------------------------------------------------------------------

def _load_all_tc_records(tc_root: Path) -> list[dict]:
    """Load all TC records from the records/ directory."""
    records_dir = tc_root / "records"
    if not records_dir.exists():
        return []
    records = []
    for tc_dir in sorted(records_dir.iterdir()):
        record_path = tc_dir / "tc_record.json"
        if record_path.exists():
            try:
                with open(record_path, "r", encoding="utf-8") as f:
                    records.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
    return records


def _classify_tc(record: dict) -> str:
    """Classify a TC's git integration state."""
    git = record.get("git")
    if git is None or not isinstance(git, dict):
        return "unlinked"
    commits = git.get("commits", [])
    remotes = git.get("remotes", [])
    has_pr = any(r.get("pr") for r in remotes if isinstance(r, dict))
    if has_pr:
        return "online-enriched"
    if commits:
        return "linked"
    return "unlinked"


def _get_all_linked_shas(records: list[dict]) -> set[str]:
    """Collect all commit SHAs linked to any TC."""
    shas: set[str] = set()
    git = None
    for record in records:
        git = record.get("git")
        if git and isinstance(git, dict):
            for commit in git.get("commits", []):
                sha = commit.get("sha", "")
                if sha:
                    shas.add(sha)
    return shas


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_git_status_report(
    tc_root: Path,
    repo_path: str = ".",
    unlinked_only: bool = False,
    show_candidates: bool = True,
) -> str:
    """Generate a git status report for all TCs."""
    records = _load_all_tc_records(tc_root)
    if not records:
        return "No TC records found."

    lines: list[str] = []
    lines.append("=== TC Git Integration Status ===")
    lines.append("")

    # Classify all TCs
    unlinked: list[dict] = []
    linked: list[dict] = []
    enriched: list[dict] = []

    for record in records:
        cls = _classify_tc(record)
        if cls == "unlinked":
            unlinked.append(record)
        elif cls == "linked":
            linked.append(record)
        else:
            enriched.append(record)

    total = len(records)
    lines.append(f"Total TCs: {total}")
    lines.append(f"  Linked (git commits):    {len(linked)}")
    lines.append(f"  Online-enriched (PR):    {len(enriched)}")
    lines.append(f"  Unlinked (no git data):  {len(unlinked)}")
    lines.append("")

    # Unlinked TCs
    if unlinked:
        lines.append("--- TCs without git data ---")
        for r in unlinked:
            tc_id = r.get("tc_id", "?")
            title = r.get("title", "?")
            status = r.get("status", "?")
            lines.append(f"  [{status:12s}] {tc_id}: {title}")
        lines.append("")

    if unlinked_only:
        return "\n".join(lines)

    # Linked TCs
    if linked or enriched:
        lines.append("--- TCs with git data ---")
        for r in linked + enriched:
            tc_id = r.get("tc_id", "?")
            title = r.get("title", "?")
            git = r.get("git", {})
            commit_count = len(git.get("commits", []))
            pr_info = ""
            for remote in git.get("remotes", []):
                pr = remote.get("pr")
                if pr and isinstance(pr, dict) and pr.get("number"):
                    pr_info = f" | PR #{pr['number']} ({pr.get('state', '?')})"
            lines.append(f"  {tc_id}: {title} [{commit_count} commit(s){pr_info}]")
        lines.append("")

    # Candidate commits (on current branch, not linked to any TC)
    if show_candidates:
        branch = _get_current_branch(repo_path)
        if branch:
            lines.append(f"--- Unlinked commits on '{branch}' ---")
            recent = _get_recent_commits(repo_path, count=30)
            all_linked_shas = _get_all_linked_shas(records)

            candidates = [c for c in recent if c["sha"] not in all_linked_shas]
            if candidates:
                for c in candidates[:15]:
                    lines.append(f"  {c['short_sha']} {c['subject'][:65]}")
                if len(candidates) > 15:
                    lines.append(f"  ... and {len(candidates) - 15} more")
            else:
                lines.append("  All recent commits are linked to TCs.")
            lines.append("")

    # Uncommitted files matching in-progress TCs
    in_progress = [r for r in records if r.get("status") in ("in_progress", "blocked")]
    if in_progress:
        uncommitted = set(_get_uncommitted_files(repo_path))
        if uncommitted:
            lines.append("--- Uncommitted files matching active TCs ---")
            for r in in_progress:
                tc_files = {fa["path"] for fa in r.get("files_affected", []) if isinstance(fa, dict)}
                overlap = uncommitted & tc_files
                if overlap:
                    lines.append(f"  {r['tc_id']}:")
                    for f in sorted(overlap):
                        lines.append(f"    M {f}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tc_git_status.py <tc_root_dir> [--unlinked-only] [--show-candidates]")
        return 2

    tc_root = Path(sys.argv[1])
    if not tc_root.exists():
        print(f"ERROR: TC root not found: {tc_root}")
        return 2

    unlinked_only = "--unlinked-only" in sys.argv
    show_candidates = "--show-candidates" in sys.argv or "--unlinked-only" not in sys.argv

    # Detect repo root
    repo_path = "."
    for parent in tc_root.resolve().parents:
        if (parent / ".git").exists():
            repo_path = str(parent)
            break

    report = generate_git_status_report(
        tc_root,
        repo_path=repo_path,
        unlinked_only=unlinked_only,
        show_candidates=show_candidates,
    )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
