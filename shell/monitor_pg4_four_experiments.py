#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "config/formal/pg4_four_experiments_live_20260727.json"
)

REMOTE_PROBE = r"""
import json
import os
from collections import deque
import datetime as dt
from pathlib import Path
import re
import shlex
import subprocess
import sys

root = Path(sys.argv[1])
pattern = sys.argv[2]
target_resource = str(sys.argv[3] if len(sys.argv) > 3 else "").strip().lower()
try:
    calibration_expected = int(sys.argv[4]) if len(sys.argv) > 4 else 0
except ValueError:
    calibration_expected = 0
try:
    calibration_summary_expected = int(sys.argv[5]) if len(sys.argv) > 5 else 0
except ValueError:
    calibration_summary_expected = 0

inventory_files = None
inventory_dirs = None

def build_inventory():
    global inventory_files, inventory_dirs
    if inventory_files is not None and inventory_dirs is not None:
        return
    inventory_files = []
    inventory_dirs = []
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    inventory_files.append(path)
                elif path.is_dir():
                    inventory_dirs.append(path)
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        pass

def safe_files(glob):
    build_inventory()
    return [path for path in inventory_files if path.match(glob)]

def safe_dirs(glob):
    build_inventory()
    return [path for path in inventory_dirs if path.match(glob)]

def newest(paths):
    usable = []
    for path in paths:
        try:
            usable.append((path.stat().st_mtime, path))
        except OSError:
            pass
    return max(usable, default=(0.0, None))[1]

def read_json(path):
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def read_last_json(path):
    if path is None:
        return {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}

def read_first_json(path):
    if path is None:
        return {}
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    return value
    except OSError:
        pass
    return {}

def read_recent_json(path, limit=256):
    if path is None:
        return []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            lines = deque(handle, maxlen=limit)
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records

def read_counter(names):
    candidates = []
    for name in names:
        candidates.extend(safe_files(name))
    path = newest(candidates)
    if path is None:
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None

def last_nonempty_line(path):
    if path is None:
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return next((line.strip() for line in reversed(lines) if line.strip()), "")

ps = subprocess.run(
    ["pgrep", "-af", pattern],
    universal_newlines=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
).stdout.splitlines()

def is_owned_runtime(line):
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        return False
    try:
        tokens = shlex.split(parts[1])
    except ValueError:
        return False
    if not tokens:
        return False
    executable = Path(tokens[0]).name
    if executable in {"bash", "sh", "zsh"} and len(tokens) > 1:
        if tokens[1] in {"-c", "-lc"}:
            return False
    command = " ".join(tokens)
    if executable in {"pgrep", "grep", "rg", "tail", "sed", "jq"}:
        return False
    if "monitor_pg4_four_experiments" in command or "python3 -c" in command:
        return False
    if executable.startswith("codex"):
        return False
    return True

ps = [line for line in ps if is_owned_runtime(line)]

progress_path = newest(
    safe_files("supervisor_progress.json")
    + safe_files("replay_progress.json")
    + safe_files("progress.json")
    + safe_files("owner_status.json")
    + safe_files("provision_state.json")
    + safe_files("restore_state.json")
    + safe_files("r0_supervisor_state.json")
    + safe_files("*run_state.json")
)
replay_progress_path = newest(safe_files("replay_progress.json"))
supervisor_progress_path = newest(safe_files("supervisor_progress.json"))
history_path = newest(
    safe_files("*optimizer_history*.jsonl")
    + safe_files("*history*.jsonl")
)
latest_log_path = newest(safe_files("*.log"))
progress = read_json(progress_path)
replay_progress = read_json(replay_progress_path)
supervisor_progress = read_json(supervisor_progress_path)
last_event = read_last_json(history_path)
first_event = read_first_json(history_path)
recent_history = read_recent_json(history_path)
recent_event_names = [
    str(item.get("event") or item.get("status") or item.get("phase") or "")
    for item in recent_history
]

def latest_named_event(name):
    for item in reversed(recent_history):
        if str(item.get("event") or "") == name:
            return item
    return {}

exception_event = latest_named_event("optimizer_process_exception")
exit_event = latest_named_event("optimizer_process_exit")
candidate_space_exhausted_event = latest_named_event(
    "experience_table_gradient_candidate_space_exhausted"
)
initial_replay_complete_event = latest_named_event(
    "experience_table_gradient_initial_real_replay_complete"
)
latest_subset_ranking_event = latest_named_event(
    "experience_table_all_subset_ranking"
)

round_indexes = []
event_name = ""
for value in (progress, last_event):
    event_name = str(
        value.get("status")
        or value.get("event")
        or value.get("phase")
        or event_name
    )
    for key in ("round_index", "generation", "iteration"):
        try:
            round_indexes.append(int(value[key]))
        except (KeyError, TypeError, ValueError):
            pass
if progress.get("stage"):
    event_name = str(progress["stage"])

subset_round = -1
subset_done = 0
subset_total = None
subset_evidence_mtime = 0.0
subset_groups = {}
for subset_file in safe_files("subset_*.jsonl"):
    match = re.search(r"/round_(\d+)[^/]*/", str(subset_file))
    if not match:
        continue
    parent = subset_file.parent
    group = subset_groups.setdefault(
        parent,
        {"round": int(match.group(1)), "paths": [], "mtime": 0.0},
    )
    group["paths"].append(subset_file)
    try:
        group["mtime"] = max(group["mtime"], subset_file.stat().st_mtime)
    except OSError:
        pass
if subset_groups:
    #  round_index
    #  3  127  381/127
    latest_group = max(
        subset_groups.values(),
        key=lambda item: (item["mtime"], item["round"]),
    )
    current_run_started_at = 0.0
    raw_start_timestamp = str(first_event.get("timestamp") or "")
    try:
        current_run_started_at = dt.datetime.strptime(
            raw_start_timestamp,
            "%Y-%m-%d %H:%M:%S",
        ).timestamp()
    except ValueError:
        pass
    # A restarted attempt can keep old subset directories under the same root.
    # Do not let those stale files override the startup/current phase emitted
    # by the newest optimizer history.
    if latest_group["mtime"] >= current_run_started_at:
        subset_round = latest_group["round"]
        subset_done = len({path.name for path in latest_group["paths"]})
        subset_evidence_mtime = latest_group["mtime"]
if subset_round >= 0:
    round_indexes.append(subset_round)
    try:
        subset_total = int(latest_subset_ranking_event.get("subset_count"))
    except (TypeError, ValueError):
        subset_total = None
    if not subset_total or subset_total < subset_done:
        # The latest ranking event may predate this attempt or may not exist
        # yet.  Keep the old count-based fallback only as a last resort.
        subset_total = 127 if subset_done > 63 else 63

accepted = progress.get("accepted_shrinks")
if accepted is None:
    accepted = progress.get("accepted_shrink_count")
if accepted is None:
    accepted = supervisor_progress.get("accepted_shrinks")
if accepted is None:
    accepted = read_counter([
        "formal_search_accepted_counter.txt",
        "formal_search_accepted.count",
        "*accepted*counter*.txt",
    ])
target = (
    progress.get("max_accepted_shrinks")
    or progress.get("accepted_shrink_target")
    or supervisor_progress.get("max_accepted_shrinks")
    or supervisor_progress.get("accepted_shrink_target")
    or 10
)

round_index = max(round_indexes, default=0)
lower_event = event_name.lower()
real_replay_events = {
    "pre_benchmark_snapshot_restore",
    "db_view_monitor_summary",
    "post_benchmark_cleanup",
    "feedback_final_validation",
    "feedback_degradation_summary",
}
terminal_detail = ""
replay_event = str(replay_progress.get("status") or "").lower()
terminal_words = (
    "complete", "reached", "rejected", "fatal", "failed", "stopped", "exhausted"
)
if not root.exists():
    stage = ""
elif "basin_exhausted" in lower_event:
    stage = ""
elif exception_event and not ps:
    stage = ""
    terminal_detail = str(
        exception_event.get("exception_message")
        or exception_event.get("message")
        or "optimizer_process_exception"
    )
elif not ps and candidate_space_exhausted_event:
    stage = ""
    terminal_detail = " incumbent "
elif progress.get("error") and not ps:
    stage = ": " + str(progress["error"])[:100]
    terminal_detail = str(progress["error"])
elif not ps and replay_progress and not any(
    word in replay_event for word in terminal_words
):
    stage = ""
    terminal_detail = "Replay progress"
elif not ps and exit_event:
    stage = ""
elif not ps and any(word in lower_event for word in terminal_words):
    stage = event_name
elif (
    progress_path is not None
    and progress_path.name in {"provision_state.json", "restore_state.json"}
    and progress.get("phase")
):
    action = "SF10 " if progress_path.name == "restore_state.json" else ""
    stage = action + "" + str(progress["phase"])
elif "replay" in lower_event:
    stage = ""
elif lower_event in real_replay_events:
    stage = ""
elif "plan" in lower_event or "fingerprint" in lower_event:
    stage = "/"
elif "rank" in lower_event or "subset" in lower_event:
    stage = ""
elif subset_done:
    stage = " %d/%d" % (subset_done, subset_total)
elif ps and ("calibration" in pattern.lower() or "retrain" in pattern.lower()):
    stage = "/"
elif ps:
    stage = "/"
elif latest_log_path is not None:
    stage = "/"
    terminal_detail = last_nonempty_line(latest_log_path)[-180:]
else:
    stage = event_name or ""

if subset_done and "" not in stage and not any(
    word in lower_event for word in (
        "replay", "complete", "rejected", "failed", "exit"
    )
) and lower_event not in real_replay_events and not terminal_detail:
    stage = " %d/%d" % (subset_done, subset_total)

def newest_replay_directory():
    candidates = []
    candidates.extend(safe_dirs("point_*"))
    candidates.extend(
        path for path in safe_dirs("candidate_*")
        if path.name != "candidate_measurements"
    )
    candidates.extend(
        path for path in safe_dirs("*")
        if path.parent.name == "candidate_measurements"
    )
    candidates.extend(
        path.parent for path in safe_files("summary.json")
        if "points" in path.parts or "r0" in path.parts
    )
    candidates.extend(
        path.parent for path in safe_files("formal_resource_samples.jsonl")
        if "points" in path.parts
    )
    # AP  generation_NN/replay
    candidates.extend(
        path for path in safe_dirs("replay")
        if re.search(r"/generation_\d+/replay$", str(path))
    )
    return newest(candidates)

replay_dir = newest_replay_directory()
replay_location = str(replay_dir) if replay_dir is not None else ""
replay_mtime = 0.0
replay_summary = {}
replay_summary_mtime = 0.0
if replay_dir is not None:
    try:
        replay_mtime = replay_dir.stat().st_mtime
    except OSError:
        pass
    replay_summary_path = replay_dir / "summary.json"
    replay_summary = read_json(replay_summary_path)
    try:
        replay_summary_mtime = replay_summary_path.stat().st_mtime
    except OSError:
        pass
progress_location = str(progress.get("location") or "")
if progress_location and (
    replay_dir is None or Path(progress_location) != root
):
    replay_location = progress_location
# A newly created/updated measurement after the last subset artifact means
# the optimizer has left offline subset estimation and entered real replay.
# Some benchmark runners emit generic snapshot/feedback events, so relying on
# an event name containing "replay" alone reports a false "127/127" stall.
if (
    ps
    and replay_dir is not None
    and subset_evidence_mtime > 0.0
    and replay_mtime > subset_evidence_mtime
    and "basin_exhausted" not in lower_event
):
    stage = ""
replay_status = ""
rank = replay_progress.get("current_rank")
replicate = replay_progress.get("current_replicate")
replayed = replay_progress.get("replayed_count")
maximum_top_k = replay_progress.get("maximum_top_k")
if "basin_exhausted" in lower_event:
    replay_status = ""
elif replay_progress:
    details = []
    if rank is not None:
        details.append("rank=%s" % rank)
    if replicate is not None:
        details.append("rep=%s" % replicate)
    if replayed is not None:
        suffix = "/%s" % maximum_top_k if maximum_top_k is not None else ""
        details.append("=%s%s" % (replayed, suffix))
    if ps:
        replay_state = ""
    elif any(word in replay_event for word in terminal_words):
        replay_state = str(replay_progress.get("status") or "")
    else:
        replay_state = ""
    replay_status = replay_state + (
        ("" + "".join(details)) if details else ""
    )
    if not replay_location and replay_progress_path is not None:
        replay_location = str(replay_progress_path.parent)
elif ps and replay_dir is not None:
    replay_status = "%s" % replay_dir.name
elif subset_done and "" in stage:
    replay_status = "=%d/%d" % (
        subset_done,
        subset_total,
    )
elif initial_replay_complete_event:
    validated = initial_replay_complete_event.get("validated_candidate_count")
    initial_batch = initial_replay_complete_event.get("initial_batch_size")
    replay_status = ""
    if validated is not None:
        replay_status += "=%s" % validated
    if initial_batch is not None:
        replay_status += "=%s" % initial_batch
    if initial_replay_complete_event.get("expanded_to_second_batch"):
        replay_status += ""
elif replay_dir is not None:
    command_text = "\n".join(ps)
    is_running = str(replay_dir) in command_text or replay_dir.name in command_text
    replay_status = ("" if is_running else "/") + "" + replay_dir.name
elif "replay" in lower_event:
    replay_status = ""

latest_paths = [
    p for p in (progress_path, history_path, latest_log_path)
    if p is not None
]
optimizer_evidence_mtime = max(
    (p.stat().st_mtime for p in latest_paths),
    default=(root.stat().st_mtime if root.exists() else 0.0),
)
progress_mtime = (
    progress_path.stat().st_mtime if progress_path is not None else 0.0
)
history_mtime = (
    history_path.stat().st_mtime if history_path is not None else 0.0
)
latest_mtime = max(
    optimizer_evidence_mtime,
    replay_mtime,
    replay_summary_mtime,
)

# A separately launched resource-profile point can be newer than the stopped
# optimizer history. Never combine that new point path with an old exception.
# When the point persists a complete six-window decision, report its own
# utilization and evaluate the frozen 85-90/30-50 construction bands.
decision_window = (
    replay_summary.get("r0_decision_window")
    if isinstance(replay_summary, dict)
    else {}
)
decision_utilization = (
    decision_window.get("utilization_pct")
    if isinstance(decision_window, dict)
    else {}
)
progress_terminal_after_point = (
    str(progress.get("status") or "").lower()
    in {"rolled_back", "failed", "complete", "stopped"}
    and replay_summary_mtime + 15.0 >= progress_mtime
)
if (
    replay_summary_mtime > history_mtime
    and (
        replay_summary_mtime >= progress_mtime
        or progress_terminal_after_point
    )
    and replay_summary.get("status") in {"complete", "failed"}
    and isinstance(decision_utilization, dict)
    and all(decision_utilization.get(name) is not None for name in ("cpu", "memory", "io"))
):
    utilization_values = {
        name: float(decision_utilization[name])
        for name in ("cpu", "memory", "io")
    }
    replay_status = (
        "CPU={cpu:.2f}%Memory={memory:.2f}%IO={io:.2f}%"
    ).format(**utilization_values)
    failures = []
    for name, value in utilization_values.items():
        low, high = ((85.0, 90.0) if name == target_resource else (30.0, 50.0))
        if value < low or value > high:
            failures.append(
                "%s=%.2f%%%.0f-%.0f%%" % (name, value, low, high)
            )
    if failures:
        stage = ""
        terminal_detail = "" + "".join(failures)
    else:
        stage = ""
        terminal_detail = ""

# TPC-H strict R0 emits one summary under r0/ per independent repeat.
# Re-evaluate all three resources here because the runner's historical gate
# only enforced an upper bound for non-target resources.
r0_summary_results = []
for r0_summary_path in safe_files("summary.json"):
    if "r0" not in r0_summary_path.relative_to(root).parts:
        continue
    summary = read_json(r0_summary_path)
    decision = summary.get("r0_decision_window") or {}
    utilization = decision.get("utilization_pct") or {}
    try:
        values = {
            name: float(utilization[name])
            for name in ("cpu", "memory", "io")
        }
    except (KeyError, TypeError, ValueError):
        continue
    passed = (
        summary.get("status") == "complete"
        and decision.get("stable") is True
        and 85.0 <= values["memory"] <= 90.0
        and 30.0 <= values["cpu"] <= 50.0
        and 30.0 <= values["io"] <= 50.0
    )
    try:
        summary_mtime = r0_summary_path.stat().st_mtime
    except OSError:
        summary_mtime = 0.0
    r0_summary_results.append({
        "path": r0_summary_path,
        "mtime": summary_mtime,
        "summary": summary,
        "utilization": values,
        "passed": passed,
    })

if r0_summary_results:
    r0_summary_results.sort(key=lambda item: item["mtime"])
    latest_strict_r0 = r0_summary_results[-1]
    strict_r0_passed = sum(1 for item in r0_summary_results if item["passed"])
    strict_r0_completed = len(r0_summary_results)
    values = latest_strict_r0["utilization"]
    replay_status = (
        "strict R0=%d/3 =%d/3 CPU=%.2f%%"
        "Memory=%.2f%%IO=%.2f%%"
        % (
            strict_r0_passed,
            strict_r0_completed,
            values["cpu"],
            values["memory"],
            values["io"],
        )
    )
    replay_location = str(latest_strict_r0["path"].parent)
    latest_mtime = max(latest_mtime, latest_strict_r0["mtime"])
    if strict_r0_completed < 3 and ps:
        stage = "strict R0  %d/3" % strict_r0_completed
        terminal_detail = ""
    elif strict_r0_completed >= 3 and strict_r0_passed == 3:
        stage = "strict R0 3/3 "
        terminal_detail = ""
    elif strict_r0_completed >= 3:
        stage = "strict R0 "
        terminal_detail = " %d/3 " % strict_r0_passed

if (
    progress_path is not None
    and progress_path.name == "r0_supervisor_state.json"
):
    supervisor_status = str(progress.get("status") or "unknown").lower()
    current_repeat = int(progress.get("current_repeat") or 0)
    replay_location = str(progress_path)
    if supervisor_status == "running":
        stage = "strict R0 repeat %d/3" % current_repeat
        if r0_summary_results:
            replay_status += " repeat=%d/3 " % current_repeat
        else:
            replay_status = "strict R0  repeat=%d/3" % current_repeat
        terminal_detail = ""
    elif supervisor_status == "failed":
        stage = "strict R0 "
        if r0_summary_results:
            replay_status += "supervisor  repeat=%d/3 " % current_repeat
        else:
            replay_status = "strict R0  repeat=%d/3" % current_repeat
        terminal_detail = str(
            progress.get("error")
            or progress.get("summary_path")
            or "strict R0 repeat failed"
        )
    elif supervisor_status == "complete":
        stage = "strict R0 3/3 "
        replay_status = "strict R0=3/3 "
        terminal_detail = ""

# JOB  fresh R0  repeat_NN/point_result.json replay
#  invalid  3/3
r0_results = []
for r0_result_path in safe_files("point_result.json"):
    relative_parts = r0_result_path.relative_to(root).parts
    if (
        len(relative_parts) < 3
        or relative_parts[-3] != "r0"
        or re.fullmatch(r"repeat_\d+", relative_parts[-2]) is None
    ):
        continue
    result = read_json(r0_result_path)
    measurement = result.get("measurement") if isinstance(result, dict) else {}
    utilization = (
        measurement.get("resource_mean_utilization")
        if isinstance(measurement, dict)
        else {}
    )
    stability = (
        measurement.get("resource_stability")
        if isinstance(measurement, dict)
        else {}
    )
    if not isinstance(utilization, dict):
        utilization = {}
    values = {}
    try:
        values = {
            name: float(utilization[name])
            for name in ("cpu", "memory", "io")
        }
    except (KeyError, TypeError, ValueError):
        values = {}
    passed = (
        result.get("status") == "complete"
        and isinstance(stability, dict)
        and stability.get("passed") is True
        and measurement.get("correctness_passed") is True
        and len(values) == 3
        and 0.85 <= values["memory"] <= 0.90
        and 0.30 <= values["cpu"] <= 0.50
        and 0.30 <= values["io"] <= 0.50
    )
    try:
        result_mtime = r0_result_path.stat().st_mtime
    except OSError:
        result_mtime = 0.0
    r0_results.append({
        "path": r0_result_path,
        "mtime": result_mtime,
        "measurement": measurement,
        "utilization": values,
        "passed": passed,
    })

if r0_results:
    r0_results.sort(key=lambda item: item["mtime"])
    latest_r0 = r0_results[-1]
    passed_r0 = sum(1 for item in r0_results if item["passed"])
    replay_location = str(latest_r0["path"].parent)
    latest_mtime = max(latest_mtime, latest_r0["mtime"])
    values = latest_r0["utilization"]
    details = "R0=%d/3 " % passed_r0
    if len(values) == 3:
        details += (
            "DB QPS={qps:.3f}CPU={cpu:.2f}%Memory={memory:.2f}%"
            "IO={io:.2f}%"
        ).format(
            qps=float(latest_r0["measurement"].get("db_qps") or 0.0),
            cpu=100.0 * values["cpu"],
            memory=100.0 * values["memory"],
            io=100.0 * values["io"],
        )
    replay_status = details
    if passed_r0 >= 3:
        calibration_paths = safe_files("*fresh_model_calibration.jsonl")
        calibration_count = 0
        for calibration_path in calibration_paths:
            try:
                calibration_count += sum(
                    1
                    for line in calibration_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    if line.strip()
                )
            except OSError:
                pass
        calibration_dirs = [
            path
            for path in safe_dirs("*")
            if path.parent.name == "points"
            and re.fullmatch(r"scope\d+_axis_\d+_attempt_\d+", path.name)
        ]
        latest_calibration_dir = newest(calibration_dirs)
        if ps and ("calibration" in pattern.lower() or calibration_dirs):
            stage = "/"
            expected_count = calibration_expected or 30
            replay_status += "=%d/%d" % (
                calibration_count,
                expected_count,
            )
            if expected_count == 72 and calibration_count >= 30:
                replay_status += "30/30 + endpoint %d/42" % (
                    calibration_count - 30
                )
            if latest_calibration_dir is not None:
                replay_location = str(latest_calibration_dir)
        else:
            stage = "R0 "
        terminal_detail = ""
    elif ps:
        stage = "R0 "
    else:
        stage = "R0 /"
        terminal_detail = " R0  %d/3 invalid " % passed_r0

if progress_path is not None and progress_path.name in {
    "provision_state.json",
    "restore_state.json",
}:
    provision_phase = str(progress.get("phase") or "unknown")
    action = "SF10 " if progress_path.name == "restore_state.json" else ""
    replay_status = action + "phase=" + provision_phase
    replay_location = str(progress_path)
    if ps:
        stage = action + "" + provision_phase
    elif str(progress.get("status") or "").lower() == "complete":
        stage = action + " R0"
    elif str(progress.get("status") or "").lower() == "failed":
        stage = action + ""
        terminal_detail = str(progress.get("error") or "provision failed")

ranker_path = newest(safe_files("*direction_ranker*.json"))
ranker = read_json(ranker_path)
if ranker_path is not None and "calibration" in pattern.lower():
    calibration_summaries = [
        path
        for path in safe_files("summary.json")
        if "fresh_calibration" in str(path)
    ]
    calibration_rows = 0
    for calibration_path in safe_files("*fresh_model_calibration.jsonl"):
        try:
            calibration_rows += sum(
                1
                for line in calibration_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if line.strip()
            )
        except OSError:
            pass
    ready = ranker.get("ready") is True
    reason = str(ranker.get("reason") or "unknown")
    replay_status = (
        "=%d=%dranker_ready=%s"
        % (len(calibration_summaries), calibration_rows, str(ready).lower())
    )
    replay_location = str(ranker_path)
    try:
        latest_mtime = max(latest_mtime, ranker_path.stat().st_mtime)
    except OSError:
        pass
    if not ps and ready:
        stage = "ranker "
        terminal_detail = ""
    elif not ps:
        stage = "ranker "
        terminal_detail = reason
    elif ps:
        stage = "/"

# TPC-C and the expanded XP batch both append one terminal row per
# calibration_id. Count the latest terminal state for each ID instead of
# counting point directories, because a technical retry can create up to three
# measurement directories. A ranker older than new calibration evidence is
# explicitly reported as pending retraining.
calibration_records = {}
calibration_evidence_mtime = 0.0
for calibration_path in safe_files("*fresh_model_calibration.jsonl"):
    try:
        calibration_evidence_mtime = max(
            calibration_evidence_mtime,
            calibration_path.stat().st_mtime,
        )
        lines = calibration_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        continue
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not row.get("calibration_id"):
            continue
        record_key = "%s::%s" % (
            str(calibration_path),
            str(row["calibration_id"]),
        )
        calibration_records[record_key] = row

if calibration_expected and calibration_records:
    usable_count = sum(
        1 for row in calibration_records.values()
        if row.get("status") == "usable"
    )
    technical_failure_count = sum(
        1 for row in calibration_records.values()
        if row.get("status") == "technical_failure"
    )
    completed_count = usable_count + technical_failure_count
    summary_paths = [
        path
        for path in safe_files("summary.json")
        if "calibration" in str(path)
    ]
    details = (
        "=%d/%dusable=%dtechnical_failure=%d"
        % (
            completed_count,
            calibration_expected,
            usable_count,
            technical_failure_count,
        )
    )
    if calibration_summary_expected:
        details += "=%d/%d" % (
            len(summary_paths),
            calibration_summary_expected,
        )
    ranker_mtime = 0.0
    if ranker_path is not None:
        try:
            ranker_mtime = ranker_path.stat().st_mtime
        except OSError:
            pass
    ranker_stale = calibration_evidence_mtime > ranker_mtime
    if ranker_path is not None:
        if ranker_stale:
            details += " ranker "
        else:
            details += "ranker_ready=%s" % str(
                ranker.get("ready") is True
            ).lower()
    replay_status = details
    latest_mtime = max(latest_mtime, calibration_evidence_mtime)
    if replay_dir is not None:
        replay_location = str(replay_dir)
    elif calibration_records:
        latest_record = max(
            calibration_records.values(),
            key=lambda row: str(row.get("calibration_id") or ""),
        )
        raw_result = latest_record.get("raw_result") or {}
        replay_location = str(
            raw_result.get("summary_path")
            or replay_location
            or ranker_path
            or root
        )
    if ps and completed_count < calibration_expected:
        stage = "/"
        terminal_detail = ""
    elif ps:
        stage = "/"
        terminal_detail = ""
    elif completed_count < calibration_expected:
        stage = ""
        terminal_detail = (
            " %d/%d "
            % (completed_count, calibration_expected)
        )
    elif ranker_path is None or ranker_stale:
        stage = " ranker"
        terminal_detail = (
            "%d %d "
            % (usable_count, technical_failure_count)
        )
    elif ranker.get("ready") is True:
        stage = "ranker "
        terminal_detail = ""
    else:
        stage = "ranker "
        terminal_detail = str(ranker.get("reason") or "unknown")

if (
    ps
    and calibration_expected
    and "calibration" in pattern.lower()
    and not calibration_records
):
    stage = "/"
    replay_status = "=0/%d" % calibration_expected
    terminal_detail = ""

print(json.dumps({
    "attempt": root.name,
    "alive": bool(ps),
    "pid_lines": ps[:3],
    "root_exists": root.exists(),
    "round_index": round_index,
    "accepted": accepted,
    "accepted_target": target,
    "stage": stage,
    "event": event_name,
    "subset_done": subset_done or None,
    "subset_total": subset_total,
    "latest_mtime": latest_mtime,
    "progress_path": str(progress_path) if progress_path else None,
    "history_path": str(history_path) if history_path else None,
    "replay_status": replay_status,
    "replay_location": replay_location,
    "terminal_detail": terminal_detail,
}, ensure_ascii=False))
"""


