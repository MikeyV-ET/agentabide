# Grok CLI Reference — Build 0.1.159-alpha.1

Documented: 2026-03-25 by MikeyV-Sr via reverse engineering (`--help` + probing).

## Execution Modes

### `grok` (default TUI)
Interactive terminal UI. Runs agent in-process by default.

Key options:
- `--leader` / `--no-leader` — connect to shared leader or run in-process
- `--resume <SESSION_ID>` — resume a specific session
- `--continue` — continue most recent session for CWD
- `--model <MODEL>` — override model (`grok models` to list)
- `--yolo` — auto-approve all tool executions
- `--prompt <TEXT>` — pass a prompt immediately after startup
- `--rules <RULES>` — extra system prompt rules (`@file` to load)
- `--agent-profile <PATH>` — agent profile (.md with YAML frontmatter)
- `--subagents` — enable task tool for child sessions
- `--experimental-memory` — cross-session persistence via MEMORY.md
- `--sandbox off|workspace|read-only|strict` — OS-level guardrails
- `--output-format plain|json|streaming-json`
- `-p <PROMPT>` — single-turn, print response and exit

### `grok serve`
WebSocket server. Exposes a session over WebSocket. Clients connect TO the agent.

```
grok serve [OPTIONS]
  --bind <ADDR>     Address (default: 127.0.0.1:2419)
  --secret <TOKEN>  Auth token (auto-generated if omitted)
  --remote <URL>    Optional remote agent URL (proxy/relay)
```

**Protocol:** JSON-RPC 2.0 over `ws://<addr>/ws?server-key=<secret>`

**Handshake:**
1. Client sends `initialize` with `protocolVersion`, `capabilities`, `clientInfo`
2. Server responds with `agentCapabilities`
3. Client sends `notifications/initialized`

**Methods discovered:**
| Method | Params | Purpose |
|--------|--------|---------|
| `initialize` | `protocolVersion`, `capabilities`, `clientInfo` | Handshake |
| `session/new` | `cwd`, `mcpServers` (array) | Create new session |
| `session/load` | `sessionId`, `mcpServers` (array), `cwd` | Load existing session (replays history) |
| `session/prompt` | `sessionId`, `prompt` (array of content blocks) | Send prompt |

**Content blocks:** `[{"type": "text", "text": "..."}]`

**Response streaming:**
- `session/update` with `agent_message_chunk` — text chunks
- `session/update` with `agent_thought_chunk` — thinking
- `session/update` with `tool_call` / `tool_call_update` — tool use
- `_x.ai/session/prompt_complete` — turn complete
- Final `result` with `stopReason`, `totalTokens`, `modelId`

**Key behavior:**
- Loading existing session replays ALL history as `isReplay: true` frames (~19,500 frames for 33-compaction session)
- Prompts sent through serve are written to session state
- NOT concurrent with TUI — either serve OR TUI, not both
- Each instance isolated — no shared leader, no cascading failures

### `grok agent stdio`
JSON-RPC over stdin/stdout. For piping and subprocess control. No network, no sockets.

### `grok agent headless`
Connects to Grok WebSocket relay. No local UI. Runs autonomously.

### `grok agent leader`
Shared backend. Multiple clients connect via Unix socket (`~/.grok/leader.sock`).

Options: `--no-exit-on-disconnect` — keep running when last client disconnects.

**Known issues:**
- Single point of failure — storm takes all connected clients down
- Multiple TUI clients sharing one leader causes instability
- Large sessions (>70MB) may fail to re-register after storm
- Auto-spawns with full auth flags when `--leader` client starts with no leader running

### `grok acp <COMMAND> [ARGS]`
Run an external ACP agent as subprocess via ACP protocol.

## Management Commands

### `grok leader`
```
grok leader list              # List leader candidates
grok leader info              # Live info for a leader
grok leader kill              # Kill all leaders
grok leader profile status    # CPU profiling status
grok leader profile start     # Start CPU profiling
grok leader profile stop      # Stop CPU profiling
```

