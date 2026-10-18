#!/usr/bin/env bash
set -uo pipefail

max_load="$1"
max_util="$2"
device="$3"
target_mount="$4"
shift 4

required_quiet_checks="${HYXP_QUIET_CHECKS:-5}"
check_interval="${HYXP_CHECK_INTERVAL:-30}"
minimum_free_bytes="${HYXP_MIN_FREE_BYTES:-42949672960}"
blocking_pattern="${HYXP_BLOCK_PATTERN:-gaussdb|jTPCC|com.hybench.HyBench}"
quiet_checks=0

while true; do
    timestamp="$(date '+%F %T')"
    blockers="$(pgrep -af "${blocking_pattern}" 2>/dev/null || true)"
    load_one="$(awk '{print $1}' /proc/loadavg)"
    disk_free="$(df -PB1 "${target_mount}" | awk 'NR == 2 {print $4}')"
    disk_util="$(iostat -dx 1 2 "${device}" | awk -v target="${device}" '$1 == target {value=$NF} END {print value+0}')"

    if [ -n "${blockers}" ]; then
        quiet_checks=0
        printf '[%s] waiting: blocking workload is still present\n%s\n' "${timestamp}" "${blockers}"
    elif ! awk -v current="${load_one}" -v maximum="${max_load}" \
        'BEGIN {exit !(current <= maximum)}'; then
        quiet_checks=0
        printf '[%s] waiting: load1=%s > %s\n' "${timestamp}" "${load_one}" "${max_load}"
    elif ! awk -v current="${disk_util}" -v maximum="${max_util}" \
        'BEGIN {exit !(current <= maximum)}'; then
        quiet_checks=0
        printf '[%s] waiting: %s util=%s%% > %s%%\n' \
            "${timestamp}" "${device}" "${disk_util}" "${max_util}"
    elif [ "${disk_free}" -lt "${minimum_free_bytes}" ]; then
        quiet_checks=0
        printf '[%s] waiting: free bytes=%s < %s\n' \
            "${timestamp}" "${disk_free}" "${minimum_free_bytes}"
    else
        quiet_checks=$((quiet_checks + 1))
        printf '[%s] quiet check %s/%s: load1=%s, %s util=%s%%, free=%s\n' \
            "${timestamp}" "${quiet_checks}" "${required_quiet_checks}" \
            "${load_one}" "${device}" "${disk_util}" "${disk_free}"
        if [ "${quiet_checks}" -ge "${required_quiet_checks}" ]; then
            printf '[%s] quiet gate passed; starting experiment\n' "${timestamp}"
            break
        fi
    fi
    sleep "${check_interval}"
done

exec "$@"
