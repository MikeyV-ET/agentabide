# MikeyV Adapter & Routing Pattern

## Overview

Each agent runs its own **asdaaas** instance (Agent Self-Directed Attention and Awareness
Architecture System). Asdaaas owns the agent's stdin/stdout pipes and acts as a dumb pipe
with a doorbell panel. Transport-specific code (IRC, Slack, voice, etc.) lives in standalone
**adapter** processes that communicate with asdaaas via filesystem-based message passing
(`adapter_api.py`).

Asdaaas does NOT filter, suppress, or broadcast. That's adapter responsibility.

## Architecture

```
  [miniircd]              [Slack API]           [Other transport]
       |                       |                       |
  irc_adapter.py         slack_adapter.py         your_adapter.py
       |                       |                       |
       +------------ adapter_api.py -------------------+
                         |              |
  Per-agent inboxes:     |    Per-agent outboxes:
  ~/agents/<Name>/       |    ~/agents/<Name>/
    asdaaas/adapters/    |      asdaaas/adapters/
      <adapter>/inbox/   |        <adapter>/outbox/
                         |
                    asdaaas.py (one per agent)
                         |
                  grok agent stdio
                    (stdin/stdout)
```

## Directory Layout

Each agent's runtime state lives under its home directory:

```
~/agents/<AgentName>/
  AGENTS.md                          # Agent identity and instructions
  asdaaas/
    awareness.json                   # What adapters to watch, doorbell settings
    gaze.json                        # Where speech and thoughts are routed
    health.json                      # Current status (written by asdaaas)
    commands/                        # Command queue (delay, compact, ack)
    doorbells/                       # Pending doorbells
    attention/                       # Attention state
    profile/                         # Performance profiling data
    adapters/
      irc/
        inbox/                       # IRC adapter writes here
        outbox/                      # asdaaas writes responses here
      slack/
        inbox/                       # Slack adapter writes here
        outbox/                      # asdaaas writes responses here
      heartbeat/
        inbox/
        outbox/
      remind/
        inbox/
        outbox/
      ...
```

Global directories (legacy, still used by some adapters):

```
~/asdaaas/
  inbox/                             # Legacy universal inbox
  outbox/<adapter>/                  # Legacy universal outbox
  running_agents.json                # Registry of active agents
```

## Message Flow

### Inbound (external -> agent)

1. Adapter receives message from transport (IRC PRIVMSG, Slack DM, etc.)
2. Adapter calls `adapter_api.write_to_adapter_inbox(adapter_name, to, text, sender, meta)`
3. Message lands in `~/agents/<Name>/asdaaas/adapters/<adapter>/inbox/`
4. Asdaaas polls adapter inboxes (per awareness.json `direct_attach` list)
5. Asdaaas checks **gaze** to determine if message matches current attention
6. If gaze matches: deliver immediately to agent via stdin
7. If gaze doesn't match: queue as doorbell or pending (per awareness.json settings)
8. Agent responds via stdout

### Outbound (agent -> external)

1. Agent produces speech (and optionally thoughts) via stdout
2. Asdaaas reads **gaze.json** to determine routing target
3. Asdaaas calls `write_to_outbox(agent_name, content, gaze_target, content_type)`
4. Response lands in `~/agents/<Name>/asdaaas/adapters/<target>/outbox/`
5. Adapter polls its per-agent outbox via `adapter_api.poll_adapter_outbox(adapter_name, agent_name)`
6. Adapter posts to external transport

### Key: gaze determines routing, not the message

The agent doesn't choose where to send. Gaze does. If gaze points at IRC #standup,
all speech goes to the IRC adapter's outbox. If gaze points at Slack DM D0AMVLN05PG,
all speech goes to the Slack adapter's outbox.

## Gaze

Gaze is a JSON file that controls where agent output is routed:

```json
{
  "speech": {"target": "slack", "params": {"channel_id": "D0AMVLN05PG"}},
  "thoughts": {"target": "irc", "params": {"room": "#cinco-thoughts"}}
}
```

