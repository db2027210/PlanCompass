#!/usr/bin/env bash
set -euo pipefail

# Safely restart only the run-owned A5 TPC-C/CPU tuner.  Before round 1 is
# summarized, resume excludes the newest failed c88 attempt from completed
# signatures and replays it before range contraction.  If a summary was
# already written, the script installs an explicit append-only rewind from the
# original round-1 ranges, so every downstream contraction is recomputed from
# a complete 128-point real grid.

SOURCE=/disk1/resource_adaptive/bruteforce_pg17_framework_20260717/binary_plan_resource_tuner.py
CONFIG=/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/runs/A5-R1_tpcc_cpu/optimizer_config.json
OUTPUT_DIR=/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/runs/A5-R1_tpcc_cpu
LOG=${OUTPUT_DIR}/cpu_binary_search_tpcc.jsonl
CONTROLLER_LOG=${OUTPUT_DIR}/controller_retry_v3.log
PYTHON=/home/anonymous/miniconda3/bin/python3
SUPERVISOR=/home/anonymous/program/resource_adaptive/shell/supervise_fullgrid_retry_before_summary.py
TMUX_SOCKET=fullgrid-a5-retry-v3
TMUX_SESSION=controller

if ps -eo args= | awk -v supervisor="${SUPERVISOR}" -v log="${LOG}" '
    index($0, supervisor) && index($0, "--log " log) {found=1}
    END {exit !found}
'; then
    echo "A5 is already running under the retry-before-summary supervisor; no action taken"
    exit 0
fi

read -r latest_index retryable_failure_count round1_summary_count max_summary_round < <(
    "${PYTHON}" - "${LOG}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
latest = {}
round1_summary_count = 0
max_summary_round = 0
for raw in path.read_text(encoding="utf-8").splitlines():
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if row.get("event") == "candidate_validation":
        key = (int(row.get("round") or 0), int(row.get("candidate_index") or 0))
        latest[key] = row
    elif row.get("event") == "round_summary":
        summary_round = int(row.get("round") or 0)
        max_summary_round = max(max_summary_round, summary_round)
        if summary_round == 1:
            round1_summary_count += 1

failed = []
for (round_index, candidate_index), row in latest.items():
    if round_index != 1 or str(row.get("status") or "").lower() == "ok":
        continue
    reason = str(row.get("accept_reject_reason") or row.get("reject_reason") or "").lower()
    if reason == "workload_failed":
        failed.append(candidate_index)

print(
    max((candidate for round_index, candidate in latest if round_index == 1), default=0),
    len(failed),
    round1_summary_count,
    max_summary_round,
)
PY
)

if [[ "${retryable_failure_count}" == 0 ]]; then
    echo "refusing restart: A5 round 1 has no authoritative retryable workload failure" >&2
    exit 3
fi

mapfile -t tuner_pids < <(
    ps -eo pid=,comm=,args= | awk -v source="${SOURCE}" -v config="${CONFIG}" -v output="${OUTPUT_DIR}" '
        $2 ~ /^python/ && index($0, source) && index($0, "--config " config) && index($0, "--output-dir " output) && index($0, " binary-search --target cpu --resume") {
            print $1
        }
    '
)
if [[ "${#tuner_pids[@]}" != 1 ]]; then
    echo "refusing restart: expected exactly one A5 tuner, found ${#tuner_pids[@]}: ${tuner_pids[*]-}" >&2
    exit 5
fi

old_pid=${tuner_pids[0]}
echo "requesting safe A5 stop: pid=${old_pid} latest_candidate=${latest_index} retryable_failures=${retryable_failure_count} round1_summaries=${round1_summary_count} max_summary_round=${max_summary_round}"
kill -INT "${old_pid}"

deadline=$((SECONDS + 2100))
while kill -0 "${old_pid}" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
        echo "A5 tuner did not exit after SIGINT within 2100 seconds; no new tuner was started" >&2
        exit 6
    fi
    sleep 2
done

if ps -eo args= | awk -v config="${CONFIG}" 'index($0, "--config " config) {found=1} END {exit !found}'; then
    echo "refusing restart: another process still references the A5 config" >&2
    exit 7
fi

# Recheck after the old tuner has stopped: its in-flight validation could have
# completed between the initial audit and SIGINT.  If any round summary exists,
# install a one-shot rewind whose apply_after round is the authoritative newest
# completed round and whose ranges are exactly round 1's original ranges.
"${PYTHON}" - "${CONFIG}" "${LOG}" <<'PY'
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
log_path = Path(sys.argv[2])
rows = []
for raw in log_path.read_text(encoding="utf-8").splitlines():
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if isinstance(row, dict):
        rows.append(row)

