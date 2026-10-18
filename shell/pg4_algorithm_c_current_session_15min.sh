#!/usr/bin/env bash

set -Eeuo pipefail

readonly DEFAULT_INTERVAL_SECONDS=1800
readonly DEFAULT_DELIVERY_TIMEOUT_SECONDS=45
readonly DEFAULT_MESSAGE=''
readonly STATE_DIR="${PG4_SESSION_MONITOR_STATE_DIR:-/tmp/pg4_algorithm_c_current_session_15min}"
readonly PID_FILE="${STATE_DIR}/monitor.pid"
readonly THREAD_FILE="${STATE_DIR}/thread_id"
readonly NEXT_RUN_FILE="${STATE_DIR}/next_run_epoch_s"
readonly LOG_FILE="${STATE_DIR}/monitor.log"
readonly LOCK_FILE="${STATE_DIR}/monitor.lock"
readonly TMUX_SESSION="${PG4_SESSION_MONITOR_TMUX_SESSION:-pg4_algorithm_c_15min}"

SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "${SCRIPT_PATH}")"
APP_SERVER_SENDER="${SCRIPT_DIR}/codex_app_server_thread_message.py"
APP_SERVER_SOCKET="${CODEX_APP_SERVER_SOCKET:-${CODEX_HOME:-/home/anonymous/.codex}/app-server-control/app-server-control.sock}"
INTERVAL_SECONDS="${PG4_CHECK_INTERVAL_SECONDS:-${DEFAULT_INTERVAL_SECONDS}}"
DELIVERY_TIMEOUT_SECONDS="${PG4_CHECK_DELIVERY_TIMEOUT_SECONDS:-${DEFAULT_DELIVERY_TIMEOUT_SECONDS}}"
MESSAGE="${PG4_CHECK_MESSAGE:-${DEFAULT_MESSAGE}}"

usage() {
    cat <<'EOF'
Usage:
  shell/pg4_algorithm_c_current_session_15min.sh start [THREAD_ID]
  shell/pg4_algorithm_c_current_session_15min.sh status
  shell/pg4_algorithm_c_current_session_15min.sh stop
  shell/pg4_algorithm_c_current_session_15min.sh log
  shell/pg4_algorithm_c_current_session_15min.sh once [THREAD_ID]
  shell/pg4_algorithm_c_current_session_15min.sh dry-run [THREAD_ID]

The daemon waits 1800 seconds before its first delivery. Delivery is
fire-and-forget after App Server accepts the turn, with a short hard timeout;
the monitor never waits for the repair turn itself to finish.
EOF
}

timestamp() {
    date '+%Y-%m-%dT%H:%M:%S%z'
}

log_line() {
    printf '%s %s\n' "$(timestamp)" "$*"
}

ensure_state_dir() {
    mkdir -p "${STATE_DIR}"
}

resolve_thread_id() {
    local explicit="${1:-}"
    if [[ -n "${explicit}" ]]; then
        printf '%s\n' "${explicit}"
    elif [[ -n "${CODEX_THREAD_ID:-}" ]]; then
        printf '%s\n' "${CODEX_THREAD_ID}"
    elif [[ -s "${THREAD_FILE}" ]]; then
        tr -d '[:space:]' < "${THREAD_FILE}"
    else
        return 1
    fi
}

validate_common() {
    local thread_id="$1"
    if [[ ! -f "${APP_SERVER_SENDER}" ]]; then
        log_line "ERROR App Server sender not found: ${APP_SERVER_SENDER}"
        return 1
    fi
    if [[ ! -S "${APP_SERVER_SOCKET}" ]]; then
        log_line "ERROR Codex App Server socket not found: ${APP_SERVER_SOCKET}"
        return 1
    fi
    if [[ ! "${thread_id}" =~ ^[0-9a-fA-F-]{36}$ ]]; then
        log_line "ERROR invalid Codex thread id: ${thread_id}"
        return 1
    fi
    if [[ ! "${INTERVAL_SECONDS}" =~ ^[0-9]+$ || "${INTERVAL_SECONDS}" -lt 60 ]]; then
        log_line "ERROR interval must be an integer of at least 60 seconds"
        return 1
    fi
    if [[ ! "${DELIVERY_TIMEOUT_SECONDS}" =~ ^[0-9]+$ || "${DELIVERY_TIMEOUT_SECONDS}" -lt 10 ]]; then
        log_line "ERROR delivery timeout must be an integer of at least 10 seconds"
        return 1
    fi
    if ! command -v timeout >/dev/null 2>&1; then
        log_line "ERROR timeout command is required"
        return 1
    fi
}

