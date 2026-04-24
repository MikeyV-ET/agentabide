#!/usr/bin/env python3
"""
ASDAAAS Ops Dashboard -- Agent infrastructure monitoring.
=========================================================
Shows agent health, adapter status, queue depths, and running processes.
Works on any install -- reads paths from asdaaas_config.

Usage:
  python3 ops_dashboard.py           # Launch TUI (auto-refresh 10s)
  python3 ops_dashboard.py --once    # Print snapshot and exit

Dependencies: textual, rich (pip install -r requirements.txt)
"""

import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# Find asdaaas_config
try:
    sys.path.insert(0, str(Path(__file__).parent.parent / "live" / "comms"))
    from asdaaas_config import config
except ModuleNotFoundError:
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
        from asdaaas_config import config
    except ModuleNotFoundError:
        # Fallback defaults
        class _FallbackConfig:
            agents_home = Path(os.path.expanduser("~/agents"))
            asdaaas_dir = Path(os.path.expanduser("~/asdaaas"))
            adapters_dir = asdaaas_dir / "adapters"
            running_agents_file = asdaaas_dir / "running_agents.json"
        config = _FallbackConfig()

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Static
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _time_ago(iso_ts):
    """Convert ISO timestamp to human-readable age."""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            now = datetime.now()  # naive local time
        else:
            now = datetime.now(timezone.utc)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return "future?"
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"
        return f"{secs // 86400}d"
    except (ValueError, TypeError):
        return "?"


def _context_bar(total_tokens, context_window):
    """Create a colored context usage bar."""
    if not context_window or not total_tokens:
        return Text("?", style="dim")
    pct = total_tokens / context_window * 100
    remaining_k = (context_window - total_tokens) / 1000
    if pct < 45:
        color = "green"
    elif pct < 65:
        color = "yellow"
    elif pct < 80:
        color = "dark_orange"
    else:
        color = "red"
    return Text(f"{pct:.0f}% ({remaining_k:.0f}k left)", style=color)


def _count_files(directory):
    """Count JSON files in a directory."""
    try:
        return len([f for f in Path(directory).glob("*.json")])
    except (FileNotFoundError, PermissionError):
        return 0


def _get_process_rss_mb(pid):
    """Get RSS memory in MB for a PID via /proc."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB -> MB
    except (FileNotFoundError, PermissionError, ValueError, IndexError):
        pass
    return None


def _get_updates_jsonl_size(agent_name):
    """Get size of updates.jsonl for an agent's current session."""
    try:
        import urllib.parse
        encoded = urllib.parse.quote(f"/home/eric/agents/{agent_name}", safe="")
        session_dir = Path.home() / ".grok" / "sessions" / encoded
        if not session_dir.is_dir():
            return None
        # Most recent session directory (skip files like prompt_history.jsonl)
        dirs = [d for d in session_dir.iterdir() if d.is_dir()]
        if not dirs:
            return None
        latest = max(dirs, key=lambda p: p.name)
        updates = latest / "updates.jsonl"
        if updates.is_file():
            return updates.stat().st_size
    except (FileNotFoundError, ValueError, StopIteration):
        pass
    return None


def _format_size(size_bytes):
    """Format bytes as human-readable size."""
    if size_bytes is None:
        return "-"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}K"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.0f}M"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f}G"


def _get_running_agents():
    """Get registered agents from running_agents.json."""
    data = _read_json(config.running_agents_file)
    if not data:
        return {}
    if isinstance(data, list):
        return {name: {} for name in data}
    return data


def _discover_agents():
    """Find all agent directories (registered or not)."""
    agents = {}
    # From running_agents.json
    for name, info in _get_running_agents().items():
        home = info.get("home", str(config.agents_home / name)) if isinstance(info, dict) else str(config.agents_home / name)
        agents[name] = {"home": home, "registered": True}
    # From filesystem
    if config.agents_home.is_dir():
        for d in sorted(config.agents_home.iterdir()):
            if d.is_dir() and (d / "asdaaas").is_dir() and d.name not in agents:
                agents[d.name] = {"home": str(d), "registered": False}
    return agents


def _get_adapters():
    """Get registered adapters."""
    adapters = []
    if config.adapters_dir.is_dir():
        for f in sorted(config.adapters_dir.glob("*.json")):
            data = _read_json(f)
            if data:
                adapters.append(data)
    return adapters


