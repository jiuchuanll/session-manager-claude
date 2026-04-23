# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Code skill (plugin) that manages session naming through a three-tier defense system. It installs into `~/.claude/skills/session-manager-claude/` and operates on Claude Code's `.jsonl` transcript files under `~/.claude/projects/`.

## Architecture

Three independent layers ensure every session gets named:

1. **`/bye` command** (interactive, primary) — User triggers before exiting. Claude generates a name from conversation context (zero API cost), user confirms, saved as `user_confirmed`.
2. **SessionEnd hook** (`session-namer.py`) — Fires on `/exit`. If already `user_confirmed`, writes that name as the final title. Otherwise calls an external LLM API (configurable, default zhipu-flash) to auto-generate, saved as `auto`.
3. **SessionStart hook** (`session-start-reminder.py`) — Fires on new session. Scans ALL workspaces for `auto` or unnamed sessions, outputs reminder text that Claude presents to the user.

The `/bye` command is defined in `commands/bye.md` (copied to `~/.claude/commands/`) and triggers the flow described in `SKILL.md`.

## Key Data Files (not in repo, gitignored)

- `session-meta.json` — Tracks naming state per session (`namingStatus`: `user_confirmed` | `auto` | `renamed`). Source of truth for whether a session needs naming.
- `session-namer-config.json` — LLM API credentials for SessionEnd auto-naming. Created via `--setup`.
- `logs/session-namer.log` — Debug log for SessionEnd hook.

## File Modification Pattern

All scripts that modify `.jsonl` files use the same atomic-write pattern:
1. Read all lines, filter out ALL existing `custom-title` and `agent-name` entries
2. Append exactly one `custom-title` + one `agent-name` entry at EOF
3. Write to `.tmp`, then `os.replace()` with retry loop (5 attempts, 0.5s apart) for Windows file lock handling

This logic is duplicated in `session-namer.py` and `session-rename.py` as `modify_title_in_jsonl()`.

## Workspace Key Convention

Claude Code stores transcripts at `~/.claude/projects/<workspace-key>/<session-id>.jsonl`. The workspace key is derived from the working directory: `D:\code\project` becomes `D--code-project` (colons and slashes replaced with hyphens, leading hyphen stripped).

## Testing

No test suite. Manual testing:

```bash
# Test SessionEnd hook with fake stdin
echo '{"session_id":"abc","transcript_path":"path/to/file.jsonl","cwd":"D:\\code\\project"}' | python scripts/session-namer.py

# Test API config
python scripts/session-namer.py --test
python scripts/session-namer.py --show

# Test session listing
python scripts/session-list.py --workspace all --json

# Test rename (--check is read-only)
python scripts/session-rename.py --current-dir "D:\code\project" --check

# Test cleanup candidates (read-only)
python scripts/session-clean.py --list
```

## Development Notes

- All scripts call `sys.stdout.reconfigure(encoding='utf-8')` at module level for Windows compatibility.
- All scripts output JSON to stdout. The skill (SKILL.md) instructs Claude to format this as user-friendly tables.
- Hook scripts receive session context via stdin JSON (fields: `session_id`, `transcript_path`, `cwd`, `session_id`).
- `_SKILL_DIR` is always resolved as `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` — the skill root relative to `scripts/`.
- The repo is the source; deployment target is `~/.claude/skills/session-manager-claude/`. After editing, sync files to the skill directory.
- Prompt pollution detection: if an auto-generated name contains 2+ fragments from the naming prompt itself, it's discarded. This guards against LLM echo.
- SessionEnd hook exits with code 0 even on fatal errors (`sys.exit(0)`) to avoid blocking Claude Code shutdown.
- Context reduction on API failure: if the LLM returns "context too long", retry with fewer messages (30 -> 20 -> 10).

## Hook Configuration

Hooks are configured in `~/.claude/settings.json` (not in this repo). The repo's `.claude/settings.local.json` only contains permission rules for development.

```json
{
  "hooks": {
    "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": "python \"path/to/session-namer.py\"", "timeout": 30000}]}],
    "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "python \"path/to/session-start-reminder.py\"", "timeout": 10000}]}]
  }
}
```
