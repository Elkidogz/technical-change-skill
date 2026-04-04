## Technical Change (TC) Tracking (MANDATORY)

All code changes in this project are tracked using the TC (Technical Change) system.
TC records live at `docs/TC/` and are the system of record for what changed, why, and by whom.

### Session Start Protocol (MANDATORY)
On EVERY new session:
1. Read `docs/TC/tc_registry.json`
2. Find any TCs with status `in_progress` or `blocked`
3. If found: display the handoff summary (progress, next steps, blockers, key context)
4. Ask the user: "Resume TC-NNN? (y/n)"
5. If resuming: update session_context with new session info, continue from handoff next_steps

### Tracking Rules — Non-Blocking Subagent Pattern
TC tracking MUST NOT interrupt the main workflow. Follow these rules:

**During work: NEVER stop to update TC records inline.** Focus entirely on the task.

**At natural milestones** (feature complete, test passing, logical stopping point, or when asked):
- Spawn a **background Agent** to handle TC bookkeeping
- The background agent reads what files were changed, updates the active TC record
  (files_affected, revision_history, session_context), and regenerates HTML
- The main agent continues working without waiting

**Only surface a question when genuinely needed:**
- "This work doesn't match any active TC — should I create one?" (ask once, not per file)
- "TC-NNN looks complete — transition to implemented?" (suggest at milestones, don't nag)

**At session end or before a long pause:**
- Spawn a final background Agent to write the handoff summary (progress, next steps,
  blockers, key context, files in progress, decisions made)
- This is the critical data the next session needs

### TC Commands
- `/tc init` — Initialize TC tracking in this project (already done)
- `/tc create <name>` — Create a new TC record for a functionality
- `/tc update <tc-id>` — Update a TC (status, files, tests, notes, etc.)
- `/tc status [tc-id]` — View status of one TC or all TCs
- `/tc resume <tc-id>` — Resume work on a TC from a previous session
- `/tc close <tc-id>` — Transition TC to deployed + final approval
- `/tc export` — Regenerate all HTML files from JSON records
- `/tc dashboard` — Regenerate the dashboard index.html
- `/tc retro <changelog.json>` — Batch-create TCs from project history

### HTML Generation
After TC record changes, regenerate HTML:
```bash
python "{skills_library_path}/generators/generate_tc_html.py" "docs/TC/records/<tc-dir>/tc_record.json"
```

After registry changes (create/status change/close), regenerate dashboard:
```bash
python "{skills_library_path}/generators/generate_dashboard.py" "docs/TC/tc_registry.json"
```

### Research First (MANDATORY)
Before making code changes:
1. Read `docs/TC/tc_registry.json` to understand active work
2. Read relevant TC records to understand context and decisions
3. Check handoff data for context from previous sessions

### State Machine
Valid status transitions:
- planned → in_progress, blocked
- in_progress → blocked, implemented
- blocked → in_progress, planned
- implemented → tested, in_progress (rework)
- tested → deployed, in_progress (rework)
- deployed → in_progress (hotfix cycle)

Every state transition MUST include a reason and be recorded in revision_history.
