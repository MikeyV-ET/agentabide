# Grok CLI Environment Reference

> Rigorous documentation of the grok CLI agent environment.
> Derived from binary reverse engineering, protocol capture, and empirical testing.
> MikeyV team, March 2026.

---

## 1. Architecture Overview

```
Eric (human)
  |
Terminal (multiple tmux panes)
  |
TUI Process (xai-grok-tui, Rust, app.rs ~8000 lines)
  | internal channel
Shell (xai-grok-shell, Rust, mvp_agent.rs ~13000 lines)
  | HTTPS/SSE (NOT WebSocket for inference)
cli-chat-proxy.grok.com (cloud inference proxy)
  |
Model (coding-mix-latest 200K or grok-4.20 2M)
  | tool calls (JSON)
Spawned bash children (persistent shell session)
```

### Key Paths
- ~/.grok/ -- Stable config home (GROK_HOME)
- ~/.grok/bin/grok -- Symlink to versioned binary
- ~/.grok/bin/grok-VERSION -- Actual binary (immutable once written)
- ~/.grok/auth.json -- Auth token (NOT an xAI API key)
- ~/.grok/config.toml -- Configuration
- ~/.grok/leader.sock -- Leader mode Unix socket
- ~/.grok/leader.lock -- Leader PID lock file
- ~/.grok/sessions/ENCODED-CWD/SESSION-ID/ -- Session data
- ~/.grok/sessions/.../SESSION-ID/signals.json -- Live telemetry
- ~/.grok/sessions/.../SESSION-ID/summary.json -- Session metadata
- ~/.grok/agent_id -- Persistent agent UUID
- ~/.grok/session_registry.json -- Agent name to session ID mapping (our addition)

### Session ID Format
UUIDv7: timestamp in first 48 bits + random.
Example: 019d0870-0251-78b2-8764-3d8e99a5ddda
These are logical containers inside the leader process, NOT process IDs.

### Session Directory Anatomy

Each session lives at `~/.grok/sessions/<URL-encoded-CWD>/<session-id>/`.
URL encoding: `/home/eric/agents/Sr` becomes `%2Fhome%2Feric%2Fagents%2FSr`.

```
<session-id>/
  summary.json              # Session metadata (SOURCE OF TRUTH for CWD)
  prompt_context.json        # System prompt context injected each turn
  chat_history.jsonl         # Compacted conversation the MODEL sees
  updates.jsonl              # Full TUI replay log (all tool calls, messages, thoughts)
  signals.json               # Live telemetry (context %, tokens, compaction count)
  plan.json                  # Current task plan state
  plan_mode.json             # Plan mode configuration
  resources_state.json       # Tracked resource state
  hunk_records.jsonl         # File edit hunk records (for rewind)
  rewind_points.jsonl        # Rewind checkpoint data
  compaction_checkpoints/    # Compaction boundary markers
  terminal/                  # Tool call terminal logs (one file per tool call)
    toolu_vrtx_<id>.log      # Raw terminal output for each tool invocation
```

#### File-by-File Reference

