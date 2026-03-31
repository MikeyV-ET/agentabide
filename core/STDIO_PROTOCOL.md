# grok agent stdio — Protocol Reference

Documented: 2026-03-26 by MikeyV-Sr.
Binary: `grok agent stdio` (0.1.158+).
Transport: JSON-RPC 2.0 over stdin/stdout. One JSON object per line.

## Overview

`grok agent stdio` runs the grok agent as a subprocess with exclusive stdin/stdout pipes. No network, no sockets, no shared state. The caller owns the process and the pipes.

This is the foundation of ASDAAAS — one `grok agent stdio` subprocess per agent, managed by one `asdaaas.py` instance.

## Launching

```bash
grok agent stdio
```

Options:
- `--cli-chat-proxy-base-url <URL>` — override chat proxy
- `--xai-api-base-url <URL>` — override xAI API

Environment:
- Process inherits caller's environment
- CWD matters — session operations are scoped to it

**Important:** Must be launched detached (`setsid nohup`) if the caller is a long-running process. Using `is_background: true` in tool calls causes updates.jsonl bloat and OOM. See launch_asdaaas.sh.

## Initialization Sequence

Every stdio session must complete this handshake before sending prompts:

### 1. initialize (client → server)

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "asdaaas",
      "version": "0.1"
    }
  }
}
```

**Response:** Server returns `agentCapabilities` with tool support info, model list, etc.

### 2. notifications/initialized (client → server)

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized"
}
```

**Note:** This is a notification (no `id`). No response expected.

### 3. Load or create session

Either load an existing session:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/load",
  "params": {
    "sessionId": "019d1ec0-5dd0-7e32-8eec-2577d8c541dd",
    "cwd": "/home/eric/MikeyV-Cinco",
    "mcpServers": []
  }
}
```

Or create a new one:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/home/eric/MikeyV-Cinco",
    "mcpServers": []
  }
}
```

**session/load caveats:**
- `cwd` MUST match the directory where the session was originally created. Sessions are stored under URL-encoded paths in `~/.grok/sessions/`. Mismatch causes "No such file or directory" errors.
- Does NOT return `sessionId` in the result. Use the ID you sent.
- Replays ALL session history as `isReplay: true` frames. Large sessions (33+ compactions) can produce 19,500+ replay frames.
- Replay frames can exceed the default 64KB readline buffer. Set StreamReader limit to 16MB.

**session/new response:** Returns `sessionId` in the result.

### 4. Enable yolo mode (optional but recommended)

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "<session_id>",
    "prompt": [{"type": "text", "text": "/yolo on"}]
  }
}
```

This auto-approves all tool executions. Without it, the agent will pause on tool calls waiting for approval that never comes (no UI to approve).

## Sending Prompts

```json
{
  "jsonrpc": "2.0",
  "id": 100,
  "method": "session/prompt",
  "params": {
    "sessionId": "<session_id>",
    "prompt": [
      {"type": "text", "text": "Hello, what's your status?"}
    ]
  }
}
```

Content blocks are an array. Each block has `type` and `text`. Multiple blocks can be sent in one prompt.

## Response Streaming

After sending a prompt, the server streams response frames. Each is a JSON-RPC notification or result.

### Agent text chunks

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<session_id>",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": {
        "type": "text",
        "text": "Here is my response..."
      }
    }
  }
}
```

**Path to text:** `params.update.content.text`
**Type check:** `params.update.sessionUpdate == "agent_message_chunk"`

### Agent thinking chunks

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<session_id>",
    "update": {
      "sessionUpdate": "agent_thought_chunk",
      "content": {
        "type": "thinking",
        "text": "Let me consider..."
      }
    }
  }
}
```

### Tool calls

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<session_id>",
    "update": {
      "sessionUpdate": "tool_call",
      "content": [...]
    }
  }
}
```

**IMPORTANT:** For tool calls, `content` is a **LIST**, not a dict. You must check `isinstance(content, dict)` before calling `.get("text")` or you'll crash.

### Tool call updates

```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "<session_id>",
    "update": {
      "sessionUpdate": "tool_call_update",
      ...
    }
  }
}
```

### Turn completion

