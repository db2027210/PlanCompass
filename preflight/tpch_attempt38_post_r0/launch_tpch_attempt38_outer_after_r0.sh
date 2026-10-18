#!/usr/bin/env bash
set -euo pipefail

: "${RA_TPCH_OUTER_DRIVER_SHA256:?set the audited outer-driver SHA before launch}"

lane_root=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
stage_root="$lane_root/preflight/tpch_attempt38_post_r0"
workspace="$lane_root/outer_attempt38/workspace"
authorization="$stage_root/r0_attempt38_outer_authorization.json"
driver="$workspace/run_tpch_experience_outer.py"
python_bin="$lane_root/runtime/python311/bin/python"

test -x "$python_bin"
test -f "$driver"
test "$(sha256sum "$driver" | awk '{print $1}')" = "$RA_TPCH_OUTER_DRIVER_SHA256"
test ! -e "$lane_root/runtime/tpch_attempt38_outer.pid"
if pgrep -f '[r]un_pg_weighted_sql_baseline.py.*baseline_r0_attempt_38' >/dev/null; then
    echo "attempt38 R0 runner is still active" >&2
    exit 4
fi
if pgrep -f '[r]un_tpch_experience_outer.py.*tpch_attempt38' >/dev/null; then
    echo "attempt38 outer runner is already active" >&2
    exit 5
fi

"$python_bin" -u "$stage_root/verify_tpch_attempt38_outer_ready.py" \
    --stage-root "$stage_root" \
    --workspace "$workspace" \
    --authorization "$authorization"

export PYTHONPATH="$workspace"
export PYTHONNOUSERSITE=1
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=10
export RA_PG_CPUSET=0-15,36-51
export RA_PG_CPUSET_MEMS=0
export RA_PG_CPU_CORES=8.97
export RA_PG_MEMORY_BYTES=2147483648
export RA_PG_IO_READ_BPS=15834
export RA_PG_IO_WRITE_BPS=18500
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1
export RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_TARGET_RESOURCE=memory
export RA_PG_EXPECTED_DB_QPS_SOURCE=pg_stat_statements_sum_calls_delta_per_second
export RA_PG_QUERY_COMPLETION_QPS_FALLBACK=0
export RA_PG_STARTUP_BASELINE_MAXIMUM=1
export RA_PG_STARTUP_BASELINE_EXCLUDED_FROM_CANDIDATE_BUDGET=1
export RA_PG_FORMAL_SEARCH_ACCEPTED_TARGET=10

printf '%s\n' "$$" > "$lane_root/runtime/tpch_attempt38_outer.pid"
exec "$python_bin" -u "$driver" \
    --protocol "$stage_root/tpch_attempt38_post_r0_outer_protocol.json" \
    --checkpoint "$stage_root/tpch_attempt38_outer_checkpoint.json" \
    --experience-table "$stage_root/experience_table_T_tpch_memory_v2.json" \
    --formal-engine "$workspace/formal_pg_engine.py" \
    --formal-runner "$workspace/formal_pg_point_runner.py" \
    --initial-real-replay-top-k 5 \
    --maximum-real-replay-top-k 10 \
    --accepted-shrink-target 10