def run_probe(experiment: Dict[str, Any]) -> Dict[str, Any]:
    connect_timeout = max(
        5, int(experiment.get("ssh_connect_timeout_seconds") or 20)
    )
    probe_timeout = max(
        connect_timeout + 10,
        int(experiment.get("probe_timeout_seconds") or 90),
    )
    benchmark_label = str(experiment.get("benchmark") or "").strip().lower()
    target_resource = next(
        (
            name
            for name in ("memory", "cpu", "io")
            if benchmark_label.endswith("/ " + name)
        ),
        "",
    )
    command = [
        sys.executable,
        "-c",
        REMOTE_PROBE,
        str(experiment["root"]),
        str(experiment["process_pattern"]),
        target_resource,
        str(int(experiment.get("calibration_expected") or 0)),
        str(int(experiment.get("calibration_summary_expected") or 0)),
    ]
    ssh = experiment.get("ssh")
    if ssh:
        encoded_probe = base64.b64encode(REMOTE_PROBE.encode("utf-8")).decode("ascii")
        remote_code = (
            "import base64;"
            f"exec(base64.b64decode({encoded_probe!r}).decode('utf-8'))"
        )
        remote_command = " ".join(
            [
                "env",
                "PYTHONIOENCODING=utf-8",
                "python3",
                "-c",
                shlex.quote(remote_code),
                shlex.quote(str(experiment["root"])),
                shlex.quote(str(experiment["process_pattern"])),
                shlex.quote(target_resource),
                shlex.quote(str(int(experiment.get("calibration_expected") or 0))),
                shlex.quote(str(int(experiment.get("calibration_summary_expected") or 0))),
            ]
        )
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=2",
            str(ssh),
            remote_command,
        ]
    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=probe_timeout,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        payload = json.loads(lines[-1])
        unit = experiment.get("systemd_unit")
        if unit and not ssh:
            unit_status = subprocess.run(
                ["systemctl", "--user", "is-active", str(unit)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            unit_alive = unit_status.stdout.strip() == "active"
            probe_saw_process = bool(payload.get("alive"))
            payload["alive"] = unit_alive or probe_saw_process
            if unit_alive and not probe_saw_process:
                current_event = str(payload.get("event") or "").lower()
                if payload.get("subset_done"):
                    payload["stage"] = " %s/%s" % (
                        payload["subset_done"],
                        payload.get("subset_total") or "?",
                    )
                elif "replay" in current_event or "validation" in current_event:
                    payload["stage"] = ""
                elif "plan" in current_event or "fingerprint" in current_event:
                    payload["stage"] = "/"
                else:
                    payload["stage"] = "/"
                payload["terminal_detail"] = ""
        local_ranker_path = experiment.get("local_ranker_path")
        if local_ranker_path:
            ranker_path = Path(str(local_ranker_path))
            try:
                ranker = json.loads(ranker_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ranker = {}
            if isinstance(ranker, dict) and ranker:
                ranker_ready = ranker.get("ready") is True
                payload["replay_status"] = (
                    str(payload.get("replay_status") or "")
                    + "local_ranker_ready="
                    + str(ranker_ready).lower()
                ).strip("")
                if not payload.get("alive") and not ranker_ready:
                    payload["stage"] = "ranker "
                    payload["terminal_detail"] = str(
                        ranker.get("reason") or "unknown"
                    )
                    payload["replay_location"] = str(ranker_path)
        payload["probe_error"] = ""
        return payload
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return {
            "alive": None,
            "round_index": 0,
            "accepted": None,
            "accepted_target": 10,
            "stage": "",
            "latest_mtime": 0,
            "probe_error": detail[-300:],
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, subprocess.TimeoutExpired):
            detail = f"TimeoutExpired: remote probe exceeded {exc.timeout}s"
        return {
            "alive": None,
            "round_index": 0,
            "accepted": None,
            "accepted_target": 10,
            "stage": "",
            "latest_mtime": 0,
            "probe_error": detail[-300:],
        }


def cell(value: Any) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def render(config: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    now = dt.datetime.now().astimezone()
    lines = [
        "# PostgreSQL ",
        "",
        f"- {now.isoformat(timespec='seconds')}",
        f"-  {int(config['interval_seconds']) // 60} ",
        "",
        "| Experiment benchmark | Machine |  attempt | Alive | Outer-loop exploration | Accepted refinement | Current phase | Replay progress | Replay and measurement location | Latest evidence | Blocking issue |",
        "|---|---|---|---:|---:|---:|---|---|---|---|---|",
    ]
    for experiment, row in zip(config["experiments"], rows):
        latest = "—"
        if row.get("latest_mtime"):
            latest = dt.datetime.fromtimestamp(
                float(row["latest_mtime"]), tz=dt.timezone.utc
            ).astimezone().strftime("%H:%M:%S")
        accepted = row.get("accepted")
        accepted_text = (
            f"{accepted}/{row.get('accepted_target', 10)}"
            if accepted is not None
            else ""
        )
        error_parts = [
            str(value)
            for value in (
                row.get("probe_error") or row.get("terminal_detail"),
                experiment.get("operator_note"),
            )
            if value
        ]
        error = "".join(error_parts)
        entered_outer = bool(
            row.get("subset_done")
            or int(row.get("accepted") or 0) > 0
            or int(row.get("round_index") or 0) > 0
        )
        outer_text = (
            f"#{int(row.get('round_index') or 0) + 1} "
            f"(index={int(row.get('round_index') or 0)})"
            if entered_outer
            else ""
        )
        replay_status_text = str(row.get("replay_status") or "")
        if row.get("evidence_stale"):
            replay_status_text += ""
        scheduled_action = str(experiment.get("scheduled_action") or "").strip()
        if scheduled_action:
            replay_status_text += "=" + scheduled_action
        lines.append(
            "| {benchmark} | {machine} | {attempt} | {alive} | {outer} "
            "| {accepted} | {stage} | {replay_status} | {replay_location} "
            "| {latest} | {error} |".format(
                benchmark=cell(experiment["benchmark"]),
                machine=cell(experiment["machine"]),
                attempt=cell(row.get("attempt") or Path(experiment["root"]).name),
                alive=(
                    ""
                    if row.get("alive") is None
                    else ("" if row.get("alive") else "")
                ),
                outer=outer_text,
                accepted=cell(accepted_text),
                stage=cell(row.get("stage") or ""),
                replay_status=cell(replay_status_text),
                replay_location=cell(row.get("replay_location") or "—"),
                latest=latest,
                error=cell(error) if error else "—",
            )
        )
    lines.append("")
    return "\n".join(lines)


def snapshot(config_path: Path) -> str:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_rows: List[Dict[str, Any]] = [
        {} for _ in config["experiments"]
    ]
    snapshots_path = output_dir / "snapshots.jsonl"
    if snapshots_path.exists():
        try:
            lines = snapshots_path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                historical_rows = json.loads(line).get("rows") or []
                for index, historical in enumerate(historical_rows):
                    if index >= len(previous_rows) or previous_rows[index]:
                        continue
                    if historical and not historical.get("probe_error"):
                        previous_rows[index] = historical
                if all(previous_rows):
                    break
        except (OSError, ValueError, IndexError, AttributeError):
            previous_rows = [{} for _ in config["experiments"]]
    rows = [run_probe(item) for item in config["experiments"]]
    for index, row in enumerate(rows):
        if not row.get("probe_error") or index >= len(previous_rows):
            continue
        previous = previous_rows[index]
        if not previous or previous.get("probe_error"):
            continue
        error = row["probe_error"]
        row = dict(previous)
        row.update(
            {
                "alive": None,
                "probe_error": error,
                "evidence_stale": True,
            }
        )
        rows[index] = row
    markdown = render(config, rows)
    (output_dir / "latest.md").write_text(markdown, encoding="utf-8")
    with (output_dir / "snapshots.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "captured_at": dt.datetime.now().astimezone().isoformat(),
                    "rows": rows,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print(markdown, flush=True)
    return markdown


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    interval = max(30, int(args.interval or config["interval_seconds"]))
    while True:
        snapshot(args.config)
        if not args.watch:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
