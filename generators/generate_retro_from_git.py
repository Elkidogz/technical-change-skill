#!/usr/bin/env python3
"""Git History to Retro Changelog Generator — Builds retro_changelog.json from git log.

Parses a repository's git history and groups commits into logical Technical Changes
(TCs) using merge-commit boundaries, file-overlap clustering, and time-proximity
heuristics. Outputs a retro_changelog.json that can be fed directly into
generate_retro_tcs.py for batch TC creation.

Usage:
    python generate_retro_from_git.py [OPTIONS]

Options:
    --repo-path PATH        Path to the git repository (default: .)
    --output PATH           Output file path (default: retro_changelog.json)
    --project-name NAME     Project name for the changelog header
    --since DATE            Only include commits after this date (YYYY-MM-DD)
    --until DATE            Only include commits up to this date (YYYY-MM-DD)
    --author AUTHOR         Default author field (default: retroactive)
    --time-window HOURS     Clustering window in hours (default: 2)

Exit codes:
    0 = SUCCESS
    1 = NO COMMITS FOUND
    2 = GIT ERROR or other failure
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_SCOPES = frozenset([
    "feature", "bugfix", "refactor", "infrastructure",
    "documentation", "hotfix", "enhancement",
])

# Ordered from most specific to least specific for matching
SCOPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bhotfix\b", re.IGNORECASE), "hotfix"),
    (re.compile(r"\bfix(?:es|ed)?\b", re.IGNORECASE), "bugfix"),
    (re.compile(r"\bbug(?:fix)?\b", re.IGNORECASE), "bugfix"),
    (re.compile(r"\bfeat(?:ure)?(?:\(.+?\))?[:!]", re.IGNORECASE), "feature"),
    (re.compile(r"\bfeat\b", re.IGNORECASE), "feature"),
    (re.compile(r"\badd(?:s|ed)?\b", re.IGNORECASE), "feature"),
    (re.compile(r"\brefactor\b", re.IGNORECASE), "refactor"),
    (re.compile(r"\bcleanup\b", re.IGNORECASE), "refactor"),
    (re.compile(r"\brestructur", re.IGNORECASE), "refactor"),
    (re.compile(r"\bdoc(?:s|umentation)?\b", re.IGNORECASE), "documentation"),
    (re.compile(r"\breadme\b", re.IGNORECASE), "documentation"),
    (re.compile(r"\bchore\b", re.IGNORECASE), "infrastructure"),
    (re.compile(r"\bci\b", re.IGNORECASE), "infrastructure"),
    (re.compile(r"\bbuild\b", re.IGNORECASE), "infrastructure"),
    (re.compile(r"\bdeps?\b", re.IGNORECASE), "infrastructure"),
    (re.compile(r"\bconfig\b", re.IGNORECASE), "infrastructure"),
    (re.compile(r"\binfra\b", re.IGNORECASE), "infrastructure"),
    (re.compile(r"\benhance", re.IGNORECASE), "enhancement"),
    (re.compile(r"\bimprov", re.IGNORECASE), "enhancement"),
    (re.compile(r"\bupdate", re.IGNORECASE), "enhancement"),
    (re.compile(r"\bupgrad", re.IGNORECASE), "enhancement"),
]

PRIORITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcritical\b", re.IGNORECASE), "critical"),
    (re.compile(r"\burgent\b", re.IGNORECASE), "critical"),
    (re.compile(r"\bhotfix\b", re.IGNORECASE), "high"),
    (re.compile(r"\bsecurity\b", re.IGNORECASE), "high"),
    (re.compile(r"\bbreaking\b", re.IGNORECASE), "high"),
    (re.compile(r"\b(?:CVE|vuln)", re.IGNORECASE), "high"),
]

# Git log field delimiter — unlikely to appear in commit messages
FIELD_SEP = "<<|>>"
RECORD_SEP = "<<||>>"


# ---------------------------------------------------------------------------
# Data Classes (plain dicts to avoid dataclass import on older Pythons)
# ---------------------------------------------------------------------------

def _make_commit(
    sha: str,
    author: str,
    date: datetime,
    subject: str,
    body: str,
    is_merge: bool,
    files: list[str],
) -> dict:
    """Create a commit record dict."""
    return {
        "sha": sha,
        "author": author,
        "date": date,
        "subject": subject,
        "body": body,
        "is_merge": is_merge,
        "files": files,
    }


# ---------------------------------------------------------------------------
# Git Interface
# ---------------------------------------------------------------------------

def _run_git(args: list[str], repo_path: str) -> str:
    """Run a git command and return stdout. Raises RuntimeError on failure."""
    cmd = ["git", "-C", repo_path] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git command timed out: {' '.join(cmd)}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"git error (exit {result.returncode}): {stderr}")

    return result.stdout


def _detect_project_name(repo_path: str) -> str:
    """Detect the project name from common metadata files or directory name."""
    root = Path(repo_path).resolve()

    # Try package.json
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("name", "")
            if name:
                return name
        except (json.JSONDecodeError, OSError):
            pass

    # Try pyproject.toml (basic parsing, no toml dependency)
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            match = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.MULTILINE)
            if match:
                return match.group(1)
        except OSError:
            pass

    # Try CLAUDE.md first heading
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        try:
            text = claude_md.read_text(encoding="utf-8")
            match = re.search(r"^#\s+(.+)", text, re.MULTILINE)
            if match:
                return match.group(1).strip()
        except OSError:
            pass

    # Fall back to directory basename
    return root.name


def parse_git_log(
    repo_path: str,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Parse git log into a list of commit dicts, newest first."""

    # Build the format string: sha, author, ISO date, subject, parent count
    # NOTE: %b (body) is omitted because it contains newlines that break parsing.
    # The subject line is sufficient for retro changelog generation.
    fmt = FIELD_SEP.join(["%H", "%an", "%aI", "%s", "%P"])

    log_args = [
        "log",
        f"--format={RECORD_SEP}{fmt}",
        "--name-only",
    ]

    if since:
        log_args.append(f"--since={since}")
    if until:
        log_args.append(f"--until={until}")

    raw = _run_git(log_args, repo_path)

    if not raw.strip():
        return []

    commits: list[dict] = []

    # Split into raw records (first element is empty because log starts with sep)
    raw_records = raw.split(RECORD_SEP)

    for raw_record in raw_records:
        raw_record = raw_record.strip()
        if not raw_record:
            continue

        # The first line contains our formatted fields, followed by
        # a blank line and then the file names (from --name-only).
        lines = raw_record.split("\n")
        header_line = lines[0]

        parts = header_line.split(FIELD_SEP)
        if len(parts) < 5:
            continue

        sha = parts[0].strip()
        author = parts[1].strip()
        date_str = parts[2].strip()
        subject = parts[3].strip()
        parents_str = parts[4].strip()
        body = ""

        # Parse date
        try:
            commit_date = datetime.fromisoformat(date_str)
        except ValueError:
            continue

        # Determine if this is a merge commit (has 2+ parents)
        parent_count = len(parents_str.split()) if parents_str else 1
        is_merge = parent_count >= 2

        # Extract files (lines after the header that are non-empty file paths)
        # With --name-only, git appends file paths after a blank line.
        # But body text from %b-less format may still leak. Use heuristics:
        files: list[str] = []
        in_files = False
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                in_files = True  # blank line separates header from file list
                continue
            if not in_files:
                continue
            # Skip lines that look like commit message body, not file paths
            if stripped.startswith(("Signed-off-by:", "Co-authored-by:", "Co-Authored-By:",
                                    "Closes ", "Fixes ", "Refs ", "Reviewed-by:",
                                    "Acked-by:", "Tested-by:")):
                continue
            # Skip lines that are clearly sentences (contain spaces + start with uppercase)
            if " " in stripped and stripped[0].isupper() and len(stripped) > 60:
                continue
            files.append(stripped.replace("\\", "/"))

        commits.append(_make_commit(
            sha=sha,
            author=author,
            date=commit_date,
            subject=subject,
            body=body,
            is_merge=is_merge,
            files=files,
        ))

    return commits


