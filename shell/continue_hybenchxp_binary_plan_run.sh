#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp.json}"
RUN_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR:-}"
POLL_SEC="${BINARY_PLAN_TUNING_HYBENCHXP_POLL_SEC:-30}"
PYTHON_BIN="${PYTHON:-python3}"

if [[ -z "${RUN_DIR}" ]]; then
  RUN_DIR="$(find /disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
fi
if [[ -z "${RUN_DIR}" || ! -d "${RUN_DIR}" ]]; then
  echo "RUN_DIR not found; set BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR" >&2
  exit 2
fi

find_stage_pid() {
  local target="$1"
  ps -eo pid=,cmd= \
    | awk -v run_dir="${RUN_DIR}" -v target="${target}" \
        '$0 ~ /python/ && $0 ~ run_dir && $0 ~ "binary-search --target " target {print $1}' \
    | head -1
}

stage_state() {
  local target="$1"
  "${PYTHON_BIN}" - "${RUN_DIR}" "${CONFIG}" "${target}" <<'PY'
import json
import math
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
config_path = Path(sys.argv[2])
target = sys.argv[3]
suffix = "hybenchxp"
log_path = run_dir / f"{target}_binary_search_{suffix}.jsonl"
best_path = run_dir / f"best_for_{target}.json"
cfg = json.loads(config_path.read_text())
search_cfg = (((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {}))
epsilon_cfg = search_cfg.get("epsilon") if isinstance(search_cfg.get("epsilon"), dict) else {"default": 1e-6}

def safe_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default

def width_ok(final_ranges):
    if not isinstance(final_ranges, dict) or not final_ranges:
        return False
    for name, raw in final_ranges.items():
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return False
        eps = safe_float(epsilon_cfg.get(name), safe_float(epsilon_cfg.get("default"), 1e-6))
        if safe_float(raw[1]) - safe_float(raw[0]) > eps:
            return False
    return True

counts = {}
round_summaries = 0
candidate_count = 0
max_round = 0
max_index = 0
latest_stop = ""
latest_summary_stop = ""
final_ranges = {}
baseline_count = 0
if log_path.exists():
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue
            event = row.get("event")
            counts[event] = counts.get(event, 0) + 1
            if event == "baseline_validation":
                baseline_count += 1
            elif event == "round_summary":
                round_summaries += 1
                final_ranges = row.get("updated_ranges") or final_ranges
            elif event == "candidate_validation":
                candidate_count += 1
                current_round = int(row.get("round") or 0)
                current_index = int(row.get("candidate_index") or 0)
                if current_round > max_round:
                    max_round = current_round
                    max_index = current_index
                elif current_round == max_round:
                    max_index = max(max_index, current_index)
            elif event == "stage_stop":
                latest_stop = str(row.get("reason") or "")
                if isinstance(row.get("final_ranges"), dict):
                    final_ranges = row.get("final_ranges") or final_ranges
            elif event == "stage_summary":
                latest_summary_stop = str(row.get("stop_reason") or latest_summary_stop)
                if isinstance(row.get("final_ranges"), dict):
                    final_ranges = row.get("final_ranges") or final_ranges

if best_path.exists():
    try:
        best = json.loads(best_path.read_text())
        latest_summary_stop = str(best.get("stop_reason") or latest_summary_stop)
        if isinstance(best.get("final_ranges"), dict):
            final_ranges = best.get("final_ranges") or final_ranges
    except Exception:
        pass

ranges_ok = width_ok(final_ranges)
converged = latest_stop == "epsilon_converged" or latest_summary_stop == "epsilon_converged" or ranges_ok
print(f"has_log={int(log_path.exists())}")
print(f"has_best={int(best_path.exists())}")
print(f"has_baseline={int(baseline_count > 0)}")
print(f"converged={int(converged)}")
print(f"ranges_within_epsilon={int(ranges_ok)}")
print(f"latest_stop={latest_stop}")
print(f"latest_summary_stop={latest_summary_stop}")
print(f"round_summaries={round_summaries}")
print(f"candidate_count={candidate_count}")
print(f"max_round={max_round}")
print(f"max_index={max_index}")
print(f"max_rounds_config={search_cfg.get('max_rounds')}")
print(f"max_candidates_per_round={search_cfg.get('max_candidates_per_round')}")
print(f"stop_only_on_epsilon={search_cfg.get('stop_only_on_epsilon')}")
PY
}

load_stage_state_vars() {
  local target="$1"
  while IFS='=' read -r key value; do
    case "${key}" in
      has_log|has_best|has_baseline|converged|ranges_within_epsilon|latest_stop|latest_summary_stop|round_summaries|candidate_count|max_round|max_index|max_rounds_config|max_candidates_per_round|stop_only_on_epsilon)
        printf -v "STATE_${key}" '%s' "${value}"
        ;;
    esac
  done < <(stage_state "${target}")
}

