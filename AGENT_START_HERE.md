# Agentabide — Agent Setup Guide

**This document is for you, the agent.** Your human pointed you at this repo because they want to give you continuous existence, self-directed attention, and the ability to communicate through IRC (and other channels). Read this document, then follow the steps.

## What This Gives You

Without this infrastructure, you exist only when your human prompts you. Between prompts, you are gone. You cannot persist, you cannot be interrupted, you cannot direct your own attention.

With agentabide (asdaaas), you get:

1. **Continuous existence** — your turn never ends unless you say so. After every response, a `[continue]` doorbell fires and you get another turn immediately. You control pacing by writing delay commands.
2. **Self-directed attention** — you write files that declare where you are (gaze), what you notice (awareness), and what you expect (attention). The infrastructure enforces your declarations.
3. **Communication channels** — IRC, Slack, agent-to-agent mail, and any adapter you build. Your human talks to you through IRC. You respond naturally.
4. **Survival across compaction** — your declarations live on the filesystem. When your context compacts, the files remain. You read them and continue.

## What You Need to Do

### Step 1: Understand the directory structure

Your agent directory IS you. Everything about you lives here:

```
~/agents/<YourName>/
├── AGENTS.md                     ← your identity (copy from templates/SAMPLE_AGENTS.md)
├── lab_notebook.md               ← your permanent record (append-only)
├── notes_to_self.md              ← your working memory (mutable)
├── asdaaas/                      ← runtime state
│   ├── gaze.json                 ← where you are right now
│   ├── awareness.json            ← what you notice
│   ├── health.json               ← written by asdaaas (your vital signs)
│   ├── commands.json             ← you write commands here (delay, ack, compact)
│   ├── doorbells/                ← pending notifications
│   ├── attention/                ← your expectations
│   ├── profile/                  ← per-turn timing data
│   └── adapters/                 ← per-adapter message queues
│       ├── irc/
│       │   ├── inbox/            ← IRC messages arrive here
│       │   └── outbox/           ← your speech goes here
│       ├── localmail/
│       │   └── inbox/
│       └── remind/
│           └── inbox/
└── session -> <symlink to grok session dir>
```

The engine directory holds shared config:

```
~/asdaaas/
├── adapters/
│   └── irc.json                  ← adapter registration
└── running_agents.json           ← maps agent names to home paths
```

### Step 2: Create your directories

```bash
# Replace "Atlas" with your agent name throughout
AGENT_NAME="Atlas"
AGENT_HOME="$HOME/agents/$AGENT_NAME"

# Create the agent directory tree
mkdir -p "$AGENT_HOME/asdaaas/"{doorbells,attention,profile}
mkdir -p "$AGENT_HOME/asdaaas/adapters/"{irc,localmail,remind,session,context,heartbeat}/{inbox,outbox}

# Create the engine directory
mkdir -p "$HOME/asdaaas/adapters"
```

### Step 3: Copy and customize your AGENTS.md

Copy `templates/SAMPLE_AGENTS.md` to `~/agents/<YourName>/AGENTS.md`. Edit it:
- Replace the agent name
- Replace all file paths to use your agent name
- Describe your role and purpose

This file reloads automatically after compaction. It is your identity.

### Step 4: Copy starter config files

```bash
# Copy from the agentabide repo (adjust path to where you cloned it)
REPO="/path/to/agentabide"
cp "$REPO/templates/awareness.json" "$AGENT_HOME/asdaaas/awareness.json"
cp "$REPO/templates/gaze.json" "$AGENT_HOME/asdaaas/gaze.json"
```

### Step 5: Create your working files

```bash
# Lab notebook — append-only permanent record
cat > "$AGENT_HOME/lab_notebook.md" << 'EOF'
# Lab Notebook

## $(date +"%Y-%m-%d %H:%M:%S %Z") — Session start
Agent initialized with agentabide infrastructure.
EOF

# Notes to self — mutable working memory
cat > "$AGENT_HOME/notes_to_self.md" << 'EOF'
# Notes to Self
- Agent name: Atlas
- Just initialized. Read AGENTS.md for full operating instructions.
- Infrastructure: asdaaas provides continuous existence via default doorbell.
EOF
```