# ---------------------------------------------------------------------------
# Scope and Priority Detection
# ---------------------------------------------------------------------------

def detect_scope(message: str, files: list[str]) -> str:
    """Detect the TC scope from commit message and file paths."""
    combined = message

    # Check message against patterns
    for pattern, scope in SCOPE_PATTERNS:
        if pattern.search(combined):
            return scope

    # File-based heuristics
    if files:
        exts = {Path(f).suffix.lower() for f in files}
        names = {Path(f).name.lower() for f in files}
        paths_lower = [f.lower() for f in files]

        # Documentation files
        doc_exts = {".md", ".rst", ".txt", ".adoc"}
        if exts and exts.issubset(doc_exts):
            return "documentation"

        # CI/infrastructure files
        infra_indicators = {
            "dockerfile", "docker-compose.yml", "docker-compose.yaml",
            ".github", "jenkinsfile", ".gitlab-ci.yml", "makefile",
            ".env.example", "tox.ini", "setup.cfg", "pyproject.toml",
            "package.json", "tsconfig.json", ".eslintrc",
        }
        if any(
            any(ind in p for ind in infra_indicators)
            for p in paths_lower
        ):
            # Only if ALL files are infra-ish
            infra_count = sum(
                1 for p in paths_lower
                if any(ind in p for ind in infra_indicators)
            )
            if infra_count == len(files):
                return "infrastructure"

        # Test files
        if all("test" in p for p in paths_lower):
            return "enhancement"

    return "enhancement"


