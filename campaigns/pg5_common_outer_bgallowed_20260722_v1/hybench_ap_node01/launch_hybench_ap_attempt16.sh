#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive
LANE="$BASE/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01"
PACKAGE="$LANE/package"
WORKSPACE="$BASE/workspace/pg"
FROZEN_BENCHMARK_MATRIX="$BASE/frozen_config_v9/benchmark_matrix.json"
FIXED_DRIVER_SOURCE="$LANE/private_jdbc/FormalPgCommitDriver.java"
FIXED_DRIVER_JAR="$LANE/private_jdbc/build/formal-pg-commit-fixed.jar"
FIXED_DRIVER_REGRESSION="$LANE/regressions/ap13_fixed_driver_v2/summary.json"
SCORING_CONTRACT="$LANE/common_scoring_contract.json"
RESOURCE_GATE_CONTRACT="$LANE/r0_resource_gate_contract.json"
STABILITY_CONTRACT="$LANE/stability_gate_contract.json"
IO_UTILIZATION_CONTRACT="$LANE/io_utilization_contract.json"
FROZEN_BENCHMARK_MATRIX_SHA256=23b20f81a129cc90d48456f01983fb911a9d76d9171af716fe028ca5edb87121
FIXED_DRIVER_SOURCE_SHA256=d1c1f7ba6edcfd31bd5132aa5c94a7b38e2850435338d01cb6ab30137d0b5109
FIXED_DRIVER_JAR_SHA256=e687e11704d1401bc03cbbad7f80bd51a8dfa89ae76502407800c5bcfb82d186
FIXED_DRIVER_REGRESSION_SHA256=f086d2f33833137a260bf8a38af2cbf497a683ae1ec0d594a6c82b920a3b7dc0
SCORING_SHA256=47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588
RESOURCE_GATE_SHA256=c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e
STABILITY_SHA256=ef368b7e40ebf13e3ee75fef4144bb8d8ce8893ac9510c42823e16f854b488e2
IO_UTILIZATION_SHA256=35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064
RUNNER_SHA256=d5beb60869683932935b9fd12e8f5ece18819fdbc22dc8f807dd0f4d241d538b
FORMAL_CONTRACTS_SHA256=9d097a12d7c79c1903a1d286b30394686f5e1683d44ded18b68baec19a04022d

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
test "$(sha256sum "$FROZEN_BENCHMARK_MATRIX" | awk '{print $1}')" = "$FROZEN_BENCHMARK_MATRIX_SHA256"
test "$(sha256sum "$FIXED_DRIVER_SOURCE" | awk '{print $1}')" = "$FIXED_DRIVER_SOURCE_SHA256"
test "$(sha256sum "$FIXED_DRIVER_JAR" | awk '{print $1}')" = "$FIXED_DRIVER_JAR_SHA256"
test "$(sha256sum "$FIXED_DRIVER_REGRESSION" | awk '{print $1}')" = "$FIXED_DRIVER_REGRESSION_SHA256"
test "$(jq -r '.status' "$FIXED_DRIVER_REGRESSION")" = passed
test "$(jq -r '.all_passed' "$FIXED_DRIVER_REGRESSION")" = true
test "$(jq -r '.results | length' "$FIXED_DRIVER_REGRESSION")" = 13
test "$(sha256sum "$SCORING_CONTRACT" | awk '{print $1}')" = "$SCORING_SHA256"
test "$(sha256sum "$RESOURCE_GATE_CONTRACT" | awk '{print $1}')" = "$RESOURCE_GATE_SHA256"
test "$(sha256sum "$STABILITY_CONTRACT" | awk '{print $1}')" = "$STABILITY_SHA256"
test "$(sha256sum "$IO_UTILIZATION_CONTRACT" | awk '{print $1}')" = "$IO_UTILIZATION_SHA256"
test "$(sha256sum "$LANE/baseline_once.py" | awk '{print $1}')" = "$RUNNER_SHA256"
test "$(sha256sum "$LANE/formal_contracts.py" | awk '{print $1}')" = "$FORMAL_CONTRACTS_SHA256"

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
if test -e "$LANE/results/shared_baseline/P0_repeat_1_attempt_16"; then
    printf 'shared baseline attempt_16 output already exists; refusing overwrite\n' >&2
    exit 4
fi

mkdir -p "$LANE/logs" "$LANE/results" "$LANE/runtime"
cd "$WORKSPACE"
nohup setsid "$BASE/python311_c7/bin/python" -u "$LANE/baseline_once.py" \
    --lane-root "$LANE" --measurement-seconds 600 --attempt-id attempt_16 \
    --cpu-quota-cores 2.0 --memory-bytes 536870912 \
    --io-read-bps 8388608 --io-write-bps 8388608 \
    --scoring-contract "$SCORING_CONTRACT" \
    --scoring-contract-sha256 "$SCORING_SHA256" \
    --resource-gate-contract "$RESOURCE_GATE_CONTRACT" \
    --resource-gate-contract-sha256 "$RESOURCE_GATE_SHA256" \
    --stability-contract "$STABILITY_CONTRACT" \
    --stability-contract-sha256 "$STABILITY_SHA256" \
    --io-utilization-contract "$IO_UTILIZATION_CONTRACT" \
    --io-utilization-contract-sha256 "$IO_UTILIZATION_SHA256" \
    >"$LANE/logs/baseline_attempt_16.log" 2>&1 </dev/null &
runner_pid=$!
printf '%s\n' "$runner_pid" >"$LANE/baseline_attempt_16.pid"
nohup setsid "$BASE/python311_c7/bin/python" -u "$LANE/background_sampler.py" \
    --watched-pid "$runner_pid" --output "$LANE/logs/host_background_attempt_16.jsonl" \
    >"$LANE/logs/background_sampler_attempt_16.log" 2>&1 </dev/null &
sampler_pid=$!
printf '%s\n' "$sampler_pid" >"$LANE/background_sampler_attempt_16.pid"
printf 'baseline_pid=%s sampler_pid=%s output=%s benchmark_matrix_sha256=%s fixed_driver_jar_sha256=%s regression_sha256=%s runner_sha256=%s\n' \
    "$runner_pid" "$sampler_pid" "$LANE" "$FROZEN_BENCHMARK_MATRIX_SHA256" \
    "$FIXED_DRIVER_JAR_SHA256" "$FIXED_DRIVER_REGRESSION_SHA256" "$RUNNER_SHA256"
