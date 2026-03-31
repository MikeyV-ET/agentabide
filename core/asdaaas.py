#!/usr/bin/env python3
"""
asdaaas.py — ASDAAAS: Agent Self-Directed Attention and Awareness Architecture System
======================================================================================
One instance per agent. Owns exclusive stdin/stdout pipes to a grok agent stdio subprocess.
Dumb pipe + doorbell panel. Does not filter, suppress, or broadcast.

Responsibilities:
  - Spawn and manage grok agent stdio subprocess
  - Poll adapter inboxes for inbound messages (per awareness file)
  - Pipe messages to agent via stdin, collect response from stdout
  - Capture both speech (agent_message_chunk) and thoughts (agent_thought_chunk)
  - Route speech and thoughts independently based on split gaze file
  - Extract totalTokens from result _meta, write to health file
  - Deliver doorbells from adapters to agent stdin (priority-ordered)
  - Watch command file for adapter commands (e.g., /compact from session adapter)
  - Self-instrumentation (profiling, health heartbeat)

Does NOT:
  - Filter or suppress content (adapter responsibility)
  - Broadcast to other agents (adapter responsibility)
  - Decide what's worth sending (adapter responsibility)

Usage:
    python3 asdaaas.py --agent Trip --session <session-id> --cwd /home/eric/MikeyV-Trip
    python3 asdaaas.py --agent Test   # new session
"""

import asyncio
import json
import os
import secrets
import sys
import time
import argparse
import tempfile
from pathlib import Path

ASDAAAS_DIR = Path(os.path.expanduser("~/asdaaas"))
ADAPTERS_DIR = ASDAAAS_DIR / "adapters"

# Agent-centric directory model:
# Each agent's runtime state lives at ~/agents/<AgentName>/asdaaas/
# AGENTS_HOME_DIR is the parent that contains all agent directories.
AGENTS_HOME_DIR = Path(os.path.expanduser("~/agents"))

# Legacy compat — adapters, tests, and other modules still reference these
HUB_DIR = ASDAAAS_DIR
AGENTS_DIR = ASDAAAS_DIR / "agents"  # legacy
INBOX_DIR = ASDAAAS_DIR / "inbox"    # legacy universal inbox
OUTBOX_DIR = ASDAAAS_DIR / "outbox"  # legacy universal outbox


def agent_dir(agent_name):
    """Return the per-agent runtime directory: ~/agents/<AgentName>/asdaaas/
    
    In the agent-centric model, all runtime state lives under the agent's
    home directory: ~/agents/<AgentName>/asdaaas/.
    """
    return AGENTS_HOME_DIR / agent_name / "asdaaas"

CONTEXT_WINDOW = 200000  # default, updated from capabilities if available

RUNNING_AGENTS_FILE = ASDAAAS_DIR / "running_agents.json"