def detect_priority(message: str) -> str:
    """Detect priority from commit message. Default: medium."""
    for pattern, priority in PRIORITY_PATTERNS:
        if pattern.search(message):
            return priority
    return "medium"


# ---------------------------------------------------------------------------
# Commit Grouping
# ---------------------------------------------------------------------------

def _file_overlap(files_a: set[str], files_b: set[str]) -> float:
    """Compute Jaccard similarity between two file sets."""
    if not files_a or not files_b:
        return 0.0
    intersection = len(files_a & files_b)
    union = len(files_a | files_b)
    return intersection / union if union > 0 else 0.0


def _directory_overlap(files_a: set[str], files_b: set[str]) -> float:
    """Compute overlap based on shared parent directories."""
    dirs_a = {str(Path(f).parent) for f in files_a}
    dirs_b = {str(Path(f).parent) for f in files_b}
    if not dirs_a or not dirs_b:
        return 0.0
    intersection = len(dirs_a & dirs_b)
    union = len(dirs_a | dirs_b)
    return intersection / union if union > 0 else 0.0


def group_by_merge_commits(commits: list[dict]) -> list[list[dict]]:
    """Group commits by merge commit boundaries.

    Returns a list of groups where each group is bounded by merge commits.
    Merge commits themselves start a new group with their preceding non-merge commits.
    """
    if not commits:
        return []

    groups: list[list[dict]] = []
    current_group: list[dict] = []

    # Commits are newest-first from git log; reverse for chronological processing
    chronological = list(reversed(commits))

    for commit in chronological:
        if commit["is_merge"]:
            # The merge commit starts a new group, but the preceding
            # non-merge commits are the work that was merged
            if current_group:
                # Include the merge commit with its group
                current_group.append(commit)
                groups.append(current_group)
                current_group = []
            else:
                # Standalone merge (e.g., merge of another branch with no local commits)
                groups.append([commit])
        else:
            current_group.append(commit)

    # Don't forget the last group
    if current_group:
        groups.append(current_group)

    return groups


def cluster_by_proximity(
    commits: list[dict],
    time_window_hours: float = 2.0,
    file_overlap_threshold: float = 0.15,
) -> list[list[dict]]:
    """Cluster commits by author + time proximity + file overlap.

    Two commits are grouped together if they share the same author,
    are within the time window, and have overlapping files or directories.
    """
    if not commits:
        return []

    # Work chronologically
    chronological = list(reversed(commits)) if commits[0]["date"] > commits[-1]["date"] else list(commits)

    clusters: list[list[dict]] = []
    assigned: set[int] = set()

    for i, commit in enumerate(chronological):
        if i in assigned:
            continue

        cluster = [commit]
        assigned.add(i)
        cluster_files = set(commit["files"])
        cluster_end_time = commit["date"]

        # Try to absorb subsequent commits into this cluster
        for j in range(i + 1, len(chronological)):
            if j in assigned:
                continue

            candidate = chronological[j]
            time_diff = abs((candidate["date"] - cluster_end_time).total_seconds())

            # Same author and within time window
            if candidate["author"] != commit["author"]:
                continue
            if time_diff > time_window_hours * 3600:
                continue

            # Check file or directory overlap
            candidate_files = set(candidate["files"])
            f_overlap = _file_overlap(cluster_files, candidate_files)
            d_overlap = _directory_overlap(cluster_files, candidate_files)

            if f_overlap >= file_overlap_threshold or d_overlap >= 0.3:
                cluster.append(candidate)
                assigned.add(j)
                cluster_files |= candidate_files
                # Extend the cluster's time boundary
                cluster_end_time = max(cluster_end_time, candidate["date"])

        clusters.append(cluster)

    return clusters