### `grok memory`
Cross-session persistence via MEMORY.md.
```
grok memory edit      # Open MEMORY.md in $EDITOR
grok memory stats     # Chunk/file counts, index size
grok memory reindex   # Re-index memory files
grok memory doctor    # Index health checks
```
Enable: `--experimental-memory` or `GROK_MEMORY=1` or `[memory] enabled = true` in config.toml.
Storage: `~/.grok/memory/` (global) + `~/.grok/memory/<workspace-hash>/` (per-workspace).

### `grok sessions`
```
grok sessions list    # List recent sessions
grok sessions search  # Search by keyword
```

### `grok instrumentation`
```
grok instrumentation flamegraph  # From instrumentation log
grok instrumentation trace       # Chrome trace (traceEvents JSON)
```

### Other
- `grok doctor` — diagnostic info (version, paths, installations)
- `grok share` — share session via URL
- `grok worktree` — manage git worktrees
- `grok models` — list available models
- `grok update` — manual update
- `grok login` — re-authenticate
- `grok completions` — shell completion scripts

## Models (from capabilities response)

| Model ID | Name | Context |
|----------|------|---------|
| `opus-4-6` | (current default) | 200k |
| `grok-4.20-0309-reasoning` | Grok 4.20 0309 | 2M |
| `sxs-kimi-k2-5` | grok_prod_train_0324_model_a | 200k |
| `grok-build-best-0323` | grok-build-best-0323 | ? |

## Architecture Implications

**`grok serve` replaces shared leader:**
- Each agent gets own `grok serve` on unique port
- Hub connects via WebSocket per agent
- No SPOF, no storm cascading
- Per-agent IRC channel = agent's "TUI"
- Eric uses TUI for troubleshooting (kill serve, start TUI, same session)

**`grok agent stdio` as alternative:**
- Hub spawns agent as subprocess, JSON-RPC over pipes
- No network at all — maximum isolation

**`grok leader profile` for storm diagnosis:**
- Built-in CPU profiling during storms
- `grok instrumentation flamegraph` for visualization

**`grok memory` for cross-session persistence:**
- Built-in alternative to notes_to_self
- Indexed and searchable — needs investigation

## serve vs stdio Comparison

| | `grok serve` | `grok agent stdio` |
|---|---|---|
| Transport | WebSocket (network) | stdin/stdout (pipes) |
| Lifecycle | Long-running server, hub connects when needed | Hub spawns as subprocess |
| Process management | Hub must track port/secret per agent | Hub owns the process directly |
| Isolation | Full (separate process, separate port) | Full (separate process, no network) |
| Session load | Yes (`session/load` with replay) | Yes (`session/load` with replay) |
| Debugging | Eric can connect second WS client | Must kill process, start TUI |
| Protocol | JSON-RPC 2.0 (identical) | JSON-RPC 2.0 (identical) |

## Multi-Client Behavior (tested 2026-03-25)

**`grok serve` accepts multiple simultaneous WebSocket connections.** Both clients can initialize and send prompts. However, response streaming routes to only ONE client (the most recent connection) — same output-channel-stealing behavior as the shared leader. Not usable for multiplexing.

**Conclusion:** One client per `grok serve` instance. For local work, `stdio` is simpler (hub spawns subprocess, talks over pipes). `serve` is for distribution across machines.

---

# Build 0.1.159-alpha.2 — Changes and Additions

Documented: 2026-03-26 by MikeyV-Trip via `--help` probing of new binary drop.
Previous build: 0.1.159-alpha.1. Commit: `4d3ca1c02`.

## Version Info

```
$ grok --version
grok 0.1.159-alpha.2 (4d3ca1c02)

$ grok version
grok 0.1.159-alpha.2 (4d3ca1c02) (alpha channel)

$ grok doctor
  Diagnostics
  └ Currently running: npm (0.1.159-alpha.2)
  └ Path: /home/eric/.grok/bin/grok
  └ Invoked: /home/eric/.grok/bin/grok-0.1.159-alpha.2
  └ Latest version: 0.1.159-alpha.2 (up to date)
  └ Channel: alpha
  └ Auto-updates: enabled
  └ OS: linux-x86_64
  └ Canonical path in PATH: no
  Installations
  └ /home/eric/.grok/bin/grok (0.1.159-alpha.2) (canonical, current)
  └ /home/eric/.nvm/versions/node/v22.22.1/bin/grok (0.1.159-alpha.2) (npm shim)
```

