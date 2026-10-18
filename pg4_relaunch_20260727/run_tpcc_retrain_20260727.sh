#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
ATTEMPT=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt13b_direction_retrain_profile_v2_20260727
CALIBRATION="${ATTEMPT}/calibration"
DATASET="${ATTEMPT}/tpcc_cpu_direction_dataset.json"
RANKER="${ATTEMPT}/tpcc_cpu_direction_ranker.json"
PYTHON=/home/anonymous/miniconda3/bin/python3

mkdir -p "${ATTEMPT}"
cd "${PROJECT}"

# This is a PostgreSQL pilot retraining run on the frozen TPC-C lane.  It is
# not a paired cross-engine result and must not be merged into a formal table.
"${PYTHON}" -u shell/run_tpcc_direction_calibration.py \
  --output-dir "${CALIBRATION}" \
  --blocks 3 \
  --max-attempts 3 \
  --resume \
  --cpu-limit-cores 3.85 \
  --memory-limit-bytes 4294967296 \
  --io-total-limit-mib-s 54 \
  --io-read-cap-mib-s 27 \
  --io-write-cap-mib-s 27

"${PYTHON}" -u shell/build_replay_direction_dataset.py \
  --input "${CALIBRATION}" \
  --output "${DATASET}" \
  --workload tpcc_sensitive \
  --target-resource cpu

train_args=(
  "${PYTHON}" -u shell/train_real_replay_direction_ranker_artifact.py
  --dataset "${DATASET}"
  --output "${RANKER}"
  --workload tpcc_sensitive
  --target-resource cpu
  --scenario cpu
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
