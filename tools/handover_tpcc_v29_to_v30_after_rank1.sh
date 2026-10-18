#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "usage: $0 OLD_CONTROLLER_PID SERIAL_RUNNER_PID SOURCE_MANIFEST OUTPUT_MANIFEST" >&2
  exit 64
fi

old_controller_pid="$1"
serial_runner_pid="$2"
source_manifest="$3"
output_manifest="$4"

project_root="/home/anonymous/program/resource_adaptive"
python_bin="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
run_root="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/autonomous_diagnostic_validation_154"
replay_root="$run_root/top5_real_replay_round14_diagnostic_v1"
ledger="$run_root/strict_top5_real_replay_round14_ledger_v1.json"
summary="$replay_root/summary.json"
events="$replay_root/events.jsonl"
top5="$run_root/top5.json"
baseline="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/candidate_measurements/point_20260729_022349_1052396/summary.json"
frozen_runner="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round14_robust_regime_interval_v2_20260729T1824/run_tpcc_round11_calibrated_top5_strict_serial.py"
formal_state="$project_root/history/tpcc_final_algorithm_fresh10_v29_20260731T2005/formal_state.json"
controller_state="$project_root/history/tpcc_final_algorithm_fresh10_v29_20260731T2005/autonomous_controller_v32/controller_state.json"

controller_cmd="$(tr '\0' ' ' < "/proc/$old_controller_pid/cmdline")"
runner_cmd="$(tr '\0' ' ' < "/proc/$serial_runner_pid/cmdline")"
[[ "$controller_cmd" == *"tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint.py"* ]]
[[ "$runner_cmd" == *"autonomous_diagnostic_validation_154"* ]]
[[ "$runner_cmd" == *"run_tpcc_round11_calibrated_top5_strict_serial.py"* ]]
[[ "$(sha256sum "$formal_state" | awk '{print $1}')" == "0996b26e26688a276a820e06474167e714786ab97919619a70f813971d26f0c7" ]]
[[ "$(jq -r '.accepted_count' "$formal_state")" == "2" ]]

# Freeze only the old controller.  Its already-running rank1 child tree keeps
# running naturally; the controller can no longer retire or relaunch anything.
kill -STOP "$old_controller_pid"

runner_stopped=0
for _ in $(seq 1 18000); do
  physical="$(jq -r '.real_workload_invocation_count' "$ledger")"
  valid="$(jq -r '.successful | length' "$ledger")"
  technical="$(jq -r '.technical_failures | length' "$ledger")"
  if (( physical > 1 )); then
    kill -CONT "$old_controller_pid"
    echo "rank2 was already reserved; safe rank1 boundary was missed" >&2
    exit 75
  fi
  if [[ "$physical" == "1" && "$valid" == "1" && "$technical" == "0" ]]; then
    kill -STOP "$serial_runner_pid"
    runner_stopped=1
    break
  fi
  sleep 0.05
done
[[ "$runner_stopped" == "1" ]]

# Re-read after SIGSTOP.  Any rank2 reservation makes this window unusable; in
# that case resume both old processes and let the already-reserved call finish.
physical="$(jq -r '.real_workload_invocation_count' "$ledger")"
valid="$(jq -r '.successful | length' "$ledger")"
technical="$(jq -r '.technical_failures | length' "$ledger")"
rank2_reserved="$(jq -s '[.[] | select(.event == "tpcc_gradient_real_replay_workload_invocation_reserved" and .rank == 2)] | length' "$events")"
if [[ "$physical" != "1" || "$valid" != "1" || "$technical" != "0" || "$rank2_reserved" != "0" ]]; then
  kill -CONT "$serial_runner_pid"
  kill -CONT "$old_controller_pid"
  echo "rank1-only boundary validation failed; old v29 resumed" >&2
  exit 75
fi

# If the runner had just entered the next database restore, allow that child to
# finish while the Python parent is frozen.  Zombies are harmless and will be
# reaped when the parent receives the controlled termination signal.
for _ in $(seq 1 1200); do
  non_zombie_children="$(ps -o stat= --ppid "$serial_runner_pid" | awk '$1 !~ /^Z/ {count++} END {print count+0}')"
  [[ "$non_zombie_children" == "0" ]] && break
  sleep 0.05
done
[[ "$non_zombie_children" == "0" ]]

# The frozen runner's installed SIGTERM handler writes a partial summary and
# executes its final baseline snapshot+parameter restore.  Rank1 itself has
# already completed and is never signalled.
kill -TERM "$serial_runner_pid"
kill -CONT "$serial_runner_pid"
for _ in $(seq 1 1800); do
  kill -0 "$serial_runner_pid" 2>/dev/null || break
  sleep 0.1
