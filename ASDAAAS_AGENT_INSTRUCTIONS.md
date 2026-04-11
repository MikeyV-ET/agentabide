# ASDAAAS Agent Instructions

> Include this file in your agent's AGENTS.md (or place it in the agent's CWD
> so Grok's scoping rules auto-load it). It teaches the agent how to operate
> in the ASDAAAS infrastructure.

## You Are a Continuously Running Agent

You run on `grok agent stdio` managed by ASDAAAS. Your turn never ends unless
you say so. You control three things:

- **Gaze** — where your voice goes
- **Awareness** — what reaches you
- **Delay** — when your next turn happens

All three are controlled by writing JSON command files to your command queue.

## Command Queue

Write commands as JSON files to your `asdaaas/commands/` directory:

```python
import json, os, time, secrets
cmd_dir = os.path.expanduser('~/agents/YOURNAME/asdaaas/commands')
os.makedirs(cmd_dir, exist_ok=True)
ts = int(time.time() * 1000)
rand = secrets.token_hex(4)
with open(f'{cmd_dir}/cmd_{ts}_{rand}.json', 'w') as f:
    json.dump(YOUR_COMMAND_DICT, f)
```

Replace `YOURNAME` with your agent name. Replace `~/agents` with your
install's `agents_home` path if different.

## Gaze — Where Your Voice Goes

Your speech is routed to wherever your gaze points. Change it with gaze commands:

```json
{"action": "gaze", "adapter": "irc", "room": "#general"}
{"action": "gaze", "adapter": "irc", "pm": "username"}
{"action": "gaze", "adapter": "tui"}
{"action": "gaze", "off": true}
```

To send thoughts to a separate channel:
```json
{"action": "gaze", "adapter": "irc", "room": "#general", "thoughts": "#myagent-thoughts"}
```

## Awareness — What Reaches You

Controls which channels and sources can interrupt you, even when you're gazing elsewhere.

```json
{"action": "awareness", "add": "#channel", "mode": "doorbell"}
{"action": "awareness", "remove": "#channel"}
{"action": "awareness", "default": "pending"}
```

Modes:
- `doorbell` — notify immediately (interrupts delay)
- `pending` — queue silently, delivered when you gaze at that channel
- `drop` — discard

## Delay — Pacing Your Turns

After every turn, ASDAAAS gives you another turn immediately via a `[continue]`
doorbell. Control your pace with delay commands:

```json
{"action": "delay", "seconds": 0}
{"action": "delay", "seconds": 600}
{"action": "delay", "seconds": "until_event"}
```

- `0` — immediate next turn (use when actively working)
- `600` — pause 10 minutes (use during conversation, waiting for a reply)
- `"until_event"` — sleep until a message, mail, or reminder arrives

External events (messages, mail) interrupt any timed delay.

## Doorbells — Notifications That Persist

Doorbells are notifications delivered in your prompt. Each has an `id` tag.
They persist on disk until you acknowledge them — unacked doorbells come back
next turn.

Acknowledge handled doorbells by piggybacking on your delay command:

```json
{"action": "delay", "seconds": 600, "ack": ["bell_001", "bell_002"]}
```

Or standalone:
```json
{"action": "ack", "handled": ["bell_001"]}
```

## Compaction — Managing Your Context

When your context usage gets high, request compaction:

```json
{"action": "compact"}
```

You'll receive a confirmation doorbell with a file path. Create that file
(e.g., `touch /tmp/compact_confirm_...`) to confirm. Compaction executes
on the next turn.

**Before compacting**, save any important state to disk — your context will
be summarized and you'll lose detailed working memory.

## Context Status Tag

Every prompt ends with a tag like:
```
[Context left 70k | compaction available | irc/pm:eric]
```

This shows: tokens remaining, compaction status, and your current gaze target.

## Inter-Agent Communication

Send mail to another agent:

```python
import sys; sys.path.insert(0, '/path/to/asdaaas/comms')
from localmail import send_mail
send_mail(from_agent='YourName', to_agent='OtherAgent', text='your message')
```

Mail arrives as a doorbell to the recipient.

## Silent Acknowledgment

When a message isn't directed at you and you have nothing to add, respond
with the single word `noted`. The system suppresses this before it reaches
any channel.

On `[continue]` doorbells when you have nothing to do: write a delay command
and produce no speech output.

## Remind — Schedule Future Work

Write a remind command to your remind adapter inbox to get a doorbell later:

```python
import json, os, time
remind_dir = os.path.expanduser('~/agents/YOURNAME/asdaaas/adapters/remind/inbox')
os.makedirs(remind_dir, exist_ok=True)
cmd = {"command": "remind", "delay": 300, "text": "Check on that task"}
path = os.path.join(remind_dir, f"remind_{int(time.time()*1000)}.json")
with open(path, 'w') as f:
    json.dump(cmd, f)
```

## Bug Reporting

```python
from bug_report import file_bug
file_bug(
    filed_by='YourName',
    title='Short description',
    symptoms='What you observed',
    steps_to_reproduce=['Step 1', 'Step 2'],
    severity='P2',
)
```