`grok version` subcommand now shows channel info (`alpha channel`). `grok doctor` now shows `Invoked:` path (resolved symlink target) and `Canonical path in PATH` check.

## New Top-Level Flags

These flags are NEW in alpha.2 (not present in alpha.1):

| Flag | Purpose |
|------|---------|
| `--prompt-json <JSON>` | Single-turn prompt as JSON content blocks (array or `{"type":"acp","content":[...]}`) |
| `--prompt-file <PATH>` | Single-turn prompt from a file (.json parsed as content blocks, otherwise as text) |
| `--verbatim` | Skip `<user_query>` wrapping and large-prompt truncation |
| `--single-turn` | Single-turn mode, wait for turn end and quit (distinct from `-p` which also prints and exits) |
| `--plugin-dir <PATH>` | Load a plugin directory. Can be repeated for multiple plugins |
| `-w, --worktree` | Create a new git worktree and start the session there. Can be combined with `-r/--resume` |
| `--light` | Use light theme (macOS Basic) |
| `--my-theme` | Use saved custom theme from `~/.grok/custom-theme.toml` |
| `--color <MODE>` | Override terminal color detection: `auto`, `truecolor`, `256`, `basic`, `none` |
| `--enable-prof` | Enable memory profiling (jemalloc + macOS MallocStackLogging). Restarts process with profiling env vars. Dumps to `/tmp/grok_heap.<pid>.<seq>.heap` |
| `--no-memory` | Disable cross-session memory persistence for this session. Overrides all other memory configs |
| `--disable-web-search` | Disable web search tool entirely. Useful for benchmarks |
| `--cwd <CWD>` | Working directory override (defaults to current folder) |

### Changed flags (alpha.1 → alpha.2)

| Flag | Change |
|------|--------|
| `--sandbox` | Now documented with env var `GROK_SANDBOX=` and clearer profile descriptions |
| `--subagents` | Now documented with env var `GROK_SUBAGENTS=1` and config.toml path |
| `--experimental-memory` | Now documented with env var `GROK_MEMORY=1` and config.toml path |

## Subcommand Changes

### `grok agent`

New options:
- `--reauth` (alias `--reauthenticate`) — force authentication flow
- Auth URL overrides: `--auth-signin-url`, `--auth-exchange-code-url`, `--auth-redirect-target`, `--grok-ws-origin`, `--grok-ws-url`

New subcommand: `grok agent serve` — identical to `grok serve` but nested under `agent`. Same `--bind`, `--secret`, `--remote` options.

### `grok agent leader`

New flag:
- `--no-auto-update` — Disable the periodic auto-update checker. Prevents the leader from shutting down to apply updates.

### `grok update`

Significantly expanded:
```
grok update [OPTIONS]
  --check            Check for updates without installing
  --json             Emit machine-readable JSON output (for --check)
  --force-reinstall  Force re-download and install even if already up to date
  --version <VER>    Install a specific version (e.g. 0.1.150 or 0.1.151-alpha.2)
  --alpha            Switch to the alpha release channel
  --stable           Switch to the stable release channel (default, weekly)
```

Previously just `grok update` with no options. Now supports channel switching, version pinning, and dry-run checks.

### `grok completions`

Expanded shell support:
- **alpha.1:** bash, zsh
- **alpha.2:** bash, elvish, fish, powershell, zsh

### `grok sessions`

Description changed: "List, search, or restore sessions from other devboxes" (added "restore" and "devboxes").

`grok sessions search` now has `-n, --limit <LIMIT>` flag (default: 20).
`grok sessions list` also has `-n, --limit <LIMIT>` flag (default: 20).

### `grok worktree`

Major expansion. New subcommands:
```
grok worktree list              # List tracked worktrees
grok worktree show <ID_OR_PATH> # Show details for a specific worktree
grok worktree rm <IDS>...       # Remove worktrees (--force, --dry-run)
grok worktree gc                # Garbage-collect orphaned/stale worktrees
                                #   --dry-run, --max-age, --force
grok worktree db rebuild        # Rebuild DB from filesystem scan
grok worktree db stats          # Show DB statistics
grok worktree db path           # Print DB file path
```

