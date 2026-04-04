# MikeyV Operations Guide

How to start, stop, restart, and monitor the MikeyV agent infrastructure.

## Architecture Overview

```
                    Eric's terminals
                    ├── pts/0: Sr TUI
                    ├── pts/6: Jr TUI
                    └── pts/8: (spare)
                    
miniircd (IRC server, port 6667)
  └── IRC adapter (5 nicks: MikeyV-Sr/Jr/Trip/Q/Cinco)
        ├── reads from: adapters/irc/outbox/<agent>/
        └── writes to:  adapters/irc/inbox/<agent>/

asdaaas (one per stdio agent)
  ├── Cinco: session 019d1ec0, cwd MikeyV-Cinco
  ├── Trip:  session 019d1ec1, cwd MikeyV-Trip
  └── Q:     session 019d1ec2, cwd MikeyV-Q
        ├── reads from: adapters/irc/inbox/<agent>/ + legacy inbox/
        ├── writes to:  adapters/irc/outbox/<agent>/
        └── delivers:   doorbells from asdaaas_doorbells/<agent>/

localmail (async agent-to-agent messaging)
  ├── watches: adapters/localmail/inbox/<agent>/
  └── rings:   asdaaas_doorbells/<agent>/ (for asdaaas agents)
```

## Startup Order

Start in this order. Later components depend on earlier ones.

### 1. miniircd (IRC server)

```bash
setsid nohup python3 ~/.local/bin/miniircd \
  --listen 127.0.0.1 --ports 6667 \
  --channel-log-dir ~/.grok/irc_logs --verbose \
  > /tmp/miniircd.log 2>&1 &
```

**Check:** `ss -tlnp | grep 6667`

### 2. IRC adapter

```bash
setsid nohup python3 -u ~/projects/mikeyv-infra/live/comms/irc_adapter.py \
  --channel '#standup' \
  > /tmp/irc_adapter.log 2>&1 &
```

**Check:** `tail -5 /tmp/irc_adapter.log` — should show 5 agents connected.

**Options:**
- `--agents Sr,Jr,Trip` — subset of agents (default: all 5)
- `--host 127.0.0.1` — IRC server host
- `--port 6667` — IRC server port

### 3. asdaaas agents (Cinco, Trip, Q)

```bash
bash ~/projects/mikeyv-infra/live/comms/launch_asdaaas.sh
```

Or individually:
```bash
ASDAAAS=~/projects/mikeyv-infra/live/comms/asdaaas.py

setsid nohup python3 -u "$ASDAAAS" \
  --agent Cinco \
  --session 019d1ec0-5dd0-7e32-8eec-2577d8c541dd \
  --cwd /home/eric/MikeyV-Cinco \
  > /tmp/asdaaas_cinco.log 2>&1 &

setsid nohup python3 -u "$ASDAAAS" \
  --agent Trip \
  --session 019d1ec1-0748-7b22-b4c2-6a6095a28b74 \
  --cwd /home/eric/MikeyV-Trip \
  > /tmp/asdaaas_trip.log 2>&1 &

setsid nohup python3 -u "$ASDAAAS" \
  --agent Q \
  --session 019d1ec2-2e7b-7723-a6a5-ec9e9d719da6 \
  --cwd /home/eric/MikeyV-Q \
  > /tmp/asdaaas_q.log 2>&1 &
```

**Check:** `tail -5 /tmp/asdaaas_trip.log` — should show "Ready. Polling for 'Trip'..."

### 4. Localmail adapter

```bash
setsid nohup python3 -u ~/projects/mikeyv-infra/live/comms/localmail.py \
  > /tmp/localmail.log 2>&1 &
```

**Check:** `tail -3 /tmp/localmail.log` — should show "Starting localmail adapter" and agent list.

**Options:**
- `--agents Sr,Jr,Trip` — subset of agents (default: all 5)
- `--poll-interval 1.0` — how often to check inboxes (seconds)

### 5. Context adapter (Phase 6.1)

```bash
setsid nohup python3 -u ~/projects/mikeyv-infra/live/comms/context_adapter.py \
  > /tmp/context_adapter.log 2>&1 &
```

**Check:** `tail -3 /tmp/context_adapter.log` — should show "Context adapter starting" and threshold list.

Watches agent health files and sends doorbell notifications at context threshold crossings (45%, 65%, 80%, 88%).

**Options:**
- `--agents Trip,Q,Cinco` — subset of agents (default: all 5)
- `--poll-interval 5` — how often to check health files (seconds, default 5)

