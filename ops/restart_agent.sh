#!/bin/bash
# Restart a single asdaaas agent with stability checks at each stage.
#
# Usage:
#   bash restart_agent.sh <AgentName>         # restart one agent
#   bash restart_agent.sh <Agent1> <Agent2>   # restart multiple agents
#   bash restart_agent.sh --list              # list configured agents
#   bash restart_agent.sh --force <Agent>     # skip graceful shutdown
#   bash restart_agent.sh --no-check <Agent>  # skip startup verification
#
# Reads configuration from agents.json (same directory as this script).
#
# Startup stages (each verified before proceeding):
#   1. Config    - validate agents.json, check paths exist
#   2. Clean     - remove stale shutdown/delay commands from queue
#   3. Stop      - stop existing process, verify it's gone
#   4. Launch    - start asdaaas.py, verify PID survives 2s
#   5. Backend   - verify grok binary started (log: "Starting backend")
#   6. Session   - verify session loaded (log: "Session:")
#   7. Ready     - verify main loop entered (log: "Ready.")
#   8. Health    - verify health.json written and recent

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/agents.json"

if [ ! -f "$CONFIG" ]; then
    echo "FAIL: Config file not found: $CONFIG"
    exit 1
fi

# Read settings from config
ASDAAAS_DIR=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c['settings']['asdaaas_dir'])")
LOG_DIR=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c['settings']['log_dir'])")
RUNNING_AGENTS_FILE=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c['settings']['running_agents_file'])")
DEBUG=$(python3 -c "import json; c=json.load(open('$CONFIG')); print('1' if c['settings'].get('debug', False) else '')")
ASDAAAS="$ASDAAAS_DIR/asdaaas.py"
GROK_BINARY=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c['settings'].get('grok_binary', ''))" 2>/dev/null || true)

TIMEOUT_GRACEFUL=30
TIMEOUT_TERM=10
STARTUP_TIMEOUT=60  # max seconds to wait for agent to reach "Ready."

# Parse args
FORCE=false
LIST=false
NO_CHECK=false
TARGETS=()
for arg in "$@"; do
    case "$arg" in
        --force)    FORCE=true ;;
        --list)     LIST=true ;;
        --no-check) NO_CHECK=true ;;
        *)          TARGETS+=("$arg") ;;
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
    echo "       restart_agent.sh --no-check <AgentName>"
    exit 1
fi

# ---- Helper: wait for a pattern in the log file ----
# Returns 0 if pattern found, 1 if timeout.
# Only searches lines written after $LOG_START_LINE.
wait_for_log() {
    local log_file="$1"
    local pattern="$2"
    local timeout_secs="$3"
    local elapsed=0
    while [ $elapsed -lt $timeout_secs ]; do
        if [ -f "$log_file" ] && tail -n +${LOG_START_LINE:-1} "$log_file" 2>/dev/null | grep -q "$pattern"; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

# ---- Helper: show recent log lines on failure ----
show_log_tail() {
    local log_file="$1"
    local n="${2:-15}"
    if [ -f "$log_file" ]; then
        echo "  --- Last $n lines of $log_file ---"
        tail -n "$n" "$log_file" 2>/dev/null | sed 's/^/  | /'
        echo "  ---"
    fi
}

# ---- Stage 1: Config validation ----
stage_config() {
    local agent="$1"
    echo "  [1/8] Config..."

    # Check agent exists in config
    if ! python3 -c "import json,sys; c=json.load(open('$CONFIG')); sys.exit(0 if '$agent' in c['agents'] else 1)" 2>/dev/null; then
        echo "  FAIL: Agent '$agent' not found in $CONFIG"
        python3 -c "import json; c=json.load(open('$CONFIG')); [print(f'    {n}') for n in c['agents']]"
        return 1
    fi

    # Read agent config
    local home session
    home=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['home'])")
    session=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['session'])")

    # Check paths exist
    if [ ! -d "$home" ]; then
        echo "  FAIL: Agent home directory does not exist: $home"
        return 1
    fi

    if [ ! -f "$ASDAAAS" ]; then
        echo "  FAIL: asdaaas.py not found: $ASDAAAS"
        return 1
    fi

    # Check grok binary
    local bin="${GROK_BINARY:-$(which grok 2>/dev/null || true)}"
    if [ -n "$bin" ] && [ ! -x "$bin" ]; then
        echo "  FAIL: grok binary not executable: $bin"
        return 1
    fi
    if [ -z "$bin" ]; then
        echo "  FAIL: No grok binary configured and none in PATH"
        return 1
    fi

    # Check session directory exists
    local encoded_home
    encoded_home=$(python3 -c "print('$home'.replace('/', '%2F'))")
    local session_dir="$HOME/.grok/sessions/$encoded_home/$session"
    if [ ! -d "$session_dir" ]; then
        echo "  WARN: Session directory does not exist: $session_dir"
        echo "        (Will be created on first run, but session/load will fail)"
    fi

    echo "  OK   Config valid (home=$home, binary=$bin)"
}

