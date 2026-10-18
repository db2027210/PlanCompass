#!/usr/bin/env bash
set -Eeuo pipefail

LANE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01
CONTAINER=p24_postgresql_recost_gradient_descent_ap_pg5hya201v1
HELPER="$LANE/resident_memory_helper"
HELPER_SHA256=2fc5f4bf0e36a1d9f6abc773d6a719d7baa6c7596ed34d346eee56a8de209ecb
RESIDENT_BYTES=11534336

cleanup_failed_launch()
{
  runner_pid=$(cat "$LANE/baseline_attempt_28.pid" 2>/dev/null || true)
  sampler_pid=$(cat "$LANE/background_sampler_attempt_28.pid" 2>/dev/null || true)
  if [ -n "$runner_pid" ]; then kill -TERM -- "-$runner_pid" 2>/dev/null || true; fi
  if [ -n "$sampler_pid" ]; then kill -TERM -- "-$sampler_pid" 2>/dev/null || true; fi
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup_failed_launch ERR

test "$(sha256sum "$HELPER" | awk '{print $1}')" = "$HELPER_SHA256"
test "$(jq -r '.immutable_target_resource' "$LANE/attempt28_resource_envelope.json")" = memory
test "$(jq -r '.resident_memory_contract.bytes' "$LANE/attempt28_resource_envelope.json")" = "$RESIDENT_BYTES"

"$LANE/launch_hybench_ap_calibration.sh" attempt_28 6.68 209715200 67108864 36700160

container_ready=false
for _ in $(seq 1 160); do
  if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -qx true; then
    container_ready=true
    break
  fi
  runner_pid=$(cat "$LANE/baseline_attempt_28.pid")
  kill -0 "$runner_pid"
  sleep 0.25
done
test "$container_ready" = true

container_id=$(docker inspect -f '{{.Id}}' "$CONTAINER")
memory_current_before=$(docker exec "$CONTAINER" sh -c 'cat /sys/fs/cgroup/memory.current')
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
memory_current_after=$(docker exec "$CONTAINER" sh -c 'cat /sys/fs/cgroup/memory.current')
helper_log=$(docker exec "$CONTAINER" cat /tmp/resident_memory_helper.log)

jq -n \
  --argjson schema_version 1 \
  --arg attempt_id attempt_28 \
  --arg target_resource memory \
  --arg container_name "$CONTAINER" \
  --arg container_id "$container_id" \
  --argjson helper_pid "$helper_pid" \
  --argjson requested_bytes "$RESIDENT_BYTES" \
  --argjson helper_vmrss_bytes "$helper_vmrss_bytes" \
  --arg helper_cgroup "$helper_cgroup" \
  --arg init_cgroup "$init_cgroup" \
  --argjson memory_current_before "$memory_current_before" \
  --argjson memory_current_after "$memory_current_after" \
  --arg helper_source_sha256 92c367f21256259f53ead73a7af61376db86e4edf2be8b980db84131b0c596d0 \
  --arg helper_binary_sha256 "$HELPER_SHA256" \
  --arg helper_log "$helper_log" \
  '{
    schema_version: $schema_version,
    attempt_id: $attempt_id,
    target_resource: $target_resource,
    container_name: $container_name,
    container_id: $container_id,
    helper_pid: $helper_pid,
    requested_bytes: $requested_bytes,
    helper_vmrss_bytes: $helper_vmrss_bytes,
    helper_cgroup: $helper_cgroup,
    container_init_cgroup: $init_cgroup,
    same_cgroup_verified: ($helper_cgroup == $init_cgroup),
    memory_current_before: $memory_current_before,
    memory_current_after: $memory_current_after,
    helper_source_sha256: $helper_source_sha256,
    helper_binary_sha256: $helper_binary_sha256,
    helper_log: $helper_log,
    cleanup_pending: true,
    fail_closed: true
  }' >"$LANE/attempt28_resident_memory_runtime.json"

trap - ERR
printf 'resident_helper_pid=%s requested_bytes=%s same_cgroup=true memory_current_before=%s memory_current_after=%s\n' \
  "$helper_pid" "$RESIDENT_BYTES" "$memory_current_before" "$memory_current_after"