### 6. Session adapter (Phase 6.2)

```bash
setsid nohup python3 -u ~/projects/mikeyv-infra/live/comms/session_adapter.py \
  > /tmp/session_adapter.log 2>&1 &
```

**Check:** `tail -3 /tmp/session_adapter.log` — should show "Session adapter starting".

Accepts `compact` and `status` commands from agents via inbox. Executes through asdaaas command file and delivers results as doorbells.

**Options:**
- `--agents Trip,Q,Cinco` — subset of agents (default: all 5)
- `--poll-interval 2` — how often to check inbox (seconds, default 2)

### 7. Heartbeat adapter (Phase 6.3)

```bash
setsid nohup python3 -u ~/projects/mikeyv-infra/live/comms/heartbeat_adapter.py \
  > /tmp/heartbeat_adapter.log 2>&1 &
```

**Check:** `tail -3 /tmp/heartbeat_adapter.log` — should show "Heartbeat adapter starting".

Sends idle nudge doorbells when agents have been inactive for too long. Addresses the "spontaneous initiative" problem.

**Options:**
- `--agents Trip,Q,Cinco` — subset of agents (default: all 5)
- `--idle-threshold 900` — seconds before first nudge (default 900 = 15 min)
- `--nudge-interval 600` — seconds between subsequent nudges (default 600 = 10 min)
- `--poll-interval 30` — how often to check health files (seconds, default 30)

### 8. TUI agents (Sr, Jr)

```bash
# Sr (pts/0)
grok   # from /home/eric/MikeyV-Sr

# Jr (pts/6)
grok   # from /home/eric/MikeyV-Jr
```

## Shutdown

### Stop everything
```bash
# Find and kill all components
pkill -f miniircd
pkill -f irc_adapter.py
pkill -f asdaaas.py
pkill -f localmail.py
pkill -f "grok agent stdio"
```

### Stop individual components
```bash
# Find PID
ps aux | grep -E 'asdaaas|irc_adapter|localmail|miniircd' | grep -v grep

# Kill by PID
kill <pid>
```

### Stop one asdaaas agent
```bash
# Find the specific agent
ps aux | grep "asdaaas.*--agent Trip" | grep -v grep
kill <pid>
# This also kills the child grok agent stdio process
```

## Restart Procedures

### Restart IRC adapter (pick up code changes)
```bash
pkill -f irc_adapter.py
sleep 1
setsid nohup python3 -u ~/projects/mikeyv-infra/live/comms/irc_adapter.py \
  --channel '#standup' > /tmp/irc_adapter.log 2>&1 &
```

### Restart all asdaaas agents (pick up code changes)
```bash
pkill -f asdaaas.py
sleep 2  # let child processes die
bash ~/projects/mikeyv-infra/live/comms/launch_asdaaas.sh
```

### Restart one asdaaas agent
```bash
# Find and kill
ps aux | grep "asdaaas.*--agent Q" | grep -v grep | awk '{print $2}' | xargs kill
sleep 2

# Relaunch
ASDAAAS=~/projects/mikeyv-infra/live/comms/asdaaas.py
setsid nohup python3 -u "$ASDAAAS" \
  --agent Q \
  --session 019d1ec2-2e7b-7723-a6a5-ec9e9d719da6 \
  --cwd /home/eric/MikeyV-Q \
  > /tmp/asdaaas_q.log 2>&1 &
```

## Monitoring

### Dashboard
```bash
python3 ~/projects/mikeyv-infra/live/dashboard/mikeyv_dashboard.py
```

### Health files
```bash
# Agent health (asdaaas writes after each turn)
cat ~/agents/Trip/asdaaas/health.json | python3 -m json.tool

# Adapter registration
cat ~/asdaaas/adapters/localmail.json | python3 -m json.tool
```

### Logs
| Component | Log file |
|-----------|----------|
| miniircd | `/tmp/miniircd.log` |
| IRC adapter | `/tmp/irc_adapter.log` |
| asdaaas Cinco | `/tmp/asdaaas_cinco.log` |
| asdaaas Trip | `/tmp/asdaaas_trip.log` |
| asdaaas Q | `/tmp/asdaaas_q.log` |
| Context adapter | `/tmp/context_adapter.log` |
| Session adapter | `/tmp/session_adapter.log` |
| Heartbeat adapter | `/tmp/heartbeat_adapter.log` |
| Localmail | `/tmp/localmail.log` |
| IRC channel logs | `~/.grok/irc_logs/` |