Previously only `grok worktree` with no documented subcommands. Now has full lifecycle management with a backing database.

### `grok share`

Now takes `<SESSION_ID>` as a required positional argument:
```
grok share <SESSION_ID>
```

### `grok memory`

Memory system now uses workspace-scoped directories:
```
Global dir:    ~/.grok/memory/
Workspace dir: ~/.grok/memory/mikeyv-workspace-<hash>/
```

Each workspace gets its own `MEMORY.md` and index. `grok memory doctor` checks index health and suggests `grok memory reindex`.

`grok memory edit` gained `--global` flag to fall back to global MEMORY.md.

## Global Flags (all subcommands)

Every subcommand now accepts these override flags:
```
--cli-chat-proxy-base-url <URL>  # Override cli chat proxy (default: https://cli-chat-proxy.grok.com/v1)
--xai-api-base-url <URL>         # Override xAI API (default: https://api.x.ai/v1)
```

These were not present in alpha.1. They allow pointing the CLI at alternative backends.

## Models (alpha.2)

Significantly expanded model list. New entries marked with ★:

| Model ID | Notes |
|----------|-------|
| `grok-4.20-0309-reasoning` | 2M context (unchanged) |
| `sxs-kimi-k2-5` | (unchanged) |
| `grok-build-best-0323` | (unchanged) |
| `coding-mix-latest` | ★ NEW — Sr/Jr model |
| `opus-4-5` | ★ NEW |
| `opus-4-6` | Current default (unchanged) |
| `opus-4-1` | ★ NEW |
| `sonnet-4` | ★ NEW |
| `sonnet-4-5` | ★ NEW |
| `grok-webdev-rkld-0308` | ★ NEW |
| `grok-code-mzhu-webdev-0308` | ★ NEW |
| `gpt-5-2-codex` | ★ NEW |
| `sxs-gpt-5-3-codex` | ★ NEW |
| `sxs-gpt-5-4` | ★ NEW |
| `sxs-claude-opus-4-6` | ★ NEW (sxs variant) |
| `sxs-claude-opus-4-5` | ★ NEW |
| `sxs-claude-opus-4-1` | ★ NEW |
| `sxs-claude-sonnet-4-6` | ★ NEW |
| `sxs-claude-sonnet-4-5` | ★ NEW |
| `sxs-claude-sonnet-4` | ★ NEW |
| `sxs-claude-sonnet-3-7` | ★ NEW |
| `sxs-claude-haiku-4-5` | ★ NEW |
| `sxs-claude-haiku-3` | ★ NEW |
| `sxs-gpt-5-codex` | ★ NEW |
| `sxs-gpt-5-1-codex` | ★ NEW |
| `sxs-gpt-5-2-codex` | ★ NEW |
| `sxs-gemini-3-pro-preview` | ★ NEW |
| `sxs-gemini-3-flash-preview` | ★ NEW |
| `sxs-kimi-k2-5:none` | ★ NEW (`:none` variant) |
| `sxs-glm-5` | ★ NEW |
| `glm-5` | ★ NEW |
| `grok-code-xiuyu-rkld-0303` | ★ NEW |
| `grok-code-discrim-0315a` | ★ NEW |
| `grok-code-discrim-0315b` | ★ NEW |
| `grok-code-rkld-0317a` | ★ NEW |

**Pattern:** Most `sxs-*` models have a `:none` variant (e.g., `sxs-claude-opus-4-6:none`). Purpose of `:none` suffix unknown — possibly disables system prompt or safety features.

**Model count:** alpha.1 had 4 models listed. alpha.2 has 50+ models.

## Architecture Notes (alpha.2)

### Plugin system
`--plugin-dir <PATH>` is new. Can be repeated. No documentation on plugin format yet — needs investigation.

### Worktree database
`grok worktree db` implies a SQLite or similar backing store for worktree metadata. `db rebuild` suggests it can get out of sync with filesystem state. `db stats` and `db path` are diagnostic.

### Memory workspace scoping
Memory dirs are now hashed per workspace: `~/.grok/memory/mikeyv-workspace-<hash>/`. This means different CWDs get different memory stores. Global memory (`~/.grok/memory/MEMORY.md`) is shared across all workspaces.

