#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
P=${B}/job_round8_corrected_top5_v6_20260731
TARGET=${B}/job_final_algorithm_fresh10_v36_20260731T2020
PACKAGE=${TARGET}/package

cd "${PACKAGE}"
exec python3 -u "${PACKAGE}/job_formal_target10_batch_controller.py" \
  --selection "${PACKAGE}/top5_promoted_formal.json" \
  --selection-sha256 b80aefae16815373a5648007eda9ea35b648da5f75e0abf2050ce032f5fe1f6d \
  --gate "${PACKAGE}/entry_gate.json" \
  --gate-sha256 a8f7a6e540b8c727003d949a7304fced0f8d307197c3a45e72a13aba5be0e873 \
  --state "${PACKAGE}/formal_state.json" \
  --state-sha256 57a5ceef1e62770d316b0baafc146542ab5ab47b5e774d160928aa0b49e4f0a7 \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --replay-root "${TARGET}/batch001" \
  --global-counter "${B}/replay_counter.txt" \
  --formal-replay-counter "${B}/formal_search_replay_counter.txt" \
  --accepted-counter "${PACKAGE}/formal_search_accepted_counter.txt"
