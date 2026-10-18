#!/usr/bin/env python3
"""Five-minute table monitor. Run directly on compute00."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
from datetime import datetime


LANES = (
    {
        "benchmark": "JOB", "machine": "node02", "host": "anonymous@192.0.2.4",
        "root": "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726",
        "pattern": "[p]arameter_optimizer.py|[a]ttempt_7_cap5_contract_20260726", "accepted_default": 0,
    },
    {
        "benchmark": "TPC-H", "machine": "node01", "host": "anonymous@192.0.2.3",
        "root": "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726",
        "pattern": "[a]ttempt48_r0_then_optimizer|[r]un_pg_weighted_sql|[p]arameter_optimizer.py", "accepted_default": 0,
    },
    {
        "benchmark": "TPC-C", "machine": "compute00/disk2", "host": None,
        "root": "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726",
        "pattern": "[r]un_with_joblib|[r]un_tpcc_sensitive|[a]ttempt12_plan_cluster_fallback", "accepted_default": 1,
    },
    {
        "benchmark": "XP", "machine": "compute00/disk1", "host": None,
        "root": "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726",
        "pattern": "[x]p_round.*plan_scan|[f]rozen_next_direction_batch|[a]ttempt39_fresh_from_accepted3", "accepted_default": 3,
    },
)

PROBE = r'''
import json, re
from datetime import datetime
from pathlib import Path
root = Path(__ROOT__)
pattern = __PATTERN__
accepted = __ACCEPTED__

processes = []
for proc_dir in Path("/proc").glob("[0-9]*"):
    try:
        process_pid = proc_dir.name
        cmdline = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        if not cmdline or not re.search(pattern, cmdline): continue
        if "monitor_four_lanes" in cmdline: continue
        cwd = str((proc_dir / "cwd").resolve())
    except Exception:
        continue
    if str(root) in cmdline or cwd.startswith(str(root)):
        processes.append(f"{process_pid} {cmdline}")
pid = processes[0].split(None, 1)[0] if processes else None

for path in sorted(root.rglob("*accepted*counter*.txt"), key=lambda p: p.stat().st_mtime_ns, reverse=True)[:8] if root.exists() else []:
    try:
        accepted = int(path.read_text().strip()); break
    except Exception:
        pass

latest_summary = None
summaries = sorted(root.rglob("*summary*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)[:1] if root.exists() else []
if summaries:
    try: latest_summary = json.loads(summaries[0].read_text(errors="replace"))
    except Exception: latest_summary = {}
summary_path = str(summaries[0]) if summaries else str(root)

progress_files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime_ns, reverse=True)[:12] if root.exists() else []
events = []
event_path = None
for path in progress_files:
    if not any(x in path.name.lower() for x in ("progress", "event", "history")): continue
    try: lines = path.read_text(errors="replace").splitlines()[-400:]
    except Exception: continue
    parsed = []
    for line in lines:
        try:
            item = json.loads(line)
            if isinstance(item, dict): parsed.append(item)
        except Exception: pass
    if parsed:
        events = parsed; event_path = str(path); break

last = events[-1] if events else {}
outer = next((x.get("outer_round", x.get("round_index")) for x in reversed(events)
              if x.get("outer_round", x.get("round_index")) is not None), None)
replay = next((x.get("candidate_index", x.get("probe_index")) for x in reversed(events)
               if x.get("candidate_index", x.get("probe_index")) is not None), 0)
stage = str(last.get("event") or "")
blocker = "-"
evidence = str(last.get("timestamp") or last.get("time") or "-")
if latest_summary is not None:
    status = str(latest_summary.get("status") or latest_summary.get("round_outcome") or "")
    failure_stage = str(latest_summary.get("failure_stage") or "")
    error = str(latest_summary.get("error") or latest_summary.get("stop_reason") or "")
    if not stage: stage = failure_stage or status or "summary"
    if latest_summary.get("real_replay_count") is not None:
        replay = latest_summary["real_replay_count"]
    if outer is None:
        scan_id = str(latest_summary.get("scan_id") or "")
        match = re.search(r"round[_-]?(\d+)", scan_id, re.IGNORECASE)
        if match: outer = int(match.group(1))
    if status.lower() in {"failed", "error", "rejected"} or error:
        blocker = (error or status)[:160]
    elif "rejected" in status.lower() or "no_feasible" in status.lower():
        blocker = status[:160]
    try:
        evidence = str(datetime.fromtimestamp(summaries[0].stat().st_mtime).astimezone().isoformat(timespec="seconds"))
    except Exception:
        pass
if not stage: stage = "running" if pid else "idle"
if not pid and stage == "running": stage = "exited"

print(json.dumps({
    "pid": pid, "outer": outer, "accepted": accepted, "stage": stage,
    "replay": replay, "location": event_path or summary_path,
    "evidence": evidence, "blocker": blocker,
}, ensure_ascii=True))
'''


def probe(lane: dict[str, object]) -> dict[str, object]:
    source = PROBE.replace("__ROOT__", repr(lane["root"]))
    source = source.replace("__PATTERN__", repr(lane["pattern"]))
    source = source.replace("__ACCEPTED__", repr(lane["accepted_default"]))
    if lane["host"]:
        encoded = base64.b64encode(source.encode()).decode()
        args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                str(lane["host"]), f"echo {encoded} | base64 -d | python3 -"]
    else:
        args = ["python3", "-"]
    try:
        result = subprocess.run(args, input=None if lane["host"] else source, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=30, check=False)
        lines = [x for x in result.stdout.splitlines() if x.strip()]
        output = result.stdout.strip()
        if result.returncode != 0:
            raise RuntimeError(
                f"monitor_transport_failed returncode={result.returncode} "
                f"output={output or '<empty>'}"
            )
        if not lines:
            raise RuntimeError(
                "monitor_empty_output returncode=0 command_completed_without_json"
            )
        try:
            value = json.loads(lines[-1])
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"monitor_non_json_output last_line={lines[-1][:120]!r} "
                f"decode_error={error}"
            ) from error
        if not isinstance(value, dict):
            raise RuntimeError(
                f"monitor_invalid_payload_type type={type(value).__name__}"
            )
        return value
    except Exception as error:
        return {"pid": None, "outer": None, "accepted": lane["accepted_default"],
                "stage": "monitor_error", "replay": 0, "location": lane["root"],
                "evidence": "-", "blocker": str(error)[:160]}


def render() -> None:
    print(f"\nSnapshot: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print("| benchmark | machine | alive/PID | outer | accepted/10 | stage | replay x/5 | real replay/measurement location | recent evidence | blocker |")
    print("|---|---|---|---:|---:|---|---:|---|---|---|")
    for lane in LANES:  # Sequential by design: avoid SSH banner timeouts.
        result = probe(lane)
        pid = result.get("pid")
        row = (
            lane["benchmark"], lane["machine"], f"yes/{pid}" if pid else "no/-",
            result.get("outer") if result.get("outer") is not None else "-",
            f"{result.get('accepted', '-')}/10", result.get("stage") or "-",
            f"{result.get('replay', 0)}/5", result.get("location") or "-",
            result.get("evidence") or "-", str(result.get("blocker") or "-").replace("|", "/"),
        )
        print("| " + " | ".join(map(str, row)) + " |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    while True:
        render()
        if args.once: return
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
