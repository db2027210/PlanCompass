#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
P=${B}/gradient_debug_round54_packagingfix_recovery_v12_20260731T2049_package
R=${B}/gradient_debug_round54_packagingfix_recovery_v12_20260731T2049_run
BASELINE=${B}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
SESSION=tpch_round54_recovery_v12

test "$(sha256sum "${P}/top5.json" | awk '{print $1}')" = eda4bc2d56a59d80d08aa97795da36fe384051758ca934d08827b597b7461f82
test "$(sha256sum "${P}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" | awk '{print $1}')" = 12b6ade0325541dbe1eadfd40af873fd3144fc1cbf97d9eca52f11a430731872
test "$(sha256sum "${P}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" | awk '{print $1}')" = 25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008
# The wrapper resolves this driver relative to its own sealed package.  Verify
# it before any physical slot is reserved.
test "$(sha256sum "${P}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" | awk '{print $1}')" = 31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb
test "$(sha256sum "${P}/config.json" | awk '{print $1}')" = 0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92
test "$(sha256sum "${P}/gate.json" | awk '{print $1}')" = 02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1
test "$(sha256sum "${P}/resume_state.json" | awk '{print $1}')" = 335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = 50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
test ! -e "${R}"
if tmux has-session -t "${SESSION}" 2>/dev/null; then
    exit 73
fi

test -x "${P}/run_foreground.sh"
tmux new-session -d -s "${SESSION}" \
  "exec bash '${P}/run_foreground.sh' > '${P}/driver.log' 2>&1"
sleep 2
PID="$(tmux list-panes -t "${SESSION}" -F '#{pane_pid}')"
test -n "${PID}"
kill -0 "${PID}"
mkdir -p "${R}"
printf '%s\n' "${PID}" > "${R}/driver.pid"
printf 'TPCH_ROUND54_STARTED pid=%s\n' "${PID}"
