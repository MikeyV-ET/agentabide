"""
asdaaas_config.py — Central configuration for ASDAAAS.
======================================================
Single source of truth for all paths and settings. Other modules import
from here instead of hardcoding paths.

Config resolution order:
  1. ASDAAAS_CONFIG env var pointing to a JSON file
  2. config.json in the same directory as this file (install dir)
  3. Built-in defaults (~/asdaaas, ~/agents)

Usage:
  from asdaaas_config import config
  print(config.agents_home)    # Path object
  print(config.asdaaas_dir)    # Path object
  print(config.agent_home("Sr"))  # Path to agent's home dir
"""

import json
import os
from pathlib import Path


class AsdaaasConfig:
    """Immutable configuration loaded once at import time."""

    def __init__(self):
        self._data = self._load()
        self._agents_home = Path(self._data.get("agents_home",
            os.path.expanduser("~/agents")))
        self._asdaaas_dir = Path(self._data.get("asdaaas_dir",
            os.path.expanduser("~/asdaaas")))
        self._agents = self._data.get("agents", {})

    def _load(self):
        # 1. Env var
        env_path = os.environ.get("ASDAAAS_CONFIG")
        if env_path and os.path.isfile(env_path):
            with open(env_path) as f:
                return self._normalize(json.load(f))

        # 2. config.json next to this file
        here = Path(__file__).parent
        local_config = here / "config.json"
        if local_config.is_file():
            with open(local_config) as f:
                return self._normalize(json.load(f))

        # 3. agents.json next to this file (existing config format)
        agents_json = here / "agents.json"
        if agents_json.is_file():
            with open(agents_json) as f:
                return self._normalize(json.load(f))

        # 4. Defaults
        return {}

    def _normalize(self, data):
        """Normalize agents.json format to config format."""
        # agents.json uses settings.agents_dir; config.json uses agents_home
        if "settings" in data and "agents_home" not in data:
            settings = data["settings"]
            data["agents_home"] = settings.get("agents_dir",
                os.path.expanduser("~/agents"))
            data["asdaaas_dir"] = settings.get("asdaaas_system_dir",
                os.path.expanduser("~/asdaaas"))
        return data

    @property
    def agents_home(self) -> Path:
        """Parent directory containing all agent directories."""
        return self._agents_home

    @property
    def asdaaas_dir(self) -> Path:
        """Shared ASDAAAS system directory (running_agents, adapters)."""
        return self._asdaaas_dir

    @property
    def adapters_dir(self) -> Path:
        """Adapter registration directory."""
        return self._asdaaas_dir / "adapters"

    @property
    def running_agents_file(self) -> Path:
        return self._asdaaas_dir / "running_agents.json"

    @property
    def bugs_dir(self) -> Path:
        return self._agents_home / "bugs"

    @property
    def agents(self) -> dict:
        """Per-agent config from config.json (session IDs, models, etc.)."""
        return self._agents

    def agent_home(self, agent_name: str) -> Path:
        """Home directory for a specific agent."""
        # Check if agent has a custom home in config
        agent_cfg = self._agents.get(agent_name, {})
        if "home" in agent_cfg:
            return Path(agent_cfg["home"])
        return self._agents_home / agent_name

    def agent_asdaaas_dir(self, agent_name: str) -> Path:
        """Per-agent asdaaas state directory."""
        return self.agent_home(agent_name) / "asdaaas"

    def agent_doorbells_dir(self, agent_name: str) -> Path:
        return self.agent_asdaaas_dir(agent_name) / "doorbells"

    def agent_adapter_inbox(self, agent_name: str, adapter_name: str) -> Path:
        return self.agent_asdaaas_dir(agent_name) / "adapters" / adapter_name / "inbox"

    def agent_adapter_outbox(self, agent_name: str, adapter_name: str) -> Path:
        return self.agent_asdaaas_dir(agent_name) / "adapters" / adapter_name / "outbox"

    # Legacy compat aliases
    @property
    def hub_dir(self) -> Path:
        return self._asdaaas_dir

    @property
    def inbox_dir(self) -> Path:
        return self._asdaaas_dir / "inbox"

    @property
    def outbox_dir(self) -> Path:
        return self._asdaaas_dir / "outbox"


# Singleton — loaded once at import time
config = AsdaaasConfig()
