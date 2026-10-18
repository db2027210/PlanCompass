#!/usr/bin/env bash
set -euo pipefail

# Safely restart only the run-owned A2 JOB/memory tuner.  The newest attempt
# for every Cartesian point remains authoritative in the append-only JSONL.
# If a retryable failure's round has not been summarized, a normal resume
# replays it in the same grid.  If the summary raced with this script, install
# an explicit rewind from the failed row's exact ranges so all downstream
# interval contractions are recomputed from a complete real grid.

SOURCE=/disk1/resource_adaptive/bruteforce_pg17_framework_20260717/binary_plan_resource_tuner.py
CONFIG=/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/runs/A2-R1_job_memory/optimizer_config.json
OUTPUT_DIR=/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/runs/A2-R1_job_memory
LOG=${OUTPUT_DIR}/memory_binary_search_job.jsonl
CONTROLLER_LOG=${OUTPUT_DIR}/controller_retry_v6.log
PYTHON=/home/anonymous/miniconda3/bin/python3
SUPERVISOR=/home/anonymous/program/resource_adaptive/shell/supervise_fullgrid_retry_before_summary.py
TMUX_SOCKET=fullgrid-a2-retry-v6
TMUX_SESSION=controller

if ps -eo args= | awk -v supervisor="${SUPERVISOR}" -v log="${LOG}" '
    index($0, supervisor) && index($0, "--log " log) {found=1}
    END {exit !found}
'; then
    echo "A2 is already running under the retry-before-summary supervisor; no action taken"
    exit 0
fi

read -r failed_round retryable_failure_count max_summary_round < <(
    "${PYTHON}" - "${LOG}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

latest = {}
summary_rounds = []
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if row.get("event") == "candidate_validation":
        key = (int(row.get("round") or 0), int(row.get("candidate_index") or 0))
        latest[key] = row
    elif row.get("event") == "round_summary":
        summary_rounds.append(int(row.get("round") or 0))

failed = []
for (round_index, candidate_index), row in latest.items():
    if str(row.get("status") or "").lower() == "ok":
        continue
    reason = str(row.get("accept_reject_reason") or row.get("reject_reason") or "").lower()
    if reason == "workload_failed":
        failed.append((round_index, candidate_index))

failed_round = min((round_index for round_index, _ in failed), default=0)
failed_in_round = sum(1 for round_index, _ in failed if round_index == failed_round)
print(failed_round, failed_in_round, max(summary_rounds, default=0))
PY
)

if (( failed_round <= 0 || retryable_failure_count <= 0 )); then
    echo "refusing restart: A2 has no authoritative retryable workload failure" >&2
    exit 3
fi

mapfile -t tuner_pids < <(
    ps -eo pid=,comm=,args= | awk -v source="${SOURCE}" -v config="${CONFIG}" -v output="${OUTPUT_DIR}" '
        $2 ~ /^python/ && index($0, source) && index($0, "--config " config) && index($0, "--output-dir " output) && index($0, " binary-search --target memory --resume") {
            print $1
        }
    '
)
if [[ "${#tuner_pids[@]}" != 1 ]]; then
    echo "refusing restart: expected exactly one A2 tuner, found ${#tuner_pids[@]}: ${tuner_pids[*]-}" >&2
    exit 5
fi

old_pid=${tuner_pids[0]}
echo "requesting safe A2 stop: pid=${old_pid} failed_round=${failed_round} retryable_failures=${retryable_failure_count} max_summary_round=${max_summary_round}"
kill -INT "${old_pid}"

deadline=$((SECONDS + 2100))
while kill -0 "${old_pid}" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
        echo "A2 tuner did not exit after SIGINT within 2100 seconds; no new tuner was started" >&2
        exit 6
    fi
    sleep 2
done

if ps -eo args= | awk -v config="${CONFIG}" 'index($0, "--config " config) {found=1} END {exit !found}'; then
    echo "refusing restart: another process still references the A2 config" >&2
    exit 7
fi

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

latest = {}
summary_rounds = []
for row in rows:
    if row.get("event") == "candidate_validation":
        key = (int(row.get("round") or 0), int(row.get("candidate_index") or 0))
        latest[key] = row
    elif row.get("event") == "round_summary":
        summary_rounds.append(int(row.get("round") or 0))

