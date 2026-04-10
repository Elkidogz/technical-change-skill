#!/usr/bin/env python3
"""TC Git Auto-Link — Post-commit hook that links HEAD to the active TC.

Designed to run as a Claude Code PostToolUse hook on Bash commands matching
'git commit'. Detects a single in-progress TC and auto-links HEAD to it.

Behavior:
  - If exactly 1 TC is in_progress: auto-link HEAD, exit 0
  - If 0 TCs are in_progress: silent no-op, exit 0
  - If 2+ TCs are in_progress: write .pending_link file, exit 0
  - On any error: silent exit 0 (never block the commit)

Usage:
    python tc_git_autolink.py --if-commit <tc_root_dir>

    Called by PostToolUse hook. The --if-commit flag is required (safety guard
    to prevent accidental invocations).

Exit codes:
    Always 0 (hooks must never block user workflow)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT / "generators"))


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


def _get_in_progress_tcs(tc_root: Path) -> list[dict]:
    """Return registry entries with status in_progress."""
    registry_path = tc_root / "tc_registry.json"
    if not registry_path.exists():
        return []
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        return [r for r in registry.get("records", []) if r.get("status") == "in_progress"]
    except (json.JSONDecodeError, OSError):
        return []


def autolink(tc_root_path: str | None = None) -> None:
    """Main auto-link logic. Never raises — always exits cleanly."""
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

        # Verify HEAD exists (confirms a commit just happened)
        head_sha = _run_git(["rev-parse", "HEAD"], repo_path)
        if not head_sha:
            return

        # Find in-progress TCs
        active = _get_in_progress_tcs(tc_root)

        if len(active) == 0:
            return  # No active TC to link to

        if len(active) > 1:
            # Ambiguous: write pending link file for manual resolution
            pending_path = tc_root / ".pending_link"
            now = datetime.now(timezone.utc).astimezone().isoformat()
            pending = {
                "sha": head_sha,
                "timestamp": now,
                "candidates": [r["tc_id"] for r in active],
                "message": f"Commit {head_sha[:7]} matches {len(active)} in-progress TCs. Run /tc link <tc-id> {head_sha[:7]} to link manually.",
            }
            try:
                with open(pending_path, "w", encoding="utf-8") as f:
                    json.dump(pending, f, indent=2)
            except OSError:
                pass
            return

        # Exactly 1 active TC — auto-link
        tc_entry = active[0]
        tc_id = tc_entry["tc_id"]
        record_dir = tc_root / tc_entry.get("path", f"records/{tc_id}")
        record_path = record_dir / "tc_record.json"

        if not record_path.exists():
            return

        # Import link function
        from tc_git_link import link_commits_to_tc  # noqa: E402

        linked, skipped, messages = link_commits_to_tc(
            str(record_path),
            ref="HEAD",
            repo_path=repo_path,
            link_source="auto-hook",
        )

        if linked > 0:
            # Silently regenerate HTML if generator is available
            try:
                from generate_tc_html import generate_tc_html, _load_css  # noqa: E402
                with open(record_path, "r", encoding="utf-8") as f:
                    record = json.load(f)
                css = _load_css()
                html = generate_tc_html(record, css)
                html_path = record_dir / "tc_record.html"
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass  # HTML regen is best-effort

    except Exception:
        pass  # Never fail — this is a hook


def main() -> int:
    """CLI entry point."""
    if "--if-commit" not in sys.argv:
        print("Usage: python tc_git_autolink.py --if-commit [<tc_root_dir>]")
        print("  Must be invoked with --if-commit flag (safety guard).")
        return 0

    tc_root_path = None
    for i, arg in enumerate(sys.argv):
        if arg not in ("--if-commit",) and not arg.endswith(".py") and i > 0:
            tc_root_path = arg
            break

    autolink(tc_root_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
