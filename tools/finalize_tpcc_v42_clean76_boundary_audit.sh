#!/usr/bin/env bash
set -euo pipefail

parent_pid=2014482
child_pid=3369007
run=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/autonomous_diagnostic_validation_76
summary=${run}/top5_real_replay_round14_diagnostic_v1/summary.json
ledger=${run}/strict_top5_real_replay_round14_ledger_v1.json
state=/home/anonymous/program/resource_adaptive/history/tpcc_final_algorithm_fresh10_v29_20260731T2005/formal_state.json
audit=/tmp/tpcc_v42_clean_boundary_stop_20260801.audit
temporary=${audit}.tmp.$$

test ! -e "/proc/${parent_pid}"
test ! -e "/proc/${child_pid}"
test ! -e "${audit}"
test "$(sha256sum "${summary}" | awk '{print $1}')" = \
  0b7a830ae6570573836d3afacfa5459775ace092fb6ae049fec05a493b7cc100
test "$(sha256sum "${ledger}" | awk '{print $1}')" = \
  0ec0d14126522a0da69cd8385bf5b19ee94b16d5dcc381b9becd93e301d37ecd
test "$(sha256sum "${state}" | awk '{print $1}')" = \
  8575cb37b666afda71ab0bf4ffeb85cf007ca919ca2143709d014fac34b539ca

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

{
  date '+verified_at=%F %T %Z'
  echo "parent_pid=${parent_pid}"
  echo "child_pid=${child_pid}"
  echo "parent_absent=true"
  echo "child_absent=true"
  echo "boundary=child_clean5_completed_parent_exited_before_next_workload"
  echo "summary_sha256=0b7a830ae6570573836d3afacfa5459775ace092fb6ae049fec05a493b7cc100"
  echo "ledger_sha256=0ec0d14126522a0da69cd8385bf5b19ee94b16d5dcc381b9becd93e301d37ecd"
  echo "formal_state_sha256=8575cb37b666afda71ab0bf4ffeb85cf007ca919ca2143709d014fac34b539ca"
} > "${temporary}"
mv "${temporary}" "${audit}"
