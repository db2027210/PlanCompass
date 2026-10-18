#!/usr/bin/env bash
set -euo pipefail

root=/home/anonymous/program/resource_adaptive
campaign=pg5_common_outer_bgallowed_20260722_v1

cat <<'EOF'
Configuration

| benchmark | Machine/ |  |  CPU/Mem/IO |  |
|---|---|---|---|---|
| JOB / Memory | node02 / nvme2n1 | 3 CPU, 2.910 GiB, IO 256 MiB/s | 39.93% / 85.99% / 36.33% | PASS |
| HyBench XP / IO | local / disk1 | 10.5 CPU, 28.316 GiB, IO 0.894 MiB/s | 34.30% / 43.49% / 87.98% | PASS |
| TPC-C / CPU | local / disk2 | 4 CPU, 5 GiB, IO 54 MiB/s | 85.90% / 42.88% / 49.14% | PASS |
| TPC-H / Memory | node04 / nvme2n1 | 6.32 CPU, 2.125 GiB, IO 0.03799 MiB/s |  |  |

 85–90% 30–50%
EOF

if [[ "${1:-}" != "--confirm" ]]; then
    echo
    echo ""
    echo "  bash $root/shell/manual_start_pg5_five_experiments.sh --confirm"
    exit 0
fi

tpcc_config="/disk2/resource_adaptive/${campaign}/tpcc_experience_sgd/attempt10_live_target_relative_20260723/config/live.json"
tpcc_python=/usr/bin/python3
tpcc_lane_lock=/tmp/pg5_tpcc_59640_exclusive.lock

if ! "$tpcc_python" -c 'import joblib, psycopg2, sklearn' >/dev/null 2>&1; then
    echo "TPC-C optimizer Python lacks joblib/psycopg2/sklearn: $tpcc_python" >&2
    exit 5
fi
PG5_TPCC_LANE_LOCK_PATH="$tpcc_lane_lock" \
    "$root/shell/check_pg5_tpcc_lane_idle.sh"

systemd-run --user --unit=pg5-live-hyxp-a37 --collect \
    "/disk1/${campaign}/lanes/hybench_xp_compute00_59630/experience_sgd/attempt37_live_target_relative_20260723/launch_with_lane_lease.sh"

systemd-run --user --unit=pg5-live-tpcc-a10 --collect \
    --setenv=OPTIMIZER_CONFIG="$tpcc_config" \
    --setenv=PYTHONUNBUFFERED=1 \
    --setenv=BONECT_OPTIMIZER_LOCK_PATH=/tmp/pg5_live_tpcc_a10.lock \
    /usr/bin/flock -n -E 73 "$tpcc_lane_lock" \
    "$tpcc_python" -u "$root/parameter_optimizer.py"

ssh anonymous@192.0.2.4 '
    set -euo pipefail
    attempt=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_6_live_target_relative_20260723
    nohup setsid bash "$attempt/launch.sh" \
        >>"$attempt/live_optimizer.log" 2>&1 </dev/null &
    printf "%s\n" "$!" >"$attempt/optimizer.pid"
'

ssh anonymous@192.0.2.7 '
    set -euo pipefail
    attempt=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04/experience_sgd/attempt46_live_target_relative_20260723
    if pgrep -f "[p]arameter_optimizer.py.*attempt46_live_target_relative_20260723|[r]un_pg_weighted_sql_baseline.py.*--port 64304" >/dev/null; then
        echo "TPC-H lane 64304 already has an active optimizer/runner" >&2
        exit 75
    fi
    unset LD_LIBRARY_PATH
    "$attempt/code/preflight/run_tpch_weighted_point_attempt46.sh" --preflight-only
    nohup setsid env \
        OPTIMIZER_CONFIG="$attempt/config/live.json" \
        PYTHONPATH="$attempt/code" \
        PYTHONUNBUFFERED=1 \
        BONECT_OPTIMIZER_LOCK_PATH=/tmp/pg5_live_tpch_a46.lock \
        /home/anonymous/recost_runtime_py311_24_11_1/bin/python3.11 -u \
        "$attempt/code/parameter_optimizer.py" \
        >>"$attempt/live_optimizer.log" 2>&1 </dev/null &
    printf "%s\n" "$!" >"$attempt/optimizer.pid"
'

echo
echo " benchmark lane  HyBench AP "
echo "HyBench XP  XAP "