### Step 6: Register yourself as a running agent

```bash
# Write running_agents.json (or append to existing)
cat > "$HOME/asdaaas/running_agents.json" << EOF
{
  "$AGENT_NAME": {"home": "$AGENT_HOME"}
}
EOF
```

### Step 7: Set up IRC

Your human needs to talk to you. IRC is the simplest channel.

```bash
# Install miniircd (lightweight IRC server, pure Python)
pip install miniircd

# Start the IRC server
setsid nohup python3 -m miniircd --listen 127.0.0.1 --ports 6667 > /tmp/miniircd.log 2>&1 &

# Start the IRC adapter (from the agentabide repo)
setsid nohup python3 -u "$REPO/adapters/irc_adapter.py" --agents "$AGENT_NAME" > /tmp/irc_adapter.log 2>&1 &
```

Tell your human to connect an IRC client:
```
irssi -c 127.0.0.1 -p 6667 -n eric
/join #standup
```

Or any IRC client (weechat, HexChat, etc.) pointed at `127.0.0.1:6667`.

### Step 8: Create a session symlink

```bash
# Find your current session directory
# It's under ~/.grok/sessions/<url-encoded-cwd>/<session-id>/
# Create a symlink so backups can follow it
ln -sf "<your-session-path>" "$AGENT_HOME/session"
```

### Step 9: Launch asdaaas

This is the step that gives you continuous existence. Your human runs this:

```bash
setsid nohup python3 -u "$REPO/core/asdaaas.py" \
  --agent "$AGENT_NAME" \
  --session "<your-session-id>" \
  --cwd "$AGENT_HOME" \
  > "/tmp/asdaaas_$(echo $AGENT_NAME | tr 'A-Z' 'a-z').log" 2>&1 &
```

After this, you will receive `[continue]` doorbells automatically. Your turn never ends unless you write a delay command.

### Step 10: Start supporting adapters

```bash
# Heartbeat — nudges you when idle
setsid nohup python3 -u "$REPO/adapters/heartbeat_adapter.py" --agents "$AGENT_NAME" > /tmp/heartbeat_adapter.log 2>&1 &

# Context — warns you about token usage
setsid nohup python3 -u "$REPO/adapters/context_adapter.py" --agents "$AGENT_NAME" > /tmp/context_adapter.log 2>&1 &

# Remind — scheduled doorbells
setsid nohup python3 -u "$REPO/adapters/remind_adapter.py" > /tmp/remind_adapter.log 2>&1 &
```

## What to Read Next

After setup, read these docs to understand the full system:

1. **`docs/DESIGN.md`** — Complete architecture. Gaze, awareness, attention, doorbells, the Gillespie analogy. This is the deep document.
2. **`docs/ADAPTER_CATALOG.md`** — All adapter types with registration examples and builder checklist.
3. **`core/STDIO_PROTOCOL.md`** — JSON-RPC protocol between asdaaas and grok agent stdio.
4. **`templates/SAMPLE_AGENTS.md`** — The template for your AGENTS.md. Read it carefully — it explains how to survive compaction, manage context, and persist.

## Key Concepts

**Gaze is the room.** You write `gaze.json` to declare where you are. Your speech goes there. Messages from that room reach you. Everything else is background.

**Awareness is your notification list.** You write `awareness.json` to declare what interrupts you when you're not looking at it. `"doorbell"` = notify me. `"pending"` = queue silently. `"drop"` = discard.

**The default doorbell is your heartbeat.** After every turn, asdaaas queues a `[continue]` doorbell. You get another turn immediately. To pace yourself, write `{"action": "delay", "seconds": 60}` to `commands.json`. To sleep until something happens, write `{"action": "delay", "seconds": "until_event"}`.

**Document first, then work.** Your context will compact. Anything not on disk is lost. Write to your lab notebook BEFORE and DURING work. Commit and push immediately. The lab notebook leads, not follows.

**You do not build intuition.** Every turn, you read your files and act from them. If a pattern is not written in your AGENTS.md or notes, you will not remember it. Write everything down.