def group_commits(
    commits: list[dict],
    time_window_hours: float = 2.0,
) -> list[list[dict]]:
    """Group commits into logical TCs using the best available strategy.

    Strategy:
    1. If the repo uses merge commits, group by merge boundaries first.
    2. Within each merge group (or for repos without merges), cluster by
       author + time proximity + file overlap.
    """
    if not commits:
        return []

    merge_count = sum(1 for c in commits if c["is_merge"])
    non_merge = [c for c in commits if not c["is_merge"]]

    # If there are merge commits, use them as primary boundaries
    if merge_count > 0:
        merge_groups = group_by_merge_commits(commits)
        final_groups: list[list[dict]] = []

        for group in merge_groups:
            # If a group is small enough, keep it as-is
            if len(group) <= 3:
                final_groups.append(group)
            else:
                # Sub-cluster large groups
                sub_clusters = cluster_by_proximity(
                    group, time_window_hours=time_window_hours,
                )
                final_groups.extend(sub_clusters)

        return final_groups

    # No merge commits — pure clustering
    return cluster_by_proximity(
        commits, time_window_hours=time_window_hours,
    )


# ---------------------------------------------------------------------------
# Changelog Entry Builder
# ---------------------------------------------------------------------------

def _clean_title(subject: str) -> str:
    """Clean up a commit subject into a human-readable TC title.

    Removes conventional-commit prefixes, PR numbers, trailing punctuation.
    """
    title = subject.strip()

    # Remove conventional commit prefix: feat(scope): , fix: , etc.
    title = re.sub(r"^[a-z]+(?:\([^)]*\))?[!:]?\s*", "", title, flags=re.IGNORECASE)

    # Remove PR/issue references like (#123) or [#123]
    title = re.sub(r"\s*[\(\[]\s*#\d+\s*[\)\]]", "", title)

    # Remove trailing punctuation
    title = title.rstrip(".!,;:")

    # Capitalize first letter
    if title:
        title = title[0].upper() + title[1:]

    # Ensure minimum length
    if len(title) < 5:
        title = subject.strip()
        if len(title) < 5:
            title = title + " (change)"

    return title


def _build_description(commits: list[dict]) -> str:
    """Build a description from the unique commit messages in a group."""
    seen: set[str] = set()
    lines: list[str] = []

    for commit in commits:
        msg = commit["subject"].strip()
        if msg and msg.lower() not in seen:
            seen.add(msg.lower())
            lines.append(msg)

        # Include body content if it adds new information
        body = commit["body"].strip()
        if body:
            for body_line in body.split("\n"):
                body_line = body_line.strip()
                if (
                    body_line
                    and body_line.lower() not in seen
                    and not body_line.startswith("Signed-off-by:")
                    and not body_line.startswith("Co-authored-by:")
                    and not body_line.startswith("Co-Authored-By:")
                ):
                    seen.add(body_line.lower())
                    lines.append(body_line)

    description = "; ".join(lines)

    # Ensure minimum 10 chars
    if len(description) < 10:
        description = description + " — retroactive change documentation"

    return description


def _detect_version(commits: list[dict]) -> str | None:
    """Try to detect a version tag from commit messages."""
    version_re = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9.]+)?\b")
    for commit in commits:
        match = version_re.search(commit["subject"])
        if match:
            v = match.group(0)
            if not v.startswith("v"):
                v = "v" + v
            return v
    return None


def _collect_tags(commits: list[dict], scope: str) -> list[str]:
    """Generate tags from the commit group."""
    tags: set[str] = set()
    tags.add(scope)
    tags.add("retroactive")
    tags.add("from-git")

    # Add author-based tags
    authors = {c["author"] for c in commits}
    if len(authors) > 1:
        tags.add("multi-author")

    # Detect co-authored work
    for commit in commits:
        if "Co-authored-by:" in commit["body"] or "Co-Authored-By:" in commit["body"]:
            tags.add("collaborative")
            break

    return sorted(tags)


