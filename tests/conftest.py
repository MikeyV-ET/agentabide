"""Shared fixtures for MikeyV infrastructure tests."""

import json
import os
import sys
import pytest
from pathlib import Path

# Add comms directory to path
COMMS_DIR = Path(__file__).parent.parent / "live" / "comms"
sys.path.insert(0, str(COMMS_DIR))


@pytest.fixture
def hub_dir(tmp_path, monkeypatch):
    """Create a temporary asdaaas directory structure and monkeypatch all modules to use it.
    
    Agent-centric structure:
      tmp_path/agents/<AgentName>/asdaaas/{gaze,awareness,health,attention/,doorbells/,profile/,commands}.json
      tmp_path/agents/<AgentName>/asdaaas/adapters/<adapter>/{inbox,outbox}/
    
    Engine config:
      tmp_path/asdaaas/adapters/<adapter>.json  (adapter registrations)
      tmp_path/asdaaas/running_agents.json      (agent name -> home path map)
    
    Named 'hub_dir' for backward compat with existing tests.
    Returns the engine dir (tmp_path/asdaaas) — use agents_home_dir fixture for agent dirs.
    """
    hub = tmp_path / "asdaaas"
    agents_home = tmp_path / "agents"
    
    agents = ["Sr", "Jr", "Trip", "Q", "Cinco"]
    adapters = ["irc", "localmail", "session", "context", "heartbeat", "remind"]
    
    # Per-agent dirs (agent-centric: ~/agents/<name>/asdaaas/...)
    for agent in agents:
        agent_asdaaas = agents_home / agent / "asdaaas"
        (agent_asdaaas / "doorbells").mkdir(parents=True, exist_ok=True)
        (agent_asdaaas / "attention").mkdir(parents=True, exist_ok=True)
        (agent_asdaaas / "profile").mkdir(parents=True, exist_ok=True)
        # Per-agent adapter queues
        for adapter in adapters:
            (agent_asdaaas / "adapters" / adapter / "inbox").mkdir(parents=True, exist_ok=True)
            (agent_asdaaas / "adapters" / adapter / "outbox").mkdir(parents=True, exist_ok=True)
    
    # Engine config dirs
    (hub / "adapters").mkdir(parents=True, exist_ok=True)
    
    # Legacy dirs (still used by some code paths)
    (hub / "inbox").mkdir(parents=True, exist_ok=True)
    (hub / "payloads").mkdir(parents=True, exist_ok=True)
    for agent in agents:
        (hub / "outbox" / agent).mkdir(parents=True, exist_ok=True)
    (hub / "outbox" / "irc").mkdir(parents=True, exist_ok=True)
    (hub / "outbox" / "localmail").mkdir(parents=True, exist_ok=True)
    
    # Legacy agent dirs (some adapters still reference AGENTS_DIR)
    for agent in agents:
        (hub / "agents" / agent / "doorbells").mkdir(parents=True, exist_ok=True)
    
    # Monkeypatch asdaaas module
    import asdaaas
    monkeypatch.setattr(asdaaas, "ASDAAAS_DIR", hub)
    monkeypatch.setattr(asdaaas, "AGENTS_HOME_DIR", agents_home)
    monkeypatch.setattr(asdaaas, "AGENTS_DIR", hub / "agents")
    monkeypatch.setattr(asdaaas, "ADAPTERS_DIR", hub / "adapters")
    monkeypatch.setattr(asdaaas, "HUB_DIR", hub)
    monkeypatch.setattr(asdaaas, "INBOX_DIR", hub / "inbox")
    monkeypatch.setattr(asdaaas, "OUTBOX_DIR", hub / "outbox")
    monkeypatch.setattr(asdaaas, "RUNNING_AGENTS_FILE", hub / "running_agents.json")
    
    # Monkeypatch adapter_api module
    import adapter_api
    monkeypatch.setattr(adapter_api, "HUB_DIR", hub)
    monkeypatch.setattr(adapter_api, "AGENTS_DIR", hub / "agents")
    monkeypatch.setattr(adapter_api, "AGENTS_HOME_DIR", agents_home)
    monkeypatch.setattr(adapter_api, "INBOX_DIR", hub / "inbox")
    monkeypatch.setattr(adapter_api, "OUTBOX_DIR", hub / "outbox")
    monkeypatch.setattr(adapter_api, "PER_ADAPTER_DIR", hub / "adapters")
    monkeypatch.setattr(adapter_api, "ADAPTERS_DIR", hub / "adapters")
    monkeypatch.setattr(adapter_api, "PAYLOADS_DIR", hub / "payloads")
    monkeypatch.setattr(adapter_api, "STATUS_QUERY_DIR", hub / "status")
    monkeypatch.setattr(adapter_api, "SESSION_INBOX", hub / "adapters" / "session" / "inbox")
    
    # Monkeypatch localmail
    import localmail
    monkeypatch.setattr(localmail, "HUB_DIR", hub)
    monkeypatch.setattr(localmail, "AGENTS_DIR", hub / "agents")
    monkeypatch.setattr(localmail, "AGENTS_HOME_DIR", agents_home)
    monkeypatch.setattr(localmail, "LOCALMAIL_DIR", hub / "adapters" / "localmail")
    monkeypatch.setattr(localmail, "INBOX_DIR", hub / "adapters" / "localmail" / "inbox")
    
    # Context adapter
    try:
        import context_adapter
        monkeypatch.setattr(context_adapter, "HUB_DIR", hub)
        monkeypatch.setattr(context_adapter, "AGENTS_DIR", hub / "agents")
        monkeypatch.setattr(context_adapter, "AGENTS_HOME_DIR", agents_home)
    except ImportError:
        pass
    
    # Session adapter
    try:
        import session_adapter
        monkeypatch.setattr(session_adapter, "HUB_DIR", hub)
        monkeypatch.setattr(session_adapter, "AGENTS_DIR", hub / "agents")
        monkeypatch.setattr(session_adapter, "AGENTS_HOME_DIR", agents_home)
        monkeypatch.setattr(session_adapter, "SESSION_INBOX", hub / "adapters" / "session" / "inbox")
    except ImportError:
        pass
    
    # Heartbeat adapter
    try:
        import heartbeat_adapter
        monkeypatch.setattr(heartbeat_adapter, "HUB_DIR", hub)
        monkeypatch.setattr(heartbeat_adapter, "AGENTS_DIR", hub / "agents")
        monkeypatch.setattr(heartbeat_adapter, "AGENTS_HOME_DIR", agents_home)
    except ImportError:
        pass
    
    # Remind adapter
    try:
        import remind_adapter
        monkeypatch.setattr(remind_adapter, "HUB_DIR", hub)
        monkeypatch.setattr(remind_adapter, "AGENTS_DIR", hub / "agents")
        monkeypatch.setattr(remind_adapter, "AGENTS_HOME_DIR", agents_home)
    except ImportError:
        pass
    
    return hub


