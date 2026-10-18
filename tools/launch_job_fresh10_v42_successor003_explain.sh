#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
OUT=${T}/autonomous_v42/successor003_formal.json
GENERATOR=${T}/package/job_exact_regime_successor_expand.py
STATE=${T}/package/formal_state.json
SOURCE=${T}/formal_batch002_promoted_validation004

test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  7fde8e94b9ee76a07f2e7759404e7ed560973dd0f9ef6b23c89de7802c087276
test "$(sha256sum "${SOURCE}/package/top5_promoted_formal.json" | awk '{print $1}')" = \
  ef258a2c4fa0f501158182d343524cd0760a4216f3cb337c810315d946fea75d
test "$(sha256sum "${SOURCE}/replay/events.jsonl" | awk '{print $1}')" = \
  175dc24b214cfc28701c06caef532dbe64d7960e03547d9e1dbdee716e5041eb
test ! -e "${OUT}"
mkdir -p "${T}/autonomous_v42"

exec python3 -u "${GENERATOR}" \
  --history "${P}/authority/ranking_analysis.json" \
  --evidence-top5 "${SOURCE}/package/top5_promoted_formal.json" \
  --events "${SOURCE}/replay/events.jsonl" \
  --formal-state "${STATE}" \
  --incumbent-events "${T}/batch001/events.jsonl" \
  --incumbent-events "${SOURCE}/replay/events.jsonl" \
  --config "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/remote_config.json" \
  --capture "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json" \
  --incumbent-point-result "${B}/formal_points/point_0333/point_result.json" \
  --code-root "${B}/gradient_debug_round7_fresh_exclusion_v1/code" \
  --exclude "${B}/job_score_span_repeat_validation_v35_20260731T1940/package/top5.json" \
  --exclude "${T}/package/top5_promoted_formal.json" \
  --exclude "${T}/diagnostic_batch002/top5.json" \
  --exclude "${T}/diagnostic_validation_batch003/top5.json" \
  --exclude "${T}/diagnostic_validation_batch004/top5.json" \
  --exclude "${SOURCE}/package/top5_promoted_formal.json" \
  --mode formal \
  --minimum-predicted-improvement 0 \
  --output "${OUT}"
