#!/usr/bin/env python3
"""Five-minute status table for the four active PostgreSQL pilot lanes."""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import time
from datetime import datetime


LANES = (
    {
        "label": "JOB / Memory / job",
        "machine": "node02 / nvme2n1",
        "host": "anonymous@192.0.2.4",
        "root": "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_8e_codex_sealed_r0_20260727",
        "process_roots": [
            "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/workspace_attempt7",
        ],
        "experiment_pattern": (
            r"run_job_r0_profile_v2|run_job_direction_calibration|"
            r"formal_pg_point_runner|"
            r"(^|[/ ])parameter_optimizer\.py($| )"
        ),
        "controller_pattern": r"$^",
        "accepted_default": 0,
        "accepted_audit": False,
        "phase_name": "DIRECTION_CALIBRATION",
        "phase_total": 33,
        "static_blocker": "",
    },
    {
        "label": "TPC-H / Memory / tpch",
        "machine": "node01 / nvme1n1",
        "host": "anonymous@192.0.2.3",
        "root": "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/formal_sf100_dataset_20260727",
        "process_roots": [],
        "experiment_pattern": r"run_tpch_sf100_provision|DBWorkload.*tpch",
        "controller_pattern": r"$^",
        "accepted_default": 0,
        "accepted_audit": False,
        "phase_name": "DATASET_PROVISIONING",
        "phase_total": 1,
        "static_blocker": "",
    },
    {
        "label": "TPC-C / CPU / tpcc",
        "machine": "compute00 / disk2",
        "host": None,
        "root": "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt15_direction_retrain_cgroup_sealed_20260727",
        "process_roots": [],
        "experiment_pattern": (
            r"run_tpcc_retrain_20260727|run_tpcc_direction_calibration|"
            r"run_tpcc_sensitive|parameter_optimizer\.py"
        ),
        "controller_pattern": r"$^",
        "accepted_default": 0,
        "accepted_audit": False,
        "phase_name": "DIRECTION_CALIBRATION",
        "phase_total": 33,
        "static_blocker": "",
    },
    {
        "label": "XP / IO / hybench_xp",
        "machine": "compute00 / disk1",
        "host": None,
        "root": (
            "/home/anonymous/program/resource_adaptive/pg4_relaunch_20260727/"
            "retraining/xp/fresh_calibration_20260727"
        ),
        "process_roots": [
            "/home/anonymous/program/resource_adaptive/pg4_relaunch_20260727",
            "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
            "hybench_xp_compute00_59630/experience_sgd/"
            "attempt39_fresh_from_accepted3_20260726",
        ],
        "experiment_pattern": (
            r"run_xp_retrain_20260727|run_xp_direction_calibration|"
            r"candidate_stable_measurement|hybench_unblocked|com\.hybench\.HyBench"
        ),
        "controller_pattern": r"xp_continuous_direction_owner\.py",
        "accepted_default": 0,
        "accepted_audit": False,
        "phase_name": "DIRECTION_CALIBRATION",
        "phase_total": 18,
        "static_blocker": "",
    },
)


