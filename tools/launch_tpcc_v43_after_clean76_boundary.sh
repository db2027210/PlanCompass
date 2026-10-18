#!/usr/bin/env bash
set -euo pipefail

boundary_audit=/tmp/tpcc_v42_clean_boundary_stop_20260801.audit
campaign=/home/anonymous/program/resource_adaptive/history/tpcc_final_algorithm_fresh10_v29_20260731T2005
controller_root=${campaign}/autonomous_controller_v32
formal_state=${campaign}/formal_state.json
run_root=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/autonomous_diagnostic_validation_76
summary=${run_root}/top5_real_replay_round14_diagnostic_v1/summary.json
ledger=${run_root}/strict_top5_real_replay_round14_ledger_v1.json
manifest=/tmp/tpcc_final_fresh0_autonomous_v43_score_ladder_20260801/manifest.json
manifest_sha=77520b4c26afcc9f507d7ef9ae2b437a24f5fcda2ac1bd3ed7c684d54a77fbe6
formal_state_sha=8575cb37b666afda71ab0bf4ffeb85cf007ca919ca2143709d014fac34b539ca
v23_hold=/home/anonymous/program/resource_adaptive/tools/build_tpcc_autonomous_next_top5_v23.py.boundary_hold_v43
v23=/home/anonymous/program/resource_adaptive/tools/build_tpcc_autonomous_next_top5_v23.py
v23_sha=52796ab4271dc1832fc565f0ad3973b27b91e3b5540888294c8a0e2b4610b56b
launch_audit=/tmp/tpcc_v43_clean76_launch_20260801.audit

for _ in $(seq 1 2400); do
  test -f "${boundary_audit}" && break
  sleep 2
done
test -f "${boundary_audit}"
if test -e /proc/2014482; then
  # tmux may retain the exited controller as a zombie until its pane is
  # reaped.  A zombie cannot launch another workload; any live/reused PID is
  # still rejected.
  test "$(awk '{print $3}' /proc/2014482/stat)" = Z
  test "$(awk '{print $4}' /proc/2014482/stat)" = 113338
fi
test ! -e /proc/3369007
test -f "${summary}"
test -f "${ledger}"
test "$(sha256sum "${formal_state}" | awk '{print $1}')" = "${formal_state_sha}"

jq -e '
  .real_workload_invocation_count == 5 and
  .successful_real_replay_count == 5 and
  .technical_failure_count == 0 and
  .baseline_params_restored == true and
  .baseline_snapshot_restored == true and
  .accepted_count_delta == 0 and
  .gradient_update_applied == false and
  .incumbent_updated == false and
  .range_updated == false and
  .state_commit_eligible == false and
  .rejected_candidates_update_state == false
' "${summary}" >/dev/null
jq -e '
  .real_workload_invocation_count == 5 and
  (.successful | length) == 5 and
  (.technical_failures | length) == 0
' "${ledger}" >/dev/null

test ! -e "${v23}"
test "$(sha256sum "${v23_hold}" | awk '{print $1}')" = "${v23_sha}"
mv "${v23_hold}" "${v23}"
test "$(sha256sum "${v23}" | awk '{print $1}')" = "${v23_sha}"
test "$(sha256sum "${manifest}" | awk '{print $1}')" = "${manifest_sha}"

! tmux has-session -t tpcc_fresh10_v43 2>/dev/null
tmux new-session -d -s tpcc_fresh10_v43 \
  "exec /home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u /home/anonymous/program/resource_adaptive/tools/tpcc_autonomous_target10_controller_v25.py ${manifest} --armed --manifest-sha256 ${manifest_sha} >> ${campaign}/v43_score_ladder_controller.log 2>&1"
sleep 2
tmux has-session -t tpcc_fresh10_v43
pane_pid=$(tmux list-panes -t tpcc_fresh10_v43 -F '#{pane_pid}')
{
  date '+launched_at=%F %T %Z'
  echo "pane_pid=${pane_pid}"
  echo "source_diagnostic=76"
  echo "manifest_sha256=${manifest_sha}"
  echo "formal_state_sha256=${formal_state_sha}"
} > "${launch_audit}"
