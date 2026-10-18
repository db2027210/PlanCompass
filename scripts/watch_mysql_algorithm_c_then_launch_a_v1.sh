#!/usr/bin/env bash
set -euo pipefail

# Durable handoff only: wait for all four independent Algorithm C scenes to
# reach their frozen terminal state, then invoke the frozen Algorithm A
# launcher exactly once.  This script does not control or modify Algorithm C.

ROOT=/home/anonymous/program/resource_adaptive_mysql
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
RUN_ID=${1:-v39_to_a_per_scene_20260820_attempt001}
POLL_SECONDS=${POLL_SECONDS:-60}

JOB_C="$ROOT/history/mysql_four_scene_20260809/v39_to_c_per_scene_20260820_attempt073_job_cached_preemption_resume_job/run_state.json"
TPCH_C="$ROOT/history/mysql_four_scene_20260809/v39_to_c_per_scene_20260820_attempt080_tpch_cached_preemption_resume_tpch/run_state.json"
XP_CPU_C="$ROOT/history/mysql_four_scene_20260809/v39_to_c_per_scene_20260820_attempt081_xp_cpu_cached_preemption_resume_xp_cpu/run_state.json"
XP_IO_C="$ROOT/history/mysql_four_scene_20260809/v39_to_c_per_scene_20260820_attempt082_xp_io_cached_preemption_resume_xp_io/run_state.json"

is_complete() {
  local scene=$1
  local state=$2
  local outer decision
  jq -e --arg scene "$scene" '
    .algorithm == "C"
    and .async_scene == $scene
    and .formal_scope == "mysql_only"
    and .cross_engine_comparison_prohibited == true
    and .stage == "FINISHED"
    and (.accepted_rounds[$scene] == 10)
    and (.converged[$scene] == true)
    and .formal_evidence_eligible == true
    and .post_search_stability_confirmation == false
    and .other_scene_completion_required == false
  ' "$state" >/dev/null || return 1
  if jq -e '
    .status == "COMPLETE"
    and .completion_reason == "accepted_count_reached_10"
  ' "$state" >/dev/null; then
    return 0
  fi
  jq -e '
    .status == "NON_CONVERGED"
    and .completion_reason == "candidate_space_or_max_rounds_exhausted"
  ' "$state" >/dev/null || return 1
  outer=$(jq -r '.outer_attempt' "$state")
  decision="$(dirname "$state")/outer_$(printf '%03d' "$outer")/${scene}.decision.json"
  jq -e --arg scene "$scene" --argjson outer "$outer" '
    .scene == $scene
    and .outer_attempt == $outer
    and .accepted == true
    and .accepted_rounds == 10
    and .formal_evidence_eligible == true
    and .interval_convergence.contract == "pg_accepted_count_terminal_v1"
    and .interval_convergence.converged == true
    and .interval_convergence.post_search_stability_confirmation == false
  ' "$decision" >/dev/null
}

while true; do
  ready=0
  is_complete job_memory "$JOB_C" && ready=$((ready + 1))
  is_complete tpch_sf4_memory "$TPCH_C" && ready=$((ready + 1))
  is_complete hybench_xp_cpu "$XP_CPU_C" && ready=$((ready + 1))
  is_complete hybench_xp_io "$XP_IO_C" && ready=$((ready + 1))
  printf '%s algorithm_c_complete=%d/4\n' "$(date --iso-8601=seconds)" "$ready"
  if [[ $ready -eq 4 ]]; then
    break
  fi
  sleep "$POLL_SECONDS"
done

exec "$PYTHON" "$ROOT/scripts/launch_mysql_algorithm_a_async_v1.py" \
  --run-id "$RUN_ID" \
  --runtime-preflight "$ROOT/preflight/mysql_four_scene_20260809/v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json" \
  --prepared-job "$ROOT/history/mysql_four_scene_20260809/v29_prelaunch_xp_recovery_20260819_attempt001/prepared_binding.json" \
  --accounts-job "$ROOT/history/mysql_four_scene_20260809/v29_prelaunch_xp_recovery_20260819_attempt001/benchmark_accounts_binding.json" \
  --prepared-tpch "$ROOT/history/mysql_four_scene_20260809/v29_prelaunch_xp_recovery_20260819_attempt001/prepared_binding.json" \
  --accounts-tpch "$ROOT/history/mysql_four_scene_20260809/v29_prelaunch_xp_recovery_20260819_attempt001/benchmark_accounts_binding.json" \
  --prepared-xp-cpu "$ROOT/history/mysql_four_scene_20260809/v29_prelaunch_xp_recovery_20260819_attempt001/prepared_binding.json" \
  --accounts-xp-cpu "$ROOT/history/mysql_four_scene_20260809/v29_prelaunch_xp_recovery_20260819_attempt001/benchmark_accounts_binding.json" \
  --prepared-xp-io "$ROOT/history/mysql_four_scene_20260809/v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery/prepared_binding.json" \
  --accounts-xp-io "$ROOT/history/mysql_four_scene_20260809/v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery/benchmark_accounts_binding.json" \
  --r0-job "$ROOT/history/mysql_four_scene_20260809/v36_to_c_per_scene_20260819_attempt023_job_r0/r0_binding.json" \
  --r0-tpch "$ROOT/history/mysql_four_scene_20260809/v36_to_c_per_scene_20260819_attempt023_tpch_r0/r0_binding.json" \
  --r0-xp-cpu "$ROOT/history/mysql_four_scene_20260809/v36_to_c_per_scene_20260819_attempt037_xp_cpu_r0_recovered/r0_binding.json" \
  --r0-xp-io "$ROOT/history/mysql_four_scene_20260809/v36_to_c_per_scene_20260819_attempt044_xp_io_r0_recovered/r0_binding.json" \
  --c-job-state "$JOB_C" \
  --c-tpch-state "$TPCH_C" \
  --c-xp-cpu-state "$XP_CPU_C" \
  --c-xp-io-state "$XP_IO_C"