# ---- Stage 2: Clean stale commands ----
stage_clean() {
    local agent="$1"
    echo "  [2/8] Clean..."

    local home
    home=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['home'])")
    local cmd_dir="$home/asdaaas/commands"
    local cleaned=0

    if [ -d "$cmd_dir" ]; then
        # Remove shutdown commands (stale from previous crash/restart)
        for f in "$cmd_dir"/cmd_*shutdown*.json "$cmd_dir"/cmd_*force_compact*.json; do
            if [ -f "$f" ]; then
                rm "$f"
                cleaned=$((cleaned + 1))
            fi
        done

        # Remove stale delay commands that might block startup
        for f in "$cmd_dir"/cmd_*.json; do
            if [ -f "$f" ]; then
                local action
                action=$(python3 -c "import json; print(json.load(open('$f')).get('action',''))" 2>/dev/null || true)
                if [ "$action" = "shutdown" ] || [ "$action" = "force_compact" ]; then
                    rm "$f"
                    cleaned=$((cleaned + 1))
                fi
            fi
        done
    fi

    # Remove cancel flag if present
    local cancel_flag="$home/asdaaas/cancel_turn.flag"
    if [ -f "$cancel_flag" ]; then
        rm "$cancel_flag"
        cleaned=$((cleaned + 1))
    fi

    # Remove legacy commands.json if it has a shutdown
    local legacy_cmd="$home/asdaaas/commands.json"
    if [ -f "$legacy_cmd" ]; then
        local action
        action=$(python3 -c "import json; print(json.load(open('$legacy_cmd')).get('action',''))" 2>/dev/null || true)
        if [ "$action" = "shutdown" ]; then
            rm "$legacy_cmd"
            cleaned=$((cleaned + 1))
        fi
    fi

    if [ $cleaned -gt 0 ]; then
        echo "  OK   Cleaned $cleaned stale command(s)"
    else
        echo "  OK   No stale commands"
    fi
}

# ---- Stage 3: Stop existing process ----
stage_stop() {
    local agent="$1"
    echo "  [3/8] Stop..."

    if ! pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
        echo "  OK   Not running"
        return 0
    fi

    if [ "$FORCE" = true ]; then
        pkill -KILL -f "asdaaas.py --agent $agent" 2>/dev/null
        sleep 1
        if pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
            echo "  FAIL: Process survived SIGKILL"
            return 1
        fi
        echo "  OK   Force-killed"
        return 0
    fi

    # Graceful shutdown
    local agent_home
    agent_home=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['home'])")
    local cmd_dir="$agent_home/asdaaas/commands"
    mkdir -p "$cmd_dir"
    echo '{"action": "shutdown"}' > "$cmd_dir/cmd_shutdown_$(date +%s).json"

    local elapsed=0
    while [ $elapsed -lt $TIMEOUT_GRACEFUL ]; do
        if ! pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
            echo "  OK   Stopped gracefully (${elapsed}s)"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    # Escalate: SIGTERM
    pkill -TERM -f "asdaaas.py --agent $agent" 2>/dev/null
    sleep 2
    if ! pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
        echo "  OK   Stopped via SIGTERM"
        return 0
    fi

    # Escalate: SIGKILL
    pkill -KILL -f "asdaaas.py --agent $agent" 2>/dev/null
    sleep 1
    if pgrep -f "asdaaas.py --agent $agent" > /dev/null 2>&1; then
        echo "  FAIL: Process survived all kill attempts"
        return 1
    fi
    echo "  OK   Stopped via SIGKILL"
}

# ---- Stage 4: Launch process ----
stage_launch() {
    local agent="$1"
    echo "  [4/8] Launch..."

    local session home model
    session=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['session'])")
    home=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['home'])")
    model=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent'].get('model', ''))" 2>/dev/null || true)
    local log_file="$LOG_DIR/asdaaas_$(echo "$agent" | tr '[:upper:]' '[:lower:]').log"

    if [ -n "$DEBUG" ]; then
        export ASDAAAS_DEBUG=1
    fi

    local extra_args=""
    if [ -n "$model" ]; then
        extra_args="--model $model"
    fi
    if [ -n "$GROK_BINARY" ]; then
        extra_args="$extra_args --grok-binary $GROK_BINARY"
    fi

    # Record current log line count so we only check new lines
    if [ -f "$log_file" ]; then
        LOG_START_LINE=$(wc -l < "$log_file")
        LOG_START_LINE=$((LOG_START_LINE + 1))
    else
        LOG_START_LINE=1
    fi

    setsid nohup python3 -u "$ASDAAAS" --agent "$agent" --session "$session" --cwd "$home" $extra_args >> "$log_file" 2>&1 &
    local pid=$!

    # Wait 2 seconds and verify process is still alive
    sleep 2
    if ! kill -0 $pid 2>/dev/null; then
        echo "  FAIL: Process died within 2s (PID $pid)"
        show_log_tail "$log_file"
        return 1
    fi

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

    echo "  OK   PID $pid alive (session=$session, model=${model:-default})"
    echo "         Log: $log_file"

    # Export for later stages
    AGENT_PID=$pid
    AGENT_LOG="$log_file"
}

