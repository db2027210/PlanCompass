#!/usr/bin/env bash
set -euo pipefail

CONFIG="${1:-config/binary_plan_tuning_hybenchxp.json}"
OUTPUT_DIR="${2:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/hyxp_docker_full_bruteforce_warmup_20260704_014438/none_cgroup_physio_pilots_$(date +%Y%m%dT%H%M%S)}"
IO_MODE="${IO_MODE:-cgroup}"
NO_RESTORE="${NO_RESTORE:-0}"

mkdir -p "${OUTPUT_DIR}"

run_probe() {
  local worker="$1"
  local label="$2"
  shift 2
  local restore_args=()
  if [[ "${NO_RESTORE}" == "1" || "${NO_RESTORE}" == "true" ]]; then
    restore_args=(--no-restore)
  fi
  python3 shell/probe_hybenchxp_baseline.py \
    --config "${CONFIG}" \
    --output-dir "${OUTPUT_DIR}" \
    --target none \
    --worker "${worker}" \
    --label "${label}" \
    --xp-run-mins 2 \
    --io-mode "${IO_MODE}" \
    --check-none-gate \
    "${restore_args[@]}" \
    "$@" \
    > "${OUTPUT_DIR}/${label}.stdout.log" \
    2> "${OUTPUT_DIR}/${label}.stderr.log"
}

run_probe cpu_mem_w1 none_cgroup_w1_xtp6_xap1_default_2m --xtpclient 6 --xapclient 1 &
run_probe cpu_mem_w2 none_cgroup_w2_xtp4_xap2_default_2m --xtpclient 4 --xapclient 2 &
run_probe cpu_mem_w3 none_cgroup_w3_xtp2_xap4_iq56_2m --xtpclient 2 --xapclient 4 --ap-query-ids 5,6 --ap-query-weights 30,70 &
run_probe cpu_mem_w4 none_cgroup_w4_xtp1_xap6_iq6_2m --xtpclient 1 --xapclient 6 --ap-query-ids 6 --ap-query-weights 100 &
run_probe cpu_mem_w5 none_cgroup_w5_xtp6_xap2_writeheavy_2m --xtpclient 6 --xapclient 2 --at-percent-mix 20,20,20,20,10,10 &
run_probe cpu_mem_w6 none_cgroup_w6_xtp8_xap1_default_2m --xtpclient 8 --xapclient 1 &
run_probe cpu_mem_w7 none_cgroup_w7_xtp3_xap3_iq56_2m --xtpclient 3 --xapclient 3 --ap-query-ids 5,6 --ap-query-weights 50,50 &
run_probe cpu_mem_w8 none_cgroup_w8_xtp2_xap5_iq65_2m --xtpclient 2 --xapclient 5 --ap-query-ids 6,5 --ap-query-weights 80,20 &

wait

echo "${OUTPUT_DIR}"
