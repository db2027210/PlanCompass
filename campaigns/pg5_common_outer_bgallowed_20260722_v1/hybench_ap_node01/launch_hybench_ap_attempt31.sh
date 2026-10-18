#!/usr/bin/env bash
set -Eeuo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive
LANE="$BASE/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01"
CALIBRATION="$LANE/launch_hybench_ap_calibration.sh"
CALIBRATION_SHA256=8dc219c82dba8eadd3e47aa56f63ea5bdbc5a99cae4f992cfd8d554e5117c2fb
OLD_RUNNER_SHA256=36171f3e2046373ffe7efa23b5b8d2a040be18ebd6ca9e7bce6c6629923e365a
RUNNER_SHA256=0adefb99d8db027bc4e1edfee3b3e4e509089c5cceda8bd8b557d9efb5468f07
GATE_TEST_SHA256=b6b3bfb940da53d261e227b48a0755b4e8320789b403eb629913551ecc73e0ef
CONTAINER=p24_postgresql_recost_gradient_descent_ap_pg5hya201v1
HELPER="$LANE/resident_memory_helper"
HELPER_SHA256=2fc5f4bf0e36a1d9f6abc773d6a719d7baa6c7596ed34d346eee56a8de209ecb
RESIDENT_BYTES=31457280

cleanup_failed_launch()
{
  runner_pid=$(cat "$LANE/baseline_attempt_31.pid" 2>/dev/null || true)
  sampler_pid=$(cat "$LANE/background_sampler_attempt_31.pid" 2>/dev/null || true)
  if [ -n "$runner_pid" ]; then kill -TERM -- "-$runner_pid" 2>/dev/null || true; fi
  if [ -n "$sampler_pid" ]; then kill -TERM -- "-$sampler_pid" 2>/dev/null || true; fi
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}