@pytest.fixture
def write_gaze(hub_dir, tmp_path):
    """Helper to write a gaze file for an agent."""
    agents_home = tmp_path / "agents"
    def _write(agent, speech_target="irc", speech_params=None, thoughts_target=None, thoughts_params=None):
        gaze = {}
        if speech_target:
            gaze["speech"] = {"target": speech_target, "params": speech_params or {"channel": "#standup"}}
        else:
            gaze["speech"] = None
        if thoughts_target:
            gaze["thoughts"] = {"target": thoughts_target, "params": thoughts_params or {"channel": f"#{agent.lower()}-thoughts"}}
        else:
            gaze["thoughts"] = None
        
        agent_d = agents_home / agent / "asdaaas"
        agent_d.mkdir(parents=True, exist_ok=True)
        gaze_file = agent_d / "gaze.json"
        with open(gaze_file, "w") as f:
            json.dump(gaze, f)
        return gaze
    return _write


@pytest.fixture
def write_awareness(hub_dir, tmp_path):
    """Helper to write an awareness file for an agent."""
    agents_home = tmp_path / "agents"
    def _write(agent, direct_attach=None, control_watch=None, notify_watch=None, accept_from=None):
        awareness = {
            "direct_attach": direct_attach or ["irc"],
            "control_watch": control_watch or {},
            "notify_watch": notify_watch or [],
            "accept_from": accept_from or ["*"],
        }
        agent_d = agents_home / agent / "asdaaas"
        agent_d.mkdir(parents=True, exist_ok=True)
        awareness_file = agent_d / "awareness.json"
        with open(awareness_file, "w") as f:
            json.dump(awareness, f)
        return awareness
    return _write


@pytest.fixture
def write_attention_file(hub_dir, tmp_path):
    """Helper to write an attention declaration file for an agent."""
    agents_home = tmp_path / "agents"
    def _write(agent, expecting_from, msg_id="test-msg-1", timeout_s=30,
               created_at=None, message_text="test message"):
        import time as _time
        now = created_at or _time.time()
        attn = {
            "msg_id": msg_id,
            "expecting_from": expecting_from,
            "timeout_s": timeout_s,
            "created_at": now,
            "expires_at": now + timeout_s,
            "message_text": message_text,
            "status": "pending",
        }
        attn_dir = agents_home / agent / "asdaaas" / "attention"
        attn_dir.mkdir(parents=True, exist_ok=True)
        attn_file = attn_dir / f"{msg_id}.json"
        with open(attn_file, "w") as f:
            json.dump(attn, f)
        return attn
    return _write


@pytest.fixture
def write_health(hub_dir, tmp_path):
    """Helper to write a health file for an agent."""
    agents_home = tmp_path / "agents"
    def _write(agent, status="ready", total_tokens=50000):
        import time
        health = {
            "agent": agent,
            "status": status,
            "detail": f"session=test-{agent.lower()}",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pid": os.getpid(),
            "totalTokens": total_tokens,
            "contextWindow": 200000,
            "last_activity": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        agent_d = agents_home / agent / "asdaaas"
        agent_d.mkdir(parents=True, exist_ok=True)
        health_file = agent_d / "health.json"
        with open(health_file, "w") as f:
            json.dump(health, f)
        return health
    return _write