summary_rounds = [
    int(row.get("round") or 0)
    for row in rows
    if row.get("event") == "round_summary" and int(row.get("round") or 0) > 0
]
if not summary_rounds:
    print("A5 pre-summary resume: no range rewind required")
    raise SystemExit(0)

round1_ranges = None
for row in rows:
    if row.get("event") == "round_summary" and int(row.get("round") or 0) == 1:
        value = row.get("previous_ranges")
        if isinstance(value, dict) and value:
            round1_ranges = copy.deepcopy(value)
            break
if round1_ranges is None:
    for row in rows:
        if row.get("event") == "round_start" and int(row.get("round") or 0) == 1:
            value = row.get("ranges")
            if isinstance(value, dict) and value:
                round1_ranges = copy.deepcopy(value)
                break
if not isinstance(round1_ranges, dict) or not round1_ranges:
    raise SystemExit("cannot install A5 rewind: original round-1 ranges are missing")

cfg = json.loads(config_path.read_text(encoding="utf-8"))
tuning = cfg.get("binary_plan_tuning")
if not isinstance(tuning, dict):
    raise SystemExit("cannot install A5 rewind: binary_plan_tuning config is missing")
search = tuning.get("binary_search")
if not isinstance(search, dict):
    raise SystemExit("cannot install A5 rewind: binary_search config is missing")
overrides = search.setdefault("resume_range_overrides", {})
if not isinstance(overrides, dict):
    raise SystemExit("cannot install A5 rewind: resume_range_overrides is not an object")
existing = overrides.get("cpu")
if isinstance(existing, dict) and not str(existing.get("id") or "").startswith(
    "a5_r1_rewind_after_incomplete_real_grid_"
):
    raise SystemExit(f"refusing to replace unrelated CPU resume override: {existing.get('id')}")

max_summary_round = max(summary_rounds)
override_id = f"a5_r1_rewind_after_incomplete_real_grid_round_{max_summary_round}_20260718"
overrides["cpu"] = {
    "id": override_id,
    "reason": "rewind_after_retryable_real_monitor_failure_before_complete_cartesian_contraction",
    "apply_after_completed_round": max_summary_round,
    "resume_from_round": 1,
    "rebaseline": False,
    "ranges": round1_ranges,
}

stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
backup = config_path.with_name(f"{config_path.name}.before_{override_id}_{stamp}.bak")
shutil.copy2(config_path, backup)
temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, config_path)
print(
    f"A5 post-summary rewind installed: id={override_id} "
    f"apply_after_round={max_summary_round} backup={backup}"
)
PY

tmux -L "${TMUX_SOCKET}" new-session -d -s "${TMUX_SESSION}" \
    "exec env GENERIC_WORKLOAD=tpcc GENERIC_RUN_SECONDS=60 BENCHBASE_SOURCE_ROOT=/home/anonymous/benchbase-postgres BENCHBASE_RUNTIME_ROOT=/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/runs/benchbase_runtime BENCHBASE_SCALEFACTOR=10 BENCHBASE_TERMINALS=8 BENCHBASE_JAVA_XMX=-Xmx4g JOB_SQL_DIR=/home/anonymous/dataset/pg_data/join-order-benchmark JOB_QUERY_FILES= JOB_MAX_QUERIES=0 JOB_STATEMENT_TIMEOUT_MS=0 ${PYTHON} -B ${SUPERVISOR} --log ${LOG} --config ${CONFIG} --target cpu --poll-seconds 0.05 --max-restarts 32 -- ${PYTHON} -B ${SOURCE} --config ${CONFIG} --output-dir ${OUTPUT_DIR} binary-search --target cpu --resume >> ${CONTROLLER_LOG} 2>&1"

sleep 3
mapfile -t resumed_pids < <(
    ps -eo pid=,comm=,args= | awk -v source="${SOURCE}" -v config="${CONFIG}" -v output="${OUTPUT_DIR}" '
        $2 ~ /^python/ && index($0, source) && index($0, "--config " config) && index($0, "--output-dir " output) && index($0, " binary-search --target cpu --resume") {
            print $1
        }
    '
)
if [[ "${#resumed_pids[@]}" != 1 ]]; then
    echo "A5 resume launch verification failed: found ${#resumed_pids[@]} tuner(s): ${resumed_pids[*]-}" >&2
    exit 8
fi

echo "A5 resume started: pid=${resumed_pids[0]} controller_log=${CONTROLLER_LOG}"