```json
{
  "jsonrpc": "2.0",
  "method": "_x.ai/session/prompt_complete",
  "params": {
    "sessionId": "<session_id>"
  }
}
```

This signals the agent has finished its turn. Safe to send the next prompt.

### Final result (with metadata)

The prompt's JSON-RPC response (matching the request `id`) arrives with metadata:

```json
{
  "jsonrpc": "2.0",
  "id": 100,
  "result": {
    "_meta": {
      "totalTokens": 45000,
      "sessionId": "<session_id>",
      "modelId": "opus-4-6",
      "stopReason": "end_turn"
    }
  }
}
```

**Key fields:**
- `totalTokens` — current context window usage (for compaction decisions)
- `stopReason` — `"end_turn"` for normal completion
- `modelId` — which model processed the request

## Special Commands

These are sent as `session/prompt` with the command as text:

### /yolo on

Auto-approve all tool executions.

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "session/prompt",
  "params": {
    "sessionId": "<session_id>",
    "prompt": [{"type": "text", "text": "/yolo on"}]
  }
}
```

### /compact

Trigger session compaction. The agent summarizes its context and reloads with a compressed history.

```json
{
  "jsonrpc": "2.0",
  "id": 500,
  "method": "session/prompt",
  "params": {
    "sessionId": "<session_id>",
    "prompt": [{"type": "text", "text": "/compact"}]
  }
}
```

**Response:** Normal streaming response (the compaction summary), followed by `prompt_complete`. After compaction, `totalTokens` in the result metadata will be significantly reduced.

**Use case:** Agent-controlled compaction. When the agent (or ASDAAAS monitoring token count) decides the context is too heavy, send `/compact` to shed weight. This is the basis for the session control adapter.

## Collecting a Complete Response

To collect a full agent response from the stream:

```python
response_text = ""
while True:
    frame = read_line()  # read one JSON line from stdout
    if frame is None:
        break

    # Check for turn completion
    if frame.get("method") == "_x.ai/session/prompt_complete":
        break

    # Extract text chunks
    params = frame.get("params", {})
    update = params.get("update", {})
    if not isinstance(update, dict):
        continue
    if update.get("sessionUpdate") != "agent_message_chunk":
        continue
    content = update.get("content", {})
    if isinstance(content, dict):
        text = content.get("text", "")
        if text:
            response_text += text

    # Extract metadata from result
    if "result" in frame:
        meta = frame["result"].get("_meta", {})
        total_tokens = meta.get("totalTokens")
```

## Known Issues and Gotchas

1. **readline buffer overflow:** `session/load` replay can produce lines exceeding asyncio's default 64KB limit. Set `asyncio.StreamReader` limit to 16MB (`limit=16*1024*1024`).

2. **Tool call content type:** `content` field is a list for `tool_call` and `tool_call_update` frames, but a dict for `agent_message_chunk`. Always check type before accessing.

3. **session/load doesn't return sessionId:** The response to `session/load` does not include the session ID. You must track the ID you sent.

4. **CWD must match:** Sessions are stored under URL-encoded CWD paths. Loading a session from a different CWD fails silently or with cryptic errors.

5. **No concurrent access:** One stdio client per process. The process is exclusive to whoever spawned it.

6. **Replay volume:** Large sessions produce thousands of replay frames on load. A 33-compaction session produced ~19,500 frames. Be prepared to consume them all before sending prompts.

7. **Background tool call pollution:** If the stdio process is launched via a tool call with `is_background: true`, every output line gets stored in the caller's `updates.jsonl` with full accumulated buffer re-storage. This causes exponential growth (287MB observed) and OOM kills. Always launch with `setsid nohup`.

## Session Control Adapter

The `/compact` command makes agent-controlled compaction possible as a control adapter:

```
Agent writes: {"command": "compact"}
Adapter sends: /compact via session/prompt
Adapter reads: streaming response + prompt_complete
Adapter reads: totalTokens from result metadata
Adapter sends doorbell: [session:compact] ok: compacted, 185000 -> 42000 tokens
```

The adapter can also monitor `totalTokens` proactively and send a doorbell when the agent is approaching the compaction threshold:

```
[session:context] warning: 172000/200000 tokens (86%), consider compacting
```

This gives the agent awareness of its own context pressure without having to track tokens itself.
