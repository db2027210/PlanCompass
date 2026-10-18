#!/usr/bin/env bash
set -euo pipefail

source_root=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/source_sf10_migration_20260727
attempt=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt57_sf10_strict_r0_seeded_cpu7p7_w36_20260727
point_runner="$source_root/run_tpch_sf10_strict_r0_point_node01.sh"
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
state_path="$attempt/r0_supervisor_state.json"

mkdir -p "$attempt/r0"

existing_count="$(
    "$python_bin" - "$attempt/r0" "$state_path" <<'PY'
import datetime as dt
import json
import math
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
state_path = pathlib.Path(sys.argv[2])
summaries = sorted(
    root.glob("repeat_*/summary.json"),
    key=lambda path: path.stat().st_mtime,
)
for repeat, path in enumerate(summaries, start=1):
    summary = json.loads(path.read_text(encoding="utf-8"))
    decision = summary.get("r0_decision_window") or {}
    utilization = decision.get("utilization_pct") or {}
    try:
        values = {
            name: float(utilization[name])
            for name in ("cpu", "memory", "io")
        }
    except (KeyError, TypeError, ValueError):
        values = {}
    valid = (
        summary.get("status") == "complete"
        and decision.get("stable") is True
        and int(summary.get("failed_queries") or 0) == 0
        and float(summary.get("db_qps") or 0.0) > 0.0
        and len(values) == 3
        and all(math.isfinite(value) for value in values.values())
        and 30.0 <= values["cpu"] <= 50.0
        and 85.0 <= values["memory"] <= 90.0
        and 30.0 <= values["io"] <= 50.0
        and (summary.get("worker_cleanup") or {}).get(
            "database_backends_fully_released"
        ) is True
    )
    if not valid:
        state_path.write_text(
            json.dumps({
                "status": "failed",
                "phase": "strict_r0_resume_audit",
                "current_repeat": repeat,
                "summary_path": str(path),
                "db_qps": summary.get("db_qps"),
                "utilization_pct": values,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise SystemExit("existing strict R0 repeat failed resume audit")
print(len(summaries))
PY
)"

for repeat in $(seq "$((existing_count + 1))" 3); do
    started_epoch=$(date +%s)
    "$python_bin" - "$state_path" "$repeat" <<'PY'
import datetime as dt
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps({
    "status": "running",
    "phase": "strict_r0",
    "current_repeat": int(sys.argv[2]),
    "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
}, indent=2) + "\n", encoding="utf-8")
PY

    set +e
    bash "$point_runner" >>"$source_root/strict_r0_seeded_cpu7p7_w36.log" 2>&1
    runner_rc=$?
    set -e

    "$python_bin" - "$attempt/r0" "$started_epoch" "$repeat" "$runner_rc" "$state_path" <<'PY'
import datetime as dt
import json
import math
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
started_epoch = float(sys.argv[2])
repeat = int(sys.argv[3])
runner_rc = int(sys.argv[4])
state_path = pathlib.Path(sys.argv[5])
summaries = sorted(
    root.glob("repeat_*/summary.json"),
    key=lambda path: path.stat().st_mtime,
)
if not summaries or summaries[-1].stat().st_mtime < started_epoch - 2:
    raise SystemExit("strict R0 produced no new summary")
path = summaries[-1]
summary = json.loads(path.read_text(encoding="utf-8"))
decision = summary.get("r0_decision_window") or {}
utilization = decision.get("utilization_pct") or {}
values = {name: float(utilization[name]) for name in ("cpu", "memory", "io")}
valid = (
    runner_rc == 0
    and summary.get("status") == "complete"
    and decision.get("stable") is True
    and int(summary.get("failed_queries") or 0) == 0
    and float(summary.get("db_qps") or 0.0) > 0.0
    and all(math.isfinite(value) for value in values.values())
    and 30.0 <= values["cpu"] <= 50.0
    and 85.0 <= values["memory"] <= 90.0
    and 30.0 <= values["io"] <= 50.0
    and (summary.get("worker_cleanup") or {}).get(
        "database_backends_fully_released"
    ) is True
)
payload = {
    "status": "repeat_passed" if valid else "failed",
    "phase": "strict_r0",
    "current_repeat": repeat,
    "summary_path": str(path),
    "runner_returncode": runner_rc,
    "db_qps": summary.get("db_qps"),
    "utilization_pct": values,
    "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
}
state_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
if not valid:
    raise SystemExit("strict R0 repeat failed")
PY
done

"$python_bin" - "$state_path" <<'PY'
import datetime as dt
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload.update({
    "status": "complete",
    "phase": "strict_r0_complete",
    "passed_repeats": 3,
    "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
})
path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
