#!/usr/bin/env bash
set -Eeuo pipefail
trap '' HUP

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="${ROOT_DIR:-$(git -C "${script_dir}/.." rev-parse --show-toplevel 2>/dev/null || (cd "${script_dir}/.." && pwd))}"
cd "$repo_root"

CONFIG_FILE="${OPENCODE_MONITOR_CONFIG:-.opencode/bonect-monitor.conf}"
for ((i = 1; i <= $#; i++)); do
  if [[ "${!i}" == "--config" ]]; then
    next=$((i + 1))
    CONFIG_FILE="${!next:-}"
    break
  fi
done

if [[ -n "$CONFIG_FILE" && -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

if [[ "${OPENCODE_MONITOR_DISABLED:-0}" == "1" ]]; then
  echo "opencode Bonect monitor disabled by OPENCODE_MONITOR_DISABLED=1 (${CONFIG_FILE})"
  exit 0
fi

AGENT="${OPENCODE_AGENT:-bonect-monitor}"
MODEL="${OPENCODE_MODEL:-opencode/deepseek-v4-flash-free}"
TASK_FILE="${OPENCODE_TASK_FILE:-.opencode/bonect-task.md}"
STATE_FILE="${OPENCODE_STATE_FILE:-.opencode/bonect-monitor-state.md}"
RUNTIME_FILE="${OPENCODE_RUNTIME_FILE:-.opencode/bonect-monitor-runtime.md}"
SESSION_FILE="${OPENCODE_SESSION_FILE:-.opencode/bonect-monitor-session}"
SESSION_CYCLES_FILE="${OPENCODE_SESSION_CYCLES_FILE:-.opencode/bonect-monitor-session-cycles}"
LOG_DIR="${OPENCODE_LOG_DIR:-.opencode/logs/bonect-monitor}"
LOCK_DIR="${OPENCODE_LOCK_DIR:-.opencode/bonect-monitor.lock}"
INTERVAL_SECONDS="${OPENCODE_INTERVAL_SECONDS:-600}"
OPENCODE_TIMEOUT_SECONDS="${OPENCODE_TIMEOUT_SECONDS:-900}"
OPENCODE_DANGEROUSLY_SKIP_PERMISSIONS="${OPENCODE_DANGEROUSLY_SKIP_PERMISSIONS:-1}"
MAX_SESSION_CYCLES="${OPENCODE_MAX_SESSION_CYCLES:-12}"
TITLE_PREFIX="${OPENCODE_TITLE_PREFIX:-bonect-auto}"
BONECT_TAKEOVER_SESSION_NAME="${BONECT_TAKEOVER_SESSION_NAME:-Bonect bottleneck IO-to-Memory v5 bruteforce}"
BONECT_SESSION_SEARCH_NAME="${BONECT_SESSION_SEARCH_NAME:-}"
BONECT_SESSION_ID="${BONECT_SESSION_ID:-auto}"
BONECT_SESSION_MATCH_MODE="${BONECT_SESSION_MATCH_MODE:-contains}"
BONECT_DEPRECATED_SESSION_ID="${BONECT_DEPRECATED_SESSION_ID:-019efac5}"
CODEX_SESSION_INDEX="${CODEX_SESSION_INDEX:-$HOME/.codex/session_index.jsonl}"

RUN_ONCE=0
FORCE_NEW_SESSION=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: shell/opencode_bonect_monitor.sh [options]

Options:











  -h, --help


  OPENCODE_AGENT=bonect-monitor
  OPENCODE_MODEL=opencode/deepseek-v4-flash-free
  OPENCODE_DANGEROUSLY_SKIP_PERMISSIONS=1
  OPENCODE_TASK_FILE=.opencode/bonect-task.md
  OPENCODE_STATE_FILE=.opencode/bonect-monitor-state.md
  BONECT_TAKEOVER_SESSION_NAME="Bonect bottleneck IO-to-Memory v5 bruteforce"
  BONECT_SESSION_SEARCH_NAME=" Bonect "
  BONECT_SESSION_ID=auto
  OPENCODE_MAX_SESSION_CYCLES=12
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --once)
      RUN_ONCE=1
      shift
      ;;
    --new-session)
      FORCE_NEW_SESSION=1
      shift
      ;;
    --config)
      CONFIG_FILE="${2:?missing value for --config}"
      shift 2
      ;;
    --model)
      MODEL="${2:?missing value for --model}"
      shift 2
      ;;
    --session-name)
      BONECT_TAKEOVER_SESSION_NAME="${2:?missing value for --session-name}"
      BONECT_SESSION_SEARCH_NAME="${BONECT_SESSION_SEARCH_NAME:-$BONECT_TAKEOVER_SESSION_NAME}"
      shift 2
      ;;
    --search-name)
      BONECT_SESSION_SEARCH_NAME="${2:?missing value for --search-name}"
      shift 2
      ;;
    --codex-session)
      BONECT_SESSION_ID="${2:?missing value for --codex-session}"
      shift 2
      ;;
    --old-session)
      BONECT_DEPRECATED_SESSION_ID="${2:?missing value for --old-session}"
      shift 2
      ;;
    --interval)
      INTERVAL_SECONDS="${2:?missing value for --interval}"
      shift 2
      ;;
    --timeout)
      OPENCODE_TIMEOUT_SECONDS="${2:?missing value for --timeout}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$BONECT_SESSION_SEARCH_NAME" ]]; then
  BONECT_SESSION_SEARCH_NAME="$BONECT_TAKEOVER_SESSION_NAME"