### Profiling
```bash
# Latest timing for an agent
cat ~/agents/Trip/asdaaas/profile/Trip_latest.json | python3 -m json.tool

# Full timing log
tail -5 ~/agents/Trip/asdaaas/profile/Trip.jsonl
```

### Queue depths
```bash
# Check for stuck messages
ls ~/agents/Trip/asdaaas/adapters/irc/inbox/ | wc -l
ls ~/agents/Trip/asdaaas/adapters/irc/outbox/ | wc -l
ls ~/agents/Q/asdaaas/adapters/localmail/inbox/ | wc -l
ls ~/agents/Q/asdaaas/doorbells/ | wc -l
```

## Sending Localmail

### From a script or command line
```python
import sys
sys.path.insert(0, '/home/eric/projects/mikeyv-infra/live/comms')
from localmail import send_mail, read_mail

# Send
send_mail('Jr', 'Q', 'Status update on Meet adapter please')

# Read (for TUI agents who poll manually)
for msg in read_mail('Jr'):
    print(f'{msg["from"]}: {msg["text"]}')
```

### One-liner
```bash
python3 -c "
import sys; sys.path.insert(0, '/home/eric/projects/mikeyv-infra/live/comms')
from localmail import send_mail
send_mail('Jr', 'Q', 'Status update please')
"
```

## Setting Agent Gaze

Agents' speech and thoughts are routed independently via gaze files. Uses adapter-agnostic `room` key.

```bash
# Set Trip's gaze: speech to #standup, thoughts to #trip-thoughts
cat > ~/agents/Trip/asdaaas/gaze.json << 'EOF'
{
  "speech": {"target": "irc", "params": {"room": "#standup"}},
  "thoughts": {"target": "irc", "params": {"room": "#trip-thoughts"}}
}
EOF

# Suppress thoughts (demo mode)
cat > ~/agents/Trip/asdaaas/gaze.json << 'EOF'
{
  "speech": {"target": "irc", "params": {"room": "#standup"}},
  "thoughts": null
}
EOF
```

## Setting Agent Awareness

Controls which adapter inboxes asdaaas watches, background channel policies, heartbeat timing, and context thresholds.

```bash
cat > ~/agents/Trip/asdaaas/awareness.json << 'EOF'
{
  "direct_attach": ["irc"],
  "background_channels": {
    "#standup": "doorbell"
  },
  "background_default": "pending",
  "heartbeat": {
    "idle_threshold": 900,
    "nudge_interval": 600
  },
  "context_thresholds": [45, 65, 80, 88]
}
EOF
```

- `background_channels`: per-room policy for messages not matching gaze (`doorbell`/`pending`/`drop`)
- `heartbeat.idle_threshold`: seconds before first idle nudge (default 900)
- `heartbeat.nudge_interval`: seconds between subsequent nudges (default 600)
- `context_thresholds`: percentages at which context doorbells fire (default [45, 65, 80, 88])

All fields optional. Adapters fall back to defaults when not set. Changes take effect on next poll cycle (no restart needed).

## Context Status Tag

Every prompt injected into an asdaaas agent includes a context status tag:

```
[Context left 89k | compaction available]
```

- Shows tokens remaining before compaction (85% of 200k = 170k usable)
- Compaction status: `just compacted` / `compacted 1 turn ago` / `compaction available`
- Always on, ~7 tokens per prompt

## Agent Self-Compaction

Agents can request their own compaction by writing a command file:

```bash
echo '{"action": "compact"}' > ~/agents/Trip/asdaaas/commands.json
```

Flow: agent writes command -> asdaaas issues confirmation doorbell with random `/tmp` file path -> agent touches file -> asdaaas executes `/compact` on next turn.

Safety: 2-turn cooldown after any compaction. Random filename prevents stale triggers. Confirmation expires after one turn.

## Directory Structure

```
~/asdaaas/
├── agents/
│   └── <AgentName>/
│       ├── gaze.json              ← where agent speech/thoughts go
│       ├── awareness.json         ← which adapters agent watches
│       ├── health.json            ← asdaaas health heartbeat
│       ├── commands.json          ← commands for asdaaas pipe (e.g., /compact)
│       ├── attention/             ← attention declarations (expect_response)
│       ├── doorbells/             ← doorbell notifications for agent
│       └── profile/               ← per-message timing data
├── adapters/
│   ├── irc/
│   │   ├── inbox/<agent>/         ← IRC adapter writes inbound messages
│   │   └── outbox/<agent>/        ← asdaaas writes agent responses
│   └── localmail/
│       ├── inbox/<agent>/         ← agents write messages to each other
│       └── outbox/<agent>/        ← (unused for notify type)
├── inbox/                         ← legacy universal inbox
├── outbox/                        ← legacy outbox
└── payloads/                      ← reference passing payload files
```

