#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
P=${B}/gradient_debug_round54_packagingfix_recovery_v12_20260731T2049_package
R=${B}/gradient_debug_round54_packagingfix_recovery_v12_20260731T2049_run

exec /mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11 -u \
  "${P}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" \
  --diagnostic-only \
  --selection "${P}/top5.json" \
  --selection-sha256 eda4bc2d56a59d80d08aa97795da36fe384051758ca934d08827b597b7461f82 \
  --gate-summary "${P}/gate.json" \
  --gate-summary-sha256 02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1 \
  --baseline-summary "${B}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json" \
  --baseline-summary-sha256 50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67 \
  --config "${P}/config.json" \
  --config-sha256 0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92 \
  --workload "${P}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" \
  --workload-sha256 25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008 \
  --accepted-counter "${B}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt" \
  --resume-state "${P}/resume_state.json" \
  --resume-state-sha256 335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4 \
  --selection-start-rank 1 \
  --maximum-real-replay-invocations 5 \
  --output "${R}" \
  --container pg5_tpch_ap_slot_node01_20260726 \
  --database template_fullgrid_tpch_pg5 \
  --timeout-seconds 900
