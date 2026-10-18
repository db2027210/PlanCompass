#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${FULL_CONVERGENCE_RUN_ID:-hyxp_four_scenario_full_convergence_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${FULL_CONVERGENCE_RUN_DIR:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/${RUN_ID}}"
SOURCE_CONFIG="${FULL_CONVERGENCE_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp.json}"
WORKER_CONFIG="${FULL_CONVERGENCE_WORKER_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp_rebuild_20260703_180311.json}"
FROZEN_CONFIG="${RUN_DIR}/frozen_binary_plan_tuning_hybenchxp.json"

mkdir -p "${RUN_DIR}/source_snapshot"
if [[ ! -f "${FROZEN_CONFIG}" ]]; then
  cp "${SOURCE_CONFIG}" "${FROZEN_CONFIG}"
fi
cp "${ROOT_DIR}/binary_plan_resource_tuner.py" "${RUN_DIR}/source_snapshot/binary_plan_resource_tuner.py"
cp "${ROOT_DIR}/shell/continue_hybenchxp_binary_plan_run.sh" \
  "${RUN_DIR}/source_snapshot/continue_hybenchxp_binary_plan_run.sh"
cp "${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh" \
  "${RUN_DIR}/source_snapshot/run_binary_plan_tuning_hybenchxp.sh"

"${PYTHON:-python3}" - "${RUN_DIR}" "${FROZEN_CONFIG}" "${ROOT_DIR}" "${WORKER_CONFIG}" <<'PY'
import hashlib
import json
import sys
import time
from pathlib import Path

run_dir = Path(sys.argv[1])
config_path = Path(sys.argv[2])
root = Path(sys.argv[3])
worker_config = Path(sys.argv[4])

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

tracked = {
    "config": config_path,
    "tuner": root / "binary_plan_resource_tuner.py",
    "controller": root / "shell/continue_hybenchxp_binary_plan_run.sh",
    "stage_launcher": root / "shell/run_binary_plan_tuning_hybenchxp.sh",
}
manifest = {
    "event": "four_scenario_full_convergence_launch",
    "created_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    "run_dir": str(run_dir),
    "frozen_config": str(config_path),
    "scenario_order": ["cpu", "memory", "none", "io"],
    "completion_condition": "all_scenarios_epsilon_converged",
    "stop_on_target_found": False,
    "preserve_best_point": True,
    "preserve_final_output_point": True,
    "preserve_final_observed_point": True,
    "preserve_final_range_anchor_point": True,
    "preserve_full_jsonl_search_log": True,
    "worker_config": str(worker_config),
    "cpu_memory_worker_override": ["cpu_mem_w4", "cpu_mem_w5", "cpu_mem_w6", "cpu_mem_w7"],
    "io_worker_override": ["io_disk1"],
    "artifacts": {
        name: {"path": str(path), "sha256": sha256(path)}
        for name, path in tracked.items()
    },
}
manifest["artifacts"]["worker_config"] = {
    "path": str(worker_config),
    "sha256": sha256(worker_config),
}
(run_dir / "launch_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

export BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR="${RUN_DIR}"
export BINARY_PLAN_TUNING_HYBENCHXP_CONFIG="${FROZEN_CONFIG}"
export BINARY_PLAN_TUNING_HYBENCHXP_POLL_SEC="${BINARY_PLAN_TUNING_HYBENCHXP_POLL_SEC:-30}"
export BINARY_PLAN_TUNING_WORKER_CONFIG="${BINARY_PLAN_TUNING_WORKER_CONFIG:-${WORKER_CONFIG}}"
export BINARY_PLAN_TUNING_WORKER_NAMES="${BINARY_PLAN_TUNING_WORKER_NAMES:-cpu_mem_w4,cpu_mem_w5,cpu_mem_w6,cpu_mem_w7}"
export BINARY_PLAN_TUNING_CPU_MEMORY_PARALLELISM="${BINARY_PLAN_TUNING_CPU_MEMORY_PARALLELISM:-4}"
export BINARY_PLAN_TUNING_NONE_PARALLELISM="${BINARY_PLAN_TUNING_NONE_PARALLELISM:-4}"
export BINARY_PLAN_TUNING_IO_WORKER_NAMES="${BINARY_PLAN_TUNING_IO_WORKER_NAMES:-io_disk1}"
export BINARY_PLAN_TUNING_HYBENCHXP_IO_PARALLELISM="${BINARY_PLAN_TUNING_HYBENCHXP_IO_PARALLELISM:-1}"

exec > >(tee -a "${RUN_DIR}/controller.log") 2>&1
echo "[full-convergence] start run_dir=${RUN_DIR}"
echo "[full-convergence] completion=epsilon_converged for cpu,memory,none,io"
echo "[full-convergence] target hits update best point but never stop the search"

"${ROOT_DIR}/shell/continue_hybenchxp_binary_plan_run.sh"