deliver_once() {
    local thread_id="$1"
    local started_at
    local returncode
    started_at="$(date +%s)"
    log_line "DELIVERY_START thread=${thread_id} message=${MESSAGE}"
    set +e
    timeout --signal=TERM --kill-after=5s "${DELIVERY_TIMEOUT_SECONDS}s" \
        python3 "${APP_SERVER_SENDER}" \
        --thread-id "${thread_id}" \
        --message "${MESSAGE}" \
        --timeout-s "$((DELIVERY_TIMEOUT_SECONDS - 5))" \
        --socket "${APP_SERVER_SOCKET}"
    returncode=$?
    set -e
    log_line "DELIVERY_END thread=${thread_id} rc=${returncode} elapsed_s=$(( $(date +%s) - started_at ))"
    return "${returncode}"
}

run_daemon() {
    local thread_id="$1"
    local next_epoch
    ensure_state_dir
    validate_common "${thread_id}"
    exec 9>"${LOCK_FILE}"
    if ! flock -n 9; then
        log_line "ERROR another monitor already holds ${LOCK_FILE}"
        return 1
    fi
    printf '%s\n' "$$" > "${PID_FILE}"
    printf '%s\n' "${thread_id}" > "${THREAD_FILE}"
    cleanup() {
        if [[ -s "${PID_FILE}" ]] && [[ "$(tr -d '[:space:]' < "${PID_FILE}")" == "$$" ]]; then
            rm -f "${PID_FILE}" "${NEXT_RUN_FILE}"
        fi
        log_line "DAEMON_STOP thread=${thread_id} pid=$$"
    }
    trap cleanup EXIT
    trap 'exit 0' INT TERM HUP
    log_line "DAEMON_START thread=${thread_id} pid=$$ interval_s=${INTERVAL_SECONDS} delivery_mode=fire_and_forget delivery_timeout_s=${DELIVERY_TIMEOUT_SECONDS}"
    while true; do
        next_epoch=$(( $(date +%s) + INTERVAL_SECONDS ))
        printf '%s\n' "${next_epoch}" > "${NEXT_RUN_FILE}"
        log_line "WAIT next_run_epoch_s=${next_epoch}"
        sleep "${INTERVAL_SECONDS}"
        deliver_once "${thread_id}" || true
    done
}

