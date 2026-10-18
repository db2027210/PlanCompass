#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
ROOT="${PROJECT}/pg4_relaunch_20260727/retraining/job"
CALIBRATION="${ROOT}/fresh_calibration_attempt8e_20260727"
ENDPOINT_CALIBRATION="${ROOT}/endpoint_calibration_attempt8e_20260727"
DATASET="${ROOT}/job_memory_direction_dataset_attempt8e.json"
RANKER="${ROOT}/job_memory_direction_ranker_attempt8e.json"
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

cd "${PROJECT}"
"${PYTHON}" -u shell/build_replay_direction_dataset.py \
  --input "${CALIBRATION}" \
  --input "${ENDPOINT_CALIBRATION}" \
  --output "${DATASET}" \
  --workload job \
  --target-resource memory

train_args=(
  "${PYTHON}" -u shell/train_real_replay_direction_ranker_artifact.py
  --dataset "${DATASET}"
  --output "${RANKER}"
  --workload job
  --target-resource memory
  --scenario memory
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