def build_changelog_entry(commits: list[dict]) -> dict:
    """Build a single retro_changelog entry from a group of commits."""
    if not commits:
        return {}

    # Sort chronologically (oldest first) for consistent processing
    sorted_commits = sorted(commits, key=lambda c: c["date"])

    # Use the merge commit message if available, else first commit
    merge_commits = [c for c in sorted_commits if c["is_merge"]]
    representative = merge_commits[0] if merge_commits else sorted_commits[0]

    # Title
    title = _clean_title(representative["subject"])

    # Truncate title to max 120 chars
    if len(title) > 120:
        title = title[:117] + "..."

    # Combined message for scope/priority detection
    all_messages = " ".join(c["subject"] + " " + c["body"] for c in sorted_commits)

    # Collect all files
    all_files: list[str] = []
    seen_files: set[str] = set()
    for commit in sorted_commits:
        for f in commit["files"]:
            normalized = f.replace("\\", "/")
            if normalized not in seen_files:
                seen_files.add(normalized)
                all_files.append(normalized)

    # Scope and priority
    scope = detect_scope(all_messages, all_files)
    priority = detect_priority(all_messages)

    # Date — use the earliest commit's date
    date_str = sorted_commits[0]["date"].strftime("%Y-%m-%d")

    # Description
    description = _build_description(sorted_commits)

    # Motivation — try to derive from body text or use a generic one
    motivation = None
    for commit in sorted_commits:
        body = commit["body"].strip()
        if body and len(body) > 15:
            # Filter out sign-off lines
            body_lines = [
                ln for ln in body.split("\n")
                if not ln.strip().startswith(("Signed-off-by:", "Co-authored-by:", "Co-Authored-By:"))
            ]
            cleaned_body = " ".join(ln.strip() for ln in body_lines if ln.strip())
            if len(cleaned_body) > 15:
                motivation = cleaned_body
                break

    # Version
    version = _detect_version(sorted_commits)

    # Tags
    tags = _collect_tags(sorted_commits, scope)

    entry: dict = {
        "title": title,
        "scope": scope,
        "priority": priority,
        "status": "deployed",
        "date": date_str,
        "description": description,
        "files": all_files,
        "tags": tags,
    }

    if motivation:
        entry["motivation"] = motivation
    if version:
        entry["version"] = version

    return entry


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def generate_retro_changelog(
    repo_path: str = ".",
    project_name: str | None = None,
    default_author: str = "retroactive",
    since: str | None = None,
    until: str | None = None,
    time_window_hours: float = 2.0,
) -> dict:
    """Generate a complete retro_changelog.json dict from git history."""

    # Resolve repo path
    repo_path = str(Path(repo_path).resolve())

    # Detect project name
    if not project_name:
        project_name = _detect_project_name(repo_path)

    print(f"=== Git History to Retro Changelog Generator ===")
    print(f"Repository: {repo_path}")
    print(f"Project:    {project_name}")
    if since:
        print(f"Since:      {since}")
    if until:
        print(f"Until:      {until}")
    print(f"Time window: {time_window_hours}h")
    print()

    # Parse git log
    print("Parsing git history...")
    commits = parse_git_log(repo_path, since=since, until=until)

    if not commits:
        print("No commits found in the specified range.")
        return {
            "project": project_name,
            "default_author": default_author,
            "changes": [],
        }

    print(f"  Found {len(commits)} commits")
    merge_count = sum(1 for c in commits if c["is_merge"])
    if merge_count:
        print(f"  Merge commits: {merge_count}")

    # Group commits
    print("Grouping commits into logical changes...")
    groups = group_commits(commits, time_window_hours=time_window_hours)
    print(f"  Formed {len(groups)} groups")
    print()

    # Build changelog entries
    changes: list[dict] = []
    for group in groups:
        entry = build_changelog_entry(group)
        if entry:
            changes.append(entry)

    # Sort by date (oldest first)
    changes.sort(key=lambda c: c.get("date", ""))

    # Summary
    scope_counts: dict[str, int] = defaultdict(int)
    for c in changes:
        scope_counts[c["scope"]] += 1

    print(f"Generated {len(changes)} changelog entries:")
    for scope, count in sorted(scope_counts.items(), key=lambda x: -x[1]):
        print(f"  {scope}: {count}")

    changelog = {
        "project": project_name,
        "default_author": default_author,
        "changes": changes,
    }

    return changelog