is_running() {
    local pid
    [[ -s "${PID_FILE}" ]] || return 1
    pid="$(tr -d '[:space:]' < "${PID_FILE}")"
    [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
    kill -0 "${pid}" 2>/dev/null
}

start_daemon() {
    local thread_id="$1"
    local child_pid
    ensure_state_dir
    validate_common "${thread_id}"
    if is_running; then
        log_line "ALREADY_RUNNING pid=$(tr -d '[:space:]' < "${PID_FILE}") thread=$(tr -d '[:space:]' < "${THREAD_FILE}")"
        return 0
    fi
    if ! command -v tmux >/dev/null 2>&1; then
        log_line "ERROR tmux is required so the monitor survives command-session cleanup"
        return 1
    fi
    if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
        log_line "ERROR stale tmux session exists: ${TMUX_SESSION}; inspect it before replacing"
        return 1
    fi
    printf '%s\n' "${thread_id}" > "${THREAD_FILE}"
    tmux new-session -d -s "${TMUX_SESSION}" \
        "${SCRIPT_PATH} run-logged ${thread_id}"
    sleep 1
    if ! is_running; then
        log_line "ERROR monitor failed to start; inspect ${LOG_FILE}"
        return 1
    fi
    child_pid="$(tr -d '[:space:]' < "${PID_FILE}")"
    log_line "STARTED pid=${child_pid} thread=${thread_id} log=${LOG_FILE}"
}

show_status() {
    local pid=""
    local thread_id=""
    local next_epoch=""
    [[ -s "${PID_FILE}" ]] && pid="$(tr -d '[:space:]' < "${PID_FILE}")"
    [[ -s "${THREAD_FILE}" ]] && thread_id="$(tr -d '[:space:]' < "${THREAD_FILE}")"
    [[ -s "${NEXT_RUN_FILE}" ]] && next_epoch="$(tr -d '[:space:]' < "${NEXT_RUN_FILE}")"
    if is_running; then
        log_line "RUNNING pid=${pid} thread=${thread_id} interval_s=${INTERVAL_SECONDS} delivery_mode=fire_and_forget delivery_timeout_s=${DELIVERY_TIMEOUT_SECONDS} next_run_epoch_s=${next_epoch} tmux=${TMUX_SESSION} log=${LOG_FILE}"
    else
        log_line "STOPPED last_pid=${pid:-none} thread=${thread_id:-none} log=${LOG_FILE}"
        return 1
    fi
}

stop_daemon() {
    local pid
    local pgid
    if ! is_running; then
        log_line "STOPPED no active monitor"
        return 0
    fi
    pid="$(tr -d '[:space:]' < "${PID_FILE}")"
    pgid="$(ps -o pgid= -p "${pid}" | tr -d '[:space:]')"
    if [[ "${pgid}" =~ ^[0-9]+$ ]]; then
        # Bash defers a trapped TERM while waiting for sleep.  Signal the
        # monitor's dedicated process group so sleep exits immediately and
        # the daemon can run its cleanup trap.
        kill -TERM -- "-${pgid}"
        log_line "STOP_REQUESTED pid=${pid} pgid=${pgid}"
    else
        kill -TERM "${pid}"
        log_line "STOP_REQUESTED pid=${pid} pgid=unknown"
    fi
}

replace_after_child() {
    local watched_pid="$1"
    local thread_id="$2"
    local old_pid=""
    local attempts=0
    if [[ ! "${watched_pid}" =~ ^[0-9]+$ ]]; then
        log_line "ERROR invalid watched PID: ${watched_pid}"
        return 2
    fi
    log_line "REPLACE_WAIT watched_pid=${watched_pid} thread=${thread_id}"
    while kill -0 "${watched_pid}" 2>/dev/null; do
        sleep 5
    done
    [[ -s "${PID_FILE}" ]] && old_pid="$(tr -d '[:space:]' < "${PID_FILE}")"
    stop_daemon
    while [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; do
        sleep 1
        attempts=$((attempts + 1))
        if (( attempts >= 60 )); then
            log_line "ERROR old monitor did not exit pid=${old_pid}"
            return 1
        fi
    done
    start_daemon "${thread_id}"
    log_line "REPLACE_COMPLETE old_pid=${old_pid:-none} thread=${thread_id}"
}

main() {
    local command="${1:-status}"
    local explicit_thread="${2:-}"
    local thread_id
    case "${command}" in
        start)
            thread_id="$(resolve_thread_id "${explicit_thread}")" || {
                log_line "ERROR pass THREAD_ID or export CODEX_THREAD_ID"
                return 2
            }
            start_daemon "${thread_id}"
            ;;
        run)
            thread_id="$(resolve_thread_id "${explicit_thread}")" || return 2
            run_daemon "${thread_id}"
            ;;
        run-logged)
            thread_id="$(resolve_thread_id "${explicit_thread}")" || return 2
            run_daemon "${thread_id}" >> "${LOG_FILE}" 2>&1
            ;;
        once)
            thread_id="$(resolve_thread_id "${explicit_thread}")" || return 2
            validate_common "${thread_id}"
            deliver_once "${thread_id}"
            ;;
        dry-run)
            thread_id="$(resolve_thread_id "${explicit_thread}")" || return 2
            validate_common "${thread_id}"
            log_line "DRY_RUN_OK thread=${thread_id} interval_s=${INTERVAL_SECONDS} delivery_mode=fire_and_forget delivery_timeout_s=${DELIVERY_TIMEOUT_SECONDS} message=${MESSAGE}"
            ;;
        status)
            show_status
            ;;
        stop)
            stop_daemon
            ;;
        replace-after)
            if [[ -z "${explicit_thread}" || -z "${3:-}" ]]; then
                log_line "ERROR replace-after requires WATCHED_PID THREAD_ID"
                return 2
            fi
            replace_after_child "${explicit_thread}" "${3}"
            ;;
        log)
            ensure_state_dir
            touch "${LOG_FILE}"
            tail -f "${LOG_FILE}"
            ;;
        -h|--help|help)
            usage
            ;;
        *)
            usage >&2
            return 2
            ;;
    esac
}

main "$@"
