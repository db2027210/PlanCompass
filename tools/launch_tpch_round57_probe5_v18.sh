#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
P=${B}/gradient_debug_round57_probe5_recovery_v18_20260731T2310_package
R=${B}/gradient_debug_round57_probe5_recovery_v18_20260731T2310_run
BASELINE=${B}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
PY=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11

test "$(sha256sum "${P}/top5.json" | awk '{print $1}')" = d61376adfc7f6774c1cc606d0592b104005d5f82fe68fd098faa18707fc746a6
test "$(sha256sum "${P}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" | awk '{print $1}')" = 12b6ade0325541dbe1eadfd40af873fd3144fc1cbf97d9eca52f11a430731872
test "$(sha256sum "${P}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" | awk '{print $1}')" = 25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008
test "$(sha256sum "${P}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" | awk '{print $1}')" = 31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb
test "$(sha256sum "${P}/formal_measurement.py" | awk '{print $1}')" = 90feb3b7b0e9c44a820e4c9c1ff76f6204a22de0b49d9059e06615f0bb1641c6
test "$(sha256sum "${P}/formal_contracts.py" | awk '{print $1}')" = fca62960cf6328feb7528a0ecfe2e6a01e3072ff9a79742eafd6d0759dec1d8d
test "$(sha256sum "${P}/system_resource_monitor.py" | awk '{print $1}')" = c960dee360f9de2eada3908f87afaafe2290a13ccfc906cca09352bbdfcfff16
test "$(sha256sum "${P}/config.json" | awk '{print $1}')" = 0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92
test "$(sha256sum "${P}/gate.json" | awk '{print $1}')" = 02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1
test "$(sha256sum "${P}/resume_state.json" | awk '{print $1}')" = 335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = 50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
PYTHONPATH="${P}" "${PY}" \
  "${P}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" \
  --help >/dev/null
test ! -e "${R}"
exec bash "${P}/run_foreground.sh"
