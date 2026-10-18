#!/usr/bin/env bash
set -uo pipefail

max_load="$1"
shift

required_quiet_checks="${HYXP_QUIET_CHECKS:-5}"
block_comm="${HYXP_BLOCK_COMM:-}"
quiet_checks=0

while true; do
    if [ -n "${block_comm}" ] && pgrep -x "${block_comm}" >/dev/null 2>&1; then
        quiet_checks=0
        printf '[%s] waiting for blocking process to exit: comm=%s\n' \
            "$(date '+%F %T')" "${block_comm}"
        sleep 60
        continue
    fi
    load_one="$(awk '{print $1}' /proc/loadavg)"
    if awk -v current="${load_one}" -v maximum="${max_load}" 'BEGIN {exit !(current <= maximum)}'; then
        quiet_checks=$((quiet_checks + 1))
        if [ "${quiet_checks}" -ge "${required_quiet_checks}" ]; then
            printf '[%s] host quiet: load1=%s <= %s for %s checks; starting command\n' \
                "$(date '+%F %T')" "${load_one}" "${max_load}" "${required_quiet_checks}"
            break
        fi
        printf '[%s] quiet check %s/%s: load1=%s <= %s\n' \
            "$(date '+%F %T')" "${quiet_checks}" "${required_quiet_checks}" \
            "${load_one}" "${max_load}"
    else
        quiet_checks=0
        printf '[%s] waiting for quiet host: load1=%s > %s\n' "$(date '+%F %T')" "${load_one}" "${max_load}"
    fi
    sleep 60
done

exec "$@"