def _check_process(pattern):
    """Check if a process matching pattern is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=2
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def build_agent_table(agents):
    """Build the main agent status table."""
    table = Table(title="Agents", expand=True, show_lines=True)
    table.add_column("Agent", style="bold", width=10)
    table.add_column("PID", width=8, justify="right")
    table.add_column("Status", width=10)
    table.add_column("Context", width=18)
    table.add_column("RSS", width=8, justify="right")
    table.add_column("Updates", width=8, justify="right")
    table.add_column("Last Activity", width=14)
    table.add_column("Doorbells", width=10, justify="right")
    table.add_column("IRC Out", width=8, justify="right")
    table.add_column("Mail", width=8, justify="right")

    for name, info in agents.items():
        home = Path(info["home"])
        asdaaas_dir = home / "asdaaas"
        health = _read_json(asdaaas_dir / "health.json")

        if health:
            status = Text(health.get("status", "?"), style="green" if health.get("status") == "working" else "yellow")
            ctx = _context_bar(health.get("totalTokens", 0), health.get("contextWindow", 200000))
            last = _time_ago(health.get("last_activity", health.get("ts", "")))
            pid = health.get("pid")
            pid_str = str(pid) if pid else "-"
            rss = _get_process_rss_mb(pid) if pid else None
            rss_str = f"{rss:.0f}M" if rss else "-"
        else:
            status = Text("offline", style="red") if info.get("registered") else Text("found", style="dim")
            ctx = Text("-", style="dim")
            last = "-"
            pid_str = "-"
            rss_str = "-"

        updates_size = _get_updates_jsonl_size(name)
        updates_str = _format_size(updates_size)
        if updates_size and updates_size > 200 * 1024 * 1024:
            updates_str = Text(updates_str, style="red")
        elif updates_size and updates_size > 100 * 1024 * 1024:
            updates_str = Text(updates_str, style="yellow")

        doorbells = _count_files(asdaaas_dir / "doorbells")
        irc_out = _count_files(asdaaas_dir / "adapters" / "irc" / "outbox")
        mail = _count_files(asdaaas_dir / "adapters" / "localmail" / "inbox")

        table.add_row(
            name, pid_str, status, ctx, rss_str, updates_str, last,
            str(doorbells) if doorbells else "-",
            str(irc_out) if irc_out else "-",
            str(mail) if mail else "-",
        )

    return table


def build_infra_panel():
    """Build infrastructure status panel."""
    lines = []

    # Check key processes
    checks = [
        ("asdaaas agents", "asdaaas.py --agent"),
        ("IRC server", "miniircd"),
        ("IRC adapter", "irc_adapter.py"),
        ("TUI adapter", "tui_adapter.py"),
        ("Localmail", "localmail.py"),
        ("Heartbeat", "heartbeat_adapter.py"),
    ]
    for label, pattern in checks:
        running = _check_process(pattern)
        status = "[green]UP[/green]" if running else "[dim]down[/dim]"
        lines.append(f"  {label}: {status}")

    # Registered adapters
    adapters = _get_adapters()
    if adapters:
        adapter_names = [a.get("name", "?") for a in adapters]
        lines.append(f"  Adapters: {', '.join(adapter_names)}")

    return Panel("\n".join(lines), title="Infrastructure", border_style="blue")


def build_snapshot():
    """Build a text snapshot for --once mode."""
    agents = _discover_agents()
    lines = []
    lines.append("=== ASDAAAS Ops Dashboard ===")
    lines.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Agents home: {config.agents_home}")
    lines.append("")

    # Infra
    checks = [
        ("asdaaas", "asdaaas.py --agent"),
        ("IRC", "miniircd"),
        ("IRC adapter", "irc_adapter.py"),
        ("TUI adapter", "tui_adapter.py"),
        ("Localmail", "localmail.py"),
        ("Heartbeat", "heartbeat_adapter.py"),
    ]
    infra = []
    for label, pattern in checks:
        running = _check_process(pattern)
        infra.append(f"{label}: {'UP' if running else 'down'}")
    lines.append("Infra: " + " | ".join(infra))
    lines.append("")

    # Agents
    lines.append(f"{'Agent':<10} {'PID':<8} {'Status':<10} {'Context':<18} {'RSS':<8} {'Updates':<8} {'Last':<14} {'Bells':<8} {'Mail':<8}")
    lines.append("-" * 94)
    for name, info in agents.items():
        home = Path(info["home"])
        health = _read_json(home / "asdaaas" / "health.json")
        if health:
            status = health.get("status", "?")
            total = health.get("totalTokens", 0)
            window = health.get("contextWindow", 200000)
            pct = f"{total/window*100:.0f}%" if window else "?"
            last = _time_ago(health.get("last_activity", health.get("ts", "")))
            pid = health.get("pid")
            pid_str = str(pid) if pid else "-"
            rss = _get_process_rss_mb(pid) if pid else None
            rss_str = f"{rss:.0f}M" if rss else "-"
        else:
            status = "offline"
            pct = "-"
            last = "-"
            pid_str = "-"
            rss_str = "-"
        updates_str = _format_size(_get_updates_jsonl_size(name))
        bells = _count_files(home / "asdaaas" / "doorbells")
        mail = _count_files(home / "asdaaas" / "adapters" / "localmail" / "inbox")
        lines.append(f"{name:<10} {pid_str:<8} {status:<10} {pct:<18} {rss_str:<8} {updates_str:<8} {last:<14} {bells:<8} {mail:<8}")

    return "\n".join(lines)


class OpsDashboard(App):
    CSS = """
    Screen { layout: vertical; }
    #infra { height: auto; margin: 1; }
    #agents { margin: 1; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]

    def compose(self) -> ComposeResult:
        yield Static(id="infra")
        yield Static(id="agents")
        yield Footer()

    def on_mount(self):
        self._refresh_data()
        self.set_interval(10, self._refresh_data)

    def _refresh_data(self):
        agents = _discover_agents()
        try:
            self.query_one("#infra", Static).update(build_infra_panel())
            self.query_one("#agents", Static).update(build_agent_table(agents))
        except Exception:
            pass

    def action_refresh(self):
        self._refresh_data()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ASDAAAS Ops Dashboard")
    parser.add_argument("--once", action="store_true", help="Print snapshot and exit")
    args = parser.parse_args()

    if args.once:
        print(build_snapshot())
    else:
        app = OpsDashboard()
        app.run()


if __name__ == "__main__":
    main()