# ---- Stage 5: Backend started ----
stage_backend() {
    local agent="$1"
    echo "  [5/8] Backend..."

    if ! wait_for_log "$AGENT_LOG" "Starting backend" 15; then
        echo "  FAIL: 'Starting backend' not seen in log after 15s"
        show_log_tail "$AGENT_LOG"
        return 1
    fi

    # Check process still alive
    if ! kill -0 $AGENT_PID 2>/dev/null; then
        echo "  FAIL: Process died during backend startup"
        show_log_tail "$AGENT_LOG"
        return 1
    fi

    echo "  OK   Backend starting"
}

# ---- Stage 6: Session loaded ----
stage_session() {
    local agent="$1"
    echo "  [6/8] Session..."

    if ! wait_for_log "$AGENT_LOG" "Session:" 30; then
        echo "  FAIL: 'Session:' not seen in log after 30s"
        echo "        Possible causes: corrupted session, binary auth failure, network issue"
        show_log_tail "$AGENT_LOG"
        return 1
    fi

    if ! kill -0 $AGENT_PID 2>/dev/null; then
        echo "  FAIL: Process died during session load"
        show_log_tail "$AGENT_LOG"
        return 1
    fi

    echo "  OK   Session loaded"
}

# ---- Stage 7: Ready ----
stage_ready() {
    local agent="$1"
    echo "  [7/8] Ready..."

    if ! wait_for_log "$AGENT_LOG" "Ready\\." $STARTUP_TIMEOUT; then
        echo "  FAIL: 'Ready.' not seen in log after ${STARTUP_TIMEOUT}s"
        echo "        Process may have crashed during initialization"
        show_log_tail "$AGENT_LOG" 20
        return 1
    fi

    if ! kill -0 $AGENT_PID 2>/dev/null; then
        echo "  FAIL: Process died after reaching ready state"
        show_log_tail "$AGENT_LOG"
        return 1
    fi

    echo "  OK   Main loop entered"
}

# ---- Stage 8: Health file ----
stage_health() {
    local agent="$1"
    echo "  [8/8] Health..."

    local home
    home=$(python3 -c "import json; print(json.load(open('$CONFIG'))['agents']['$agent']['home'])")
    local health_file="$home/asdaaas/health.json"

    # Wait up to 5s for health file to appear/update
    local elapsed=0
    while [ $elapsed -lt 5 ]; do
        if [ -f "$health_file" ]; then
            local status
            status=$(python3 -c "import json; print(json.load(open('$health_file')).get('status',''))" 2>/dev/null || true)
            if [ "$status" = "ready" ] || [ "$status" = "idle" ] || [ "$status" = "working" ]; then
                local tokens ctx
                tokens=$(python3 -c "import json; print(json.load(open('$health_file')).get('totalTokens', '?'))" 2>/dev/null || echo "?")
                ctx=$(python3 -c "import json; print(json.load(open('$health_file')).get('contextWindow', '?'))" 2>/dev/null || echo "?")
                echo "  OK   Health: status=$status, tokens=$tokens, context=$ctx"
                return 0
            fi
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    echo "  WARN: Health file not updated (may be normal on first run)"
}

# ---- Run all stages for one agent ----
restart_one_agent() {
    local agent="$1"
    local failed=false

    echo ""
    echo "=== $agent ==="

    stage_config "$agent" || { failed=true; echo "  ABORT: Config validation failed"; return 1; }
    stage_clean "$agent"  # clean never fails fatally
    stage_stop "$agent"   || { failed=true; echo "  ABORT: Could not stop existing process"; return 1; }

    sleep 1

    stage_launch "$agent" || { failed=true; echo "  ABORT: Launch failed"; return 1; }

    if [ "$NO_CHECK" = true ]; then
        echo "  (skipping startup checks -- --no-check)"
        return 0
    fi

    stage_backend "$agent" || { failed=true; echo "  ABORT: Backend failed to start"; return 1; }
    stage_session "$agent" || { failed=true; echo "  ABORT: Session failed to load"; return 1; }
    stage_ready "$agent"   || { failed=true; echo "  ABORT: Agent failed to reach ready state"; return 1; }
    stage_health "$agent"  # health is a warning, not fatal

    echo "  === $agent UP ==="
}

# ---- Main ----
FAILED_AGENTS=()
SUCCEEDED_AGENTS=()

for agent in "${TARGETS[@]}"; do
    if restart_one_agent "$agent"; then
        SUCCEEDED_AGENTS+=("$agent")
    else
        FAILED_AGENTS+=("$agent")
    fi
done

echo ""
echo "=== Summary ==="
if [ ${#SUCCEEDED_AGENTS[@]} -gt 0 ]; then
    echo "  UP:   ${SUCCEEDED_AGENTS[*]}"
fi
if [ ${#FAILED_AGENTS[@]} -gt 0 ]; then
    echo "  FAIL: ${FAILED_AGENTS[*]}"
    exit 1
fi