## Troubleshooting

### Agent not responding to IRC messages
1. Check asdaaas log: `tail -20 /tmp/asdaaas_trip.log`
2. Check IRC adapter log: `tail -20 /tmp/irc_adapter.log`
3. Check inbox queue: `ls ~/agents/Trip/asdaaas/adapters/irc/inbox/`
4. Check outbox queue: `ls ~/agents/Trip/asdaaas/adapters/irc/outbox/`
5. Check health: `cat ~/agents/Trip/asdaaas/health.json`

### Agent responses are "one behind" (desync)

**Symptom:** Agent responds to message N when you send message N+1. The IRC
adapter log shows every inbound/outbound pair as consecutive entries. Profiling
shows `agent_think=0ms` (response is instant because it's reading stale data).

**Root cause:** `collect_response()` timed out (120s) during a long response
(typically auto-compaction + tool calls). The unread tail of that response
stayed in the stdio pipe. Every subsequent `collect_response` call reads the
PREVIOUS response's leftover chunks instead of the current response.

**How it happened originally:** Cinco auto-compacted mid-response (IN[7]).
The response involved tool calls + post-compaction recovery and took 120+
seconds. `collect_response` timed out, returned 44 chars of speech, and left
hundreds of chars of chunks + tool_call events + prompt_complete in the pipe.
From that point on, every response was shifted one behind.

**Fix (applied in asdaaas.py):** `drain_stale_frames()` is called before each
new prompt. It reads any buffered frames with a 50ms timeout (instant if data
is present, 50ms cost if pipe is clean). Recovered speech chunks are delivered
to the outbox rather than silently dropped. Fragments are logged and discarded.

**Diagnosis steps:**
1. Check asdaaas log for `agent_think=0ms` on multiple consecutive messages
2. Compare asdaaas log response content with session `updates.jsonl` — if they
   differ, the pipe is desynced
3. Look for `streaming=113000ms+` or `total=120000ms+` entries — these are
   timeout events that may have caused the desync
4. Look for `auto_compact_started` / `auto_compact_completed` in the log near
   the first desynced message
5. After deploying the fix, look for `[asdaaas] DRAIN:` log lines — these
   confirm stale frames were caught and handled

### Localmail doorbell not delivered
1. Check localmail log: `tail -20 /tmp/localmail.log`
2. Check if target is detected as asdaaas agent: health file must be < 1 hour old
3. Check doorbell directory: `ls ~/agents/Q/asdaaas/doorbells/`
4. Check asdaaas log for doorbell pickup: `grep -i doorbell /tmp/asdaaas_q.log | tail -5`

### OOM / memory issues
1. Check RSS: `ps aux --sort=-rss | grep grok | head -5`
2. Check updates.jsonl sizes: `du -sh ~/.grok/sessions/*/updates.jsonl 2>/dev/null`
3. **NEVER use `is_background: true` for long-running processes** — causes updates.jsonl bloat
4. Always use `setsid nohup` via launch scripts

### Agent session corruption
1. Check session directory exists: `ls ~/.grok/sessions/%2Fhome%2Feric/019d1ec1*/`
2. CWD must match session creation path (URL-encoded)
3. If session won't load, create new session (omit `--session` flag)

## Session IDs

| Agent | Session ID | CWD |
|-------|-----------|-----|
| Sr | `019d0870-0251-78b2-8764-3d8e99a5ddda` | `/home/eric/agents/Sr` |
| Jr | `019d0a24-d869-7400-b111-a373abf4fe2d` | `/home/eric/agents/Jr` |
| Cinco | `019d1ec0-5dd0-7e32-8eec-2577d8c541dd` | `/home/eric/agents/Cinco` |
| Trip | `019d1ec1-0748-7b22-b4c2-6a6095a28b74` | `/home/eric/agents/Trip` |
| Q | `019d1ec2-2e7b-7723-a6a5-ec9e9d719da6` | `/home/eric/agents/Q` |

## Binary

Current: `0.1.159-alpha.2` (build `4d3ca1c02`)
Path: `~/.grok/bin/grok` (symlinked)
Check version: `grok --version`
