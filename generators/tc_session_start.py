#!/usr/bin/env python3
"""TC Session Start — Check for active TCs and display handoff summaries.

Called at the beginning of a session to surface in-progress/blocked TCs and
their handoff data so a new AI session can resume seamlessly.

Usage:
    python tc_session_start.py <tc_root_dir> [--json]

    --json: Output structured JSON instead of human-readable text

Exit codes:
    0 = SUCCESS (report printed, active TCs found or not)
    2 = FILE ERROR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load_registry(tc_root: Path) -> dict | None:
    registry_path = tc_root / "tc_registry.json"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_record(tc_root: Path, entry: dict) -> dict | None:
    record_path = tc_root / entry.get("path", "") / "tc_record.json"
    if not record_path.exists():
        return None
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def session_start_report(tc_root: Path, as_json: bool = False) -> str:
    """Generate session start report with active TC handoff data."""
    registry = _load_registry(tc_root)
    if not registry:
        return "TC tracking not initialized. Run /tc init to set up."

    project = registry.get("project_name", "Unknown")
    stats = registry.get("statistics", {})
    total = stats.get("total", 0)

    # Find active TCs
    active_entries = [
        r for r in registry.get("records", [])
        if r.get("status") in ("in_progress", "blocked", "paused")
    ]

    if as_json:
        result = {
            "project": project,
            "total_tcs": total,
            "active_tcs": [],
        }
        for entry in active_entries:
            record = _load_record(tc_root, entry)
            if record:
                handoff = record.get("session_context", {}).get("handoff", {})
                result["active_tcs"].append({
                    "tc_id": entry["tc_id"],
                    "title": entry["title"],
                    "status": entry["status"],
                    "handoff": handoff,
                })
        return json.dumps(result, indent=2)

    # Human-readable output
    lines: list[str] = []
    lines.append(f"=== {project} — Session Start ===")
    lines.append(f"Total TCs: {total}")
    lines.append("")

    if not active_entries:
        lines.append("No active TCs (in_progress, blocked, or paused).")
        lines.append("Start new work with /tc create, or review history with /tc status.")
        return "\n".join(lines)

    lines.append(f"Active TCs: {len(active_entries)}")
    lines.append("")

    for entry in active_entries:
        tc_id = entry["tc_id"]
        title = entry["title"]
        status = entry["status"]
        lines.append(f"--- {tc_id}: {title} [{status}] ---")

        record = _load_record(tc_root, entry)
        if not record:
            lines.append("  (record not found)")
            lines.append("")
            continue

        handoff = record.get("session_context", {}).get("handoff", {})

        progress = handoff.get("progress_summary", "")
        if progress:
            lines.append(f"  Progress: {progress}")

        next_steps = handoff.get("next_steps", [])
        if next_steps:
            lines.append("  Next steps:")
            for i, step in enumerate(next_steps, 1):
                lines.append(f"    {i}. {step}")

        blockers = handoff.get("blockers", [])
        if blockers:
            lines.append("  BLOCKERS:")
            for b in blockers:
                lines.append(f"    ! {b}")

        key_context = handoff.get("key_context", [])
        if key_context:
            lines.append("  Key context:")
            for ctx in key_context:
                lines.append(f"    - {ctx}")

        files_ip = handoff.get("files_in_progress", [])
        if files_ip:
            lines.append("  Files in progress:")
            for fip in files_ip:
                path = fip.get("path", "?")
                state = fip.get("state", "?")
                lines.append(f"    [{state}] {path}")

        decisions = handoff.get("decisions_made", [])
        if decisions:
            lines.append(f"  Recent decisions: {len(decisions)}")
            for d in decisions[-3:]:
                lines.append(f"    - {d.get('decision', '?')}")

        # Git info
        git = record.get("git")
        if git and isinstance(git, dict):
            commits = git.get("commits", [])
            if commits:
                lines.append(f"  Git: {len(commits)} linked commit(s), branch: {git.get('initial_branch', '?')}")
            remotes = git.get("remotes", [])
            for r in remotes:
                pr = r.get("pr")
                if pr and isinstance(pr, dict) and pr.get("number"):
                    lines.append(f"  PR: #{pr['number']} ({pr.get('state', '?')}) on {r.get('provider', '?')}")

        lines.append("")

    lines.append("Resume with /tc resume <tc-id>, or start fresh with /tc create.")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tc_session_start.py <tc_root_dir> [--json]")
        return 2

    tc_root = Path(sys.argv[1])
    if not tc_root.exists():
        print(f"ERROR: TC root not found: {tc_root}")
        return 2

    as_json = "--json" in sys.argv
    print(session_start_report(tc_root, as_json=as_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