read_memory_current()
{
  docker exec "$CONTAINER" sh -c '
    if [ -r /sys/fs/cgroup/memory.current ]; then
      cat /sys/fs/cgroup/memory.current
    elif [ -r /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
      cat /sys/fs/cgroup/memory/memory.usage_in_bytes
    else
      exit 1
    fi'
}

read_memory_counter_path()
{
  docker exec "$CONTAINER" sh -c '
    if [ -r /sys/fs/cgroup/memory.current ]; then
      printf %s /sys/fs/cgroup/memory.current
    elif [ -r /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
      printf %s /sys/fs/cgroup/memory/memory.usage_in_bytes
    else
      exit 1
    fi'
}

trap cleanup_failed_launch ERR
test "$(sha256sum "$CALIBRATION" | awk '{print $1}')" = "$CALIBRATION_SHA256"
test "$(sha256sum "$LANE/baseline_once.py" | awk '{print $1}')" = "$RUNNER_SHA256"
test "$(sha256sum "$LANE/test_ap13_measurement_correctness_gate.py" | awk '{print $1}')" = "$GATE_TEST_SHA256"
test "$(sha256sum "$HELPER" | awk '{print $1}')" = "$HELPER_SHA256"
test "$(jq -r '.immutable_target_resource' "$LANE/attempt31_resource_envelope.json")" = memory
test "$(jq -r '.target_attempt' "$LANE/attempt31_resource_envelope.json")" = attempt_31
test "$(jq -r '.resource_contract.memory_bytes' "$LANE/attempt31_resource_envelope.json")" = 243269632
test "$(jq -r '.resident_memory_contract.bytes' "$LANE/attempt31_resource_envelope.json")" = "$RESIDENT_BYTES"
test "$(jq -r '.private_runner_sha256' "$LANE/attempt31_resource_envelope.json")" = "$RUNNER_SHA256"
test ! -e "$LANE/results/shared_baseline/P0_repeat_1_attempt_31"
test ! -e "$LANE/baseline_attempt_31.pid"
test ! -e "$LANE/background_sampler_attempt_31.pid"
test ! -e "$LANE/logs/baseline_attempt_31.log"
test ! -e "$LANE/logs/background_sampler_attempt_31.log"
test ! -e "$LANE/logs/host_background_attempt_31.jsonl"
test ! -e "$LANE/attempt31_resident_memory_runtime.json"
if docker inspect "$CONTAINER" >/dev/null 2>&1; then exit 3; fi

export PYTHONPATH="$LANE:$BASE/workspace/pg:$BASE"
"$BASE/python311_c7/bin/python" -m py_compile \
  "$LANE/baseline_once.py" "$LANE/test_ap13_measurement_correctness_gate.py"
"$BASE/python311_c7/bin/python" -m \
  campaigns.pg5_common_outer_bgallowed_20260722_v1.hybench_ap_node01.test_ap13_measurement_correctness_gate

sed "s/RUNNER_SHA256=$OLD_RUNNER_SHA256/RUNNER_SHA256=$RUNNER_SHA256/" \
  "$CALIBRATION" | bash -s attempt_31 6.68 243269632 67108864 36700160

container_ready=false
for _ in $(seq 1 160); do
  if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true; then
    container_ready=true
    break
  fi
  runner_pid=$(cat "$LANE/baseline_attempt_31.pid")
  kill -0 "$runner_pid"
  sleep 0.25
done
test "$container_ready" = true

container_id=$(docker inspect -f '{{.Id}}' "$CONTAINER")
memory_counter_path=$(read_memory_counter_path)
memory_current_before=$(read_memory_current)
docker cp "$HELPER" "$CONTAINER:/tmp/resident_memory_helper" >/dev/null
docker exec "$CONTAINER" chmod 0555 /tmp/resident_memory_helper
docker exec "$CONTAINER" sh -c "/tmp/resident_memory_helper $RESIDENT_BYTES >/tmp/resident_memory_helper.log 2>&1 & echo \$! >/tmp/resident_memory_helper.pid"

helper_ready=false
for _ in $(seq 1 40); do
  if docker exec "$CONTAINER" grep -q '^resident_memory_ready ' /tmp/resident_memory_helper.log 2>/dev/null; then
    helper_ready=true
    break
  fi
  sleep 0.25
done
test "$helper_ready" = true

helper_pid=$(docker exec "$CONTAINER" cat /tmp/resident_memory_helper.pid)
docker exec "$CONTAINER" kill -0 "$helper_pid"
helper_cgroup=$(docker exec "$CONTAINER" cat "/proc/$helper_pid/cgroup")
init_cgroup=$(docker exec "$CONTAINER" cat /proc/1/cgroup)
test "$helper_cgroup" = "$init_cgroup"
helper_vmrss_bytes=$(docker exec "$CONTAINER" awk '/^VmRSS:/ {print $2 * 1024}' "/proc/$helper_pid/status")
memory_current_after=$(read_memory_current)
helper_log=$(docker exec "$CONTAINER" cat /tmp/resident_memory_helper.log)

jq -n \
  --argjson schema_version 1 \
  --arg attempt_id attempt_31 \
  --arg target_resource memory \
  --arg container_name "$CONTAINER" \
  --arg container_id "$container_id" \
  --argjson helper_pid "$helper_pid" \
  --argjson requested_bytes "$RESIDENT_BYTES" \
  --argjson helper_vmrss_bytes "$helper_vmrss_bytes" \
  --arg helper_cgroup "$helper_cgroup" \
  --arg init_cgroup "$init_cgroup" \
  --arg memory_counter_path "$memory_counter_path" \
  --argjson memory_current_before "$memory_current_before" \
  --argjson memory_current_after "$memory_current_after" \
  --arg helper_source_sha256 92c367f21256259f53ead73a7af61376db86e4edf2be8b980db84131b0c596d0 \
  --arg helper_binary_sha256 "$HELPER_SHA256" \
  --arg runner_sha256 "$RUNNER_SHA256" \
  --arg gate_test_sha256 "$GATE_TEST_SHA256" \
  --arg helper_log "$helper_log" \
  '{schema_version: $schema_version, attempt_id: $attempt_id, target_resource: $target_resource,
    container_name: $container_name, container_id: $container_id, helper_pid: $helper_pid,
    requested_bytes: $requested_bytes, helper_vmrss_bytes: $helper_vmrss_bytes,
    helper_cgroup: $helper_cgroup, container_init_cgroup: $init_cgroup,
    same_cgroup_verified: ($helper_cgroup == $init_cgroup), memory_counter_path: $memory_counter_path,
    memory_current_before: $memory_current_before, memory_current_after: $memory_current_after,
    helper_source_sha256: $helper_source_sha256, helper_binary_sha256: $helper_binary_sha256,
    runner_sha256: $runner_sha256, gate_test_sha256: $gate_test_sha256,
    helper_log: $helper_log, cleanup_pending: true, fail_closed: true}' \
  >"$LANE/attempt31_resident_memory_runtime.json"

trap - ERR
printf 'resident_helper_pid=%s requested_bytes=%s same_cgroup=true memory_counter=%s memory_current_before=%s memory_current_after=%s\n' \
  "$helper_pid" "$RESIDENT_BYTES" "$memory_counter_path" "$memory_current_before" "$memory_current_after"
