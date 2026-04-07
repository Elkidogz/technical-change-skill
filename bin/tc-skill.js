#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VERSION = '1.0.0';
const PACKAGE_ROOT = path.resolve(__dirname, '..');
const CWD = process.cwd();

const SKILL_DIRS = ['schemas', 'validators', 'generators', 'templates', 'init'];
const SKILL_FILES = ['SKILL.md'];

const AGENT_TARGETS = {
  '--claude': path.join('.claude', 'skills', 'tc'),
  '--cursor': path.join('.cursor', 'skills', 'tc'),
  '--codex':  path.join('.codex',  'skills', 'tc'),
  '--gemini': path.join('.gemini', 'skills', 'tc'),
};

const CATEGORIES = [
  'feature', 'bugfix', 'refactor', 'infrastructure',
  'documentation', 'hotfix', 'enhancement',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Recursively copy a directory tree. Creates destination dirs as needed. */
function copyDirSync(src, dest) {
  mkdirpSync(dest);
  const entries = fs.readdirSync(src, { withFileTypes: true });
  for (const entry of entries) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      // Skip __pycache__ and hidden dirs
      if (entry.name === '__pycache__' || entry.name.startsWith('.')) continue;
      copyDirSync(srcPath, destPath);
    } else {
      // Skip .pyc files
      if (entry.name.endsWith('.pyc') || entry.name.endsWith('.pyo')) continue;
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

/** mkdir -p equivalent */
function mkdirpSync(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

/** Detect project name from common sources in CWD. */
function detectProjectName() {
  // 1. CLAUDE.md first heading
  const claudeMdPath = path.join(CWD, 'CLAUDE.md');
  if (fs.existsSync(claudeMdPath)) {
    try {
      const content = fs.readFileSync(claudeMdPath, 'utf8');
      const match = content.match(/^#\s+(.+)/m);
      if (match) return match[1].trim();
    } catch (_) { /* ignore */ }
  }

  // 2. package.json name
  const pkgPath = path.join(CWD, 'package.json');
  if (fs.existsSync(pkgPath)) {
    try {
      const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
      if (pkg.name) return pkg.name;
    } catch (_) { /* ignore */ }
  }

  // 3. pyproject.toml name
  const pyprojectPath = path.join(CWD, 'pyproject.toml');
  if (fs.existsSync(pyprojectPath)) {
    try {
      const content = fs.readFileSync(pyprojectPath, 'utf8');
      const match = content.match(/^name\s*=\s*"([^"]+)"/m);
      if (match) return match[1];
    } catch (_) { /* ignore */ }
  }

  // 4. Directory basename
  return path.basename(CWD);
}

/** Write JSON to a file with 2-space indent. */
function writeJsonSync(filePath, data) {
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + '\n', 'utf8');
}

/** Format an ISO 8601 timestamp. */
function isoNow() {
  return new Date().toISOString();
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

/**
 * npx tc-skill init
 *
 * 1. Copy skill files into .tc-skill/
 * 2. Create docs/TC/ directory structure
 * 3. Create tc_config.json and tc_registry.json
 */
function cmdInit() {
  const tcRoot = path.join(CWD, 'docs', 'TC');
  const configPath = path.join(tcRoot, 'tc_config.json');

  // Check if already initialized
  if (fs.existsSync(configPath)) {
    console.log('TC tracking is already initialized in this project.');
    try {
      const registry = JSON.parse(
        fs.readFileSync(path.join(tcRoot, 'tc_registry.json'), 'utf8')
      );
      const total = registry.statistics ? registry.statistics.total : 0;
      console.log('  Records: ' + total);
      if (registry.statistics && registry.statistics.by_status) {
        const s = registry.statistics.by_status;
        const active = (s.in_progress || 0) + (s.blocked || 0);
        if (active > 0) {
          console.log('  Active (in_progress + blocked): ' + active);
        }
      }
    } catch (_) { /* ignore */ }
    console.log('\nRun "npx tc-skill install" to install the SKILL.md into your agent.');
    return;
  }

  const projectName = detectProjectName();
  const now = isoNow();
  const tcSkillDir = path.join(CWD, '.tc-skill');

  console.log('Initializing TC tracking for project: ' + projectName);
  console.log('');

  // --- Step 1: Copy skill files into .tc-skill/ ---
  console.log('Copying skill files to .tc-skill/ ...');
  mkdirpSync(tcSkillDir);

  for (const dir of SKILL_DIRS) {
    const src = path.join(PACKAGE_ROOT, dir);
    if (fs.existsSync(src)) {
      copyDirSync(src, path.join(tcSkillDir, dir));
    }
  }
  for (const file of SKILL_FILES) {
    const src = path.join(PACKAGE_ROOT, file);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(tcSkillDir, file));
    }
  }

  // --- Step 2: Create docs/TC/ directory structure ---
  console.log('Creating docs/TC/ directory structure ...');
  mkdirpSync(path.join(tcRoot, 'records'));
  mkdirpSync(path.join(tcRoot, 'evidence'));

  // --- Step 3: Create tc_config.json ---
  const skillsLibraryPath = path.resolve(tcSkillDir);
  const config = {
    project_name: projectName,
    tc_root: 'docs/TC',
    created: now,
    skills_library_path: skillsLibraryPath,
    auto_track: true,
    auto_regenerate_html: true,
    auto_regenerate_dashboard: true,
    default_author: 'Claude',
    categories: CATEGORIES,
  };
  writeJsonSync(configPath, config);

  // --- Step 4: Create tc_registry.json ---
  const registry = {
    project_name: projectName,
    created: now,
    updated: now,
    next_tc_number: 1,
    records: [],
    statistics: {
      total: 0,
      by_status: {
        planned: 0,
        in_progress: 0,
        blocked: 0,
        implemented: 0,
        tested: 0,
        deployed: 0,
      },
      by_scope: {
        feature: 0,
        bugfix: 0,
        refactor: 0,
        infrastructure: 0,
        documentation: 0,
        hotfix: 0,
        enhancement: 0,
      },
      by_priority: {
        critical: 0,
        high: 0,
        medium: 0,
        low: 0,
      },
    },
  };
  writeJsonSync(path.join(tcRoot, 'tc_registry.json'), registry);

  // --- Report ---
  console.log('');
  console.log('Created:');
  console.log('  .tc-skill/                   Skill files (schemas, validators, generators, templates)');
  console.log('  .tc-skill/SKILL.md           Skill definition');
  console.log('  docs/TC/tc_config.json       Project configuration');
  console.log('  docs/TC/tc_registry.json     Master TC registry');
  console.log('  docs/TC/records/             TC record storage');
  console.log('  docs/TC/evidence/            Test evidence storage');
  console.log('');
  console.log('Next steps:');
  console.log('  1. Run "npx tc-skill install" to install SKILL.md into your agent');
  console.log('  2. Use /tc create <name> to start tracking a change');
  console.log('');
  console.log('Tip: Add .tc-skill/ to .gitignore if you don\'t want skill source in your repo.');
}

/**
 * npx tc-skill install [--claude|--cursor|--codex|--gemini]
 *
 * Copies SKILL.md into the agent's skill directory.
 */
function cmdInstall(args) {
  // Determine target agent(s)
  let targets = [];
  for (const arg of args) {
    if (AGENT_TARGETS[arg]) {
      targets.push({ flag: arg, dir: AGENT_TARGETS[arg] });
    }
  }

  // Default to --claude if no target specified
  if (targets.length === 0) {
    targets.push({ flag: '--claude', dir: AGENT_TARGETS['--claude'] });
  }

  // Find SKILL.md source — prefer .tc-skill/ (from init), fall back to package root
  let skillSrc = path.join(CWD, '.tc-skill', 'SKILL.md');
  if (!fs.existsSync(skillSrc)) {
    skillSrc = path.join(PACKAGE_ROOT, 'SKILL.md');
  }

  if (!fs.existsSync(skillSrc)) {
    console.error('Error: SKILL.md not found. Run "npx tc-skill init" first.');
    process.exit(1);
  }

  // Resolve the skills_library_path to embed in SKILL.md
  // If .tc-skill/ exists, use that; otherwise use the npm package location
  let skillsLibraryPath = path.join(CWD, '.tc-skill');
  if (!fs.existsSync(skillsLibraryPath)) {
    skillsLibraryPath = PACKAGE_ROOT;
  }

  // Read SKILL.md content
  let skillContent = fs.readFileSync(skillSrc, 'utf8');

  for (const target of targets) {
    const destDir = path.join(CWD, target.dir);
    const destFile = path.join(destDir, 'SKILL.md');

    mkdirpSync(destDir);
    fs.writeFileSync(destFile, skillContent, 'utf8');

    console.log('Installed SKILL.md to ' + path.relative(CWD, destFile));
  }

  // Also install the CLAUDE.md snippet for Claude agent
  const hasClaude = targets.some(function(t) { return t.flag === '--claude'; });
  if (hasClaude) {
    installClaudeMdSnippet(skillsLibraryPath);
    installClaudeSettings(skillsLibraryPath);
  }

  console.log('');
  console.log('Done. Your agent will now recognize /tc commands.');
}

/**
 * Append the TC tracking snippet to CLAUDE.md if not already present.
 */
function installClaudeMdSnippet(skillsLibraryPath) {
  const snippetSrc = path.join(skillsLibraryPath, 'init', 'claude_md_snippet.md');
  if (!fs.existsSync(snippetSrc)) return;

  const claudeMdPath = path.join(CWD, 'CLAUDE.md');
  const marker = '## Technical Change (TC) Tracking (MANDATORY)';

  let existing = '';
  if (fs.existsSync(claudeMdPath)) {
    existing = fs.readFileSync(claudeMdPath, 'utf8');
    if (existing.includes(marker)) {
      console.log('CLAUDE.md already contains TC tracking rules (skipped).');
      return;
    }
  }

  let snippet = fs.readFileSync(snippetSrc, 'utf8');
  // Replace {skills_library_path} placeholder with actual path
  snippet = snippet.split('{skills_library_path}').join(skillsLibraryPath);

  const separator = existing.length > 0 ? '\n\n' : '';
  fs.writeFileSync(claudeMdPath, existing + separator + snippet + '\n', 'utf8');
  console.log('Updated CLAUDE.md with TC tracking rules.');
}

/**
 * Merge TC permissions into .claude/settings.local.json.
 */
function installClaudeSettings(skillsLibraryPath) {
  const templateSrc = path.join(skillsLibraryPath, 'init', 'settings_template.json');
  if (!fs.existsSync(templateSrc)) return;

  const settingsDir = path.join(CWD, '.claude');
  const settingsPath = path.join(settingsDir, 'settings.local.json');

  // Read the template and substitute the path
  let templateContent = fs.readFileSync(templateSrc, 'utf8');
  templateContent = templateContent.split('{skills_library_path}').join(
    skillsLibraryPath.split(path.sep).join('/')
  );
  const template = JSON.parse(templateContent);
  const newPerms = template.permissions && template.permissions.allow
    ? template.permissions.allow
    : [];

  if (newPerms.length === 0) return;

  // Read existing settings or create default
  let settings = { permissions: { allow: [] } };
  if (fs.existsSync(settingsPath)) {
    try {
      settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
      if (!settings.permissions) settings.permissions = {};
      if (!Array.isArray(settings.permissions.allow)) settings.permissions.allow = [];
    } catch (_) {
      settings = { permissions: { allow: [] } };
    }
  }

  // Merge and deduplicate
  const existingSet = new Set(settings.permissions.allow);
  let added = 0;
  for (const perm of newPerms) {
    if (!existingSet.has(perm)) {
      settings.permissions.allow.push(perm);
      existingSet.add(perm);
      added++;
    }
  }

  if (added > 0) {
    mkdirpSync(settingsDir);
    writeJsonSync(settingsPath, settings);
    console.log('Updated .claude/settings.local.json with TC permissions (' + added + ' added).');
  } else {
    console.log('.claude/settings.local.json already has TC permissions (skipped).');
  }
}

// ---------------------------------------------------------------------------
// Help / Version
// ---------------------------------------------------------------------------

function printHelp() {
  console.log('');
  console.log('tc-skill v' + VERSION);
  console.log('');
  console.log('A structured code change tracking system for AI coding agents.');
  console.log('');
  console.log('USAGE');
  console.log('  npx tc-skill <command> [options]');
  console.log('');
  console.log('COMMANDS');
  console.log('  init        Initialize TC tracking in the current project');
  console.log('              - Copies skill files into .tc-skill/');
  console.log('              - Creates docs/TC/ directory structure');
  console.log('              - Creates tc_config.json and tc_registry.json');
  console.log('');
  console.log('  install     Install SKILL.md into your agent\'s skill directory');
  console.log('              --claude    .claude/skills/tc/SKILL.md  (default)');
  console.log('              --cursor    .cursor/skills/tc/SKILL.md');
  console.log('              --codex     .codex/skills/tc/SKILL.md');
  console.log('              --gemini    .gemini/skills/tc/SKILL.md');
  console.log('              Multiple targets can be combined:');
  console.log('              npx tc-skill install --claude --cursor');
  console.log('');
  console.log('OPTIONS');
  console.log('  --help, -h      Show this help message');
  console.log('  --version, -v   Show version number');
  console.log('');
  console.log('EXAMPLES');
  console.log('  npx tc-skill init                Set up TC tracking in your project');
  console.log('  npx tc-skill install             Install for Claude Code (default)');
  console.log('  npx tc-skill install --cursor    Install for Cursor');
  console.log('  npx tc-skill install --claude --gemini');
  console.log('                                   Install for multiple agents');
  console.log('');
  console.log('WORKFLOW');
  console.log('  1. cd your-project');
  console.log('  2. npx tc-skill init');
  console.log('  3. npx tc-skill install');
  console.log('  4. Use /tc create <name> to start tracking changes');
  console.log('');
  console.log('MORE INFO');
  console.log('  https://github.com/Elkidogz/technical-change-skill');
  console.log('');
}

function printVersion() {
  console.log(VERSION);
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

function main() {
  const args = process.argv.slice(2);

  // No arguments
  if (args.length === 0) {
    printHelp();
    return;
  }

  const command = args[0];

  // Global flags that can appear anywhere
  if (args.includes('--help') || args.includes('-h')) {
    printHelp();
    return;
  }

  if (args.includes('--version') || args.includes('-v')) {
    printVersion();
    return;
  }

  switch (command) {
    case 'init':
      cmdInit();
      break;

    case 'install':
      cmdInstall(args.slice(1));
      break;

    default:
      console.error('Unknown command: ' + command);
      console.error('Run "npx tc-skill --help" for usage.');
      process.exit(1);
  }
}

main();
