#!/usr/bin/env python3
"""
MikeyV Team Dashboard — Terminal status display
=================================================
Shows real-time status of all MikeyV agent sessions.
Reads signals.json and summary.json for each registered agent.
Detects asdaaas stdio processes per agent.
Auto-refreshes every N seconds.

Usage:
  python3 mikeyv_dashboard.py              # Default 10s refresh
  python3 mikeyv_dashboard.py --interval 5 # 5s refresh
  python3 mikeyv_dashboard.py --once       # Single snapshot, no loop
"""

import json
import os
import sys
import time
import subprocess
import argparse
from datetime import datetime, timezone, timedelta

SESSION_REGISTRY = os.path.expanduser("~/.grok/session_registry.json")
SESSIONS_BASE = os.path.expanduser("~/.grok/sessions")
HUB_DIR = os.path.expanduser("~/asdaaas")
AGENTS_DIR = os.path.join(HUB_DIR, "agents")  # legacy
AGENTS_HOME_DIR = os.path.expanduser("~/agents")
RUNNING_AGENTS_FILE = os.path.join(HUB_DIR, "running_agents.json")

# ANSI
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"

def find_session_dir(session_id):
    """Find the session directory for a given session ID.
    
    Multiple CWD dirs may contain the same session ID (e.g., testagent vs Sr).
    Pick the one with the most recently modified signals.json.
    
    No caching — the scan is ~10 stat calls, microseconds of work.
    A dashboard's job is to show what's happening NOW. Caching created
    a data trust problem: stale paths served for the TTL duration,
    showing wrong context percentages after compaction.
    """
    best = None
    best_mtime = 0
    for cwd_dir in os.listdir(SESSIONS_BASE):
        candidate = os.path.join(SESSIONS_BASE, cwd_dir, session_id)
        if os.path.isdir(candidate):
            sig = os.path.join(candidate, "signals.json")
            try:
                mtime = os.path.getmtime(sig)
            except OSError:
                mtime = 0
            if mtime > best_mtime:
                best = candidate
                best_mtime = mtime
    return best


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def context_bar(pct, width=15):
    filled = int(width * pct / 100)
    empty = width - filled
    if pct >= 80:
        color = RED
    elif pct >= 60:
        color = YELLOW
    elif pct >= 40:
        color = CYAN
    else:
        color = GREEN
    bar = f"{color}{chr(9608) * filled}{DIM}{chr(9617) * empty}{RESET}"
    return f"[{bar}] {color}{pct:3d}%{RESET}"


def fmt_dur(seconds):
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.0f}m"
    elif seconds < 86400:
        return f"{int(seconds//3600)}h{int((seconds%3600)//60):02d}m"
    else:
        return f"{int(seconds//86400)}d{int((seconds%86400)//3600)}h"


