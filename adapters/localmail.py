#!/usr/bin/env python3
"""
MikeyV Localmail Adapter — Async agent-to-agent messaging via filesystem.
=========================================================================
Notify adapter type. Agents write messages to each other's inboxes.
Localmail watches for new messages and rings doorbells via ASDAAAS.

For asdaaas agents: doorbell carries full message content (inline).
For TUI agents: message stays in inbox, agent polls with read_localmail().

Directory structure:
  ~/agents/<agent>/asdaaas/adapters/localmail/inbox/   — messages TO this agent

Doorbell format (written to ~/agents/<agent>/asdaaas/doorbells/):
  {
    "adapter": "localmail",
    "priority": 3,
    "text": "Mail from Jr: <message content>",
    "from": "Jr",
    "msg_id": "uuid"
  }

Usage:
  python3 localmail.py                  # watch all agents
  python3 localmail.py --agents Sr,Jr   # watch specific agents

Sending mail (from any agent or script):
  python3 -c "
  import sys; sys.path.insert(0, '/home/eric/projects/mikeyv-infra/live/comms')
  from localmail import send_mail
  send_mail(from_agent='Jr', to_agent='Q', text='Status update please')
  "

Reading mail (for TUI agents):
  python3 -c "
  import sys; sys.path.insert(0, '/home/eric/projects/mikeyv-infra/live/comms')
  from localmail import read_mail
  for msg in read_mail('Jr'): print(f'{msg[\"from\"]}: {msg[\"text\"]}')
  "
"""

import json
import os
import sys
import time
import tempfile
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter_api

# ============================================================================
# PATHS
# ============================================================================

from asdaaas_config import config

HUB_DIR = config.hub_dir
AGENTS_DIR = HUB_DIR / "agents"  # legacy
AGENTS_HOME_DIR = config.agents_home
LOCALMAIL_DIR = HUB_DIR / "adapters" / "localmail"
INBOX_DIR = LOCALMAIL_DIR / "inbox"
DOORBELL_DIR = AGENTS_DIR  # legacy alias for test monkeypatching

ALL_AGENTS = ["Sr", "Jr", "Trip", "Q", "Cinco"]

# ============================================================================
# SEND / READ API (importable by agents)
# ============================================================================

def send_mail(from_agent: str, to_agent: str, text: str, 
              priority: int = 3, meta: dict = None) -> str:
    """
    Send a localmail message to another agent.
    
    Can be called from any context — TUI agent, asdaaas agent, script.
    Returns the message ID.
    """
    inbox = AGENTS_HOME_DIR / to_agent / "asdaaas" / "adapters" / "localmail" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    
    import uuid
    msg_id = str(uuid.uuid4())
    
    msg = {
        "id": msg_id,
        "from": from_agent,
        "to": to_agent,
        "text": text,
        "priority": priority,
        "meta": meta or {},
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    
    ts_prefix = f"mail_{int(time.time()*1000000):016d}_"
    fd, tmp_path = tempfile.mkstemp(dir=str(inbox), suffix=".tmp", prefix=ts_prefix)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(msg, f)
        final = tmp_path.replace(".tmp", ".json")
        os.rename(tmp_path, final)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    
    return msg_id


def read_mail(agent_name: str, delete: bool = True) -> list:
    """
    Read all pending localmail for an agent.
    
    For TUI agents who can't receive doorbells — call this to check mail.
    Returns list of message dicts, oldest first.
    """
    inbox = AGENTS_HOME_DIR / agent_name / "asdaaas" / "adapters" / "localmail" / "inbox"
    if not inbox.exists():
        return []
    
    messages = []
    for entry in sorted(inbox.iterdir()):
        if not entry.name.endswith(".json"):
            continue
        try:
            with open(entry, "r") as f:
                data = json.load(f)
            messages.append(data)
            if delete:
                entry.unlink()
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"[localmail] Error reading {entry}: {e}")
    
    return messages


def peek_mail(agent_name: str) -> list:
    """Check mail without deleting. Returns list of message dicts."""
    return read_mail(agent_name, delete=False)


# ============================================================================
# DOORBELL WRITING
# ============================================================================