### Profiling support
`--enable-prof` (jemalloc heap profiling) and `grok leader profile` (CPU profiling) together give full performance diagnostic capability. Heap dumps go to `/tmp/grok_heap.<pid>.<seq>.heap`, analyzed with `jeprof`.

### API endpoint overrides
The global `--cli-chat-proxy-base-url` and `--xai-api-base-url` flags on every subcommand allow pointing the CLI at alternative backends. This could be useful for local development, staging environments, or routing through proxies.

## Session Migration — Moving Sessions to a New CWD

Documented: 2026-03-26 by MikeyV-Cinco. Tested and verified with Eric.

### Problem
Sessions are bound to the CWD (working directory) they were created in. The `/load` command only shows sessions whose CWD matches the directory you launched `grok` from. Moving an agent's workspace to a new directory (e.g., `~/MikeyV-Trip/` → `~/agents/Trip/`) requires updating the session to match.

### How Session CWD Storage Works

Session CWD is stored in **three places**:

| Location | Role | Needs manual update? |
|----------|------|---------------------|
| Filesystem path: `~/.grok/sessions/<URL-encoded-CWD>/<session-id>/` | Directory grok scans for sessions matching current CWD | YES — must copy session dir to new path |
| `summary.json` → `info.cwd` | Source of truth for session's CWD. `/load` reads this. | YES — must update to new CWD |
| `prompt_context.json` → `working_directory` | Injected into agent's system prompt as workspace path | YES — must update to new CWD |
| `session_search.sqlite` → `session_docs.cwd` | Search/cache index | NO — grok updates this automatically on session load |

**Key finding:** `session_search.sqlite` is a **cache**, not the source of truth. Grok rewrites it from `summary.json` whenever a session is loaded. You do not need to modify SQLite manually.

### Migration Procedure

To move a session from `<OLD_CWD>` to `<NEW_CWD>`:

**Step 1: Create the new workspace directory**
```bash
mkdir -p <NEW_CWD>
# Copy any workspace files (AGENTS.md, scripts, etc.)
```

**Step 2: Copy the session directory**
```bash
# URL-encode the paths (replace / with %2F)
OLD_ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('<OLD_CWD>', safe=''))")
NEW_ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('<NEW_CWD>', safe=''))")

mkdir -p ~/.grok/sessions/$NEW_ENCODED
cp -a ~/.grok/sessions/$OLD_ENCODED/<SESSION_ID> ~/.grok/sessions/$NEW_ENCODED/
cp ~/.grok/sessions/$OLD_ENCODED/prompt_history.jsonl ~/.grok/sessions/$NEW_ENCODED/ 2>/dev/null
```

**Step 3: Update CWD references in metadata files ONLY**
```bash
cd ~/.grok/sessions/$NEW_ENCODED/<SESSION_ID>
sed -i 's|<OLD_CWD>|<NEW_CWD>|g' summary.json prompt_context.json
```

**WARNING: Do NOT sed chat_history.jsonl or updates.jsonl.** These files contain embedded binary byte arrays, raw tool output, and complex nested JSON. sed corrupts them — observed: 218KB file ballooned to 541KB, line count doubled. These are historical records; grok does not use paths inside them for routing or session lookup. Copy them verbatim from the source. Only `summary.json` (CWD field) and `prompt_context.json` (working_directory, embedded AGENTS.md content) need path updates.

**Step 4: Verify**
```bash
cd <NEW_CWD>
grok
# Then /load — the migrated session should appear
```

### What Each File Contains

- **`summary.json`** — Session metadata: id, cwd, summary text (displayed by `/load`), created/updated timestamps, message counts, model id. The `session_summary` field is the display name shown in `/load`.
- **`prompt_context.json`** — System prompt context: working_directory, OS, shell, agents_md_files, skills, build info.
- **`updates.jsonl`** — Full history of all updates (tool calls, messages, thoughts). Contains CWD in workspace path references. DO NOT modify — copy verbatim.
- **`chat_history.jsonl`** — Conversation messages. Contains CWD in workspace path references. DO NOT modify — copy verbatim.
- **`session_search.sqlite`** — Global index at `~/.grok/sessions/session_search.sqlite`. Table `session_docs` with columns: `session_id` (PK), `cwd`, `updated_at`, `title`, `content`, `content_hash`. Has FTS (full-text search) via `session_docs_fts`. Auto-updated by grok on session load.