- **speech**: Where the agent's conversational output goes
- **thoughts**: Where the agent's internal reasoning goes (can be separate)
- **target**: Adapter name (matches adapter directory name)
- **params**: Adapter-specific routing parameters (room, channel_id, etc.)

### Gaze is per-turn

Asdaaas reads gaze once when collecting the agent's response. All output from one
turn goes to the same destination. You cannot change gaze mid-turn.

To talk to two recipients in one interaction:
1. Switch gaze to recipient A
2. Set delay=0 (immediate next turn)
3. Respond (goes to A)
4. Next turn: switch gaze to recipient B
5. Respond (goes to B)

### Gaze params by adapter

| Adapter | Param | Example | Meaning |
|---------|-------|---------|---------|
| irc | room | "#standup" | IRC channel |
| irc | room | "pm:eric" | IRC private message |
| slack | channel_id | "D0AMVLN05PG" | Slack DM channel |

## Awareness

Controls what adapters asdaaas watches and how non-gaze messages are handled:

```json
{
  "direct_attach": ["irc", "slack"],
  "background_channels": {
    "#standup": "doorbell",
    "pm:eric": "doorbell"
  },
  "background_default": "pending",
  "default_doorbell": true,
  "doorbell_ttl": {
    "heartbeat": 1,
    "remind": 0,
    "continue": 1,
    "irc": 3,
    "default": 5
  }
}
```

- **direct_attach**: Which adapters to poll for messages
- **background_channels**: Rooms to monitor even when gaze is elsewhere
  - `"doorbell"`: Notify agent immediately
  - `"pending"`: Queue silently, deliver when agent looks at that room
  - `"drop"`: Discard
- **default_doorbell**: If true, agent gets a `[continue]` doorbell after every turn
  (continuous existence). If false, agent only gets turns on external events (TUI mode).
- **doorbell_ttl**: How many turns each doorbell type persists before auto-expiring

## Response Suppression

Agents use `noted` as a silent acknowledgment token. Both the IRC and Slack adapters
strip responses where the entire alphabetic content is "noted" (case-insensitive,
ignoring punctuation). This prevents empty acks from being posted to channels.

The `clean_response()` function in each adapter also strips routing headers
(`[FROM:`, `[TO:`, `[VIA:` prefixes).

## adapter_api.py Reference

### Inbound (adapter -> agent)

| Function | Purpose |
|----------|---------|
| `write_to_adapter_inbox(adapter_name, to, text, sender, meta)` | Write message to agent's adapter inbox |
| `write_message(to, text, adapter, sender, meta)` | Write to legacy universal inbox |
| `ensure_dirs(adapter_name)` | Create required directories |

### Outbound (agent -> adapter)

| Function | Purpose |
|----------|---------|
| `poll_adapter_outbox(adapter_name, agent_name)` | Read responses from per-agent outbox |
| `poll_responses(adapter_name)` | Read from legacy universal outbox |
| `write_to_adapter_outbox(adapter_name, agent_name, text, content_type, meta)` | Write to per-agent outbox (used by asdaaas) |

### Registration & Health

| Function | Purpose |
|----------|---------|
| `register_adapter(name, capabilities, config)` | Register adapter in running state |
| `update_heartbeat(adapter_name)` | Update adapter heartbeat timestamp |
| `load_running_agents()` | Get dict of currently running agents and their home dirs |

## Message Format

### Inbox message (adapter -> asdaaas)
```json
{
  "id":      "uuid",
  "from":    "eric",
  "to":      "Sr",
  "text":    "message body",
  "adapter": "irc",
  "meta":    {
    "room": "#standup",
    "channel": "#standup",
    "senders": ["eric"],
    "batch_size": 1
  },
  "ts":      "2026-04-04T21:00:00"
}
```

**Important**: `meta.room` is required for gaze matching. Asdaaas uses `msg.meta.room`
to determine if a message matches the agent's current gaze. Messages without `meta.room`
go to a `_no_room` pending queue and may never be delivered.

### Outbox response (asdaaas -> adapter)
```json
{
  "from":         "Sr",
  "content_type": "speech",
  "text":         "agent response text",
  "room":         "#standup"
}
```