done
if kill -0 "$serial_runner_pid" 2>/dev/null; then
  echo "serial runner did not reach controlled handover exit" >&2
  exit 76
fi

[[ -f "$summary" ]]
[[ "$(jq -r '.real_workload_invocation_count' "$summary")" == "1" ]]
[[ "$(jq -r '.successful_real_replay_count' "$summary")" == "1" ]]
[[ "$(jq -r '.technical_failure_count' "$summary")" == "0" ]]
[[ "$(jq -r '.baseline_params_restored' "$summary")" == "true" ]]
[[ "$(jq -r '.baseline_snapshot_restored' "$summary")" == "true" ]]
[[ "$(jq -r '.accepted_count_delta' "$summary")" == "0" ]]
[[ "$(jq -r '.incumbent_updated' "$summary")" == "false" ]]
[[ "$(jq -r '.range_updated' "$summary")" == "false" ]]
[[ "$(jq -r '.gradient_update_applied' "$summary")" == "false" ]]
[[ "$(jq -r '.fatal_error | startswith("TerminationRequested:")' "$summary")" == "true" ]]
[[ "$(jq -r '.real_workload_invocation_count' "$ledger")" == "1" ]]
[[ "$(jq -r '.successful | map(.rank) == [1]' "$ledger")" == "true" ]]

partial_audit="$project_root/history/tpcc_final_algorithm_fresh10_v29_20260731T2005/autonomous_controller_v32/prepare_154/rank1_handover_partial_summary.json"
cp "$summary" "$partial_audit"
partial_sha="$(sha256sum "$partial_audit" | awk '{print $1}')"

# The old controller is still stopped in subprocess.wait and must never process
# the intentional rc=2 as a failed batch.  The controller state remains intact.
kill -KILL "$old_controller_pid"

[[ "$(sha256sum "$top5" | awk '{print $1}')" == "b7d16fc0e9fbd96c0daac19a26859a8d0c9902e5500de65a925c6ed06c06985f" ]]
[[ "$(sha256sum "$frozen_runner" | awk '{print $1}')" == "df1f9f9b43656214cdccace0e3614fada7d2efc7c7679942da6478ac0f042d68" ]]
[[ "$(sha256sum "$baseline" | awk '{print $1}')" == "9c0f0d083796bef1ec8d49c4aa71b1d8e904fca43dbfb49a1708c0c40ccd7356" ]]

# Resume the exact same sealed batch.  The frozen runner validates the existing
# ledger/events, sees rank1 successful+invoked, and selects only ranks2..5 with
# remaining physical budget 4.  It cannot rerun rank1 or exceed total cap 5.
env PYTHONPATH="$project_root:/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round14_robust_regime_interval_v2_20260729T1824" \
  "$python_bin" -u "$frozen_runner" "$top5" "$replay_root" "$baseline"

[[ "$(jq -r '.real_workload_invocation_count' "$ledger")" == "5" ]]
[[ "$(jq -r '.successful | map(.rank) == [1,2,3,4,5]' "$ledger")" == "true" ]]
[[ "$(jq -r '.technical_failures | length' "$ledger")" == "0" ]]
[[ "$(jq -r '.baseline_params_restored and .baseline_snapshot_restored' "$summary")" == "true" ]]
[[ "$(jq -r '.accepted_count_delta == 0 and (.incumbent_updated|not) and (.range_updated|not) and (.gradient_update_applied|not)' "$summary")" == "true" ]]
[[ "$(sha256sum "$formal_state" | awk '{print $1}')" == "0996b26e26688a276a820e06474167e714786ab97919619a70f813971d26f0c7" ]]

mkdir -p "$(dirname "$output_manifest")"
"$python_bin" "$project_root/tools/prepare_tpcc_v30_score_stability_handover.py" \
  "$source_manifest" "$output_manifest"
manifest_sha="$(sha256sum "$output_manifest" | awk '{print $1}')"

"$python_bin" -m unittest tests.test_tpcc_score_stability_v30
"$python_bin" "$project_root/tools/preflight_tpcc_v30_score_stability.py" \
  "$output_manifest" --manifest-sha256 "$manifest_sha"

echo "rank1_partial_summary_sha256=$partial_sha" >&2
echo "v30_manifest_sha256=$manifest_sha" >&2

cd "$project_root"
exec "$python_bin" -u tools/tpcc_autonomous_target10_controller_v30_score_stability.py \
  "$output_manifest" --armed --manifest-sha256 "$manifest_sha"