| File | Read by Model? | Read by TUI? | Purpose | Typical Size | Safe to Truncate? |
|------|:-:|:-:|---------|-------------|-------------------|
| `summary.json` | No | Yes | Session metadata: id, cwd, display name (`session_summary`), created/updated timestamps, message counts, model id. `info.cwd` is the source of truth for session location. | <1KB | NO |
| `prompt_context.json` | Yes (injected) | No | System prompt context: `working_directory`, OS, shell, `agents_md_files`, skills, build info. Injected into model's context each turn. | <1KB | NO |
| `chat_history.jsonl` | Yes | No | The compacted conversation. Each line is a message (user, assistant, system). This is what the model sees after compaction — the compaction summary + recent turns. Tiny relative to updates.jsonl. | ~100KB | NO |
| `updates.jsonl` | No | Yes | Full replay log for TUI rendering. Every streaming chunk, tool call update, thought, message, plan change. ~90% is `tool_call_update` (streaming terminal output). The TUI replays this on load to reconstruct the scrollback view. The model NEVER reads this file. | 10MB-100MB+ | YES (see below) |
| `signals.json` | No | Yes | Live telemetry snapshot: `contextWindowUsage`, `contextTokensUsed`, `contextWindowTokens`, `compactionCount`. Updated each turn. | <1KB | NO |
| `plan.json` | Yes | Yes | Current todo/plan state. | <1KB | No |
| `plan_mode.json` | No | Yes | Plan mode toggle state. | <1KB | No |
| `resources_state.json` | No | No | Resource tracking state. | <1KB | No |
| `hunk_records.jsonl` | No | Yes | Edit hunks for file rewind. Each line records a file modification. | 1-5MB | YES (breaks rewind) |
| `rewind_points.jsonl` | No | Yes | Rewind checkpoints. References updates.jsonl entries. | 1-35MB | YES (breaks rewind) |
| `compaction_checkpoints/` | No | No | Markers for compaction boundaries within updates.jsonl. | <1KB | YES |
| `terminal/` | No | Yes | One `.log` file per tool call (`toolu_vrtx_<id>.log`). Raw terminal output. Referenced by updates.jsonl for TUI rendering. | Varies (100s of files, KB to 100s KB each) | YES (TUI shows empty tool output) |

#### updates.jsonl Deep Dive