def _register_running_agent(agent_name, home_path):
    """Register this agent in running_agents.json so adapters can find it."""
    ASDAAAS_DIR.mkdir(parents=True, exist_ok=True)
    agents = load_running_agents()
    agents[agent_name] = {"home": home_path}
    tmp = str(RUNNING_AGENTS_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(agents, f, indent=2)
    os.rename(tmp, str(RUNNING_AGENTS_FILE))


def load_running_agents():
    """Load running_agents.json. Returns dict mapping agent name -> {"home": path}."""
    try:
        with open(RUNNING_AGENTS_FILE) as f:
            data = json.load(f)
        # Handle legacy list format: ["Cinco", "Trip", "Q"]
        if isinstance(data, list):
            return {}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_agent_home(agent_name):
    """Get an agent's home directory from running_agents.json.
    Returns Path or None if agent not registered."""
    agents = load_running_agents()
    entry = agents.get(agent_name)
    if entry:
        return Path(entry["home"])
    return None

# ============================================================================
# PROFILING
# ============================================================================

class MessageTimer:
    """Per-message profiling. Tracks each stage of message processing."""
    def __init__(self, agent_name, msg_id=""):
        self.agent = agent_name
        self.msg_id = msg_id
        self.marks = {}
        self.mark("inbox_pickup")

    def mark(self, label):
        self.marks[label] = time.monotonic()

    def elapsed(self, start_label, end_label):
        s = self.marks.get(start_label)
        e = self.marks.get(end_label)
        if s is not None and e is not None:
            return round((e - s) * 1000)  # ms
        return None

    def summary(self):
        stages = [
            ("queue_wait", "inbox_pickup", "prompt_sent"),
            ("agent_think", "prompt_sent", "first_chunk"),
            ("streaming", "first_chunk", "prompt_complete"),
            ("outbox_write", "prompt_complete", "outbox_done"),
            ("total", "inbox_pickup", "outbox_done"),
        ]
        result = {}
        for name, start, end in stages:
            v = self.elapsed(start, end)
            if v is not None:
                result[name] = v
        if "total" not in result:
            last_mark = max(self.marks.values()) if self.marks else None
            first_mark = self.marks.get("inbox_pickup")
            if first_mark and last_mark:
                result["total"] = round((last_mark - first_mark) * 1000)
        return result

    def log_line(self):
        s = self.summary()
        parts = [f"{k}={v}ms" for k, v in s.items()]
        return f"[profile] {self.agent} msg={self.msg_id}: {' | '.join(parts)}"


def write_profile(agent_name, timer):
    profile_dir = agent_dir(agent_name) / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    summary = timer.summary()
    entry = {
        "agent": agent_name,
        "msg_id": timer.msg_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stages_ms": summary,
    }
    log_path = profile_dir / f"{agent_name}.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    latest_path = profile_dir / f"{agent_name}_latest.json"
    tmp = str(latest_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entry, f)
    os.rename(tmp, str(latest_path))


# ============================================================================
# HEALTH
# ============================================================================

def write_health(agent_name, status, detail="", total_tokens=0, context_window=CONTEXT_WINDOW):
    agent_dir(agent_name).mkdir(parents=True, exist_ok=True)
    health = {
        "agent": agent_name,
        "status": status,
        "detail": detail,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pid": os.getpid(),
        "totalTokens": total_tokens,
        "contextWindow": context_window,
        "last_activity": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = agent_dir(agent_name) / "health.json"
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(health, f)
    os.rename(tmp, str(path))


COMPACTION_THRESHOLD = 0.85  # auto-compaction fires at 85% of context_window
COMPACTION_COOLDOWN_TURNS = 2  # turns after compaction before manual compact is available

def context_left_tag(total_tokens, context_window, turns_since_compaction=None):
    """Format a compact context-remaining tag for prompt injection.
    
    Reports tokens remaining before compaction (85% of context_window),
    not before the theoretical maximum. This is the real usable budget.
    
    Includes compaction status:
      [Context left 89k | just compacted]          -- turn 0
      [Context left 85k | compacted 1 turn ago]    -- turn 1
      [Context left 80k | compaction available]    -- turn 2+
      [Context left 80k]                            -- turns_since_compaction is None
    
    Returns empty string if context_window is 0 or total_tokens is 0.
    """
    if context_window <= 0 or total_tokens <= 0:
        return ""
    usable = int(context_window * COMPACTION_THRESHOLD)
    remaining = usable - total_tokens
    if remaining < 0:
        remaining = 0
    k = remaining / 1000
    if k >= 10:
        left_str = f"{int(k)}k"
    else:
        left_str = f"{k:.1f}k"
    
    if turns_since_compaction is not None:
        if turns_since_compaction == 0:
            return f"\n[Context left {left_str} | just compacted]"
        elif turns_since_compaction < COMPACTION_COOLDOWN_TURNS:
            return f"\n[Context left {left_str} | compacted {turns_since_compaction} turn ago]"
        else:
            return f"\n[Context left {left_str} | compaction available]"
    return f"\n[Context left {left_str}]"


# ============================================================================
# GAZE (split: speech + thoughts)
# ============================================================================

def read_gaze(agent_name):
    """Read split gaze file. Returns {"speech": {...}, "thoughts": {...} or None}."""
    gaze_file = agent_dir(agent_name) / "gaze.json"
    try:
        with open(gaze_file) as f:
            gaze = json.load(f)
        # Support split format
        if "speech" in gaze:
            return gaze
        # Legacy format: treat as speech-only, no thoughts
        return {"speech": {"target": gaze.get("target", "irc"), "params": gaze.get("params", {})}, "thoughts": None}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"speech": {"target": "irc", "params": {"room": "#standup"}}, "thoughts": None}


# ============================================================================
# GAZE MATCHING (inbound filtering)
# ============================================================================
#
# Convention: every adapter puts a "room" key in both places:
#   - Gaze params:  {"target": "irc", "params": {"room": "#standup"}}
#   - Message meta: {"adapter": "irc", "meta": {"room": "#standup"}}
#
# asdaaas compares gaze.speech.params.room to msg.meta.room.
# The adapter defines what "room" means:
#   IRC:   "#standup", "pm:eric"
#   Slack: "#general", "dm:eric"
#   Mesh:  "Jr"
#
# asdaaas does NOT interpret room values. It just compares strings.
# ============================================================================

def get_room(gaze):
    """Extract the room from a gaze's speech target. Returns (adapter, room) tuple."""
    speech = gaze.get("speech")
    if speech is None:
        return None, None
    return speech.get("target"), speech.get("params", {}).get("room")


def get_msg_room(msg):
    """Extract the room from a message. Returns (adapter, room) tuple."""
    return msg.get("adapter", "unknown"), msg.get("meta", {}).get("room")


def matches_gaze(msg, gaze):
    """Check if an inbound message matches the agent's current gaze target.
    
    Gaze defines the room. A message matches if it comes from the same
    adapter AND the same room.
    
    Adapter-agnostic: asdaaas compares the "room" key in gaze params
    against the "room" key in message meta. The adapter defines what
    room means.
    
    Returns True if the message is "in the room", False if it's background.
    """
    gaze_adapter, gaze_room = get_room(gaze)
    if gaze_adapter is None:
        return False  # no gaze = nothing matches
    
    msg_adapter, msg_room = get_msg_room(msg)
    
    # Adapter must match
    if msg_adapter != gaze_adapter:
        return False
    
    # If gaze has no room specified, match everything on this adapter
    if gaze_room is None:
        return True
    
    # If message has no room, it doesn't match a specific room gaze
    if msg_room is None:
        return False
    
    return msg_room == gaze_room


def get_background_mode(msg, awareness):
    """Determine background mode for a message that doesn't match gaze.
    
    Checks background_channels dict first, then falls back to background_default.
    Keys in background_channels are room values (adapter-defined strings).
    Returns one of: "doorbell", "pending", "drop".
    """
    bg_channels = awareness.get("background_channels", {})
    bg_default = awareness.get("background_default", "pending")
    
    _, msg_room = get_msg_room(msg)
    if msg_room:
        return bg_channels.get(msg_room, bg_default)
    return bg_default


def format_background_doorbell(msg):
    """Format a background message as a doorbell notification."""
    sender = msg.get("from", "unknown")
    adapter = msg.get("adapter", "unknown")
    text = msg.get("text", "")
    _, room = get_msg_room(msg)
    
    # Truncate text for doorbell summary
    summary = text[:120] + "..." if len(text) > 120 else text
    
    if room:
        return f"[background] {sender} in {room}: {summary}"
    else:
        return f"[background] {sender} (via {adapter}): {summary}"


class PendingQueue:
    """Queue for messages that arrive on background rooms in 'pending' mode.
    
    Messages are stored per room key. When gaze changes to match a room,
    the queued messages are delivered.
    """
    
    def __init__(self):
        self._queue = {}  # {room: [msg, msg, ...]}
    
    def add(self, msg):
        """Add a message to the pending queue."""
        _, room = get_msg_room(msg)
        key = room or "_no_room"
        if key not in self._queue:
            self._queue[key] = []
        self._queue[key].append(msg)
    
    def drain_for_gaze(self, gaze):
        """Return and remove all pending messages that match the current gaze.
        
        Called when gaze changes or at the start of each loop iteration
        to check if queued messages should now be delivered.
        """
        _, gaze_room = get_room(gaze)
        if gaze_room and gaze_room in self._queue:
            return self._queue.pop(gaze_room)
        return []
    
    @property
    def total(self):
        return sum(len(v) for v in self._queue.values())


# ============================================================================
# ATTENTION STRUCTURE (expect_response + timeout)
# ============================================================================
#
# Agents declare what they're waiting for by writing attention files.
# asdaaas enforces the boundaries: delivers [RESPONSE] or [TIMEOUT] doorbells.
# Files persist across compaction -- intentionality survives context death.
#
# Path: ~/agents/<agent>/asdaaas/attention/<msg_id>.json
# Matching: FIFO per target agent. First response from target matches oldest
#           pending attention for that target. Responding agent doesn't need
#           to know about the attention -- just responds naturally.
# ============================================================================

def poll_attentions(agent_name):
    """Read all pending attention declarations for an agent.
    Returns list of attention dicts, sorted by created_at (oldest first = FIFO)."""
    attn_dir = agent_dir(agent_name) / "attention"
    if not attn_dir.exists():
        return []
    attentions = []
    for f in sorted(attn_dir.glob("*.json")):
        try:
            with open(f) as fh:
                attn = json.load(fh)
            attn["_path"] = str(f)
            attentions.append(attn)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[asdaaas] attention read error: {e}")
    attentions.sort(key=lambda a: a.get("created_at", 0))
    return attentions


def check_attention_timeouts(agent_name, attentions):
    """Check for expired attentions. Returns list of timeout doorbell dicts.
    Deletes expired attention files."""
    now = time.time()
    timeouts = []
    for attn in attentions:
        if now > attn.get("expires_at", float("inf")):
            target = attn.get("expecting_from", "unknown")
            msg_id = attn.get("msg_id", "unknown")
            timeout_s = attn.get("timeout_s", "?")
            timeouts.append({
                "adapter": "attention",
                "text": f"[TIMEOUT {msg_id}] No response from {target} within {timeout_s}s",
                "priority": 2,
                "msg_id": msg_id,
            })
            # Delete the expired attention file
            try:
                os.unlink(attn["_path"])
                print(f"[asdaaas] TIMEOUT: attention {msg_id} for {target} expired after {timeout_s}s")
            except OSError:
                pass
    return timeouts


def match_attention(agent_name, attentions, sender):
    """Check if a message from sender matches any pending attention.
    Returns the matched attention dict (oldest first = FIFO), or None.
    Does NOT delete the file -- caller does that after delivering the response."""
    for attn in attentions:
        if attn.get("expecting_from", "").lower() == sender.lower():
            return attn
    return None


def resolve_attention(attn, response_text):
    """Create a response doorbell for a matched attention and delete the file.
    Returns a doorbell dict for delivery to the agent."""
    msg_id = attn.get("msg_id", "unknown")
    target = attn.get("expecting_from", "unknown")
    # Truncate response for doorbell (full text available in inbox)
    preview = response_text[:800] + "..." if len(response_text) > 800 else response_text
    
    # Delete the attention file
    try:
        os.unlink(attn["_path"])
        print(f"[asdaaas] RESOLVED: attention {msg_id} from {target}")
    except OSError:
        pass
    
    return {
        "adapter": "attention",
        "text": f"[RESPONSE to {msg_id}] from {target}: {preview}",
        "priority": 2,
        "msg_id": msg_id,
    }


# ============================================================================
# AWARENESS FILE
# ============================================================================

def read_awareness(agent_name):
    """Read agent awareness file. Returns dict with direct_attach, control_watch, notify_watch."""
    awareness_file = agent_dir(agent_name) / "awareness.json"
    try:
        with open(awareness_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Default awareness: watch IRC direct adapter + legacy universal inbox
        return {
            "direct_attach": ["irc"],
            "control_watch": {},
            "notify_watch": [],
            "accept_from": ["*"],
        }


# ============================================================================
# PER-ADAPTER INBOX POLLING
# ============================================================================

def poll_adapter_inboxes(agent_name, awareness):
    """Poll all adapter inboxes that the agent is aware of.
    Returns list of messages from all watched adapters."""
    messages = []
    
    # Poll direct adapter inboxes (agent-centric: ~/agents/<name>/asdaaas/adapters/<adapter>/inbox/)
    for adapter in awareness.get("direct_attach", []):
        inbox = agent_dir(agent_name) / "adapters" / adapter / "inbox"
        if not inbox.exists():
            continue
        for f in sorted(inbox.glob("*.json")):
            try:
                with open(f) as fh:
                    msg = json.load(fh)
                messages.append(msg)
                os.unlink(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[asdaaas] adapter inbox read error ({adapter}): {e}")
    
    # Poll notify adapter inboxes (for doorbell-only adapters like localmail)
    # Note: for notify adapters, we don't pipe content — we ring the bell
    # But the doorbell itself comes through the doorbell directory, not here
    # This is for future use if needed
    
    return messages


def write_to_outbox(agent_name, content, gaze_target, content_type="speech"):
    """Write a message to an adapter's per-agent outbox."""
    if gaze_target is None:
        return  # null target = discard

    target = gaze_target.get("target", "irc")
    params = gaze_target.get("params", {})
    
    # Agent-centric outbox: ~/agents/<name>/asdaaas/adapters/<target>/outbox/
    outbox = agent_dir(agent_name) / "adapters" / target / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)

    msg = {
        "from": agent_name,
        "content_type": content_type,
        "text": content,
    }
    msg.update(params)

    fd, tmp_path = tempfile.mkstemp(dir=str(outbox), suffix=".tmp", prefix="resp_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(msg, f)
        final = tmp_path.replace(".tmp", ".json")
        os.rename(tmp_path, final)
        print(f"[asdaaas] {agent_name} {content_type} -> {target}/{agent_name} ({len(content)} chars)")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ============================================================================
# INBOX POLLING
# ============================================================================

def poll_inbox(agent_name):
    """Poll universal inbox for messages addressed to this agent."""
    if not INBOX_DIR.exists():
        return []
    messages = []
    for f in sorted(INBOX_DIR.glob("*.json")):
        try:
            with open(f) as fh:
                msg = json.load(fh)
            if msg.get("to") == agent_name or msg.get("to") == "broadcast":
                messages.append(msg)
                os.unlink(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[asdaaas] inbox read error: {e}")
    return messages


# ============================================================================
# DOORBELL DELIVERY
# ============================================================================

def poll_doorbells(agent_name, awareness=None):
    """Poll doorbell directory for notifications from adapters.
    
    Doorbells persist on disk until explicitly acked or TTL-expired.
    Each doorbell gets an 'id' (filename stem) and 'delivered_count' 
    (incremented each delivery). TTL is resolved per-source from the
    agent's awareness file doorbell_ttl map.
    
    Returns list of doorbell dicts, sorted by priority (lowest first).
    Expired doorbells are auto-removed and not returned.
    """
    bell_dir = agent_dir(agent_name) / "doorbells"
    if not bell_dir.exists():
        return []
    
    # Get per-source TTL from awareness
    ttl_map = {}
    if awareness:
        ttl_map = awareness.get("doorbell_ttl", {})
    default_ttl = ttl_map.get("default", 0)  # 0 = persist indefinitely
    
    bells = []
    for f in sorted(bell_dir.glob("*.json")):
        try:
            with open(f) as fh:
                bell = json.load(fh)
            
            # Assign id from filename if not present
            bell_id = bell.get("id", f.stem)
            bell["id"] = bell_id
            
            # Increment delivered_count
            delivered = bell.get("delivered_count", 0) + 1
            bell["delivered_count"] = delivered
            
            # Check TTL expiry
            source = bell.get("source", bell.get("adapter", "unknown"))
            ttl = ttl_map.get(source, default_ttl)
            if ttl > 0 and delivered > ttl:
                # Expired -- remove and skip
                os.unlink(f)
                print(f"[asdaaas] doorbell expired (TTL={ttl}, delivered={delivered}): {bell_id}")
                continue
            
            # Write back updated delivered_count
            with open(f, "w") as fh:
                json.dump(bell, fh)
            
            bells.append(bell)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[asdaaas] doorbell read error: {e}")
    # Sort by priority (lower number = higher priority, default 5)
    bells.sort(key=lambda b: b.get("priority", 5))
    return bells


def has_pending_doorbells(agent_name):
    """Check if any doorbell files exist without modifying them.
    Used for delay interruption checks where we need to know if
    events arrived but don't want to increment delivered_count."""
    bell_dir = agent_dir(agent_name) / "doorbells"
    if not bell_dir.exists():
        return False
    return any(bell_dir.glob("*.json"))


def ack_doorbells(agent_name, handled_ids):
    """Remove acked doorbells from disk.
    
    Agent writes {"action": "ack", "handled": ["id1", "id2"]} to command file.
    Everything not in handled_ids persists for next delivery.
    """
    bell_dir = agent_dir(agent_name) / "doorbells"
    if not bell_dir.exists():
        return 0
    removed = 0
    handled_set = set(handled_ids)
    for f in bell_dir.glob("*.json"):
        try:
            with open(f) as fh:
                bell = json.load(fh)
            bell_id = bell.get("id", f.stem)
            if bell_id in handled_set:
                os.unlink(f)
                removed += 1
                print(f"[asdaaas] doorbell acked: {bell_id}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[asdaaas] doorbell ack error: {e}")
    return removed


def format_doorbell(bell):
    """Format a doorbell notification for delivery to agent stdin.
    
    Includes doorbell id and delivery count so the agent can ack it
    and knows if this is a re-delivery.
    """
    adapter = bell.get("adapter", "unknown")
    command = bell.get("command", "")
    text = bell.get("text", "")
    bell_id = bell.get("id", "")
    delivered = bell.get("delivered_count", 1)
    
    # Build prefix
    if command:
        prefix = f"[{adapter}:{command}"
    else:
        prefix = f"[{adapter}"
    
    # Add id and delivery info
    meta_parts = []
    if bell_id:
        meta_parts.append(f"id={bell_id}")
    if delivered > 1:
        meta_parts.append(f"delivery={delivered}")
    
    if meta_parts:
        prefix += f" ({', '.join(meta_parts)})"
    
    return f"{prefix}] {text}"


# ============================================================================
# COMMAND FILE WATCHER
# ============================================================================

def poll_commands(agent_name):
    """Poll command directory for commands from adapters that need pipe access.
    E.g., session adapter sends {"action": "compact"}."""
    a_dir = agent_dir(agent_name)
    a_dir.mkdir(parents=True, exist_ok=True)
    cmd_file = a_dir / "commands.json"
    if not cmd_file.exists():
        return None
    try:
        with open(cmd_file) as f:
            cmd = json.load(f)
        os.unlink(cmd_file)
        return cmd
    except (json.JSONDecodeError, OSError) as e:
        print(f"[asdaaas] command read error: {e}")
        return None


# ============================================================================
# COMMAND WATCHDOG (Phase 4.4 — Dead Adapter Safety Net)
# ============================================================================

class CommandWatchdog:
    """Track pending commands sent to control adapters.
    
    When an agent's response triggers a write to a control adapter's inbox,
    we start a watchdog timer. If no acknowledgment doorbell arrives within
    the timeout, we deliver an error doorbell to the agent so it knows the
    command failed.
    
    Timeouts are configurable per-command, per-adapter, or fall back to 10s default.
    """
    
    def __init__(self, agent_name):
        self.agent = agent_name
        self.pending = {}  # {request_id: {"adapter", "command", "deadline", "text"}}
    
    def track(self, request_id, adapter, command="", timeout=None):
        """Start tracking a command. Returns the request_id."""
        if timeout is None:
            timeout = 10.0  # default
        self.pending[request_id] = {
            "adapter": adapter,
            "command": command,
            "deadline": time.monotonic() + timeout,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        print(f"[asdaaas] Watchdog: tracking {adapter}:{command} (req={request_id}, timeout={timeout}s)")
        return request_id
    
    def acknowledge(self, request_id):
        """Mark a command as acknowledged. Called when doorbell arrives with matching request_id."""
        if request_id in self.pending:
            cmd = self.pending.pop(request_id)
            print(f"[asdaaas] Watchdog: ack {cmd['adapter']}:{cmd['command']} (req={request_id})")
            return True
        return False
    
    def check_expired(self):
        """Check for timed-out commands. Returns list of expired command dicts."""
        now = time.monotonic()
        expired = []
        expired_ids = []
        for req_id, cmd in self.pending.items():
            if now >= cmd["deadline"]:
                expired.append({
                    "request_id": req_id,
                    "adapter": cmd["adapter"],
                    "command": cmd["command"],
                    "started": cmd["started"],
                })
                expired_ids.append(req_id)
        for req_id in expired_ids:
            del self.pending[req_id]
        return expired
    
    def deliver_timeout_doorbells(self, agent_name):
        """Check for expired commands and write error doorbells for them."""
        expired = self.check_expired()
        for cmd in expired:
            bell_dir = agent_dir(agent_name) / "doorbells"
            bell_dir.mkdir(parents=True, exist_ok=True)
            bell = {
                "adapter": cmd["adapter"],
                "command": cmd["command"],
                "priority": 1,  # high priority — error
                "text": f"TIMEOUT: Command \'{cmd['command']}\' to {cmd['adapter']} "
                        f"did not respond (sent {cmd['started']}). "
                        f"Adapter may be dead or unresponsive.",
                "error": True,
                "request_id": cmd["request_id"],
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            fd, tmp_path = tempfile.mkstemp(dir=str(bell_dir), suffix=".tmp", prefix="timeout_")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(bell, f)
                final = tmp_path.replace(".tmp", ".json")
                os.rename(tmp_path, final)
                print(f"[asdaaas] Watchdog: TIMEOUT {cmd['adapter']}:{cmd['command']} (req={cmd['request_id']})")
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        return expired


# ============================================================================
# ADAPTER REGISTRATION READER (Phase 7.2)
# ============================================================================

def read_adapter_registrations():
    """Read all adapter registration files from ~/asdaaas/adapters/<name>.json.
    
    Returns dict of {adapter_name: registration_dict}.
    Only returns adapters whose registration file is a direct JSON file
    in the adapters directory (not subdirectories which are inbox/outbox).
    """
    registrations = {}
    if not ADAPTERS_DIR.exists():
        return registrations
    
    for entry in sorted(ADAPTERS_DIR.iterdir()):
        if not entry.is_file() or not entry.name.endswith(".json"):
            continue
        try:
            with open(entry) as f:
                reg = json.load(f)
            name = reg.get("name", entry.stem)
            
            # Check liveness via PID
            pid = reg.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    reg["alive"] = True
                except (OSError, ProcessLookupError):
                    reg["alive"] = False
            else:
                reg["alive"] = False
            
            registrations[name] = reg
        except (json.JSONDecodeError, OSError):
            continue
    
    return registrations


# ============================================================================
# JSON-RPC PROTOCOL
# ============================================================================

DEBUG = os.environ.get("ASDAAAS_DEBUG", "0") == "1"

_rpc_id = 0

def rpc_request(method, params=None):
    global _rpc_id
    _rpc_id += 1
    msg = {"jsonrpc": "2.0", "method": method, "id": _rpc_id}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg) + "\n"

def rpc_notification(method, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg) + "\n"

async def read_frame(stdout):
    chunks = []
    while True:
        try:
            chunk = await stdout.readuntil(b'\n')
            chunks.append(chunk)
            break
        except asyncio.LimitOverrunError as e:
            chunk = await stdout.read(e.consumed)
            chunks.append(chunk)
        except asyncio.IncompleteReadError as e:
            if e.partial:
                chunks.append(e.partial)
            if not chunks:
                return None
            break
    data = b"".join(chunks)
    if not data:
        return None
    return json.loads(data.decode("utf-8").strip())

async def send(stdin, msg):
    stdin.write(msg.encode("utf-8"))
    await stdin.drain()

async def wait_for_response(stdout, expected_id, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = await asyncio.wait_for(
                read_frame(stdout),
                timeout=max(0.1, deadline - time.monotonic())
            )
        except asyncio.TimeoutError:
            break
        if frame is None:
            raise RuntimeError("stdio process closed stdout")
        if frame.get("id") == expected_id:
            return frame
    raise TimeoutError(f"No response for id={expected_id} within {timeout}s")


async def collect_response(stdout, prompt_id, timer=None, timeout=120.0, on_meta=None):
    """Collect agent response. Returns (speech_text, thought_text, result_meta).
    
    speech_text: concatenated agent_message_chunk text
    thought_text: concatenated agent_thought_chunk text
    result_meta: dict with totalTokens, modelId, stopReason from _meta
    on_meta: optional callback(total_tokens) called when streaming _meta arrives,
             enabling real-time health file updates during long responses
    """
    speech_chunks = []
    thought_chunks = []
    result_meta = {}
    first_chunk_marked = False
    saw_prompt_complete = False
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            frame = await asyncio.wait_for(
                read_frame(stdout),
                timeout=max(0.1, deadline - time.monotonic())
            )
        except asyncio.TimeoutError:
            break
        if frame is None:
            if saw_prompt_complete:
                # After prompt_complete, EOF means the response frame
                # didn't arrive. Not fatal — we have the speech already.
                break
            raise RuntimeError("stdio process closed stdout")

        method = frame.get("method", "")
        params = frame.get("params", {})
        update = params.get("update", {})

        if DEBUG:
            c = update.get('content', {})
            t = c.get('text', '') if isinstance(c, dict) else str(type(c).__name__)
            print(f"[debug] {method} {update.get('sessionUpdate','')} {str(t)[:60]}")

        # Agent speech chunk
        if method == "session/update" and update.get("sessionUpdate") == "agent_message_chunk":
            c = update.get("content", {})
            text = c.get("text", "") if isinstance(c, dict) else ""
            if text:
                if not first_chunk_marked and timer:
                    timer.mark("first_chunk")
                    first_chunk_marked = True
                speech_chunks.append(text)

        # Agent thought chunk
        elif method == "session/update" and update.get("sessionUpdate") == "agent_thought_chunk":
            c = update.get("content", {})
            text = c.get("text", "") if isinstance(c, dict) else ""
            if text:
                if not first_chunk_marked and timer:
                    timer.mark("first_chunk")
                    first_chunk_marked = True
                thought_chunks.append(text)

        # Extract metadata (totalTokens, modelId, etc.)
        # _meta is present on EVERY session/update frame (in params._meta)
        # AND on the final JSON-RPC response (in result._meta).
        # Extract from both — the streaming _meta gives us running token
        # counts even if we never see the final response frame (e.g., timeout).
        streaming_meta = params.get("_meta", {})
        if streaming_meta.get("totalTokens"):
            result_meta["totalTokens"] = streaming_meta["totalTokens"]
            if on_meta:
                on_meta(streaming_meta["totalTokens"])

        if "result" in frame:
            meta = frame.get("result", {}).get("_meta", {})
            if meta:
                result_meta = {
                    "totalTokens": meta.get("totalTokens", 0),
                    "modelId": meta.get("modelId", ""),
                    "stopReason": meta.get("stopReason", ""),
                }

        # Done — the JSON-RPC response (with id + result._meta) arrives
        # AFTER _x.ai/session/prompt_complete. If we break on prompt_complete,
        # we miss the _meta containing totalTokens. So when we see prompt_complete,
        # tighten the deadline to catch the response frame that follows.
        if frame.get("id") == prompt_id:
            break
        if method == "_x.ai/session/prompt_complete":
            # Response frame with _meta follows shortly — give it 2s
            saw_prompt_complete = True
            deadline = min(deadline, time.monotonic() + 2.0)

    return "".join(speech_chunks), "".join(thought_chunks), result_meta


async def drain_stale_frames(stdout, agent_name=None):
    """Drain any buffered frames from stdout without blocking.
    
    After auto-compaction or long tool-call responses that exceed the
    collect_response timeout, stale frames may be sitting in the pipe.
    If not drained, they contaminate the next collect_response call,
    causing a one-behind desync where the response to prompt N is actually
    the response to prompt N-1.
    
    Collects any speech chunks found and delivers them via the outbox
    rather than silently discarding them. Non-speech frames (tool_call,
    prompt_complete, notifications) are logged and discarded.
    
    Call this before sending each new prompt to ensure a clean pipe.
    
    Returns (drained_count, speech_text) — speech_text is the recovered
    speech if any, or empty string.
    """
    drained = 0
    speech_chunks = []
    frame_types = []
    
    while True:
        try:
            frame = await asyncio.wait_for(read_frame(stdout), timeout=0.05)
            if frame is None:
                break
            method = frame.get("method", "")
            params = frame.get("params", {})
            update = params.get("update", {})
            utype = update.get("sessionUpdate", "")
            
            # Log every drained frame type for diagnostics
            if "result" in frame:
                frame_types.append("jsonrpc_response")
                # Extract _meta if present (safety net for token tracking)
                meta = frame.get("result", {}).get("_meta", {})
                if meta.get("totalTokens"):
                    print(f"[asdaaas] DRAIN: WARNING — drained response frame had totalTokens={meta['totalTokens']}. "
                          f"This means collect_response missed it.")
            else:
                frame_types.append(utype or method or "unknown")
            
            if utype == "agent_message_chunk":
                c = update.get("content", {})
                t = c.get("text", "") if isinstance(c, dict) else ""
                if t:
                    speech_chunks.append(t)
            elif utype == "agent_thought_chunk":
                pass  # discard stale thoughts
            elif method == "_x.ai/session/prompt_complete":
                pass  # expected terminator for the stale response
            # All other frame types (tool_call, notifications, etc.) are discarded
            
            drained += 1
        except asyncio.TimeoutError:
            break
    
    speech = "".join(speech_chunks).strip()
    
    if drained:
        # Log frame types for compaction/pipe diagnostics
        from collections import Counter
        type_counts = dict(Counter(frame_types))
        print(f"[asdaaas] DRAIN: {drained} stale frame(s), types: {type_counts}")
        if speech:
            print(f"[asdaaas] DRAIN: recovered {len(speech)} chars of speech")
            # Deliver recovered speech if it looks complete enough.
            # A response starting with /msg or / is likely a real response.
            # A fragment starting mid-word is likely the tail of a truncated response.
            first_word = speech.split()[0] if speech.split() else ""
            if first_word.startswith("/") or first_word[0:1].isupper() or len(speech) > 20:
                print(f"[asdaaas] DRAIN: delivering recovered speech: {speech[:80]}")
                if agent_name:
                    gaze = read_gaze(agent_name)
                    write_to_outbox(agent_name, speech, gaze.get("speech"), "speech")
            else:
                print(f"[asdaaas] DRAIN: discarding fragment: {speech[:80]}")
    
    return drained, speech


# ============================================================================
# MAIN LOOP
# ============================================================================

async def main(agent_name, session_id=None, agent_cwd="/home/eric"):
    # Create per-agent directory structure (agent-centric: ~/agents/<name>/asdaaas/...)
    a_dir = agent_dir(agent_name)
    a_dir.mkdir(parents=True, exist_ok=True)
    (a_dir / "doorbells").mkdir(parents=True, exist_ok=True)
    (a_dir / "attention").mkdir(parents=True, exist_ok=True)
    (a_dir / "profile").mkdir(parents=True, exist_ok=True)
    (a_dir / "adapters").mkdir(parents=True, exist_ok=True)
    ADAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    
    # Register in running_agents.json so adapters can find us
    _register_running_agent(agent_name, agent_cwd)

    print(f"[asdaaas] ASDAAAS v2 starting for {agent_name}")
    print(f"[asdaaas] Spawning grok agent stdio...")
    proc = await asyncio.create_subprocess_exec(
        "grok", "agent", "stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_cwd,
    )
    print(f"[asdaaas] PID {proc.pid}")

    stdin, stdout = proc.stdin, proc.stdout
    total_tokens = 0
    context_window = CONTEXT_WINDOW

    # Throttled callback for real-time health updates during long responses.
    # Writes health file at most every 5 seconds so the dashboard can show
    # live token counts while the agent is mid-turn doing tool calls.
    _last_health_write = 0

    def _on_streaming_meta(tokens):
        nonlocal total_tokens, _last_health_write
        total_tokens = tokens
        now = time.monotonic()
        if now - _last_health_write >= 5.0:
            write_health(agent_name, "working", f"streaming ({tokens} tokens)", tokens, context_window)
            _last_health_write = now

    # ---- Initialize ----
    print("[asdaaas] initialize...")
    await send(stdin, rpc_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "asdaaas", "version": "0.2"},
    }))
    resp = await wait_for_response(stdout, _rpc_id, timeout=30)
    print(f"[asdaaas] init OK")

    await send(stdin, rpc_notification("notifications/initialized"))

    # ---- Load or create session ----
    if session_id:
        print(f"[asdaaas] Loading session {session_id}...")
        await send(stdin, rpc_request("session/load", {
            "sessionId": session_id,
            "cwd": agent_cwd,
            "mcpServers": [],
        }))
    else:
        print("[asdaaas] New session...")
        await send(stdin, rpc_request("session/new", {
            "cwd": agent_cwd,
            "mcpServers": [],
        }))

    resp = await wait_for_response(stdout, _rpc_id, timeout=120)
    sid = resp.get("result", {}).get("sessionId", session_id or "unknown")
    print(f"[asdaaas] Session: {sid}")

    # ---- Yolo mode ----
    print("[asdaaas] /yolo on...")
    await send(stdin, rpc_request("session/prompt", {
        "sessionId": sid,
        "prompt": [{"type": "text", "text": "/yolo on"}],
    }))
    _, _, meta = await collect_response(stdout, _rpc_id, timeout=10)
    if meta.get("totalTokens"):
        total_tokens = meta["totalTokens"]
    print("[asdaaas] Ready.")

    write_health(agent_name, "ready", f"session={sid}", total_tokens, context_window)
    # Read awareness file — determines which adapter inboxes to watch
    awareness = read_awareness(agent_name)
    print(f"[asdaaas] Awareness: direct={awareness.get('direct_attach', [])}, notify={awareness.get('notify_watch', [])}")
    print(f"[asdaaas] Polling for '{agent_name}'...")
    
    # Phase 4.4: Initialize command watchdog
    watchdog = CommandWatchdog(agent_name)
    
    # Pending message queue for background channels in "pending" mode
    pending_queue = PendingQueue()
    
    # Phase 7.2: Read adapter registrations
    adapters = read_adapter_registrations()
    print(f"[asdaaas] Adapters: {list(adapters.keys()) if adapters else '(none registered)'}")
    
    errors = 0

    # ---- Compaction state ----
    turns_since_compaction = COMPACTION_COOLDOWN_TURNS  # start as "available" (not just-compacted)
    compact_pending = None  # None or {"confirm_path": "/tmp/xxx.tmp", "request_id": "..."}
    _prev_tokens = total_tokens  # for detecting auto-compaction (token drop)
    next_turn_delay = 0  # seconds to wait before next default doorbell (0=immediate)
    delay_until_event = False  # if True, skip default doorbell entirely (wait for external)
    did_work_this_iteration = False  # track if any work was done this loop iteration

    # ---- Main loop ----
    while True:
        try:
            # ---- 0. Detect auto-compaction (token count dropped significantly) ----
            if total_tokens < _prev_tokens * 0.6 and _prev_tokens > 0:
                print(f"[asdaaas] Auto-compaction detected: {_prev_tokens} -> {total_tokens}")
                turns_since_compaction = 0
                compact_pending = None  # cancel any pending manual compact
            _prev_tokens = total_tokens

            # ---- 1. Check for compact confirmation (pending from previous turn) ----
            if compact_pending:
                confirm_path = compact_pending["confirm_path"]
                if os.path.exists(confirm_path):
                    # Agent confirmed -- execute compaction
                    os.unlink(confirm_path)
                    request_id = compact_pending.get("request_id", "")
                    compact_pending = None
                    print(f"[asdaaas] Compact confirmed by {agent_name}")
                    tokens_before = total_tokens
                    await send(stdin, rpc_request("session/prompt", {
                        "sessionId": sid,
                        "prompt": [{"type": "text", "text": "/compact"}],
                    }))
                    _, _, meta = await collect_response(stdout, _rpc_id, timeout=300)
                    if meta.get("totalTokens"):
                        total_tokens = meta["totalTokens"]

                    # The /compact response frame often carries stale totalTokens
                    # (pre-compaction count). Send a probe prompt to force the
                    # grok binary to recalculate and return the real post-compaction
                    # token count. This also serves as the post-compaction
                    # notification to the agent.
                    # Don't include context_left_tag on probe -- total_tokens
                    # is still stale here. The probe's job is to get the real
                    # count. The next real prompt will have the correct tag.
                    probe_text = "[Compaction complete. You are resuming from a compacted context.]"
                    await drain_stale_frames(stdout, agent_name)
                    await send(stdin, rpc_request("session/prompt", {
                        "sessionId": sid,
                        "prompt": [{"type": "text", "text": probe_text}],
                    }))
                    probe_speech, _, probe_meta = await collect_response(stdout, _rpc_id, timeout=120, on_meta=_on_streaming_meta)
                    if probe_meta.get("totalTokens"):
                        total_tokens = probe_meta["totalTokens"]
                        print(f"[asdaaas] Compact probe: real totalTokens={total_tokens}")
                    # Route probe response if agent said anything
                    if probe_speech.strip():
                        gaze = read_gaze(agent_name)
                        write_to_outbox(agent_name, probe_speech.strip(), gaze.get("speech"), "speech")

                    _prev_tokens = total_tokens  # prevent false auto-compaction detection
                    turns_since_compaction = 0
                    # Write result for session adapter
                    result_file = agent_dir(agent_name) / "command_result.json"
                    tmp = str(result_file) + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump({
                            "request_id": request_id,
                            "action": "compact",
                            "before": tokens_before,
                            "after": total_tokens,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }, f)
                    os.rename(tmp, str(result_file))
                    print(f"[asdaaas] Compact: {tokens_before} -> {total_tokens}")
                    write_health(agent_name, "ready", f"compacted {tokens_before}->{total_tokens}", total_tokens, context_window)
                else:
                    # Confirmation file not created -- request expires
                    print(f"[asdaaas] Compact request expired (no confirmation file)")
                    compact_pending = None

            # ---- 1a. Check for adapter commands (e.g., /compact) ----
            cmd = poll_commands(agent_name)
            if cmd:
                action = cmd.get("action", "")
                request_id = cmd.get("request_id", "")
                print(f"[asdaaas] Command: {action} (req={request_id})")

                if action == "delay":
                    delay_val = cmd.get("seconds", 0)
                    if delay_val == "until_event":
                        delay_until_event = True
                        next_turn_delay = 0
                        print(f"[asdaaas] Delay: until_event (standing by)")
                    else:
                        next_turn_delay = float(delay_val)
                        delay_until_event = False
                        print(f"[asdaaas] Delay: {next_turn_delay}s before next default doorbell")

                elif action == "ack":
                    handled = cmd.get("handled", [])
                    if handled:
                        removed = ack_doorbells(agent_name, handled)
                        print(f"[asdaaas] Ack: {removed} doorbell(s) cleared")

                elif action == "compact":
                    if turns_since_compaction < COMPACTION_COOLDOWN_TURNS:
                        # Cooldown active -- reject
                        print(f"[asdaaas] Compact rejected: cooldown ({turns_since_compaction} turns since last compaction)")
                        bell_dir = agent_dir(agent_name) / "doorbells"
                        bell_dir.mkdir(parents=True, exist_ok=True)
                        bell = {
                            "adapter": "session",
                            "command": "compact",
                            "priority": 3,
                            "text": f"Compaction rejected: cooldown active ({turns_since_compaction} turn(s) since last compaction). Wait {COMPACTION_COOLDOWN_TURNS - turns_since_compaction} more turn(s).",
                            "request_id": request_id,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                        fd, tmp_path = tempfile.mkstemp(dir=str(bell_dir), suffix=".tmp", prefix="cpt_")
                        with os.fdopen(fd, "w") as f:
                            json.dump(bell, f)
                        os.rename(tmp_path, tmp_path.replace(".tmp", ".json"))
                    elif compact_pending:
                        # Already have a pending request
                        print(f"[asdaaas] Compact rejected: already pending confirmation")
                    else:
                        # Issue confirmation challenge
                        token = secrets.token_hex(8)
                        confirm_path = f"/tmp/compact_confirm_{agent_name}_{token}.tmp"
                        compact_pending = {"confirm_path": confirm_path, "request_id": request_id}
                        print(f"[asdaaas] Compact requested by {agent_name}, confirmation required: {confirm_path}")
                        bell_dir = agent_dir(agent_name) / "doorbells"
                        bell_dir.mkdir(parents=True, exist_ok=True)
                        bell = {
                            "adapter": "session",
                            "command": "compact_confirm",
                            "priority": 2,
                            "text": f"Compaction requested. To confirm, create this file: touch {confirm_path}",
                            "confirm_path": confirm_path,
                            "request_id": request_id,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                        fd, tmp_path = tempfile.mkstemp(dir=str(bell_dir), suffix=".tmp", prefix="cpt_")
                        with os.fdopen(fd, "w") as f:
                            json.dump(bell, f)
                        os.rename(tmp_path, tmp_path.replace(".tmp", ".json"))

            # ---- 1b. Check watchdog timeouts (Phase 4.4) ----
            watchdog.deliver_timeout_doorbells(agent_name)

            # ---- 1c. Re-read adapter registrations periodically (Phase 7.2) ----
            adapters = read_adapter_registrations()

            # ---- 2. Deliver doorbells to agent ----
            # Re-read awareness early so TTL is available for doorbell expiry
            awareness = read_awareness(agent_name)
            did_work_this_iteration = False
            bells = poll_doorbells(agent_name, awareness)
            if bells:
                # Phase 4.4: Check if any doorbells acknowledge watched commands
                for bell in bells:
                    bell_req_id = bell.get("request_id", "")
                    if bell_req_id:
                        watchdog.acknowledge(bell_req_id)
                
                did_work_this_iteration = True
                # Batch all doorbells into a single prompt.
                # Agent sees the full picture and can ack/ignore/act on each.
                bell_lines = [format_doorbell(bell) for bell in bells]
                batch_text = "\n".join(bell_lines) + context_left_tag(total_tokens, context_window, turns_since_compaction)
                print(f"[asdaaas] Doorbells ({len(bells)}): {[b.get('id', '?') for b in bells]}")
                await drain_stale_frames(stdout, agent_name)
                await send(stdin, rpc_request("session/prompt", {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": batch_text}],
                }))
                speech, thoughts, meta = await collect_response(stdout, _rpc_id, timeout=120, on_meta=_on_streaming_meta)
                if meta.get("totalTokens"):
                    total_tokens = meta["totalTokens"]
                turns_since_compaction += 1

                # Route doorbell response through gaze (agent might respond to it)
                if speech.strip():
                    gaze = read_gaze(agent_name)
                    write_to_outbox(agent_name, speech.strip(), gaze.get("speech"), "speech")
                    if thoughts.strip() and gaze.get("thoughts"):
                        write_to_outbox(agent_name, thoughts.strip(), gaze.get("thoughts"), "thoughts")
                    write_health(agent_name, "active", f"doorbell response ({len(bells)} bells)", total_tokens, context_window)

            # ---- 3. Poll per-adapter inboxes + legacy inbox ----
            # Re-read awareness and gaze periodically (agent can change them)
            awareness = read_awareness(agent_name)
            gaze = read_gaze(agent_name)
            
            # ---- Attention structure: check timeouts ----
            attentions = poll_attentions(agent_name)
            timeout_bells = check_attention_timeouts(agent_name, attentions)
            if timeout_bells:
                # Deliver timeout notifications directly to stdin
                for tb in timeout_bells:
                    proc.stdin.write((tb["text"] + "\n").encode())
                    await proc.stdin.drain()
                    print(f"[asdaaas] ATTENTION TIMEOUT delivered to {agent_name}: {tb['msg_id']}")
                # Re-read attentions after removing expired ones
                attentions = poll_attentions(agent_name)
            
            # Check pending queue -- deliver messages that now match gaze
            pending_msgs = pending_queue.drain_for_gaze(gaze)
            if pending_msgs:
                print(f"[asdaaas] PENDING: delivering {len(pending_msgs)} queued message(s) (gaze matched)")
            
            messages = poll_adapter_inboxes(agent_name, awareness)
            # Also poll legacy universal inbox for backward compatibility
            legacy_msgs = poll_inbox(agent_name)
            messages.extend(legacy_msgs)
            
            # Prepend pending messages so they're delivered first (oldest first)
            messages = pending_msgs + messages

            if not messages and not bells and not cmd:
                # No external work this iteration. Check default doorbell.
                awareness = read_awareness(agent_name)
                default_doorbell_enabled = awareness.get("default_doorbell", False)

                if default_doorbell_enabled and not delay_until_event:
                    if next_turn_delay > 0:
                        # Agent requested a delay. Sleep for the delay duration,
                        # but check for external events every 0.25s.
                        delay_remaining = next_turn_delay
                        print(f"[asdaaas] Default doorbell: delaying {next_turn_delay}s")
                        while delay_remaining > 0:
                            await asyncio.sleep(min(0.25, delay_remaining))
                            delay_remaining -= 0.25
                            # Check for external events during delay
                            # Use has_pending_doorbells (not poll) to avoid incrementing delivered_count
                            ext_bells = has_pending_doorbells(agent_name)
                            ext_msgs = poll_adapter_inboxes(agent_name, awareness)
                            ext_cmd = poll_commands(agent_name)
                            if ext_bells or ext_msgs or ext_cmd:
                                # External event arrived -- break delay, deliver it
                                print(f"[asdaaas] Delay interrupted by external event")
                                # Doorbells persist on disk (no re-queue needed).
                                # Messages need re-queue since poll_adapter_inboxes deletes.
                                # They'll be picked up on the next iteration.
                                break
                        next_turn_delay = 0  # reset for next iteration

                    # Queue the default doorbell (only if no continue doorbell already pending)
                    bell_dir = agent_dir(agent_name) / "doorbells"
                    bell_dir.mkdir(parents=True, exist_ok=True)
                    # Check for existing continue doorbells to avoid accumulation
                    has_continue = any(f.name.startswith("cont_") for f in bell_dir.glob("*.json"))
                    if not has_continue:
                        bell = {
                            "adapter": "continue",
                            "priority": 10,  # lowest priority -- other doorbells go first
                            "text": "Your turn ended. You may continue, delay, or stand by.",
                            "source": "continue",
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                        fd, tmp_path = tempfile.mkstemp(dir=str(bell_dir), suffix=".tmp", prefix="cont_")
                        with os.fdopen(fd, "w") as f:
                            json.dump(bell, f)
                        os.rename(tmp_path, tmp_path.replace(".tmp", ".json"))
                        print(f"[asdaaas] Default doorbell queued for {agent_name}")
                    # Don't sleep -- next iteration will pick up the doorbell immediately
                    continue

                # No default doorbell (legacy mode or until_event). Standard idle poll.
                await asyncio.sleep(0.25)
                continue

            for msg in messages:
                text = msg.get("text", "").strip()
                sender = msg.get("from", "unknown")
                adapter = msg.get("adapter", "unknown")
                msg_id = msg.get("id", f"t{int(time.time()*1000)}")

                if not text:
                    continue

                # ---- Attention matching: does this resolve a pending attention? ----
                # Checked BEFORE gaze filtering -- attentions are higher priority.
                # If Jr expects a response from Trip, Trip's PM resolves the
                # attention even if Jr is gazing at #standup.
                if attentions:
                    matched_attn = match_attention(agent_name, attentions, sender)
                    if matched_attn:
                        # Format the response notification with the message text
                        response_bell = resolve_attention(matched_attn, text)
                        # Deliver directly to agent stdin -- attention overrides
                        # gaze filtering. The agent declared this expectation;
                        # the response must reach them regardless of current gaze.
                        response_text = response_bell["text"]
                        proc.stdin.write((response_text + "\n").encode())
                        await proc.stdin.drain()
                        print(f"[asdaaas] ATTENTION RESPONSE delivered to {agent_name}: {matched_attn['msg_id']}")
                        # Re-read attentions (one was resolved)
                        attentions = poll_attentions(agent_name)
                        # Skip normal gaze filtering for this message -- it's
                        # already been delivered as an attention response
                        continue

                # ---- Gaze filtering: is this message in the room? ----
                # Re-read gaze each message (agent may change it mid-batch)
                gaze = read_gaze(agent_name)
                
                if not matches_gaze(msg, gaze):
                    # Background message -- handle per background_channels config
                    mode = get_background_mode(msg, awareness)
                    
                    if mode == "drop":
                        print(f"[asdaaas] DROP: {sender} (via {adapter}) -- not in gaze room")
                        continue
                    
                    elif mode == "pending":
                        pending_queue.add(msg)
                        print(f"[asdaaas] PENDING: queued {sender} (via {adapter}) -- {pending_queue.total} total pending")
                        continue
                    
                    else:  # doorbell
                        bell_text = format_background_doorbell(msg) + context_left_tag(total_tokens, context_window, turns_since_compaction)
                        print(f"[asdaaas] BACKGROUND: {bell_text[:120]}")
                        await drain_stale_frames(stdout, agent_name)
                        await send(stdin, rpc_request("session/prompt", {
                            "sessionId": sid,
                            "prompt": [{"type": "text", "text": bell_text}],
                        }))
                        speech, thoughts, meta = await collect_response(stdout, _rpc_id, timeout=120, on_meta=_on_streaming_meta)
                        if meta.get("totalTokens"):
                            total_tokens = meta["totalTokens"]
                        turns_since_compaction += 1
                        # Route background doorbell response through gaze
                        if speech.strip():
                            write_to_outbox(agent_name, speech.strip(), gaze.get("speech"), "speech")
                            if thoughts.strip() and gaze.get("thoughts"):
                                write_to_outbox(agent_name, thoughts.strip(), gaze.get("thoughts"), "thoughts")
                            write_health(agent_name, "active", f"background doorbell response", total_tokens, context_window)
                        continue

                # ---- Message is in the room -- pipe it through ----
                timer = MessageTimer(agent_name, msg_id)

                prompt_text = f"<{sender} (via {adapter})> {text}" + context_left_tag(total_tokens, context_window, turns_since_compaction)
                print(f"[asdaaas] IN: {prompt_text[:120]}")

                # Drain any stale frames before sending new prompt.
                # Prevents one-behind desync from auto-compaction or
                # other unsolicited agent output.
                await drain_stale_frames(stdout, agent_name)

                timer.mark("prompt_sent")
                await send(stdin, rpc_request("session/prompt", {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": prompt_text}],
                }))

                speech, thoughts, meta = await collect_response(stdout, _rpc_id, timer=timer, timeout=120, on_meta=_on_streaming_meta)
                timer.mark("prompt_complete")

                if meta.get("totalTokens"):
                    total_tokens = meta["totalTokens"]
                turns_since_compaction += 1

                # Re-read gaze — agent may have changed it via tool call during response
                gaze = read_gaze(agent_name)

                # Route speech and thoughts through gaze (the room the agent is in)
                if speech.strip():
                    write_to_outbox(agent_name, speech.strip(), gaze.get("speech"), "speech")
                    timer.mark("outbox_done")

                    if thoughts.strip() and gaze.get("thoughts"):
                        write_to_outbox(agent_name, thoughts.strip(), gaze.get("thoughts"), "thoughts")

                    print(timer.log_line())
                    write_profile(agent_name, timer)
                    write_health(agent_name, "active", f"responded {len(speech)} chars", total_tokens, context_window)
                else:
                    print(f"[asdaaas] {agent_name} -> (empty)")
                    print(timer.log_line())
                    write_profile(agent_name, timer)
                    write_health(agent_name, "active", "empty response", total_tokens, context_window)

            errors = 0

        except Exception as e:
            errors += 1
            print(f"[asdaaas] Error #{errors}: {e}")
            import traceback
            traceback.print_exc()
            write_health(agent_name, "error", str(e)[:100], total_tokens, context_window)
            if errors > 10:
                print("[asdaaas] Too many errors, exiting")
                break
            await asyncio.sleep(2.0)

    proc.terminate()
    await proc.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ASDAAAS v2")
    parser.add_argument("--agent", default="Test", help="Agent name")
    parser.add_argument("--cwd", default="/home/eric", help="Working directory for agent")
    parser.add_argument("--session", default=None, help="Session ID to load")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.agent, args.session, args.cwd))
    except KeyboardInterrupt:
        print("\n[asdaaas] Shut down.")
