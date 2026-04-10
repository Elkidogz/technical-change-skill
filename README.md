# TC (Technical Change) Skill

A structured code change tracking system for [Claude Code](https://claude.ai/claude-code). Documents every code change with JSON records and accessible HTML output, enabling AI bot sessions to seamlessly resume work when previous sessions expire or are abandoned.

![TC Dashboard — dark theme with status overview, filterable TC list, activity timeline, and scope breakdown](docs/TC/screenshots/dashboard.png)

![Individual TC Record — full detail view with overview, revision history, session handoff, and approval status](docs/TC/screenshots/tc-record.png)

## What It Does

- **Tracks every code change** with structured JSON records (what, why, who, when)
- **AI session handoff** — when a bot session expires, the next one picks up exactly where it left off
- **Accessible HTML output** — dark theme, WCAG AA+ compliant, rem-based fonts for low-vision users
- **State machine** — enforces a strict lifecycle: Planned > In Progress > Blocked > Implemented > Tested > Deployed > Paused > Voided
- **Git integration** — link commits, auto-link on commit, find unlinked TCs, merge driver for registry conflicts
- **Online PR sync (opt-in)** — enrich TCs with GitHub/GitLab PR metadata via `gh`/`glab` CLI
- **Test cases with evidence** — structured test protocols with log snippet proofs
- **Dashboard** — CSS-only filterable overview of all TCs in a project (no JavaScript required)
- **Cross-project** — deploy to any project with `/tc init`

## Quick Start

### Claude Code (default)

```bash
# Clone and install as a skill
git clone https://github.com/Elkidogz/technical-change-skill.git ~/.claude/skills/tc
```

Then in any project: `/tc init` to set up tracking, `/tc create <name>` to start.

### Cursor

```bash
git clone https://github.com/Elkidogz/technical-change-skill.git .cursor/skills/tc
```

Invoke with `/tc` or `@tc` in Cursor's agent.

### Codex CLI (OpenAI)

```bash
git clone https://github.com/Elkidogz/technical-change-skill.git .agents/skills/tc
```

Reference with `$tc` or via `/skills` listing.

### Gemini CLI (Google)

```bash
git clone https://github.com/Elkidogz/technical-change-skill.git .gemini/skills/tc
```

Gemini auto-activates skills based on context — no manual trigger needed.

### GitHub Copilot

```bash
git clone https://github.com/Elkidogz/technical-change-skill.git .github/skills/tc
```

Invoke with `/tc` or via `/skills` command.

## Commands

### Core

| Command | Description |
|---------|-------------|
| `/tc init` | Initialize TC tracking in the current project |
| `/tc create <name>` | Create a new TC record for a functionality |
| `/tc update <tc-id>` | Update a TC (status, files, tests, notes) |
| `/tc status [tc-id]` | View status of one or all TCs |
| `/tc resume <tc-id>` | Resume work from a previous session's handoff |
| `/tc close <tc-id>` | Transition to deployed + final approval |
| `/tc export` | Regenerate all HTML from JSON records |
| `/tc dashboard` | Regenerate the dashboard index.html |
| `/tc retro` | Bulk-create TCs from git history or changelog |

### Git Integration (Offline)

| Command | Description |
|---------|-------------|
| `/tc link <tc-id> [sha]` | Link commit(s) to a TC record (default: HEAD) |
| `/tc git status` | Show linked/unlinked TCs, unlinked commits on branch |
| `/tc git install-merge-driver` | Register 3-way merge driver for `tc_registry.json` |

### Online (Opt-in — requires `gh` or `glab` CLI)

| Command | Description |
|---------|-------------|
| `/tc pr link <tc-id> [pr#]` | Link a GitHub/GitLab PR to a TC |
| `/tc sync [tc-id]` | Refresh PR metadata (state, review, merge commit) |

## TC Naming Convention

```
TC-NNN-MM-DD-YY-functionality-slug     (parent TC)
TC-NNN.A                                (sub-TC revision A)
TC-NNN.A.1                              (sub-revision 1 of revision A)
```

## State Machine

```
planned --> in_progress --> implemented --> tested --> deployed
   |             |               |            |          |
   +-> blocked <-+               +-> in_progress <------+
   |    |  |                        (rework/hotfix)
   |    |  +-> paused --> in_progress
   |    |       |
   |    +------→+-> voided (terminal — cancelled)
   +-> voided
```

| Status | Meaning |
|--------|---------|
| `planned` | Scoped but not started |
| `in_progress` | Active development |
| `blocked` | Waiting on dependency/decision |
| `paused` | Dev work temporarily stopped |
| `implemented` | Code complete, ready for testing |
| `tested` | Tests pass, ready for deploy |
| `deployed` | Live in production |
| `voided` | Cancelled entirely (terminal) |

Every transition requires a reason and is recorded in the append-only revision history.

## Git Integration

The TC system integrates with git at two levels:

### Offline (default — works without network)
- **`/tc link`** — connect commits to TC records, building a commit table in the TC's `git.commits[]` block
- **Auto-link hook** — PostToolUse hook fires after `git commit`, automatically links HEAD to the single in-progress TC
- **Pre-commit advisory** — PreToolUse hook warns (non-blocking) if staged files aren't tracked by any active TC
- **`/tc git status`** — shows TCs without git data, unlinked commits on branch, uncommitted files matching active TCs
- **Registry merge driver** — 3-way merge for `tc_registry.json` conflicts (union records by tc_id, recompute stats)
- **`/tc retro --from-git`** — auto-generates TCs from git history with commit SHAs preserved in the `git` block

### Online (opt-in — requires `gh` or `glab` CLI)
- **`/tc pr link`** — enrich a TC with PR metadata (number, URL, state, review decision)
- **`/tc sync`** — refresh PR state for all linked TCs (merged? approved? review requested?)
- Graceful degradation: if CLI is missing or unauthenticated, reports the issue and exits cleanly

### Session Lifecycle
- **Session start** — displays handoff summary for active TCs including git context (branch, last commit, PR state)
- **Session end** — archives current session, captures uncommitted files, writes handoff for next session

## Per-Project Structure

When you run `/tc init`, it creates:

```
{project}/docs/TC/
  tc_config.json          Project settings
  tc_registry.json        Master index of all TCs
  index.html              Dashboard with metrics + git badges
  records/
    TC-001-04-03-26-name/
      tc_record.json      System of record (with optional git block)
      tc_record.html      Human-readable with Git Activity section
  evidence/
    TC-001/               Log snippets, screenshots
```

### TC Record — Git Block (optional)

Each TC record can carry an optional `git` block:

```json
{
  "git": {
    "initial_branch": "feature/auth",
    "commits": [
      {
        "sha": "abc1234...",
        "short_sha": "abc1234",
        "author": "Elkidogz",
        "subject": "Add login endpoint",
        "link_source": "auto-hook"
      }
    ],
    "remotes": [
      {
        "provider": "github",
        "pr": { "number": 42, "state": "merged" }
      }
    ],
    "release_tags": ["v1.2.0"]
  }
}
```

TCs without a `git` block remain fully valid — the field is additive and backward-compatible.

## Skill Structure

```
TC/
  SKILL.md                  Skill definition (14 commands + auto-detection rules)
  schemas/                  JSON Schemas for records, registry, config, retro changelog
  validators/               Python state machine + schema validation (incl. git block)
  generators/
    generate_tc_html.py     Per-TC HTML with Git Activity section
    generate_dashboard.py   Dashboard with git badges on TC cards
    generate_retro_tcs.py   Batch TC creation (populates git block from retro data)
    generate_retro_from_git.py  Git log → retro changelog with commit metadata
    tc_git_link.py          /tc link command
    tc_git_status.py        /tc git status command
    tc_git_autolink.py      Post-commit auto-link hook
    tc_precommit_check.py   Pre-commit advisory hook
    tc_registry_merge.py    3-way merge driver for tc_registry.json
    tc_pr_link.py           /tc pr link (online, opt-in)
    tc_sync.py              /tc sync (online, opt-in)
    tc_session_start.py     Session start report with handoff
    tc_session_end.py       Session end — archive + write handoff
  templates/                Accessible CSS + HTML templates (paused/voided badges)
  init/                     CLAUDE.md snippet, settings template with hook config
  examples/                 Complete worked examples
```

## Key Design Decisions

- **Python stdlib only** — zero external dependencies, runs anywhere Python 3.10+ exists
- **CSS-only interactivity** — dashboard filters work without JavaScript, files open from `file://` URLs
- **Append-only history** — revision entries are never modified or deleted
- **Self-contained HTML** — CSS is inlined into every generated page
- **Accessible** — WCAG AA+ contrast ratios, rem-based sizing, skip links, aria labels, print stylesheet

## Accessibility

All generated HTML is designed for low-vision users:
- Dark theme with high contrast (13:1+ body text, 7:1+ code text)
- All sizing in `rem` — scales with browser font size / Ctrl+Plus zoom
- Skip links and keyboard navigation support
- Semantic HTML with aria labels
- Print stylesheet (white background, dark text)

## License

MIT