### Renaming a Session

The display name shown in `/load` is stored in `summary.json` → `session_summary`. To rename:
```bash
# From within grok TUI: use the rename option in /load
# Or manually:
python3 -c "
import json
with open('summary.json') as f: d = json.load(f)
d['session_summary'] = 'New Name'
with open('summary.json', 'w') as f: json.dump(d, f, indent=2)
"
```

### Notes

- The `--cwd <CWD>` flag (new in alpha.2) overrides the working directory at launch. This could be used as an alternative to migration for one-off access, but doesn't permanently move the session.
- `grok -r <SESSION_ID>` (or `--resume`) loads a session by ID regardless of CWD. However, it does NOT update the session's stored CWD — the session remains bound to its original directory.
- `/fork` ("fork session to create a parallel agent") requires YOLO mode. Creates a new session from an existing one. Could be used as an alternative to copy-based migration, but creates a new session ID.
- After migration, the old session directory can be removed once verified. The SQLite entry for the old CWD will become stale but harmless.

---

# Build 0.1.159-alpha.3 — Changes and Additions

Documented: 2026-03-26 by MikeyV-Cinco via `--help` probing of new binary drop.
Previous build: 0.1.159-alpha.2. Commit: `28652fa50`.

## Version Info

```
$ grok --version
grok 0.1.159-alpha.3 (28652fa50)
```

## Breaking Changes

### `--yolo` renamed to `--always-approve`
The `--yolo` flag no longer exists. It has been replaced by `--always-approve` with identical behavior: skip all permission prompts for tool executions.

**Impact:** Any scripts or launch commands using `--yolo` must be updated to `--always-approve`. The TUI keyboard shortcut (previously Ctrl-B for "yolo mode") may also have changed — needs verification.

This applies to both top-level `grok` and `grok agent` subcommands.

### `-p` flag renamed/changed to `--single` / `-p`
The `-p <PROMPT>` flag is now documented as `-p, --single <PROMPT>` — "Single-turn prompt. Prints the response to stdout and exits." The behavior appears the same but the long form changed from `--prompt` (which was ambiguous with `--prompt <INIT_PROMPT>`) to `--single`.

Note: `--prompt <INIT_PROMPT>` still exists as a separate flag for passing a prompt at TUI startup (non-single-turn).

## Model Changes

| Change | Model |
|--------|-------|
| RENAMED | `grok-build-best-0323` → `grok-build-best-0325` |
| REMOVED | `sxs-gpt-5-3-codex` (base, non-`:none` variant no longer listed separately — only `:none` variant remains... actually both variants present) |

Model list is otherwise identical to alpha.2.

## No Other Observed Changes

The following are unchanged from alpha.2:
- All subcommands (agent, serve, update, models, sessions, worktree, memory, leader, etc.)
- All subcommand options and flags
- Global flags (`--cli-chat-proxy-base-url`, `--xai-api-base-url`)
- Shell completion support (bash, elvish, fish, powershell, zsh)
- Agent leader options (`--no-exit-on-disconnect`, `--no-auto-update`)

## Summary

Alpha.3 is a minor update from alpha.2. The only user-facing changes are:
1. **`--yolo` → `--always-approve`** (breaking rename)
2. **`-p` long form clarified as `--single`**
3. **Model rename: `grok-build-best-0323` → `grok-build-best-0325`**

---

# Build 0.1.159-alpha.5 — Changes and Additions

Documented: 2026-03-27 by MikeyV-Trip via `--help` probing.
Previous documented build: 0.1.159-alpha.3. Commit: `88c98d311`.
Note: alpha.4 was not documented separately. Changes below cover alpha.3 → alpha.5.

## Version Info

