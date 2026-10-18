#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive
LANE="$BASE/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01"
PACKAGE="$LANE/package"
WORKSPACE="$BASE/workspace/pg"
RUNNER_SHA256=36171f3e2046373ffe7efa23b5b8d2a040be18ebd6ca9e7bce6c6629923e365a

case "$LANE $PACKAGE" in *'/disk1'*|*'/disk2'*) exit 2 ;; esac
test "$(sha256sum "$LANE/baseline_once.py" | awk '{print $1}')" = "$RUNNER_SHA256"
test "$(sha256sum "$LANE/private_jdbc/build/formal-pg-commit-fixed.jar" | awk '{print $1}')" = e687e11704d1401bc03cbbad7f80bd51a8dfa89ae76502407800c5bcfb82d186
test "$(sha256sum "$LANE/regressions/ap13_fixed_driver_v2/summary.json" | awk '{print $1}')" = f086d2f33833137a260bf8a38af2cbf497a683ae1ec0d594a6c82b920a3b7dc0
test "$(jq -r '.all_passed' "$LANE/regressions/ap13_fixed_driver_v2/summary.json")" = true
test "$(sha256sum "$BASE/frozen_config_v9/benchmark_matrix.json" | awk '{print $1}')" = 23b20f81a129cc90d48456f01983fb911a9d76d9171af716fe028ca5edb87121
test "$(sha256sum "$LANE/common_scoring_contract.json" | awk '{print $1}')" = 47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588
test "$(sha256sum "$LANE/r0_resource_gate_contract.json" | awk '{print $1}')" = c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e
test "$(sha256sum "$LANE/stability_gate_contract.json" | awk '{print $1}')" = ef368b7e40ebf13e3ee75fef4144bb8d8ce8893ac9510c42823e16f854b488e2
test "$(sha256sum "$LANE/io_utilization_contract.json" | awk '{print $1}')" = 35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064
test ! -e "$LANE/results/shared_baseline/P0_repeat_1_attempt_25"
if docker inspect p24_postgresql_recost_gradient_descent_ap_pg5hya201v1 >/dev/null 2>&1; then exit 3; fi

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

mkdir -p "$LANE/logs" "$LANE/results" "$LANE/runtime"
cd "$WORKSPACE"
nohup setsid "$BASE/python311_c7/bin/python" -u "$LANE/baseline_once.py" \
  --lane-root "$LANE" --measurement-seconds 600 --attempt-id attempt_25 \
  --cpu-quota-cores 6.68 --memory-bytes 243269632 --io-read-bps 67108864 --io-write-bps 36700160 \
  --scoring-contract "$LANE/common_scoring_contract.json" --scoring-contract-sha256 47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588 \
  --resource-gate-contract "$LANE/r0_resource_gate_contract.json" --resource-gate-contract-sha256 c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e \
  --stability-contract "$LANE/stability_gate_contract.json" --stability-contract-sha256 ef368b7e40ebf13e3ee75fef4144bb8d8ce8893ac9510c42823e16f854b488e2 \
  --io-utilization-contract "$LANE/io_utilization_contract.json" --io-utilization-contract-sha256 35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064 \
  >"$LANE/logs/baseline_attempt_25.log" 2>&1 </dev/null &
runner_pid=$!
printf '%s\n' "$runner_pid" >"$LANE/baseline_attempt_25.pid"
nohup setsid "$BASE/python311_c7/bin/python" -u "$LANE/background_sampler.py" \
  --watched-pid "$runner_pid" --output "$LANE/logs/host_background_attempt_25.jsonl" \
  >"$LANE/logs/background_sampler_attempt_25.log" 2>&1 </dev/null &
sampler_pid=$!
printf '%s\n' "$sampler_pid" >"$LANE/background_sampler_attempt_25.pid"
printf 'baseline_pid=%s sampler_pid=%s runner_sha256=%s resources=6.68cpu_232MiB_64plus35MiBps\n' "$runner_pid" "$sampler_pid" "$RUNNER_SHA256"