fi

require_int() {
  local name="$1" value="$2"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "$name $value" >&2
    exit 2
  fi
}

require_int "INTERVAL_SECONDS" "$INTERVAL_SECONDS"
require_int "OPENCODE_TIMEOUT_SECONDS" "$OPENCODE_TIMEOUT_SECONDS"
require_int "MAX_SESSION_CYCLES" "$MAX_SESSION_CYCLES"

mkdir -p "$(dirname "$STATE_FILE")" "$LOG_DIR"
mkdir -p "$(dirname "$RUNTIME_FILE")"

if [[ ! -f "$TASK_FILE" ]]; then
  echo "$TASK_FILE" >&2
  exit 2
fi

if [[ ! -f "$STATE_FILE" ]]; then
  {
    echo "# Bonect Monitor State"
    echo
    echo "-  shell/opencode_bonect_monitor.sh  $(date -Iseconds)"
  } > "$STATE_FILE"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$LOCK_DIR" >&2
    echo "" >&2
    exit 1
  fi
  trap 'rm -rf "$LOCK_DIR"' EXIT
fi

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_DIR/monitor.log"
}

append_state_event() {
  {
    echo
    echo "##  $(date -Iseconds)"
    echo "- $*"
  } >> "$STATE_FILE"
}

resolve_codex_session_id() {
  if [[ -n "$BONECT_SESSION_ID" && "$BONECT_SESSION_ID" != "auto" ]]; then
    printf '%s\n' "$BONECT_SESSION_ID"
    return 0
  fi

  if [[ ! -f "$CODEX_SESSION_INDEX" ]]; then
    echo " Codex session $CODEX_SESSION_INDEX" >&2
    return 1
  fi

  local resolved
  resolved="$(
    python3 - "$CODEX_SESSION_INDEX" "$BONECT_SESSION_SEARCH_NAME" "$BONECT_DEPRECATED_SESSION_ID" "$BONECT_SESSION_MATCH_MODE" <<'PY'
import json
import sys

index_path, name, deprecated, mode = sys.argv[1:5]
matches = []
with open(index_path, "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = str(item.get("id") or "")
        thread = str(item.get("thread_name") or "")
        updated = str(item.get("updated_at") or "")
        if deprecated and sid.startswith(deprecated):
            continue
        ok = thread == name if mode == "exact" else name in thread
        if ok:
            matches.append((updated, sid, thread))

if not matches:
    sys.exit(1)

matches.sort()
print(matches[-1][1])
PY
  )" || return 1

  printf '%s\n' "$resolved"
}

