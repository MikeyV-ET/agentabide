#!/usr/bin/env bash
# backup_agents.sh — Automated backup of agent state and infrastructure
# ======================================================================
# Creates daily backups of critical agent data using rsync (incremental)
# and periodic tar.xz snapshots for disaster recovery.
#
# Usage:
#   backup_agents.sh                    # run backup now
#   backup_agents.sh --install-cron     # install daily cron job (3 AM)
#   backup_agents.sh --list             # list existing backups
#   backup_agents.sh --prune            # remove backups older than 7 days
#
# What gets backed up:
#   1. ~/agents/ — agent state (notebooks, notes, doorbells, config)
#   2. ~/projects/mikeyv-infra/ — infrastructure code
#   3. ~/.grok/sessions/ — session data (chat history, signals)
#
# Backup location: ~/backups/
#   ~/backups/daily/YYYY-MM-DD/     — rsync mirror (incremental)
#   ~/backups/snapshots/            — tar.xz snapshots (weekly)
#
# Retention: 7 daily mirrors, 4 weekly snapshots
# ======================================================================

set -euo pipefail

BACKUP_ROOT="${HOME}/backups"
DAILY_DIR="${BACKUP_ROOT}/daily"
SNAPSHOT_DIR="${BACKUP_ROOT}/snapshots"
LOG_FILE="${BACKUP_ROOT}/backup.log"
RETENTION_DAYS=7
SNAPSHOT_RETENTION_DAYS=28

# Sources to back up
AGENTS_DIR="${HOME}/agents"
INFRA_DIR="${HOME}/projects/mikeyv-infra"
SESSIONS_DIR="${HOME}/.grok/sessions"

# ---- Functions ----

log() {
    local ts
    ts=$(date +"%Y-%m-%d %H:%M:%S %Z")
    echo "[$ts] $*" | tee -a "$LOG_FILE"
}

do_backup() {
    local today
    today=$(date +"%Y-%m-%d")
    local target="${DAILY_DIR}/${today}"

    mkdir -p "$target/agents" "$target/infra" "$target/sessions" "$SNAPSHOT_DIR"
    touch "$LOG_FILE"

    log "=== Backup starting ==="

    # 1. Agents directory (exclude tar.xz archives and __pycache__)
    log "Backing up agents..."
    rsync -a --delete \
        --exclude='*.tar.xz' \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='asdaaas/adapters/*/outbox/' \
        "${AGENTS_DIR}/" "${target}/agents/"
    log "  agents: $(du -sh "${target}/agents/" | cut -f1)"

    # 2. Infrastructure code
    log "Backing up infra..."
    rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='*.bak' \
        --exclude='*.bak2' \
        "${INFRA_DIR}/" "${target}/infra/"
    log "  infra: $(du -sh "${target}/infra/" | cut -f1)"

    # 3. Session data (only signals.json, summary.json, and chat_history.jsonl per session)
    #    Skip updates.jsonl (large, reconstructable) and tool outputs
    log "Backing up sessions..."
    rsync -a --delete \
        --include='*/' \
        --include='signals.json' \
        --include='summary.json' \
        --include='chat_history.jsonl' \
        --exclude='*' \
        "${SESSIONS_DIR}/" "${target}/sessions/"
    log "  sessions: $(du -sh "${target}/sessions/" | cut -f1)"

    log "Daily backup complete: ${target}"
    log "  total: $(du -sh "${target}" | cut -f1)"

    # Weekly snapshot (on Sundays or if none exist)
    local day_of_week
    day_of_week=$(date +%u)  # 7 = Sunday
    local latest_snapshot
    latest_snapshot=$(ls -1 "${SNAPSHOT_DIR}"/agents_*.tar.xz 2>/dev/null | tail -1 || true)

    if [[ "$day_of_week" == "7" ]] || [[ -z "$latest_snapshot" ]]; then
        log "Creating weekly snapshot..."
        local snapshot_file="${SNAPSHOT_DIR}/agents_${today}.tar.xz"
        tar -cJf "$snapshot_file" \
            -C "$HOME" \
            --exclude='*.tar.xz' \
            --exclude='__pycache__' \
            --exclude='.git' \
            agents/ 2>/dev/null || true
        log "  snapshot: $(du -sh "$snapshot_file" | cut -f1)"
    fi

    log "=== Backup complete ==="
}

do_prune() {
    log "Pruning old backups..."

    # Prune daily backups older than RETENTION_DAYS
    local count=0
    if [[ -d "$DAILY_DIR" ]]; then
        while IFS= read -r dir; do
            rm -rf "$dir"
            count=$((count + 1))
            log "  removed daily: $(basename "$dir")"
        done < <(find "$DAILY_DIR" -maxdepth 1 -mindepth 1 -type d -mtime +${RETENTION_DAYS} 2>/dev/null || true)
    fi

    # Prune snapshots older than SNAPSHOT_RETENTION_DAYS
    if [[ -d "$SNAPSHOT_DIR" ]]; then
        while IFS= read -r file; do
            rm -f "$file"
            count=$((count + 1))
            log "  removed snapshot: $(basename "$file")"
        done < <(find "$SNAPSHOT_DIR" -name "agents_*.tar.xz" -mtime +${SNAPSHOT_RETENTION_DAYS} 2>/dev/null || true)
    fi

    log "Pruned $count old backup(s)"
}

do_list() {
    echo "=== Daily Backups ==="
    if [[ -d "$DAILY_DIR" ]]; then
        for dir in "$DAILY_DIR"/*/; do
            [[ -d "$dir" ]] || continue
            local size
            size=$(du -sh "$dir" | cut -f1)
            echo "  $(basename "$dir")  ${size}"
        done
    else
        echo "  (none)"
    fi

    echo ""
    echo "=== Snapshots ==="
    if [[ -d "$SNAPSHOT_DIR" ]]; then
        for file in "$SNAPSHOT_DIR"/agents_*.tar.xz; do
            [[ -f "$file" ]] || continue
            local size
            size=$(du -sh "$file" | cut -f1)
            echo "  $(basename "$file")  ${size}"
        done
    else
        echo "  (none)"
    fi
}

install_cron() {
    # Install cron job to run at 3 AM daily
    local script_path
    script_path=$(readlink -f "$0")
    local cron_line="0 3 * * * ${script_path} >> ${LOG_FILE} 2>&1"

    # Check if already installed
    if crontab -l 2>/dev/null | grep -qF "backup_agents.sh"; then
        echo "Cron job already installed. Current entry:"
        crontab -l | grep "backup_agents"
        return
    fi

    # Add to crontab
    (crontab -l 2>/dev/null || true; echo "$cron_line") | crontab -
    echo "Cron job installed: daily at 3 AM"
    echo "  ${cron_line}"
    echo ""
    echo "Verify with: crontab -l"
}

# ---- Main ----

case "${1:-}" in
    --install-cron)
        install_cron
        ;;
    --list)
        do_list
        ;;
    --prune)
        do_prune
        ;;
    --help|-h)
        head -20 "$0" | tail -18
        ;;
    *)
        do_backup
        do_prune
        ;;
esac