## Technical Change (TC) Tracking (MANDATORY)

All code changes in this project MUST be tracked using the TC (Technical Change) system.
TC records live at `docs/TC/` and are the system of record for what changed, why, and by whom.

### Session Start Protocol (MANDATORY)
On EVERY new session:
1. Read `docs/TC/tc_registry.json`
2. Find any TCs with status `in_progress` or `blocked`
3. If found: display the handoff summary (progress, next steps, blockers, key context)
4. Ask the user: "Resume TC-NNN? (y/n)"
5. If resuming: update session_context with new session info, continue from handoff next_steps

### Auto-Tracking Rules (MANDATORY)
After EVERY code change (file create/modify/delete):
1. Read `docs/TC/tc_registry.json` — find any `in_progress` TCs
2. If an active TC exists:
   - Add/update the file in `files_affected` (path, action, description)
   - Update `session_context.current_session.last_active`
   - Append a lightweight revision entry to `revision_history`
   - If `auto_regenerate_html` is true in tc_config.json: regenerate the TC's HTML
3. If NO active TC exists:
   - Prompt: "This change to {filename} isn't tracked by a TC. Create one? (/tc create)"
   - If user declines repeatedly, respect that for the session

### TC Commands
- `/tc init` — Initialize TC tracking in this project (already done)
- `/tc create <name>` — Create a new TC record for a functionality
- `/tc update <tc-id>` — Update a TC (status, files, tests, notes, etc.)
- `/tc status [tc-id]` — View status of one TC or all TCs
- `/tc resume <tc-id>` — Resume work on a TC from a previous session
- `/tc close <tc-id>` — Transition TC to deployed + final approval
- `/tc export` — Regenerate all HTML files from JSON records
- `/tc dashboard` — Regenerate the dashboard index.html

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