def time_ago(iso_ts):
    try:
        if '.' in iso_ts:
            base, frac = iso_ts.split('.', 1)
            suffix = ''
            for i, c in enumerate(frac):
                if not c.isdigit():
                    suffix = frac[i:]
                    frac = frac[:i]
                    break
            frac = frac[:6]
            iso_ts = f"{base}.{frac}{suffix}"
        if iso_ts.endswith('Z'):
            dt = datetime.fromisoformat(iso_ts.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(iso_ts)
        return fmt_dur((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return "?"


def proc_up(pattern):
    try:
        return subprocess.run(["pgrep", "-f", pattern],
                              capture_output=True, timeout=2).returncode == 0
    except Exception:
        return False


def count_procs(pattern):
    try:
        r = subprocess.run(["pgrep", "-fc", pattern],
                           capture_output=True, timeout=2, text=True)
        return int(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        return 0


def get_asdaaas_agents():
    """Get list of agents running on asdaaas stdio."""
    try:
        with open(RUNNING_AGENTS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def get_agent_health(agent_name):
    """Read health file for an agent. Returns (status, detail, age_seconds, health_dict)."""
    path = os.path.join(AGENTS_HOME_DIR, agent_name, "asdaaas", "health.json")
    try:
        with open(path) as f:
            h = json.load(f)
        age = time.time() - os.path.getmtime(path)
        return h.get("status", "?"), h.get("detail", ""), age, h
    except Exception:
        return None, None, None, None


def _read_rss(pid):
    """Read RSS in MB from /proc/<pid>/status."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024  # KB to MB
    except (FileNotFoundError, PermissionError, ValueError):
        pass
    return None


def _find_grok_child(parent_pid):
    """Find grok binary child of a given parent PID."""
    try:
        r = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True, timeout=2, text=True
        )
        if r.returncode != 0:
            return None
        for cpid in r.stdout.strip().split():
            try:
                with open(f"/proc/{cpid}/comm") as f:
                    if f.read().strip() == "grok":
                        return int(cpid)
                # Check children of children (node shim -> grok)
                r2 = subprocess.run(
                    ["pgrep", "-P", cpid],
                    capture_output=True, timeout=2, text=True
                )
                if r2.returncode == 0:
                    for gcpid in r2.stdout.strip().split():
                        try:
                            with open(f"/proc/{gcpid}/comm") as f:
                                if f.read().strip() == "grok":
                                    return int(gcpid)
                        except (FileNotFoundError, PermissionError):
                            pass
            except (FileNotFoundError, PermissionError):
                pass
    except Exception:
        pass
    return None


def get_agent_rss(agent_name):
    """Get RSS in MB for an agent's grok process.
    Works for both stdio agents (via asdaaas parent) and TUI agents."""
    # Method 1: Find via asdaaas parent process
    try:
        r = subprocess.run(
            ["pgrep", "-f", f"asdaaas.*--agent {agent_name}"],
            capture_output=True, timeout=2, text=True
        )
        if r.returncode == 0:
            asdaaas_pid = int(r.stdout.strip().split()[0])
            grok_pid = _find_grok_child(asdaaas_pid)
            if grok_pid:
                rss = _read_rss(grok_pid)
                if rss:
                    return rss
    except Exception:
        pass

    # Method 2: Health file PID
    try:
        path = os.path.join(AGENTS_HOME_DIR, agent_name, "asdaaas", "health.json")
        with open(path) as f:
            h = json.load(f)
        pid = h.get("pid")
        if pid:
            grok_pid = _find_grok_child(pid)
            if grok_pid:
                return _read_rss(grok_pid)
    except Exception:
        pass

    # Method 3: Match TUI grok process by CWD
    # TUI grok processes have CWD = ~/agents/<Name>/
    try:
        r = subprocess.run(["pgrep", "-x", "grok"],
                           capture_output=True, timeout=2, text=True)
        if r.returncode == 0:
            for pid in r.stdout.strip().split():
                try:
                    cwd = os.readlink(f"/proc/{pid}/cwd")
                    if cwd.endswith(f"/agents/{agent_name}") or cwd.endswith(f"/{agent_name}"):
                        return _read_rss(int(pid))
                except (FileNotFoundError, PermissionError):
                    pass
    except Exception:
        pass

    return None


def get_session_updates_size(session_id):
    """Get updates.jsonl size in MB for a session."""
    sdir = find_session_dir(session_id)
    if not sdir:
        return None
    path = os.path.join(sdir, "updates.jsonl")
    try:
        return os.path.getsize(path) / 1048576  # bytes to MB
    except Exception:
        return None


def get_system_memory():
    """Return (used_mb, total_mb, pct) from /proc/meminfo."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0) // 1024
        avail = info.get("MemAvailable", 0) // 1024
        used = total - avail
        pct = int(100 * used / total) if total else 0
        return used, total, pct
    except Exception:
        return None, None, None


def physical_turns_recent(agent_name, window_s=300):
    """Count physical turns in the last `window_s` seconds from profile log."""
    profile_path = os.path.join(AGENTS_HOME_DIR, agent_name, "asdaaas", "profile", f"{agent_name}.jsonl")
    cutoff = time.time() - window_s
    count = 0
    try:
        with open(profile_path) as f:
            # Read from end — most recent entries are last
            lines = f.readlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("ts", "")
                ts_dt = datetime.fromisoformat(ts_str)
                # Assume local time (asdaaas writes with time.strftime, no tz)
                ts_epoch = ts_dt.replace(tzinfo=timezone(timedelta(hours=-7))).timestamp()
                if ts_epoch >= cutoff:
                    count += 1
                else:
                    break  # entries are chronological, stop once we pass the window
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
    except FileNotFoundError:
        return 0
    except Exception:
        return 0
    return count


def doorbell_counts_for(agent_name):
    """Count pending doorbells for an agent, split by type.
    Returns (total, continue_count, other_count)."""
    bell_dir = os.path.join(AGENTS_HOME_DIR, agent_name, "asdaaas", "doorbells")
    try:
        files = [f for f in os.listdir(bell_dir) if f.endswith(".json")]
        total = len(files)
        cont = len([f for f in files if f.startswith("cont_")])
        return total, cont, total - cont
    except Exception:
        return 0, 0, 0


def last_physical_turn_age(agent_name):
    """Return seconds since last physical turn, or None if no profile data."""
    profile_path = os.path.join(AGENTS_HOME_DIR, agent_name, "asdaaas", "profile", f"{agent_name}.jsonl")
    try:
        with open(profile_path, "rb") as f:
            # Seek to end, read last line efficiently
            f.seek(0, 2)
            fsize = f.tell()
            if fsize == 0:
                return None
            # Read last 1KB (enough for one JSON line)
            f.seek(max(0, fsize - 1024))
            lines = f.read().decode("utf-8", errors="replace").strip().split("\n")
            last_line = lines[-1].strip()
            if not last_line:
                return None
            entry = json.loads(last_line)
            ts_str = entry.get("ts", "")
            ts_dt = datetime.fromisoformat(ts_str)
            ts_epoch = ts_dt.replace(tzinfo=timezone(timedelta(hours=-7))).timestamp()
            return time.time() - ts_epoch
    except Exception:
        return None


def get_logical_turn(agent_name, h_status):
    """Determine if agent is in a self-directed logical turn.
    
    Uses doorbell presence + profile log as fallback. Continue doorbells
    have TTL=1 and are consumed in ~0.25s, so the dashboard (10s poll)
    almost never catches them. Instead, check if the last physical turn
    was recent (within 60s) to infer the continue loop is active.
    
    Returns (is_active, detail_string).
    """
    total, cont, other = doorbell_counts_for(agent_name)
    in_physical = h_status in ("active", "working")
    
    if other > 0 and in_physical:
        return True, f"run+{other}"
    elif other > 0:
        return True, f"pend:{other}"
    elif cont > 0 and in_physical:
        return True, "run"
    elif cont > 0:
        return True, "cont"
    else:
        # Fallback: check profile log for recent activity
        age = last_physical_turn_age(agent_name)
        if age is not None and age < 60:
            return True, f"cont {fmt_dur(age)}"
        return False, ""


def inbox_count_for(agent_name):
    """Count inbox messages for a specific agent."""
    inbox = os.path.join(HUB_DIR, "inbox")
    count = 0
    try:
        for f in os.listdir(inbox):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(inbox, f)) as fh:
                    msg = json.load(fh)
                if msg.get("to") == agent_name:
                    count += 1
            except Exception:
                pass
    except Exception:
        pass
    return count


def inbox_count():
    inbox = os.path.join(HUB_DIR, "inbox")
    try:
        return len([f for f in os.listdir(inbox) if f.endswith(".json")])
    except Exception:
        return 0


def outbox_count(adapter="irc"):
    outbox = os.path.join(HUB_DIR, "outbox", adapter)
    try:
        return len([f for f in os.listdir(outbox) if f.endswith(".json")])
    except Exception:
        return 0


ROLES = {
    "Sr": ("Infra",   "Eric"),
    "Jr": ("Mgmt",    "Eric"),
    "Trip": ("Slides", "Jr"),
    "Q": ("Voice",    "Jr"),
    "Cinco": ("Comms", "Sr"),
}
ORDER = ["Sr", "Jr", "Trip", "Q", "Cinco"]


def render(interval):
    reg = load_json(SESSION_REGISTRY) or {}
    now = datetime.now(timezone(timedelta(hours=-7)))
    asdaaas_agents = get_asdaaas_agents()
    o = []

    o.append("")
    o.append(f"  {BOLD}{CYAN}+{'='*76}+{RESET}")
    o.append(f"  {BOLD}{CYAN}|{RESET}  {BOLD}MikeyV Team Dashboard{RESET}                          {DIM}{now.strftime('%Y-%m-%d %H:%M:%S PT')}{RESET}  {BOLD}{CYAN}|{RESET}")
    o.append(f"  {BOLD}{CYAN}+{'='*76}+{RESET}")
    o.append("")

    # Infra status
    irc_s = f"{GREEN}UP{RESET}" if proc_up("miniircd") else f"{RED}DOWN{RESET}"
    irc_a = f"{GREEN}UP{RESET}" if proc_up("irc_adapter") else f"{RED}DOWN{RESET}"
    stdio_n = count_procs("grok agent stdio")
    stdio_s = f"{GREEN}{stdio_n} proc{RESET}" if stdio_n > 0 else f"{RED}0{RESET}"
    hub_s = f"{GREEN}UP{RESET}" if proc_up("mikeyv_hub.py") else f"{DIM}OFF{RESET}"
    ldr_s = f"{GREEN}UP{RESET}" if proc_up("grok agent leader") else f"{DIM}OFF{RESET}"

    mem_used, mem_total, mem_pct = get_system_memory()
    if mem_pct is not None:
        if mem_pct >= 80:
            mem_color = RED
        elif mem_pct >= 60:
            mem_color = YELLOW
        else:
            mem_color = GREEN
        mem_s = f"{mem_color}{mem_used}M/{mem_total}M ({mem_pct}%){RESET}"
    else:
        mem_s = f"{DIM}?{RESET}"

    o.append(f"  {BOLD}Infra:{RESET}  IRC: {irc_s}  Adapter: {irc_a}  stdio: {stdio_s}  {DIM}hub: {hub_s}  leader: {ldr_s}{RESET}")
    o.append(f"  {BOLD}Memory:{RESET} {mem_s}")

    # ASDAAAS agents
    if asdaaas_agents:
        agent_list = ", ".join(asdaaas_agents)
        o.append(f"  {BOLD}ASDAAAS:{RESET} {GREEN}{agent_list}{RESET}")
    else:
        o.append(f"  {BOLD}ASDAAAS:{RESET} {DIM}no agents registered{RESET}")

    # Queue status
    iq = inbox_count()
    oq = outbox_count()
    iq_s = f"{YELLOW}{iq}{RESET}" if iq > 0 else f"{DIM}0{RESET}"
    oq_s = f"{YELLOW}{oq}{RESET}" if oq > 0 else f"{DIM}0{RESET}"
    o.append(f"  {BOLD}Queues:{RESET}  inbox: {iq_s}  outbox/irc: {oq_s}")

    o.append(f"  {BOLD}Hackathon:{RESET}  {YELLOW}TBD{RESET} (~next weekend, may publish independently)")
    o.append("")

    # Table header
    o.append(f"  {BOLD}{'Agent':<7} {'Role':<7} {'Pipe':<6} {'Health':<10} {'LT':<9} {'PT5m':>4} {'Context':^27}  {'RSS':>5} {'Upd':>5} {'Cmp':>4} {'Qd':>3}{RESET}")
    o.append(f"  {DIM}{'-'*102}{RESET}")

    tot = {"turns": 0, "comp": 0, "tools": 0, "err": 0}

    for name in ORDER:
        entry = reg.get(name)
        role, mgr = ROLES.get(name, ("?", "?"))

        if not entry:
            o.append(f"  {DIM}{name:<7} not registered{RESET}")
            continue

        sid = entry.get("session_id", "")
        sdir = find_session_dir(sid)

        sig = load_json(os.path.join(sdir, "signals.json")) if sdir else None
        summ = load_json(os.path.join(sdir, "summary.json")) if sdir else None

        # Connection type
        if name in asdaaas_agents:
            pipe = f"{GREEN}stdio{RESET}"
        elif proc_up(f"grok.*{name}") or proc_up(f"MikeyV-{name}"):
            pipe = f"{CYAN}tui{RESET}"
        else:
            pipe = f"{DIM}---{RESET}"

        # Health
        h_status, h_detail, h_age, h_data = get_agent_health(name)
        # Check if asdaaas process is still alive (PID from health file)
        h_pid_alive = False
        if h_data and h_data.get("pid"):
            try:
                os.kill(h_data["pid"], 0)  # signal 0 = existence check
                h_pid_alive = True
            except (OSError, TypeError):
                pass
        if h_age is not None and h_age > 300 and not h_pid_alive:
            # Health file old AND process dead = genuinely stale
            health = f"{RED}{chr(9679)} stale {fmt_dur(h_age)}{RESET}"
        elif h_age is not None and h_age > 300 and h_pid_alive:
            # Health file old but process alive = standing by
            health = f"{CYAN}{chr(9679)} idle {fmt_dur(h_age)}{RESET}"
        elif h_status == "working":
            health = f"{GREEN}{chr(9679)} working{RESET}"
        elif h_status == "active":
            if h_age is not None and h_age < 30:
                health = f"{GREEN}{chr(9679)} {fmt_dur(h_age)}{RESET}"
            elif h_age is not None and h_age < 120:
                health = f"{YELLOW}{chr(9679)} {fmt_dur(h_age)}{RESET}"
            else:
                health = f"{RED}{chr(9679)} {fmt_dur(h_age) if h_age else '?'}{RESET}"
        elif h_status == "ready":
            health = f"{CYAN}{chr(9679)} idle{RESET}"
        elif h_status == "error":
            health = f"{RED}{chr(9888)} err{RESET}"
        elif name in asdaaas_agents:
            health = f"{YELLOW}...{RESET}"
        else:
            health = f"{DIM}---{RESET}"

        # Per-agent inbox queue
        aq = inbox_count_for(name)

        # Logical turn
        lt_active, lt_detail = get_logical_turn(name, h_status)
        if lt_active:
            lt_s = f"{GREEN}{lt_detail}{RESET}"
        else:
            lt_s = f"{DIM}---{RESET}"

        # Physical turns in last 5 min
        pt5 = physical_turns_recent(name)
        if pt5 > 0:
            pt5_s = f"{GREEN}{pt5}{RESET}"
        else:
            pt5_s = f"{DIM}0{RESET}"

        if not sig:
            o.append(f"  {name:<7} {role:<7} {pipe:<15} {health:<19} {lt_s:<18} {pt5_s:>13} {'no session data':^27}")
            continue

        pct = sig.get("contextWindowUsage", 0)
        used_k = sig.get("contextTokensUsed", 0) // 1000
        total_k = sig.get("contextWindowTokens", 0) // 1000

        # For stdio agents, health file has live token counts from streaming
        # _meta (updated every 5s mid-turn). signals.json only updates at
        # turn boundaries. Use health data when it shows higher tokens.
        if h_data and h_data.get("totalTokens", 0) > 0:
            h_tokens = h_data["totalTokens"]
            h_window = h_data.get("contextWindow", 200000)
            h_pct = round(h_tokens / h_window * 100)
            if h_pct > pct:
                pct = h_pct
                used_k = h_tokens // 1000
                total_k = h_window // 1000
        turns = sig.get("turnCount", 0)
        comp = sig.get("compactionCount", 0)
        tools = sig.get("toolCallCount", 0)

        tot["turns"] += turns
        tot["comp"] += comp
        tot["tools"] += tools
        tot["err"] += sig.get("errorCount", 0)

        bar = context_bar(pct, 12)
        ctx = f"{bar}{DIM}{used_k}k/{total_k}k{RESET}"

        upd = time_ago(summ.get("updated_at", "")) if summ else "?"

        # Memory: RSS of grok process
        rss = get_agent_rss(name)
        if rss is not None:
            if rss > 500:
                rss_s = f"{RED}{rss}M{RESET}"
            elif rss > 200:
                rss_s = f"{YELLOW}{rss}M{RESET}"
            else:
                rss_s = f"{GREEN}{rss}M{RESET}"
        else:
            rss_s = f"{DIM}---{RESET}"

        # updates.jsonl size
        upd_size = get_session_updates_size(sid)
        if upd_size is not None:
            if upd_size > 200:
                upd_s = f"{RED}{upd_size:.0f}M{RESET}"
            elif upd_size > 100:
                upd_s = f"{YELLOW}{upd_size:.0f}M{RESET}"
            else:
                upd_s = f"{GREEN}{upd_size:.0f}M{RESET}"
        else:
            upd_s = f"{DIM}---{RESET}"

        o.append(f"  {BOLD}{name:<7}{RESET} {role:<7} {pipe:<15} {health:<19} {lt_s:<18} {pt5_s:>13} {ctx}  {rss_s:>14} {upd_s:>14} {comp:>4} {aq:>3}")

    o.append("")
    o.append(f"  {BOLD}Totals:{RESET} {tot['turns']} turns | {tot['tools']} tools | {tot['comp']} compactions | {tot['err']} errors")
    o.append("")
    o.append(f"  {DIM}Ctrl+C to exit | Refreshing every {interval}s{RESET}")
    return "\n".join(o)



# === TIME-SERIES LOGGING ===

LOG_DIR = os.path.expanduser("~/.grok/dashboard_logs")

def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)

def get_tui_rss(session_id):
    """Get RSS for a TUI grok process by finding it via session ID in cmdline or pts."""
    try:
        # Find grok processes that aren't stdio
        r = subprocess.run(
            ["pgrep", "-f", "grok$"],
            capture_output=True, timeout=2, text=True
        )
        if r.returncode != 0:
            # Try broader match
            r = subprocess.run(
                ["pgrep", "-x", "grok"],
                capture_output=True, timeout=2, text=True
            )
        if r.returncode != 0:
            return None
        
        for pid in r.stdout.strip().split():
            pid = pid.strip()
            if not pid:
                continue
            try:
                # Check if this is a TUI process (has a pts)
                with open(f"/proc/{pid}/stat") as f:
                    stat = f.read()
                # Get RSS from status
                with open(f"/proc/{pid}/status") as f:
                    rss_kb = None
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss_kb = int(line.split()[1])
                            break
                if rss_kb and rss_kb > 30000:  # >30MB, likely a real session
                    return rss_kb // 1024
            except (FileNotFoundError, PermissionError, ValueError):
                continue
        return None
    except Exception:
        return None


def get_all_grok_rss():
    """Get RSS for ALL grok processes, mapped by PID."""
    results = []
    try:
        r = subprocess.run(
            ["pgrep", "-a", "grok"],
            capture_output=True, timeout=2, text=True
        )
        if r.returncode != 0:
            return results
        for line in r.stdout.strip().split('\n'):
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            pid = parts[0]
            cmdline = parts[1]
            if 'node' in cmdline:
                continue  # skip node shims
            try:
                with open(f"/proc/{pid}/status") as f:
                    rss_kb = None
                    for l in f:
                        if l.startswith("VmRSS:"):
                            rss_kb = int(l.split()[1])
                            break
                if rss_kb:
                    is_stdio = 'agent stdio' in cmdline
                    results.append({
                        'pid': int(pid),
                        'rss_mb': rss_kb // 1024,
                        'type': 'stdio' if is_stdio else 'tui',
                        'cmdline': cmdline[:100]
                    })
            except (FileNotFoundError, PermissionError, ValueError):
                continue
    except Exception:
        pass
    return results


def log_snapshot():
    """Write a timestamped snapshot of all agent memory and session sizes to a log file."""
    ensure_log_dir()
    
    reg = load_json(SESSION_REGISTRY) or {}
    asdaaas_agents = get_asdaaas_agents()
    ts = datetime.now(timezone.utc).isoformat()
    
    snapshot = {
        "timestamp": ts,
        "agents": {},
        "system": {},
        "grok_processes": get_all_grok_rss()
    }
    
    # System memory
    mem_used, mem_total, mem_pct = get_system_memory()
    snapshot["system"] = {
        "mem_used_mb": mem_used,
        "mem_total_mb": mem_total,
        "mem_pct": mem_pct
    }
    
    for name in ORDER:
        entry = reg.get(name)
        if not entry:
            continue
        
        sid = entry.get("session_id", "")
        sdir = find_session_dir(sid)
        sig = load_json(os.path.join(sdir, "signals.json")) if sdir else None
        
        # Prefer live health file tokens over stale signals.json
        _h_s, _h_d, _h_a, _h_data = get_agent_health(name)
        ctx_pct = sig.get("contextWindowUsage", 0) if sig else None
        ctx_tokens = sig.get("contextTokensUsed", 0) if sig else None
        if _h_data and _h_data.get("totalTokens", 0) > 0:
            _h_t = _h_data["totalTokens"]
            _h_w = _h_data.get("contextWindow", 200000)
            _h_p = round(_h_t / _h_w * 100)
            if ctx_pct is None or _h_p > ctx_pct:
                ctx_pct = _h_p
                ctx_tokens = _h_t

        agent_data = {
            "session_id": sid,
            "context_pct": ctx_pct,
            "context_tokens": ctx_tokens,
            "compaction_count": sig.get("compactionCount", 0) if sig else None,
            "turn_count": sig.get("turnCount", 0) if sig else None,
            "tool_count": sig.get("toolCallCount", 0) if sig else None,
            "pipe": "stdio" if name in asdaaas_agents else "tui",
        }
        
        # RSS
        rss = get_agent_rss(name)
        agent_data["rss_mb"] = rss
        
        # updates.jsonl size
        upd_size = get_session_updates_size(sid)
        agent_data["updates_mb"] = round(upd_size, 1) if upd_size else None

        # Logical turn
        _h_s2 = _h_s if _h_s else ""
        lt_active, lt_detail = get_logical_turn(name, _h_s2)
        agent_data["logical_turn"] = lt_active
        agent_data["logical_turn_detail"] = lt_detail
        _total, _cont, _other = doorbell_counts_for(name)
        agent_data["pending_doorbells"] = _total
        agent_data["pending_doorbells_other"] = _other
        agent_data["pending_doorbells_continue"] = _cont
        agent_data["physical_turns_5m"] = physical_turns_recent(name)
        
        snapshot["agents"][name] = agent_data
    
    # Write to daily log file (NDJSON)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"dashboard_{date_str}.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(snapshot) + "\n")
    
    return log_path



def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=10)
    p.add_argument("--once", action="store_true")
    p.add_argument("--log-interval", type=int, default=60,
                   help="Seconds between log snapshots (default: 60)")
    p.add_argument("--no-log", action="store_true",
                   help="Disable time-series logging")
    p.add_argument("--log-only", action="store_true",
                   help="Log snapshots without display (background mode)")
    a = p.parse_args()

    if a.once:
        print(render(a.interval))
        if not a.no_log:
            log_snapshot()
        return

    if a.log_only:
        # Background logging mode -- no display
        try:
            while True:
                log_snapshot()
                time.sleep(a.log_interval)
        except KeyboardInterrupt:
            print("Logging stopped.")
        return

    last_log = 0
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
            print(render(a.interval))
            # Log at log_interval frequency
            if not a.no_log and (time.time() - last_log) >= a.log_interval:
                log_snapshot()
                last_log = time.time()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print(f"\n{DIM}Dashboard stopped.{RESET}")


if __name__ == "__main__":
    main()
