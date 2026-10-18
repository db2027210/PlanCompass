#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
ROOT="${PROJECT}/pg4_relaunch_20260727/retraining/xp"
CALIBRATION="${ROOT}/fresh_calibration_20260727"
DATASET="${ROOT}/xp_io_direction_dataset_retrained.json"
RANKER="${ROOT}/xp_io_direction_ranker_retrained.json"
HISTORY=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

cd "${PROJECT}"
"${PYTHON}" -u pg4_relaunch_20260727/run_xp_direction_calibration.py \
  --output-dir "${CALIBRATION}" \
  --blocks 7 \
  --resume

"${PYTHON}" -u shell/build_replay_direction_dataset.py \
  --input "${HISTORY}" \
  --input "${CALIBRATION}" \
  --output "${DATASET}" \
  --workload hybench_xp \
  --target-resource io

train_args=(
  "${PYTHON}" -u shell/train_real_replay_direction_ranker_artifact.py
  --dataset "${DATASET}"
  --output "${RANKER}"
  --workload hybench_xp
  --target-resource io
  --scenario io
)
for parameter in \
  seq_page_cost random_page_cost cpu_tuple_cost cpu_index_tuple_cost \
  cpu_operator_cost parallel_setup_cost parallel_tuple_cost
do
  train_args+=(--active-param "${parameter}")
done
"${train_args[@]}"

"${PYTHON}" -c '
import json
import pathlib
import sys

artifact = json.loads(pathlib.Path(sys.argv[1]).read_text())
ready = bool(artifact.get("ready"))
print("ranker_ready=" + str(ready).lower(), flush=True)
raise SystemExit(0 if ready else 42)
' "${RANKER}"
