#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive
LANE="$BASE/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01"
PACKAGE="$LANE/package"
WORKSPACE="$BASE/workspace/pg"
SCORING_CONTRACT="$LANE/common_scoring_contract.json"
RESOURCE_GATE_CONTRACT="$LANE/r0_resource_gate_contract.json"
SCORING_SHA256=47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588
RESOURCE_GATE_SHA256=c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e

case "$LANE $PACKAGE" in
    *'/disk1'*|*'/disk2'*)
        printf 'forbidden storage path detected\n' >&2
        exit 2
        ;;
esac

test -f "$PACKAGE/templates/postgresql/hybench/template_manifest.json"
test -f "$PACKAGE/templates/postgresql/hybench/pgdata/PG_VERSION"
test -x "$PACKAGE/assets/hybench/hybench"
test -f "$PACKAGE/assets/hybench/stmt_postgres.toml"
test -f "$PACKAGE/assets/hybench/HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"
test "$(sha256sum "$SCORING_CONTRACT" | awk '{print $1}')" = "$SCORING_SHA256"
test "$(sha256sum "$RESOURCE_GATE_CONTRACT" | awk '{print $1}')" = "$RESOURCE_GATE_SHA256"

export PATH="$BASE/jdk/jdk-23.0.2-oracle-x64/bin:$BASE/python311_c7/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="$WORKSPACE"
export PYTHONNOUSERSITE=1
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_FROZEN_ROOT="$BASE/frozen_config_v9"
export RA_PG_TEMPLATE_ROOT="$PACKAGE/templates/postgresql"
export RA_PG_WORK_ROOT="$LANE/runtime/postgresql"
export RA_PG_OUTPUT_ROOT="$LANE/results/method_runtime"
export RA_PG_MOUNT_ROOT=/mnt/nvme1n1
export RA_PG_DISABLE_IO_CAP=1
export RA_PG_PRESERVE_RESTORE_CACHE=1
export RA_PG_CPUSET=16-23
export RA_PG_CPUSET_MEMS=0
export RA_PG_CONTAINER_SUFFIX=pg5hya201v1
export RA_PG_PORT_OFFSET=7000
export RA_PG_IMAGE=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458
export RA_PG_JDBC_JAR="$BASE/assets/postgresql-42.7.4.jar"
export RA_PG_HYBENCH_ROOT="$PACKAGE/assets/hybench"
export RA_PG_HYBENCH_LAUNCHER="$PACKAGE/assets/hybench/hybench"
export RA_PG_HYBENCH_STATEMENTS="$PACKAGE/assets/hybench/stmt_postgres.toml"
export RA_PG_HYBENCH_JAR="$PACKAGE/assets/hybench/HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"

if docker inspect p24_postgresql_recost_gradient_descent_ap_pg5hya201v1 >/dev/null 2>&1; then
    printf 'unique lane container already exists; refusing overwrite\n' >&2
    exit 3
fi
if test -e "$LANE/results/shared_baseline/P0_repeat_1_attempt_7"; then
    printf 'shared baseline attempt_7 output already exists; refusing overwrite\n' >&2
    exit 4
fi

mkdir -p "$LANE/logs" "$LANE/results" "$LANE/runtime"
cd "$WORKSPACE"
nohup setsid "$BASE/python311_c7/bin/python" -u "$LANE/baseline_once.py" \
    --lane-root "$LANE" --measurement-seconds 600 --attempt-id attempt_7 \
    --cpu-quota-cores 7.5 --memory-bytes 536870912 \
    --io-read-bps 196608 --io-write-bps 196608 \
    --scoring-contract "$SCORING_CONTRACT" \
    --scoring-contract-sha256 "$SCORING_SHA256" \
    --resource-gate-contract "$RESOURCE_GATE_CONTRACT" \
    --resource-gate-contract-sha256 "$RESOURCE_GATE_SHA256" \
    >"$LANE/logs/baseline_attempt_7.log" 2>&1 </dev/null &
runner_pid=$!
printf '%s\n' "$runner_pid" >"$LANE/baseline_attempt_7.pid"
nohup setsid "$BASE/python311_c7/bin/python" -u "$LANE/background_sampler.py" \
    --watched-pid "$runner_pid" --output "$LANE/logs/host_background_attempt_7.jsonl" \
    >"$LANE/logs/background_sampler_attempt_7.log" 2>&1 </dev/null &
sampler_pid=$!
printf '%s\n' "$sampler_pid" >"$LANE/background_sampler_attempt_7.pid"
printf 'baseline_pid=%s sampler_pid=%s output=%s scoring_sha256=%s resource_gate_sha256=%s\n' \
    "$runner_pid" "$sampler_pid" "$LANE" "$SCORING_SHA256" "$RESOURCE_GATE_SHA256"