def ring_doorbell(agent_name: str, msg: dict):
    """Write a doorbell notification for an asdaaas-managed agent."""
    bell_dir = AGENTS_HOME_DIR / agent_name / "asdaaas" / "doorbells"
    bell_dir.mkdir(parents=True, exist_ok=True)
    
    sender = msg.get("from", "unknown")
    text = msg.get("text", "")
    priority = msg.get("priority", 3)
    msg_id = msg.get("id", "")
    
    # For long messages, write a payload file and reference it in the doorbell.
    # The agent can read the full message from the payload path.
    # (Bug fix: previously truncated to 500 chars and said "full message in inbox",
    # but the inbox file was deleted. Agent got truncated text with no recovery path.
    # Trip hit this 3x in Session 42.)
    if len(text) > 500:
        payload_dir = AGENTS_HOME_DIR / agent_name / "asdaaas" / "adapters" / "localmail" / "payloads"
        payload_dir.mkdir(parents=True, exist_ok=True)
        payload_path = payload_dir / f"{msg_id}.json"
        try:
            fd, tmp = tempfile.mkstemp(dir=str(payload_dir), suffix=".tmp", prefix="pay_")
            with os.fdopen(fd, "w") as f:
                json.dump(msg, f, indent=2)
            os.rename(tmp, str(payload_path))
        except Exception:
            try:
                os.unlink(tmp)
            except (OSError, UnboundLocalError):
                pass
        preview = text[:500] + "..."
        size_kb = len(text) / 1024
        approx_tokens = len(text) // 4
        bell_text = f"[localmail] Mail from {sender}:\n{preview}\n(Full message: cat {payload_path} — {size_kb:.1f}KB, ~{approx_tokens} tokens)"
    else:
        bell_text = f"[localmail] Mail from {sender}:\n{text}"
    
    bell = {
        "adapter": "localmail",
        "priority": priority,
        "text": bell_text,
        "from": sender,
        "msg_id": msg_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    
    fd, tmp_path = tempfile.mkstemp(dir=str(bell_dir), suffix=".tmp", prefix="bell_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(bell, f)
        final = tmp_path.replace(".tmp", ".json")
        os.rename(tmp_path, final)
        print(f"[localmail] Doorbell: {sender} -> {agent_name} ({len(text)} chars, priority {priority})")
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ============================================================================
# ASDAAAS AGENT DETECTION
# ============================================================================

def get_asdaaas_agents():
    """Detect which agents are running on asdaaas (can receive doorbells)."""
    if not AGENTS_HOME_DIR.exists():
        return set()
    
    asdaaas_agents = set()
    now = time.time()
    
    for agent_d in AGENTS_HOME_DIR.iterdir():
        if not agent_d.is_dir():
            continue
        health_file = agent_d / "asdaaas" / "health.json"
        if not health_file.exists():
            continue
        try:
            # Only consider agents with recent health heartbeats
            age = now - health_file.stat().st_mtime
            if age > 3600:  # stale after 1 hour (idle agents are still alive)
                continue
            with open(health_file) as f:
                health = json.load(f)
            agent = health.get("agent", "")
            status = health.get("status", "")
            if agent and status in ("ready", "active", "working"):
                asdaaas_agents.add(agent)
        except (json.JSONDecodeError, OSError):
            pass
    
    return asdaaas_agents


# ============================================================================
# WATCHER LOOP
# ============================================================================

def watch_loop(agents: list, poll_interval: float = 1.0):
    """Main loop: watch inboxes, ring doorbells for asdaaas agents.
    
    For TUI agents, messages stay in inbox — they poll with read_mail().
    For asdaaas agents, we ring a doorbell AND delete the inbox file
    (the doorbell carries the content inline).
    """
    print(f"[localmail] Starting localmail adapter")
    print(f"[localmail] Watching agents: {', '.join(agents)}")
    
    # Ensure directories exist
    for agent in agents:
        (AGENTS_HOME_DIR / agent / "asdaaas" / "adapters" / "localmail" / "inbox").mkdir(parents=True, exist_ok=True)
    
    # Register adapter
    adapter_api.register_adapter(
        name="localmail",
        capabilities=["send", "receive", "notify"],
        config={"type": "notify", "agents": agents},
    )
    
    heartbeat_interval = 30
    last_heartbeat = time.time()
    
    while True:
        try:
            # Detect which agents are on asdaaas
            asdaaas_agents = get_asdaaas_agents()
            
            for agent in agents:
                inbox = AGENTS_HOME_DIR / agent / "asdaaas" / "adapters" / "localmail" / "inbox"
                if not inbox.exists():
                    continue
                
                for entry in sorted(inbox.iterdir()):
                    if not entry.name.endswith(".json"):
                        continue
                    
                    try:
                        with open(entry, "r") as f:
                            msg = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        continue
                    
                    sender = msg.get("from", "unknown")
                    text = msg.get("text", "")
                    
                    if agent in asdaaas_agents:
                        # Agent is on asdaaas — ring doorbell with inline content
                        ring_doorbell(agent, msg)
                        try:
                            entry.unlink()
                        except OSError:
                            pass
                    else:
                        # Agent is on TUI or unknown — leave message in inbox
                        # They'll poll with read_mail()
                        print(f"[localmail] {sender} -> {agent} (inbox, TUI agent)")
            
            # Heartbeat
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                adapter_api.update_heartbeat("localmail")
                last_heartbeat = now
            
        except Exception as e:
            print(f"[localmail] Error: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(poll_interval)


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="MikeyV Localmail Adapter")
    parser.add_argument("--agents", default=None, help="Comma-separated agent list (default: all)")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Poll interval in seconds")
    args = parser.parse_args()
    
    if args.agents:
        agents = [a.strip() for a in args.agents.split(",")]
    else:
        agents = list(ALL_AGENTS)
    
    try:
        watch_loop(agents, args.poll_interval)
    except KeyboardInterrupt:
        print("\n[localmail] Shutting down.")
        adapter_api.deregister_adapter("localmail")


if __name__ == "__main__":
    main()