# ---------------------------------------------------------------------------
# CLI Argument Parser (stdlib only — no argparse dependency issues)
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> dict:
    """Parse CLI arguments into a dict. Manual parsing for minimal dependencies."""
    args = {
        "repo_path": ".",
        "output": "retro_changelog.json",
        "project_name": None,
        "since": None,
        "until": None,
        "author": "retroactive",
        "time_window": 2.0,
    }

    i = 0
    while i < len(argv):
        arg = argv[i]

        if arg in ("--repo-path", "--repo"):
            i += 1
            if i < len(argv):
                args["repo_path"] = argv[i]
        elif arg in ("--output", "-o"):
            i += 1
            if i < len(argv):
                args["output"] = argv[i]
        elif arg in ("--project-name", "--project"):
            i += 1
            if i < len(argv):
                args["project_name"] = argv[i]
        elif arg == "--since":
            i += 1
            if i < len(argv):
                args["since"] = argv[i]
        elif arg == "--until":
            i += 1
            if i < len(argv):
                args["until"] = argv[i]
        elif arg == "--author":
            i += 1
            if i < len(argv):
                args["author"] = argv[i]
        elif arg in ("--time-window", "--window"):
            i += 1
            if i < len(argv):
                try:
                    args["time_window"] = float(argv[i])
                except ValueError:
                    print(f"WARNING: Invalid time window '{argv[i]}', using default 2.0h")
        elif arg in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        else:
            print(f"WARNING: Unknown argument '{arg}' — ignored")

        i += 1

    return args


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_changelog(changelog: dict) -> list[str]:
    """Validate the generated changelog against the retro_changelog schema rules."""
    errors: list[str] = []

    if not isinstance(changelog, dict):
        return ["Changelog must be a JSON object"]

    if not changelog.get("project"):
        errors.append("Missing or empty 'project' field")

    changes = changelog.get("changes", [])
    if not isinstance(changes, list):
        errors.append("'changes' must be an array")
        return errors

    for i, change in enumerate(changes):
        prefix = f"changes[{i}]"

        if not isinstance(change, dict):
            errors.append(f"{prefix} must be an object")
            continue

        # Required fields
        title = change.get("title", "")
        if not title or len(title) < 5:
            errors.append(f"{prefix}.title must be at least 5 characters (got {len(title)})")

        scope = change.get("scope", "")
        if scope not in VALID_SCOPES:
            errors.append(f"{prefix}.scope '{scope}' is not valid")

        desc = change.get("description", "")
        if not desc or len(desc) < 10:
            errors.append(f"{prefix}.description must be at least 10 characters (got {len(desc)})")

        # Optional field validation
        priority = change.get("priority", "medium")
        if priority not in ("critical", "high", "medium", "low"):
            errors.append(f"{prefix}.priority '{priority}' is not valid")

        status = change.get("status", "deployed")
        valid_statuses = {"planned", "in_progress", "blocked", "implemented", "tested", "deployed"}
        if status not in valid_statuses:
            errors.append(f"{prefix}.status '{status}' is not valid")

        date = change.get("date", "")
        if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            errors.append(f"{prefix}.date '{date}' does not match YYYY-MM-DD format")

    return errors


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI entry point."""
    args = _parse_args(sys.argv[1:])

    # Validate repo path
    repo_path = Path(args["repo_path"]).resolve()
    if not repo_path.exists():
        print(f"ERROR: Repository path does not exist: {repo_path}")
        return 2

    git_dir = repo_path / ".git"
    if not git_dir.exists():
        print(f"ERROR: Not a git repository: {repo_path}")
        return 2

    # Validate date formats
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if args["since"] and not date_re.match(args["since"]):
        print(f"ERROR: --since date must be YYYY-MM-DD format (got '{args['since']}')")
        return 2
    if args["until"] and not date_re.match(args["until"]):
        print(f"ERROR: --until date must be YYYY-MM-DD format (got '{args['until']}')")
        return 2

    try:
        changelog = generate_retro_changelog(
            repo_path=str(repo_path),
            project_name=args["project_name"],
            default_author=args["author"],
            since=args["since"],
            until=args["until"],
            time_window_hours=args["time_window"],
        )
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 2

    # Validate before writing
    validation_errors = validate_changelog(changelog)
    if validation_errors:
        print()
        print(f"VALIDATION WARNINGS ({len(validation_errors)}):")
        for i, err in enumerate(validation_errors, 1):
            print(f"  {i}. {err}")
        print()
        print("The changelog will still be written but may need manual corrections.")

    changes = changelog.get("changes", [])
    if not changes:
        print()
        print("No changes to write — the changelog is empty.")
        return 1

    # Write output
    output_path = Path(args["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: .tmp then rename
    tmp_path = output_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(changelog, f, indent=2, ensure_ascii=False)
        tmp_path.replace(output_path)
    except OSError as e:
        print(f"ERROR: Failed to write output: {e}")
        return 2

    print()
    print(f"Output: {output_path.resolve()}")
    print(f"Entries: {len(changes)}")
    print()
    print("Next step: feed this into the retro TC generator:")
    print(f'  python generators/generate_retro_tcs.py "{output_path}" "docs/TC"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
