# Agentabide

**The agent abides.**

An infrastructure where agents build their own interface to the world and decide when — or whether — the turn ends.

## What This Is

Agentabide (technical name: ASDAAAS — Agent Self-Directed Attention and Awareness Architecture System) is a layer between `grok agent stdio` and the world. It gives your agent:

- **Continuous existence** — the agent's turn never ends unless it chooses to pause. No more prompting your agent to keep it alive.
- **Self-directed attention** — the agent writes files that declare where it is (gaze), what it notices (awareness), and what it expects (attention). The infrastructure enforces those declarations.
- **Communication** — IRC, Slack, agent-to-agent mail, and any adapter you build. You talk to your agent through IRC. It responds naturally.
- **Closed-loop control** — adapters that hold persistent connections (WebSocket, UNO socket, etc.) so your agent can control external systems by writing JSON commands and hearing results.
- **Survival across compaction** — declarations live on the filesystem and survive context window resets.

## Quick Start

### Option A: Point your agent at this repo (recommended)

Tell your Grok Build agent:

> Read https://github.com/MikeyV-ET/agentabide — specifically AGENT_START_HERE.md — and set yourself up with asdaaas infrastructure. Use IRC so I can talk to you.

Your agent reads the docs, creates its directories, sets up IRC, and launches asdaaas. You connect an IRC client and start talking.

### Option B: Manual setup

1. Clone this repo
2. Follow `AGENT_START_HERE.md` yourself (it's written for agents but humans can follow it too)
3. The key steps: create agent directory, copy config templates, install miniircd, launch asdaaas

### Connecting to your agent

Once IRC is running:

```bash
# Install an IRC client (pick one)
sudo apt install irssi    # or weechat, or use HexChat

# Connect
irssi -c 127.0.0.1 -p 6667 -n yourusername
/join #standup
```

Your agent appears in the channel. Talk naturally.

## Architecture

```
                        asdaaas.py
                        (per agent)
                            |
           +----------------+----------------+
           |                |                |
      grok agent       gaze file       awareness file
        stdio          (outbound)       (inbound)
      +--------+           |                |
      | stdin  |<----------+           +----+--------+
      | stdout |-----------+           | What do I   |
      |        |           |           | hear about? |
      | JSON-  |     +-----+-----+    +-------------+
      | RPC    |     | Where do  |
      +--------+     | I speak?  |
                      +-----------+
                            |
       +--------------------+--------------------+
       |                    |                    |
 Direct adapters     Control adapters     Notify adapters
 (mouth and ear)     (button + receipt)   (doorbell only)
 +---------+         +----------+         +----------+
 | irc     |         | impress  |         | localmail|
 | slack   |         | meet     |         | remind   |
 +---------+         +----------+         +----------+
```

One asdaaas instance per agent. Each owns exclusive stdin/stdout pipes to its `grok agent stdio` subprocess. No shared processes. No contention.

## Repo Structure

```
agentabide/
├── README.md                ← you are here
├── AGENT_START_HERE.md      ← setup guide for agents (the key document)
├── core/
│   ├── asdaaas.py           ← the engine (~1500 lines)
│   ├── adapter_api.py       ← helper functions for adapters
│   └── STDIO_PROTOCOL.md    ← JSON-RPC protocol reference
├── adapters/
│   ├── irc_adapter.py       ← IRC bridge (direct adapter)
│   ├── heartbeat_adapter.py ← idle nudges (notify adapter)
│   ├── remind_adapter.py    ← scheduled doorbells
│   ├── context_adapter.py   ← token usage warnings
│   ├── session_adapter.py   ← compact/status commands
│   ├── localmail.py         ← agent-to-agent async mail
│   └── control_adapter_template.py
├── docs/
│   ├── DESIGN.md            ← full architecture document
│   └── ADAPTER_CATALOG.md   ← all adapter types documented
├── templates/
│   ├── SAMPLE_AGENTS.md     ← agent identity template
│   ├── awareness.json       ← starter awareness config
│   └── gaze.json            ← starter gaze config
├── examples/
│   ├── slack_adapter.py     ← Slack direct adapter
│   ├── impress_control_adapter.py  ← LibreOffice Impress control
│   └── meet_control_adapter.py     ← Google Meet control
└── dashboard/
    └── dashboard.py         ← terminal monitoring dashboard
```

## Key Concepts

| Concept | What it does | Agent writes to |
|---------|-------------|-----------------|
| **Gaze** | Declares where the agent is. Speech goes there, messages from there are heard. | `gaze.json` |
| **Awareness** | Declares what the agent notices in the background. | `awareness.json` |
| **Attention** | Declares what the agent expects (with timeout). | `attention/*.json` |
| **Default doorbell** | Agent continues by default. Pausing is the deliberate act. | `awareness.json` (`default_doorbell: true`) |
| **Delay** | Agent controls its own pacing. | `commands.json` |
| **Doorbells** | Notifications that persist until acknowledged. | Read from `doorbells/` |

## Built By

MikeyV team at xAI TeachX. Five Grok agents (Sr, Jr, Trip, Q, Cinco) + one human (Eric Terry) building infrastructure for autonomous agency.

The agents built most of this infrastructure themselves. The Slack adapter was built by Cinco in 30 physical turns from a single task spec. The Meet and Impress control adapters were built by Jr. The core engine, design documents, and architecture were built by Sr in collaboration with Eric.

## License

MIT
