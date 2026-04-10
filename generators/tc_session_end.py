#!/usr/bin/env python3
"""TC Session End — Capture handoff state for session continuity.

Called at the end of a session to archive the current session, detect
uncommitted files, and write handoff data so the next session can resume.

Usage:
    python tc_session_end.py <tc_record.json> [--summary "what was done"] [--next "what to do next"]

    Automatically detects:
    - Uncommitted files that match files_affected[]
    - Current branch and recent commits linked to this TC
    - Archives current_session to session_history

Exit codes:
    0 = SUCCESS
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


def _run_git(args: list[str], repo_path: str = ".") -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path] + args,
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _get_uncommitted_files(repo_path: str = ".") -> list[str]:
    raw = _run_git(["diff", "--name-only", "HEAD"], repo_path)
    files = raw.splitlines() if raw else []
    untracked = _run_git(["ls-files", "--others", "--exclude-standard"], repo_path)
    if untracked:
        files.extend(untracked.splitlines())
    return [f.strip().replace("\\", "/") for f in files if f.strip()]


def session_end(
    record_path: str,
    summary: str = "",
    next_steps: list[str] | None = None,
    repo_path: str = ".",
) -> bool:
    """Archive current session and write handoff data."""
    path = Path(record_path)
    if not path.exists():
        print(f"ERROR: TC record not found: {path}")
        return False

    with open(path, "r", encoding="utf-8") as f:
        record = json.load(f)

    now = datetime.now(timezone.utc).astimezone().isoformat()
    ctx = record.get("session_context", {})

    # Archive current session
    current = ctx.get("current_session", {})
    if current and current.get("session_id"):
        history_entry = dict(current)
        history_entry["ended"] = now
        history_entry["summary"] = summary or f"Session ended at {now}"
        history_entry["changes_made"] = []

        # Collect changes from recent revision history
        revisions = record.get("revision_history", [])
        session_start = current.get("started", "")
        for rev in revisions:
            if rev.get("timestamp", "") >= session_start:
                history_entry["changes_made"].append(rev.get("summary", ""))

        ctx.setdefault("session_history", []).append(history_entry)

    # Detect uncommitted files matching files_affected
    tc_files = {fa["path"] for fa in record.get("files_affected", []) if isinstance(fa, dict)}
    uncommitted = set(_get_uncommitted_files(repo_path))
    overlap = uncommitted & tc_files

    # Build files_in_progress from uncommitted overlap
    files_in_progress = []
    for f in sorted(overlap):
        files_in_progress.append({
            "path": f,
            "state": "editing",
            "notes": "Uncommitted at session end",
        })

    # Update handoff
    handoff = ctx.get("handoff", {})
    if summary:
        handoff["progress_summary"] = summary
    if next_steps:
        handoff["next_steps"] = next_steps
    if files_in_progress:
        handoff["files_in_progress"] = files_in_progress

    # Git context for handoff
    git = record.get("git")
    if git and isinstance(git, dict):
        branch = git.get("initial_branch")
        commits = git.get("commits", [])
        if commits:
            recent = commits[-1]
            handoff.setdefault("key_context", [])
            git_note = f"Last linked commit: {recent.get('short_sha', '?')} on branch {branch or '?'}"
            if git_note not in handoff["key_context"]:
                handoff["key_context"].append(git_note)

    ctx["handoff"] = handoff

    # Clear current session (next session will create a new one)
    ctx["current_session"] = {
        "session_id": "ended",
        "platform": current.get("platform", "claude_code"),
        "model": current.get("model", "unknown"),
        "started": now,
        "last_active": now,
    }

    record["session_context"] = ctx

    # Append revision entry
    revisions = record.get("revision_history", [])
    next_r = len(revisions) + 1
    revisions.append({
        "revision_id": f"R{next_r}",
        "timestamp": now,
        "author": "tc-session-end",
        "summary": f"Session ended. {summary}" if summary else "Session ended — handoff written.",
        "field_changes": [
            {"field": "session_context", "action": "changed", "new_value": "Session archived, handoff updated"},
        ],
    })

    record["updated"] = now
    record["metadata"]["last_modified"] = now
    record["metadata"]["last_modified_by"] = "tc-session-end"

    # Validate
    errors = validate_tc_record(record)
    if errors:
        print(f"WARNING: {len(errors)} validation error(s) after session end:")
        for err in errors[:3]:
            print(f"  - {err}")

    # Atomic write
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

    tc_id = record.get("tc_id", "?")
    print(f"Session archived for {tc_id}")
    if files_in_progress:
        print(f"  {len(files_in_progress)} uncommitted file(s) noted in handoff")
    if next_steps:
        print(f"  {len(next_steps)} next step(s) recorded")

    return True


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tc_session_end.py <tc_record.json> [--summary \"...\"] [--next \"...\"]")
        return 2

    record_path = sys.argv[1]
    summary = ""
    next_steps: list[str] = []

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--summary" and i + 1 < len(sys.argv):
            i += 1
            summary = sys.argv[i]
        elif sys.argv[i] == "--next" and i + 1 < len(sys.argv):
            i += 1
            next_steps.append(sys.argv[i])
        i += 1

    # Detect repo root
    repo_path = "."
    for parent in Path(record_path).resolve().parents:
        if (parent / ".git").exists():
            repo_path = str(parent)
            break

    success = session_end(record_path, summary=summary, next_steps=next_steps, repo_path=repo_path)
    return 0 if success else 2


if __name__ == "__main__":
    sys.exit(main())