PROBE = r'''
import json
import re
from datetime import datetime
from pathlib import Path

root = Path(__ROOT__)
process_roots = [Path(x) for x in __PROCESS_ROOTS__]
experiment_pattern = re.compile(__EXPERIMENT_PATTERN__)
controller_pattern = re.compile(__CONTROLLER_PATTERN__)
accepted = __ACCEPTED__
phase_name = __PHASE_NAME__
phase_total = __PHASE_TOTAL__
static_blocker = __STATIC_BLOCKER__

experiment_pids = []
controller_pids = []
for proc_dir in Path("/proc").glob("[0-9]*"):
    try:
        cmdline = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            errors="replace"
        ).strip()
        if not cmdline or "monitor_four_lanes_5min" in cmdline:
            continue
        cwd = Path((proc_dir / "cwd").resolve())
        in_scope = str(root) in cmdline or str(cwd).startswith(str(root))
        if not in_scope:
            in_scope = any(
                str(item) in cmdline or str(cwd).startswith(str(item))
                for item in process_roots
            )
        if not in_scope:
            continue
        if experiment_pattern.search(cmdline):
            experiment_pids.append(int(proc_dir.name))
        elif controller_pattern.search(cmdline):
            controller_pids.append(int(proc_dir.name))
    except Exception:
        continue

counter_paths = []
if root.exists():
    for pattern in ("**/*accepted*.count", "**/*accepted*counter*.txt"):
        counter_paths.extend(root.glob(pattern))
for path in sorted(
    {p for p in counter_paths if p.is_file()},
    key=lambda p: p.stat().st_mtime_ns,
    reverse=True,
):
    try:
        accepted = int(path.read_text().strip())
        break
    except Exception:
        continue

events = []
event_path = None
if root.exists():
    candidates = sorted(
        (
            path
            for path in root.rglob("*.jsonl")
            if any(word in path.name.lower() for word in ("history", "progress", "event"))
        ),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    for path in candidates[:40]:
        try:
            parsed = []
            for line in path.read_text(errors="replace").splitlines()[-600:]:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        parsed.append(value)
                except Exception:
                    pass
            if parsed:
                events = parsed
                event_path = str(path)
                break
        except Exception:
            pass

last = events[-1] if events else {}
stage = str(last.get("event") or "running" if experiment_pids else "idle")
outer = next(
    (
        item.get("outer_round", item.get("round_index"))
        for item in reversed(events)
        if item.get("outer_round", item.get("round_index")) is not None
    ),
    None,
)
replay = next(
    (
        item.get("candidate_index", item.get("replay_ordinal"))
        for item in reversed(events)
        if item.get("candidate_index", item.get("replay_ordinal")) is not None
    ),
    0,
)
evidence = str(last.get("timestamp") or last.get("time") or "-")
blocker = "-"

# During retraining, report actual authoritative completed points instead of
# the optimizer's later per-round Top-5 budget.  BenchBase's nested
# *.summary.json files are deliberately excluded.
completed_points = 0
if root.exists():
    # Direction calibration may retry one logical design point several times.
    # Its append-only calibration_id ledger is authoritative; counting point
    # summaries would inflate progress with failed attempts (for example a
    # TPC-C SQLSTATE 40P01 retry).
    calibration_ledgers = list(root.rglob("fresh_model_calibration.jsonl"))
    baseline_artifacts = list(root.rglob("fresh_baseline.json"))
    if calibration_ledgers or baseline_artifacts:
        logical_ids = set()
        for path in calibration_ledgers:
            try:
                for line in path.read_text(errors="replace").splitlines():
                    value = json.loads(line)
                    calibration_id = value.get("calibration_id")
                    if calibration_id and value.get("status") in {
                        "usable", "technical_failure"
                    }:
                        logical_ids.add(str(calibration_id))
            except Exception:
                pass
        completed_points = len(logical_ids) + len(
            {str(path.resolve()) for path in baseline_artifacts if path.is_file()}
        )
    else:
        point_artifacts = list(root.rglob("summary.json")) + list(
            root.rglob("point_result.json")
        )
        for path in point_artifacts:
            lowered_parts = {part.lower() for part in path.parts}
            if any(
                token in part
                for part in lowered_parts
                for token in ("failed", "invalid", "quarantined")
            ):
                continue
            try:
                value = json.loads(path.read_text(errors="replace"))
                if str(value.get("status", "")).lower() in {
                    "ok", "complete", "completed"
                }:
                    completed_points += 1
            except Exception:
                pass
if experiment_pids:
    stage = phase_name
elif static_blocker:
    stage = phase_name
    blocker = static_blocker
elif completed_points >= phase_total:
    stage = f"{phase_name}_COMPLETE"

provision_state_path = root / "provision_state.json"
if provision_state_path.is_file():
    try:
        provision_state = json.loads(
            provision_state_path.read_text(errors="replace")
        )
        provision_phase = str(provision_state.get("phase") or "unknown").upper()
        provision_status = str(provision_state.get("status") or "unknown").lower()
        stage = f"{phase_name}/{provision_phase}"
        evidence = str(provision_state.get("updated_at_utc") or evidence)
        event_path = str(provision_state_path)
        if provision_status == "complete":
            completed_points = phase_total
        elif provision_status == "failed":
            blocker = str(provision_state.get("error") or "dataset provision failed")
    except Exception as error:
        blocker = f"invalid provision_state.json: {error}"

summary = None
summary_path = None
if root.exists():
    summaries = sorted(
        (
            path
            for path in root.rglob("*summary*.json")
            if not any(
                token in part.lower()
                for part in path.parts
                for token in ("failed", "invalid", "quarantined")
            )
        ),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    if summaries:
        summary_path = summaries[0]
        try:
            summary = json.loads(summary_path.read_text(errors="replace"))
        except Exception:
            summary = None

if isinstance(summary, dict):
    stop_reason = str(summary.get("stop_reason") or "")
    outcome = str(summary.get("round_outcome") or summary.get("status") or "")
    failed_outcome = outcome.lower() in {
        "failed", "error", "profile_band_failed", "technical_failure"
    }
    failed_reason = any(
        token in stop_reason.lower()
        for token in ("failed", "error", "exhausted", "no_feasible")
    )
    if stop_reason and (failed_outcome or failed_reason):
        blocker = stop_reason
    elif "no_feasible" in outcome.lower() or outcome.lower() in {"failed", "error"}:
        blocker = outcome

for item in reversed(events):
    event = str(item.get("event") or "")
    if event in {
        "experience_table_gradient_candidate_space_exhausted",
        "candidate_space_exhausted",
    }:
        blocker = "candidate_space_exhausted"
        break

if static_blocker and not experiment_pids:
    completed_points = 0
    blocker = static_blocker

location = event_path or (str(summary_path) if summary_path else str(root))
print(json.dumps({
    "experiment_pids": sorted(experiment_pids),
    "controller_pids": sorted(controller_pids),
    "outer": outer,
    "accepted": accepted,
    "stage": stage,
    "replay": replay,
    "completed_points": completed_points,
    "phase_total": phase_total,
    "location": location,
    "evidence": evidence,
    "blocker": blocker,
}, ensure_ascii=True))
'''