failed = []
for key, row in latest.items():
    status = str(row.get("status") or "").lower()
    reason = str(row.get("accept_reject_reason") or row.get("reject_reason") or "").lower()
    if status != "ok" and reason == "workload_failed":
        failed.append((key, row))
if not failed:
    raise SystemExit("A2 failure disappeared before restart; refusing unnecessary rewind")

failed_round = min(key[0] for key, _ in failed)
max_summary_round = max(summary_rounds, default=0)
if max_summary_round < failed_round:
    print(f"A2 pre-summary resume: failed_round={failed_round}; no range rewind required")
    raise SystemExit(0)

failed_row = next(row for key, row in failed if key[0] == failed_round)
failed_ranges = failed_row.get("ranges")
if not isinstance(failed_ranges, dict) or not failed_ranges:
    raise SystemExit("cannot install A2 rewind: exact failed-grid ranges are missing")

cfg = json.loads(config_path.read_text(encoding="utf-8"))
search = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
if not isinstance(search, dict):
    raise SystemExit("cannot install A2 rewind: binary_search config is missing")
overrides = search.setdefault("resume_range_overrides", {})
if not isinstance(overrides, dict):
    raise SystemExit("cannot install A2 rewind: resume_range_overrides is not an object")
existing = overrides.get("memory")
if isinstance(existing, dict) and not str(existing.get("id") or "").startswith(
    "a2_rewind_after_incomplete_real_grid_"
):
    raise SystemExit(f"refusing to replace unrelated memory resume override: {existing.get('id')}")

override_id = f"a2_rewind_after_incomplete_real_grid_r{failed_round}_after_r{max_summary_round}_20260718"
overrides["memory"] = {
    "id": override_id,
    "reason": "rewind_after_retryable_real_monitor_failure_before_complete_cartesian_contraction",
    "apply_after_completed_round": max_summary_round,
    "resume_from_round": failed_round,
    "rebaseline": False,
    "ranges": copy.deepcopy(failed_ranges),
}

stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
backup = config_path.with_name(f"{config_path.name}.before_{override_id}_{stamp}.bak")
shutil.copy2(config_path, backup)
temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, config_path)
print(
    f"A2 post-summary rewind installed: id={override_id} "
    f"apply_after_round={max_summary_round} backup={backup}"
)
PY

tmux -L "${TMUX_SOCKET}" new-session -d -s "${TMUX_SESSION}" \
    "exec env GENERIC_WORKLOAD=job GENERIC_RUN_SECONDS=60 BENCHBASE_SOURCE_ROOT=/home/anonymous/benchbase-postgres BENCHBASE_RUNTIME_ROOT=/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/runs/benchbase_runtime BENCHBASE_SCALEFACTOR=1 BENCHBASE_TERMINALS=8 BENCHBASE_JAVA_XMX=-Xmx4g JOB_SQL_DIR=/home/anonymous/dataset/pg_data/join-order-benchmark JOB_QUERY_FILES=1a.sql,5a.sql,9a.sql,12a.sql,16a.sql,20a.sql,26a.sql,33a.sql JOB_MAX_QUERIES=0 JOB_STATEMENT_TIMEOUT_MS=1800000 ${PYTHON} -B ${SUPERVISOR} --log ${LOG} --config ${CONFIG} --target memory --poll-seconds 0.05 --max-restarts 32 -- ${PYTHON} -B ${SOURCE} --config ${CONFIG} --output-dir ${OUTPUT_DIR} binary-search --target memory --resume >> ${CONTROLLER_LOG} 2>&1"

sleep 3
mapfile -t resumed_pids < <(
    ps -eo pid=,comm=,args= | awk -v source="${SOURCE}" -v config="${CONFIG}" -v output="${OUTPUT_DIR}" '
        $2 ~ /^python/ && index($0, source) && index($0, "--config " config) && index($0, "--output-dir " output) && index($0, " binary-search --target memory --resume") {
            print $1
        }
    '
)
if [[ "${#resumed_pids[@]}" != 1 ]]; then
    echo "A2 resume launch verification failed: found ${#resumed_pids[@]} tuner(s): ${resumed_pids[*]-}" >&2
    exit 8
fi

echo "A2 resume started: pid=${resumed_pids[0]} controller_log=${CONTROLLER_LOG}"