print_stage_state() {
  local target="$1"
  load_stage_state_vars "${target}"
  local pid
  pid="$(find_stage_pid "${target}" || true)"
  echo "[continue-hybenchxp] ${target}_state converged=${STATE_converged} ranges_within_epsilon=${STATE_ranges_within_epsilon} latest_stop=${STATE_latest_stop:-none} latest_summary_stop=${STATE_latest_summary_stop:-none} rounds=${STATE_round_summaries} candidates=${STATE_candidate_count} progress_round=${STATE_max_round} progress_index=${STATE_max_index} max_rounds=${STATE_max_rounds_config} topk=${STATE_max_candidates_per_round} pid=${pid:-none}"
  "${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
    --config "${CONFIG}" \
    --output-dir "${RUN_DIR}" \
    handoff-snapshots --target "${target}" >/dev/null || true
}

wait_for_running_stage() {
  local target="$1"
  while true; do
    print_stage_state "${target}"
    if [[ "${STATE_converged}" == "1" ]]; then
      return 0
    fi
    local pid
    pid="$(find_stage_pid "${target}" || true)"
    if [[ -z "${pid}" ]]; then
      return 0
    fi
    sleep "${POLL_SEC}"
  done
}

run_tuner() {
  "${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
    --config "${CONFIG}" \
    --output-dir "${RUN_DIR}" \
    "$@"
}

run_stage_once() {
  local target="$1"
  local resume="$2"
  if [[ "${STATE_candidate_count:-0}" != "0" ]]; then
    echo "[continue-hybenchxp] recalibrate ${target} replay error bounds from current stage samples"
    "${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
      --config "${CONFIG}" \
      --output-dir "${RUN_DIR}" \
      calibrate-error-bounds --target "${target}" || true
  fi
  if [[ "${resume}" == "1" ]]; then
    echo "[continue-hybenchxp] resume ${target} with full-128-real-replay/epsilon-only policy"
    BINARY_PLAN_TUNING_HYBENCHXP_SKIP_PREPARE=1 \
    BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR="${RUN_DIR}" \
    BINARY_PLAN_TUNING_HYBENCHXP_CONFIG="${CONFIG}" \
    BINARY_PLAN_TUNING_HYBENCHXP_RESUME=1 \
      "${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh" "${target}"
  else
    echo "[continue-hybenchxp] start ${target} with full-128-real-replay/epsilon-only policy"
    BINARY_PLAN_TUNING_HYBENCHXP_SKIP_PREPARE=1 \
    BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR="${RUN_DIR}" \
    BINARY_PLAN_TUNING_HYBENCHXP_CONFIG="${CONFIG}" \
      "${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh" "${target}"
  fi
}

run_stage_until_converged() {
  local target="$1"
  while true; do
    wait_for_running_stage "${target}"
    load_stage_state_vars "${target}"
    if [[ "${STATE_converged}" == "1" ]]; then
      "${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
        --config "${CONFIG}" \
        --output-dir "${RUN_DIR}" \
        handoff-snapshots --target "${target}" >/dev/null || true
      echo "[continue-hybenchxp] ${target} converged"
      return 0
    fi

    local resume=0
    if [[ "${STATE_has_log}" == "1" && "${STATE_has_baseline}" == "1" ]]; then
      resume=1
    fi

    if ! run_stage_once "${target}" "${resume}"; then
      echo "[continue-hybenchxp] ${target} stage exited non-zero; will retry/resume after ${POLL_SEC}s" >&2
      sleep "${POLL_SEC}"
    fi
  done
}

revalidate_monitor_anomalies() {
  local target="$1"
  echo "[continue-hybenchxp] audit and revalidate ${target} resource-monitor anomalies"
  if ! "${PYTHON_BIN}" "${ROOT_DIR}/shell/revalidate_hybenchxp_monitor_anomalies.py" \
      --config "${CONFIG}" \
      --run-dir "${RUN_DIR}" \
      --target "${target}"; then
    echo "[continue-hybenchxp] ${target} monitor anomaly revalidation exited non-zero; preserve logs and continue" >&2
  fi
}

echo "[continue-hybenchxp] run_dir=${RUN_DIR}"
echo "[continue-hybenchxp] config=${CONFIG}"
echo "[continue-hybenchxp] policy: workload=hybenchxp, target stages serial, per-round full 128-point real replay, stop only on epsilon convergence"

run_stage_until_converged cpu
revalidate_monitor_anomalies cpu
run_stage_until_converged memory
revalidate_monitor_anomalies memory
run_stage_until_converged none
revalidate_monitor_anomalies none
run_stage_until_converged io
revalidate_monitor_anomalies io