def probe(lane: dict[str, object]) -> dict[str, object]:
    source = PROBE.replace("__ROOT__", repr(lane["root"]))
    source = source.replace("__PROCESS_ROOTS__", repr(lane["process_roots"]))
    source = source.replace("__EXPERIMENT_PATTERN__", repr(lane["experiment_pattern"]))
    source = source.replace("__CONTROLLER_PATTERN__", repr(lane["controller_pattern"]))
    source = source.replace("__ACCEPTED__", repr(lane["accepted_default"]))
    source = source.replace("__PHASE_NAME__", repr(lane["phase_name"]))
    source = source.replace("__PHASE_TOTAL__", repr(lane["phase_total"]))
    source = source.replace("__STATIC_BLOCKER__", repr(lane["static_blocker"]))
    if lane["host"]:
        encoded = base64.b64encode(source.encode()).decode()
        args = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            str(lane["host"]), f"echo {encoded} | base64 -d | python3 -",
        ]
        stdin = None
    else:
        args = ["python3", "-"]
        stdin = source
    try:
        result = subprocess.run(
            args,
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if result.returncode or not lines:
            raise RuntimeError(result.stdout.strip() or f"exit={result.returncode}")
        return json.loads(lines[-1])
    except Exception as error:
        return {
            "experiment_pids": [],
            "controller_pids": [],
            "outer": None,
            "accepted": lane["accepted_default"],
            "stage": "MONITOR_ERROR",
            "replay": 0,
            "completed_points": 0,
            "phase_total": lane["phase_total"],
            "location": lane["root"],
            "evidence": "-",
            "blocker": str(error)[:160],
        }


def render(sequence: int) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"\n[{now}] PostgreSQL  #{sequence}", flush=True)
    print(
        "Experiment benchmark | Machine | Alive | Outer-loop exploration | Accepted refinement | Current phase | "
        "Replay progress | Replay and measurement location | Latest evidence | Blocking issue",
        flush=True,
    )
    print("---|---|---|---|---|---|---|---|---|---", flush=True)
    for lane in LANES:
        result = probe(lane)
        experiment_pids = result.get("experiment_pids") or []
        controller_pids = result.get("controller_pids") or []
        alive = f":{'' if experiment_pids else ''}"
        if controller_pids:
            alive += " / :"
        accepted = f"{result.get('accepted', '-')}/10"
        if lane["accepted_audit"]:
            accepted += ""
        outer = result.get("outer")
        completed_points = result.get("completed_points", 0)
        phase_total = result.get("phase_total", lane["phase_total"])
        row = (
            lane["label"],
            lane["machine"],
            alive,
            outer if outer is not None else "-",
            accepted,
            result.get("stage") or "-",
            f"{completed_points}/{phase_total}",
            result.get("location") or "-",
            result.get("evidence") or "-",
            str(result.get("blocker") or "-").replace("|", "/"),
        )
        print("| ".join(map(str, row)), flush=True)
    print(
        " | 4 benchmarksAP  | — | — | — | "
        "pilot/canaryformal  | — | — | "
        " pilot accepted  formal | —",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    sequence = 1
    while True:
        render(sequence)
        if args.once:
            return 0
        sequence += 1
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