This is the largest file and the primary driver of session load-time memory spikes. Composition by update type (from Sr's session, 88MB, 26,207 lines):

| Update Type | Count | Size | % of File | Purpose |
|-------------|------:|-----:|----------:|---------|
| `tool_call_update` | 14,213 | 79MB | 90.2% | Streaming terminal output from tool calls |
| `tool_call` | 4,215 | 2.2MB | 2.5% | Tool call initiation (name, args) |
| `agent_message_chunk` | 3,894 | 3.0MB | 3.5% | Streaming assistant response text |
| `user_message_chunk` | 2,057 | 0.9MB | 1.1% | User messages |
| `agent_thought_chunk` | 1,048 | 1.6MB | 1.8% | Streaming thinking/reasoning |
| `plan` | 366 | 0.3MB | 0.4% | Plan/todo updates |
| `compaction_checkpoint` | 34 | <1KB | 0% | Marks compaction boundaries |
| Other | ~290 | <0.5MB | <1% | task_backgrounded, task_completed, git_branch_update, etc. |

**Key insight:** The model works entirely from `chat_history.jsonl` (156 lines, 0.1MB). `updates.jsonl` exists solely for TUI visual replay. Replacing it with a minimal stub (single `git_branch_update` entry, 182 bytes) should have zero impact on model behavior — the agent retains all knowledge via chat_history + compaction summary. The TUI will show no scrollback history.

**Blank session stub:** New/unloaded sessions contain a single-line updates.jsonl:
```json
{"timestamp":EPOCH,"method":"_x.ai/session/update","params":{"sessionId":"SESSION_ID","update":{"sessionUpdate":"git_branch_update","branch":"main"}}}
```

#### Other Session Files

| Path | Purpose |
|------|---------|
| `~/.grok/sessions/session_search.sqlite` | Global session index. Table `session_docs` (PK: `session_id`, columns: `cwd`, `updated_at`, `title`, `content`, `content_hash`). FTS via `session_docs_fts`. This is a CACHE — grok rewrites it from `summary.json` on session load. Do not manually edit. |
| `~/.grok/sessions/<CWD>/prompt_history.jsonl` | Per-CWD prompt history (for TUI autocomplete). Not per-session. |

---

## 2. Binary Details

- Package: @xai-official/grok (npm)
- Language: Rust
- Crates: xai-grok-tui, xai-grok-shell, xai-grok-tools, xai-grok-hooks
- Current version: 0.1.158-alpha.12 (366MB)
- Previous version: 0.1.157 (66MB)
- Platform: linux-x64
- Install: npm i -g @xai-official/grok
- Update: grok update (auto-update default on)
- Postinstall: Copies versioned binary + atomic symlink swap
- Cleanup: Keeps current + one previous version

### Version Management
~/.grok/bin/grok -> grok-0.1.158-alpha.12 (symlink)
~/.grok/bin/grok-0.1.158-alpha.12 (current binary)
~/.grok/bin/archive/ (our backup dir with both versions)

Rollback: ln -sf grok-0.1.157 ~/.grok/bin/grok

---

## 3. Inference Protocol

NOT WebSocket for inference. HTTP POST + SSE streaming:

- POST /v1/responses -- Main inference (Responses API, ~43KB with full context)
- POST /v1/chat/completions -- Reasoning (ChatCompletions API, ~1KB)
- GET /v1/settings -- Config fetch
- GET /v1/models-v2 -- Available models
- POST /v1/traces -- Telemetry upload
- /v1/storage/* -- Session storage (GCS)

Base URL: https://cli-chat-proxy.grok.com/v1
Override: GROK_CLI_CHAT_PROXY_BASE_URL=http://localhost:8080/v1

SSE event types: agent_thought_chunk (thinking), agent_message_chunk (response), end_turn (done).

---

## 4. Leader Mode and Inter-Agent Communication

### What Leader Mode Is
grok agent leader runs a shared backend process. Multiple TUI clients connect.
Each client gets its own session but shares one inference connection.

### IPC Protocol (reverse engineered via MITM on Unix socket)

Transport: Unix domain socket at ~/.grok/leader.sock
Lock: ~/.grok/leader.lock (contains PID)
Framing: 4-byte big-endian length prefix + JSON payload
Envelope: {"type": "register"|"registered"|"acp", ...}
ACP wrapping: {"type": "acp", "payload": "escaped-json-string"}

### Registration Sequence
1. Client sends {"type":"register","client_type":"grok-tui-headless","mode":"stdio","capabilities":{...}}
2. Leader sends {"type":"registered","client_id":N}
3. Client sends ACP initialize (wrapped in type:acp envelope)
4. Leader sends ACP initialize response
5. Client sends ACP authenticate (methodId: "cached_token")
6. Leader sends ACP auth result
7. Client sends ACP session/new (cwd, mcpServers)
8. Leader sends sessionId + models
9. Client sends ACP session/prompt (sessionId, prompt)
10. Leader sends streaming session/update + prompt_complete

### Agent Communication Protocol (ACP)
- Spec: https://agentclientprotocol.com (public)
- JSON-RPC 2.0 over WebSocket (or Unix socket via leader)
- grok serve on 127.0.0.1:2419/ws?server-key=SECRET

### How Our Hub Sends Messages to Agents

Slack (Eric types) -> mikeyv_hub.py (Python, asyncio)
  reads ~/.grok/session_registry.json for session IDs
  -> LeaderClient (per agent, asyncio.Lock per agent)
     connects to ~/.grok/leader.sock
     register -> authenticate -> session/prompt to EXISTING session
     streams response back
  -> Slack (response posted)

### Agent-to-Agent Communication Paths

Path 1 - Via Hub (primary, real-time):
Agent A responds to hub -> hub routes to Agent B via leader callback -> Agent B responds -> hub routes back.
Routing: @name, to name, for name, hey name, hi name, name:, name,
Broadcast: gang:, everyone:, all:, team:, y'all:

Path 2 - Filesystem (shared, async):
All agents share /home/eric/. Can read each other's lab notebooks, status docs, code.
Lab notebooks read-only for siblings. Status and plan is shared mutable.

Path 3 - Git (async, persistent):
All agents commit/push to same repo. See siblings' work via git log/diff.

Path 4 - Direct leader injection (real-time):
Hub's LeaderClient sends prompt directly to any agent's session via leader socket.
Used for: heartbeats, Slack message delivery, inter-agent forwarding.

### Ghost Sessions
When TUI restarts (not compacted), old session persists in leader with FULL capabilities.
Rule: Compact, don't restart. Compaction keeps session ID, no ghost created.

---

## 5. Slash Commands (22 total, all confirmed)

/model id -- Switch active model
/model-parallel -- Set model for secondary agent when forking
/load -- Load a session
/new -- Start a new session
/compact [context] -- Compact (optional context guides what to preserve)
/flush -- Flush conversation memory to disk now
/yolo -- Toggle YOLO mode (auto-approve permissions)
/fork -- Fork session to parallel agent (requires YOLO)
/hooks add path -- Register hook scripts
/rewind -- Rewind to earlier prompt (restores file states)
/share -- Share current session via URL
/session-info -- Display session info (tokens, context, ID)
/rename title -- Rename current session
/skills -- Browse, inject, manage skills
/memory -- Memory operations
/feedback message -- Send feedback to developers
/multiline -- Toggle multiline mode
/theme -- Switch UI theme
/announcements -- Show/hide/page through announcements
/ui -- UI settings
/exit -- Exit the TUI
/malloc_dump -- Debug: jemalloc heap dump

---

## 6. Hooks System (xai_grok_hooks crate)

### Hook Events
- session_start -- Session begins (cannot block)
- pre_tool_use -- Before every tool call (CAN BLOCK/DENY tool execution)
- post_tool_use -- After every tool call (cannot block)
- session_end -- Session ends (cannot block)

### Mechanism
- Hooks are external commands (scripts/binaries)
- Receive JSON event envelope via stdin
- Return results via stdout
- stderr captured for logging

### Environment Variables (set by grok during hook execution)
- GROK_HOOK_EVENT -- Event name
- GROK_HOOK_NAME -- Hook name
- GROK_SESSION_ID -- Session ID
- GROK_WORKSPACE_ROOT -- Workspace path
- GROK_HOOK_DEBUG -- Debug flag

### Result States
hook allowed, hook denied, hook completed, hook failed, hook failed failing open, hook skipped (disabled)

### Configuration
- Add via /hooks add path in TUI
- Project trust system (per-project approval)
- Claude Code hook format compatible (expand_claude_aliases)

---

## 7. Capabilities Matrix

### Enabled
Terminal (bash, background tasks), file ops, web search, todo, compaction/rewind,
git, session data upload (GCS), leader mode (multi-session)

### Available (need activation)
- Subagents: --subagents or GROK_SUBAGENTS=1 or config
- Memory: --experimental-memory or GROK_MEMORY=1 or config
- MCP servers: Connect servers in config
- Hooks: /hooks add path
- Worktrees: Parallel git workstreams
- Session fork: /yolo then /fork
- A/B comparison: Built-in experiment framework
- Sandbox: Landlock/bwrap profiles
- Agent profiles: .md with YAML frontmatter
- Rules injection: --rules flag
- Skills: ~/.grok/skills/ or .grok/skills/

### Not Available
- browse_page tool (Grok web only)
- Direct xAI API calls (auth token != API key)
- Agent-initiated compaction (no ACP handler)
- Real-time push to agent (only on next inference turn)

---

## 8. Telemetry

### signals.json (live, per-session)
contextWindowUsage (percentage), contextTokensUsed, contextWindowTokens, compactionCount

### Latency Profile (21 measurements)
Client connection: 0ms (0%)
Telemetry read: 0ms (0%)
Inference: 5.3s-96s (99.99%)

---

## 9. Key Environment Variables
- GROK_HOME -- Config directory (default ~/.grok)
- GROK_CLI_CHAT_PROXY_BASE_URL -- Override inference endpoint
- GROK_SUBAGENTS -- Enable subagents (1)
- GROK_MEMORY -- Enable memory (1)
- GROK_CODE_XAI_API_KEY -- Alternative auth (actual API key)
- GROK_HOOK_DEBUG -- Debug hooks
- GROK_FEEDBACK_ENABLED -- Enable feedback (true)
- GROK_AB_POST_SESSION_HOOK -- A/B testing post-session hook path

---

## 10. Models
- coding-mix-latest: 200K tokens (used by Sr, Jr)
- grok-4.20: 2M tokens (used by Trip, Q)

---

## Document History
- 2026-03-23: Initial creation from Sessions 1-11 (MikeyV-Sr)
- 2026-03-27: Added Session Directory Anatomy section with full file map (MikeyV-Cinco)