write_runtime_context() {
  local resolved_session_id
  if ! resolved_session_id="$(resolve_codex_session_id)"; then
    echo " Codex session$BONECT_SESSION_SEARCH_NAME" >&2
    echo " BONECT_SESSION_ID $CONFIG_FILE  BONECT_SESSION_SEARCH_NAME" >&2
    return 1
  fi
  BONECT_RESOLVED_SESSION_ID="$resolved_session_id"

  cat > "$RUNTIME_FILE" <<EOF


$(date -Iseconds)



-  session \`${BONECT_TAKEOVER_SESSION_NAME}\`
- Codex session \`${BONECT_SESSION_SEARCH_NAME}\`
- Codex session \`${BONECT_SESSION_MATCH_MODE}\`
- Codex  session\`${resolved_session_id}\`
- Codex session id \`${BONECT_SESSION_ID}\`
-  session\`${BONECT_DEPRECATED_SESSION_ID}\`
- Codex session \`${CODEX_SESSION_INDEX}\`
- Configuration\`${CONFIG_FILE}\`
- OpenCode \`${MODEL}\`
- Codex full-access \`codex exec resume --dangerously-bypass-approvals-and-sandbox ${resolved_session_id} "Reason"\`



-  session
-  Codex session id \`${resolved_session_id}\`
- / Codex session full-access  Codex turn
-  session//
-  auth
-  opencode session  session
-  resume  session \`${BONECT_DEPRECATED_SESSION_ID}\`
-
EOF
}

extract_session_id_by_title() {
  local title="$1"
  opencode session list 2>/dev/null | awk -v title="$title" '
    NR <= 2 { next }
    $1 ~ /^ses_/ {
      id = $1
      $1 = ""
      sub(/^[[:space:]]+/, "", $0)
      if (index($0, title) == 1) {
        print id
        exit
      }
    }
  '
}

context_exhausted_output() {
  local file="$1"
  grep -Eiq 'context[_ -]?length[_ -]?exceeded|context[^[:cntrl:]]*(exhaust|limit|window|too long|length)|maximum.*token|token.*limit|too many tokens|input.*too.*long|conversation.*too.*long|request too large|prompt is too long' "$file"
}

session_cycle_count() {
  if [[ -s "$SESSION_CYCLES_FILE" ]]; then
    sed -n '1p' "$SESSION_CYCLES_FILE" | grep -E '^[0-9]+$' || echo 0
  else
    echo 0
  fi
}

record_session_cycle() {
  local new_session="$1"
  local current
  if [[ "$new_session" -eq 1 ]]; then
    echo 1 > "$SESSION_CYCLES_FILE"
    return
  fi
  current="$(session_cycle_count)"
  echo $((current + 1)) > "$SESSION_CYCLES_FILE"
}

maybe_rotate_session() {
  local current
  if [[ "$MAX_SESSION_CYCLES" -eq 0 ]]; then
    return 1
  fi
  current="$(session_cycle_count)"
  if [[ -s "$SESSION_FILE" && "$current" -ge "$MAX_SESSION_CYCLES" ]]; then
    append_state_event " ${current}  opencode session "
    rm -f "$SESSION_FILE" "$SESSION_CYCLES_FILE"
    return 0
  fi
  return 1
}

run_opencode_round() {
  local force_new="$1"
  local message="$2"
  local ts title output rc session_id new_session

  ts="$(date '+%Y%m%d_%H%M%S')"
  title="${TITLE_PREFIX}-${ts}"
  output="$LOG_DIR/run_${ts}.log"
  new_session=0
  write_runtime_context || return 1

  local cmd=(opencode run --agent "$AGENT")
  if [[ -n "$MODEL" ]]; then
    cmd+=(--model "$MODEL")
  fi
  case "$OPENCODE_DANGEROUSLY_SKIP_PERMISSIONS" in
    1|true|TRUE|yes|YES)
      cmd+=(--dangerously-skip-permissions)
      ;;
  esac
  cmd+=(--file "$TASK_FILE" --file "$STATE_FILE" --file "$RUNTIME_FILE")
  if [[ "$force_new" -eq 1 || ! -s "$SESSION_FILE" ]]; then
    cmd+=(--title "$title")
    new_session=1
  else
    session_id="$(sed -n '1p' "$SESSION_FILE")"
    cmd+=(--session "$session_id")
  fi
  cmd+=("$message")

  log "opencode output=$output new_session=$new_session"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '%q ' "${cmd[@]}"
    printf '\n'
    return 0
  fi
  ln -sfn "$(basename "$output")" "$LOG_DIR/latest.log"

  export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:1080}"
  export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:1080}"

  rc=0
  if command -v timeout >/dev/null 2>&1; then
    timeout "$OPENCODE_TIMEOUT_SECONDS" "${cmd[@]}" > "$output" 2>&1 < /dev/null || rc=$?
  else
    "${cmd[@]}" > "$output" 2>&1 < /dev/null || rc=$?
  fi

  ln -sfn "$(basename "$output")" "$LOG_DIR/latest.log"

  if [[ "$new_session" -eq 1 ]]; then
    session_id="$(extract_session_id_by_title "$title" || true)"
    if [[ -n "${session_id:-}" ]]; then
      echo "$session_id" > "$SESSION_FILE"
      log " opencode session$session_id"
    else
      log " opencode session idtitle=$title"
    fi
  fi

  record_session_cycle "$new_session"
  log "opencode rc=$rc"
  return "$rc"
}

MAIN_MESSAGE=" Bonect ConfigurationConfiguration VM  auto_continue ////// .opencode/bonect-monitor-state.mdsession id"
ROLLOVER_MESSAGE=" opencode session  token Configuration Bonect ////// .opencode/bonect-monitor-state.md"

while true; do
  if maybe_rotate_session; then
    FORCE_NEW_SESSION=1
  fi

  rc=0
  run_opencode_round "$FORCE_NEW_SESSION" "$MAIN_MESSAGE" || rc=$?
  FORCE_NEW_SESSION=0

  latest="$LOG_DIR/latest.log"
  if [[ "$DRY_RUN" -eq 0 && -e "$latest" ]] && context_exhausted_output "$latest"; then
    log "/token  opencode session"
    append_state_event " opencode /token  session"
    rm -f "$SESSION_FILE" "$SESSION_CYCLES_FILE"
    run_opencode_round 1 "$ROLLOVER_MESSAGE" || true
  elif [[ "$rc" -eq 124 ]]; then
    append_state_event "opencode  ${OPENCODE_TIMEOUT_SECONDS}s  $latest"
  elif [[ "$rc" -ne 0 ]]; then
    append_state_event "opencode rc=$rc $latest"
  fi

  if [[ "$RUN_ONCE" -eq 1 || "$DRY_RUN" -eq 1 ]]; then
    break
  fi

  log " ${INTERVAL_SECONDS}s"
  sleep "$INTERVAL_SECONDS"
done
