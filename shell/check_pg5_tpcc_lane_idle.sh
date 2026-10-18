#!/usr/bin/env bash
set -euo pipefail

lane_lock="${PG5_TPCC_LANE_LOCK_PATH:-/tmp/pg5_tpcc_59640_exclusive.lock}"

exec 9>"${lane_lock}"
if ! flock -n 9; then
    echo "TPC-C lane is locked by another owner: ${lane_lock}" >&2
    exit 7
fi

conflicts=()
while read -r proc_pid proc_comm proc_args; do
    [[ "$proc_pid" == "$$" ]] && continue
    environment=""
    if [[ "$proc_comm" == python* ]] && [[ "$proc_args" == *"parameter_optimizer.py"* ]]; then
        environment="$(
            { tr '\0' '\n' <"/proc/${proc_pid}/environ"; } 2>/dev/null || true
        )"
    fi
    if { [[ "$proc_comm" == python* ]] \
         && [[ "$proc_args" == *"run_tpcc_sensitive_stable_point.py"* ]]; } \
        || { [[ "$proc_comm" == "bash" ]] \
             && [[ "$proc_args" == *"restore_pg5_tpcc_from_template.sh"* ]]; } \
        || { [[ "$proc_comm" == "bash" ]] \
             && [[ "$proc_args" == *"safe_restore_pg_template_db.sh"* ]]; } \
        || { [[ "$proc_comm" == "pgbench" ]] \
             && [[ "$proc_args" == *"-p 59640"* ]]; } \
        || { [[ "$proc_comm" == "java" ]] \
             && [[ "$proc_args" == *"59640"*"tpcc"* ]]; } \
        || { [[ "$proc_comm" == python* ]] \
             && [[ "$proc_args" == *"parameter_optimizer.py"* ]] \
             && [[ "$environment" == *"OPTIMIZER_CONFIG="*"tpcc"* ]]; }; then
        conflicts+=("${proc_pid} ${proc_args}")
    fi
done < <(ps -u "$UID" -o pid=,comm=,args=)

if ((${#conflicts[@]})); then
    printf 'TPC-C lane 59640 has conflicting process: %s\n' "${conflicts[@]}" >&2
    exit 8
fi

echo "TPC-C lane 59640 is idle"
