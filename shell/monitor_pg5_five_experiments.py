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
    ROOT / "config/formal/pg5_five_experiments_live_20260723.json"
)

REMOTE_PROBE = r"""
import json
import os
from collections import deque
import datetime as dt
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1])
pattern = sys.argv[2]
target_resource = str(sys.argv[3] if len(sys.argv) > 3 else "").strip().lower()

def safe_files(glob):
    try:
        return [p for p in root.rglob(glob) if p.is_file()]
    except (OSError, PermissionError):
        return []

def safe_dirs(glob):
    try:
        return [p for p in root.rglob(glob) if p.is_dir()]
    except (OSError, PermissionError):
        return []

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

ps = subprocess.run(
    ["pgrep", "-af", pattern],
    universal_newlines=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
).stdout.splitlines()
ps = [
    line for line in ps
    if "monitor_pg5_five_experiments" not in line
    and "python3 -c" not in line
]

progress_path = newest(
    safe_files("supervisor_progress.json")
    + safe_files("replay_progress.json")
    + safe_files("progress.json")
)
replay_progress_path = newest(safe_files("replay_progress.json"))
supervisor_progress_path = newest(safe_files("supervisor_progress.json"))
history_path = newest(
    safe_files("*optimizer_history*.jsonl")
    + safe_files("*history*.jsonl")
)
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
terminal_words = ("complete", "reached", "rejected", "fatal", "failed", "stopped")
if not root.exists():
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
elif ps:
    stage = "/"
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
if replay_dir is not None:
    try:
        replay_mtime = replay_dir.stat().st_mtime
    except OSError:
        pass
    replay_summary = read_json(replay_dir / "summary.json")
# A newly created/updated measurement after the last subset artifact means
# the optimizer has left offline subset estimation and entered real replay.
# Some benchmark runners emit generic snapshot/feedback events, so relying on
# an event name containing "replay" alone reports a false "127/127" stall.
if (
    ps
    and replay_dir is not None
    and subset_evidence_mtime > 0.0
    and replay_mtime > subset_evidence_mtime
):
    stage = ""
replay_status = ""
rank = replay_progress.get("current_rank")
replicate = replay_progress.get("current_replicate")
replayed = replay_progress.get("replayed_count")
maximum_top_k = replay_progress.get("maximum_top_k")
if replay_progress:
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
elif ps and stage == "" and replay_dir is not None:
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

latest_paths = [p for p in (progress_path, history_path) if p is not None]
optimizer_evidence_mtime = max(
    (p.stat().st_mtime for p in latest_paths),
    default=(root.stat().st_mtime if root.exists() else 0.0),
)
latest_mtime = max(optimizer_evidence_mtime, replay_mtime)

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
if (
    replay_mtime > optimizer_evidence_mtime
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
            ]
        )
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
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
            timeout=30,
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
            payload["alive"] = unit_alive
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
        payload["probe_error"] = ""
        return payload
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return {
            "alive": False,
            "round_index": 0,
            "accepted": None,
            "accepted_target": 10,
            "stage": "",
            "latest_mtime": 0,
            "probe_error": detail[-300:],
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {
            "alive": False,
            "round_index": 0,
            "accepted": None,
            "accepted_target": 10,
            "stage": "",
            "latest_mtime": 0,
            "probe_error": f"{type(exc).__name__}: {exc}",
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
        error = row.get("probe_error") or row.get("terminal_detail") or ""
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
        lines.append(
            "| {benchmark} | {machine} | {attempt} | {alive} | {outer} "
            "| {accepted} | {stage} | {replay_status} | {replay_location} "
            "| {latest} | {error} |".format(
                benchmark=cell(experiment["benchmark"]),
                machine=cell(experiment["machine"]),
                attempt=cell(row.get("attempt") or Path(experiment["root"]).name),
                alive="" if row.get("alive") else "",
                outer=outer_text,
                accepted=cell(accepted_text),
                stage=cell(row.get("stage") or ""),
                replay_status=cell(row.get("replay_status") or ""),
                replay_location=cell(row.get("replay_location") or "—"),
                latest=latest,
                error=cell(error) if error else "—",
            )
        )
    lines.append("")
    return "\n".join(lines)


def snapshot(config_path: Path) -> str:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rows = [run_probe(item) for item in config["experiments"]]
    markdown = render(config, rows)
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
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