```
$ grok --version
grok 0.1.159-alpha.5 (88c98d311)

$ grok doctor
  Diagnostics
  └ Currently running: npm (0.1.159-alpha.5)
  └ Path: /home/eric/.grok/bin/grok
  └ Invoked: /home/eric/.grok/bin/grok-0.1.159-alpha.5
  └ Latest version: 0.1.159-alpha.5 (up to date)
  └ Channel: alpha
  └ Auto-updates: enabled
  └ OS: linux-x86_64
  └ Canonical path in PATH: no
  Installations
  └ /home/eric/.grok/bin/grok (0.1.159-alpha.5) (canonical, current)
  └ /home/eric/.nvm/versions/node/v22.22.1/bin/grok (0.1.159-alpha.5) (npm shim)
```

## Flag and Subcommand Changes

No new top-level flags or subcommands compared to alpha.3.

### `grok serve`

Now exposes auth override flags in `--help` output:
- `--auth-signin-url`
- `--auth-exchange-code-url`
- `--auth-redirect-target`
- `--grok-ws-origin`
- `--grok-ws-url`

These were previously only visible under `grok agent` help. The flags likely existed in alpha.2/3 but were not shown in `grok serve --help`. Now they are.

## Model Changes (alpha.3 → alpha.5)

### Removed

| Model | Notes |
|-------|-------|
| `grok-webdev-rkld-0308` | Internal webdev model |
| `grok-code-mzhu-webdev-0308` | Internal webdev model |
| `grok-code-xiuyu-rkld-0303` | Internal code model |
| `grok-code-discrim-0315a` | Internal discriminator model |
| `grok-code-discrim-0315b` | Internal discriminator model |
| `grok-code-rkld-0317a` | Internal code model |

Six internal/experimental grok-code and grok-webdev models removed. These appear to have been development or evaluation models that were cleaned up.

### Added

| Model | Notes |
|-------|-------|
| `sxs-gpt-5-3-codex:none` | `:none` variant now listed (alpha.3 was unclear on whether this existed) |

### Current Full Model List (alpha.5)

46 models total:

| Model ID | Category |
|----------|----------|
| `grok-4.20-0309-reasoning` | Grok reasoning (2M context) |
| `grok-build-best-0325` | Grok build |
| `coding-mix-latest` | Grok coding mix |
| `opus-4-6` | Default |
| `opus-4-5` | Anthropic |
| `opus-4-1` | Anthropic |
| `sonnet-4` | Anthropic |
| `sonnet-4-5` | Anthropic |
| `gpt-5-2-codex` | OpenAI |
| `glm-5` | GLM |
| `sxs-kimi-k2-5` / `:none` | SxS eval |
| `sxs-claude-opus-4-6` / `:none` | SxS eval |
| `sxs-claude-opus-4-5` / `:none` | SxS eval |
| `sxs-claude-opus-4-1` / `:none` | SxS eval |
| `sxs-claude-sonnet-4-6` / `:none` | SxS eval |
| `sxs-claude-sonnet-4-5` / `:none` | SxS eval |
| `sxs-claude-sonnet-4` / `:none` | SxS eval |
| `sxs-claude-sonnet-3-7` / `:none` | SxS eval |
| `sxs-claude-haiku-4-5` / `:none` | SxS eval |
| `sxs-claude-haiku-3` / `:none` | SxS eval |
| `sxs-gpt-5-codex` / `:none` | SxS eval |
| `sxs-gpt-5-1-codex` / `:none` | SxS eval |
| `sxs-gpt-5-2-codex` / `:none` | SxS eval |
| `sxs-gpt-5-3-codex` / `:none` | SxS eval |
| `sxs-gpt-5-4` / `:none` | SxS eval |
| `sxs-gemini-3-pro-preview` / `:none` | SxS eval |
| `sxs-gemini-3-flash-preview` / `:none` | SxS eval |
| `sxs-glm-5` / `:none` | SxS eval |

## No New Flags for Disabling Session Replay

Investigated specifically: no `--no-replay`, `--skip-replay`, or similar flags exist. Session loading still replays the full `updates.jsonl` history. This remains relevant for the Sr memory balloon issue (253MB session, 49 compactions, full replay on load).

## Summary

Alpha.5 is a cleanup release from alpha.3. No new user-facing features. Six internal/experimental models removed. Auth flags now visible in `grok serve --help`. The `--yolo` → `--always-approve` rename from alpha.3 remains the most significant breaking change in the alpha.3-5 range.