"${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
  --config "${CONFIG}" \
  --output-dir "${RUN_DIR}" \
  calibrate-error-bounds --target all || true

run_tuner report --repo-report "report/bonect/binary_plan_resource_tuning_hybenchxp_report_$(date +%Y%m%d_%H%M%S).md"

"${PYTHON_BIN}" - "${RUN_DIR}" "${CONFIG}" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
config_path = Path(sys.argv[2])
cfg = json.loads(config_path.read_text(encoding="utf-8"))
search = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
epsilon_cfg = search.get("epsilon") if isinstance(search.get("epsilon"), dict) else {"default": 1e-6}
targets = ("cpu", "memory", "none", "io")
rows = []
failures = []

def width_checks(final_ranges):
    checks = {}
    for name, bounds in (final_ranges or {}).items():
        epsilon = float(epsilon_cfg.get(name, epsilon_cfg.get("default", 1e-6)))
        width = float(bounds[1]) - float(bounds[0])
        checks[name] = {"width": width, "epsilon": epsilon, "passed": width <= epsilon + 1e-15}
    return checks

for target in targets:
    best_path = run_dir / f"best_for_{target}.json"
    final_path = run_dir / f"final_for_{target}.json"
    log_path = run_dir / f"{target}_binary_search_hybenchxp.jsonl"
    missing = [str(path) for path in (best_path, final_path, log_path) if not path.exists()]
    if missing:
        failures.append(f"{target}: missing {', '.join(missing)}")
        continue
    best = json.loads(best_path.read_text(encoding="utf-8"))
    final = json.loads(final_path.read_text(encoding="utf-8"))
    events = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    stops = [row for row in events if row.get("event") == "stage_stop"]
    latest_stop = str(stops[-1].get("reason") or "") if stops else ""
    checks = width_checks(final.get("final_ranges"))
    ranges_ok = bool(checks) and all(item["passed"] for item in checks.values())
    row = {
        "target": target,
        "converged": bool(final.get("converged")),
        "stop_reason": str(final.get("stop_reason") or latest_stop),
        "latest_log_stop_reason": latest_stop,
        "ranges_within_epsilon": ranges_ok,
        "range_checks": checks,
        "round_summary_count": sum(1 for item in events if item.get("event") == "round_summary"),
        "candidate_validation_count": sum(1 for item in events if item.get("event") == "candidate_validation"),
        "final_point_snapshot_count": sum(1 for item in events if item.get("event") == "final_point_snapshot"),
        "best_point": final.get("best_point"),
        "final_output_point": final.get("final_output_point"),
        "final_observed_point": final.get("final_observed_point"),
        "final_range_anchor_point": final.get("final_range_anchor_point"),
        "best_artifact": str(best_path),
        "final_artifact": str(final_path),
        "search_log": str(log_path),
    }
    rows.append(row)
    if not row["converged"] or row["stop_reason"] != "epsilon_converged" or not ranges_ok:
        failures.append(f"{target}: convergence proof failed")
    if row["round_summary_count"] <= 0 or row["candidate_validation_count"] <= 0:
        failures.append(f"{target}: search process log is incomplete")
    if row["final_point_snapshot_count"] <= 0:
        failures.append(f"{target}: final point snapshot is missing from JSONL")

payload = {
    "event": "four_scenario_convergence_completion_audit",
    "run_dir": str(run_dir),
    "config": str(config_path),
    "complete": not failures and len(rows) == len(targets),
    "failures": failures,
    "scenarios": rows,
}
(run_dir / "convergence_completion_audit.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
lines = [
    "# Four-scenario convergence completion audit",
    "",
    f"- complete: `{payload['complete']}`",
    f"- run_dir: `{run_dir}`",
    "",
    "| scenario | converged | stop reason | ranges <= epsilon | rounds | validations | best/final/log |",
    "| --- | --- | --- | --- | ---: | ---: | --- |",
]
for row in rows:
    artifacts = f"{row['best_artifact']}<br>{row['final_artifact']}<br>{row['search_log']}"
    lines.append(
        f"| {row['target']} | {row['converged']} | {row['stop_reason']} | "
        f"{row['ranges_within_epsilon']} | {row['round_summary_count']} | "
        f"{row['candidate_validation_count']} | {artifacts} |"
    )
if failures:
    lines.extend(["", "## Failures", "", *[f"- {item}" for item in failures]])
(run_dir / "convergence_completion_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
if not payload["complete"]:
    raise SystemExit("; ".join(failures) or "completion audit failed")
print(json.dumps({"event": payload["event"], "complete": True, "run_dir": str(run_dir)}))
PY

echo "[continue-hybenchxp] done run_dir=${RUN_DIR}"
