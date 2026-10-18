#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
OUT=${T}/successor_batch002_top5.json

test ! -e "${OUT}"
exec python3 -u "${T}/package/job_exact_regime_successor_expand.py" \
  --history "${B}/job_round8_corrected_top5_v6_20260731/authority/ranking_analysis.json" \
  --evidence-top5 "${B}/job_score_span_repeat_validation_v35_20260731T1940/package/top5.json" \
  --events "${B}/job_score_span_repeat_validation_v35_20260731T1940/replay/events.jsonl" \
  --formal-state "${T}/package/formal_state.json" \
  --incumbent-events "${T}/batch001/events.jsonl" \
  --config "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/remote_config.json" \
  --capture "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json" \
  --incumbent-point-result "${B}/formal_points/point_0333/point_result.json" \
  --code-root "${B}/gradient_debug_round7_fresh_exclusion_v1/code" \
  --exclude "${B}/job_score_span_repeat_validation_v35_20260731T1940/package/top5.json" \
  --exclude "${T}/package/top5_promoted_formal.json" \
  --mode formal \
  --minimum-predicted-improvement 0 \
  --output "${OUT}"
