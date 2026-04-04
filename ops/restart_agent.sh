#!/bin/bash
# Restart a single asdaaas agent.
# Gracefully stops the agent, then relaunches with the same session.
#
# Usage:
#   bash restart_agent.sh <AgentName>         # restart one agent
#   bash restart_agent.sh <Agent1> <Agent2>   # restart multiple agents
#   bash restart_agent.sh --list              # list configured agents
#   bash restart_agent.sh --force <Agent>     # skip graceful shutdown
#
# Reads configuration from agents.json (same directory as this script).
# Edit agents.json to add/remove agents or change settings.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/agents.json"

if [ ! -f "$CONFIG" ]; then
    echo "ERROR: Config file not found: $CONFIG"
    echo "Create agents.json with agent definitions. See AGENT_START_HERE.md."
    exit 1
fi

# Read settings from config
ASDAAAS_DIR=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c['settings']['asdaaas_dir'])")
LOG_DIR=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c['settings']['log_dir'])")
RUNNING_AGENTS_FILE=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c['settings']['running_agents_file'])")
DEBUG=$(python3 -c "import json; c=json.load(open('$CONFIG')); print('1' if c['settings'].get('debug', False) else '')")
ASDAAAS="$ASDAAAS_DIR/asdaaas.py"

TIMEOUT_GRACEFUL=30
TIMEOUT_TERM=10

# Parse args
FORCE=false
LIST=false
TARGETS=()
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --list)  LIST=true ;;
        *)       TARGETS+=("$arg") ;;
    esac
done

# List mode
if [ "$LIST" = true ]; then
    echo "Configured agents:"
    python3 -c "
import json
c = json.load(open('$CONFIG'))
for name, info in c['agents'].items():
    sid = info.get('short_id', info['session'][-4:])
    print(f'  {name} ({sid}): session=...{info[\"session\"][-12:]}, home={info[\"home\"]}')
"
    exit 0
fi

if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "Usage: restart_agent.sh <AgentName> [<AgentName2> ...]"
    echo "       restart_agent.sh --list"
    echo "       restart_agent.sh --force <AgentName>"
    exit 1
fi

# Validate all targets exist in config
for agent in "${TARGETS[@]}"; do
    if ! python3 -c "import json,sys; c=json.load(open('$CONFIG')); sys.exit(0 if '$agent' in c['agents'] else 1)" 2>/dev/null; then
        echo "ERROR: Agent '$agent' not found in $CONFIG"
        echo "Available agents:"
        python3 -c "import json; c=json.load(open('$CONFIG')); [print(f'  {n}') for n in c['agents']]"
        exit 1
    fi
done

stop_agent() {
    local agent="$1"
    echo "Stopping $agent..."

    if ! pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
        echo "  $agent is not running"
        return 0
    fi

    if [ "$FORCE" = true ]; then
        pkill -KILL -f "asdaaas.py --agent $agent" 2>/dev/null
        echo "  $agent force-killed"
        sleep 1
        return 0
    fi

    # Graceful: write shutdown command
    local agent_home
    agent_home=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['home'])")
    local cmd_dir="$agent_home/asdaaas/commands"
    mkdir -p "$cmd_dir"
    local cmd_file="$cmd_dir/cmd_shutdown_$(date +%s).json"
    echo '{"action": "shutdown"}' > "$cmd_file"
    echo "  Shutdown command written"

    # Wait for graceful exit
    local elapsed=0
    while [ $elapsed -lt $TIMEOUT_GRACEFUL ]; do
        if ! pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
            echo "  $agent stopped gracefully (${elapsed}s)"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
        if [ $((elapsed % 5)) -eq 0 ]; then
            echo "  Waiting... (${elapsed}s)"
        fi
    done

    # SIGTERM
    echo "  $agent did not exit in ${TIMEOUT_GRACEFUL}s, sending SIGTERM..."
    pkill -TERM -f "asdaaas.py --agent $agent" 2>/dev/null
    sleep 2

    # SIGKILL if needed
    if pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
        local waited=0
        while [ $waited -lt $TIMEOUT_TERM ]; do
            if ! pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
                break
            fi
            sleep 1
            waited=$((waited + 1))
        done
        if pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
            echo "  Sending SIGKILL..."
            pkill -KILL -f "asdaaas.py --agent $agent" 2>/dev/null
            sleep 1
        fi
    fi
    echo "  $agent stopped"
}

start_agent() {
    local agent="$1"
    echo "Starting $agent..."

    local session home
    session=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['session'])")
    home=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['home'])")
    local log_file="$LOG_DIR/asdaaas_$(echo "$agent" | tr '[:upper:]' '[:lower:]').log"

    if [ -n "$DEBUG" ]; then
        export ASDAAAS_DEBUG=1
    fi

    setsid nohup python3 -u "$ASDAAAS" --agent "$agent" --session "$session" --cwd "$home" > "$log_file" 2>&1 &
    local pid=$!
    echo "  $agent started (PID $pid)"
    echo "  Session: $session"
    echo "  Log: $log_file"

    # Update running_agents.json
    python3 -c "
import json, os
raf = '$RUNNING_AGENTS_FILE'
os.makedirs(os.path.dirname(raf), exist_ok=True)
try:
    with open(raf) as f:
        ra = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    ra = {}
ra['$agent'] = {'home': '$home'}
with open(raf + '.tmp', 'w') as f:
    json.dump(ra, f, indent=2)
os.rename(raf + '.tmp', raf)
"
    echo "  running_agents.json updated"
}

# Execute
for agent in "${TARGETS[@]}"; do
    echo ""
    echo "=== Restarting $agent ==="
    stop_agent "$agent"
    sleep 1
    start_agent "$agent"
done

echo ""
echo "=== Done ==="