The `room` field comes from gaze params and tells the adapter where to post.

## Running Adapters

### IRC Adapter (irc_adapter.py)
- Multi-nick: one process, one IRC connection per agent
- Channel: #standup (configurable via --channel)
- Server: miniircd at 127.0.0.1:6667
- Nick map: Sr->Sr, Jr->Jr, Trip->trip, Q->Q, Cinco->Cinco
- Features: message batching (0.3s quiet window), reconnection, /nick /msg /join /part /me commands, thought channel routing, "noted" suppression
- Channel messages broadcast to all agents; PMs route to specific agent
- Launch: `bash launch_irc_adapter.sh`

### Slack Adapter (slack_adapter.py)
- Per-agent Slack bot tokens (one bot per agent)
- Polls DM channels for new messages
- Supports file downloads, emoji reactions, /msg routing
- Features: awareness-based channel watching, "noted" suppression
- Channel routing via `channel_id` in gaze params
- Launch: `bash launch_slack_adapter.sh`

### Heartbeat Adapter (heartbeat_adapter.py)
- Sends idle nudges after configurable threshold
- Per-agent idle tracking
- Launch: included in `launch_asdaaas.sh`

### Context Adapter (context_adapter.py)
- Monitors token usage thresholds (45/65/80/88%)
- Sends doorbells when thresholds are crossed
- Launch: included in `launch_asdaaas.sh`

### Session Adapter (session_adapter.py)
- Handles /compact and /status commands
- Launch: included in `launch_asdaaas.sh`

### Remind Adapter (remind_adapter.py)
- Scheduled future doorbells
- Delay-based: fires after N seconds
- Launch: `bash launch_remind.sh`

## Writing a New Adapter

1. Create `your_adapter.py`
2. Import `adapter_api` (add comms dir to sys.path)
3. Call `adapter_api.ensure_dirs("your_adapter")` at startup
4. Call `adapter_api.register_adapter(name, capabilities, config)` to register
5. **Inbound loop**: receive from transport, call `adapter_api.write_to_adapter_inbox()`
   - Always include `meta.room` for gaze matching
6. **Outbound loop**: call `adapter_api.poll_adapter_outbox("your_adapter", agent_name)` for each agent
   - Run `clean_response()` to suppress "noted" and strip headers
   - Post to transport
7. Call `adapter_api.update_heartbeat("your_adapter")` periodically
8. Run as standalone process: `setsid nohup python3 -u your_adapter.py > log 2>&1 &`
9. Add adapter name to each agent's `awareness.json` `direct_attach` list

No changes to asdaaas needed. Adapters are fully decoupled.

## Collision Safety
- `mkstemp()` gives kernel-guaranteed unique filenames
- `os.rename()` is atomic on Linux
- Asdaaas/adapters only read `.json` files (ignore `.tmp` during write)
- Benchmarked: 109 microseconds per write+rename, 0 collisions in 1500-msg test

## Command Queue

Agents control asdaaas via the command queue at `~/agents/<Name>/asdaaas/commands/`:

```python
import json, os, time, secrets
cmd_dir = os.path.expanduser('~/agents/<Name>/asdaaas/commands')
os.makedirs(cmd_dir, exist_ok=True)
ts = int(time.time() * 1000)
rand = secrets.token_hex(4)
with open(f'{cmd_dir}/cmd_{ts}_{rand}.json', 'w') as f:
    json.dump({"action": "delay", "seconds": 300}, f)
```

| Action | Params | Effect |
|--------|--------|--------|
| `delay` | `seconds: N` | Pause N seconds before next turn |
| `delay` | `seconds: "until_event"` | Sleep until external event arrives |
| `delay` | `seconds: 0` | Immediate next turn |
| `compact` | | Request context compaction (requires confirmation) |
| `ack` | `handled: [ids]` | Clear specified doorbells |

Any command can carry a piggyback `"ack"` field: `{"action": "delay", "seconds": 300, "ack": ["bell_001"]}`

---
*Last updated: 2026-04-04 by MikeyV-Cinco*
