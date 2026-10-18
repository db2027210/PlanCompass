#!/usr/bin/env python3
"""Read-only five-minute monitor for the frozen four-lane PostgreSQL bundle."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
from datetime import datetime


JUMP = "anonymous@192.0.2.10"
LANES = [
    {
        "benchmark": "JOB",
        "machine": "node02",
        "target": "anonymous@192.0.2.4",
        "root": "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726",
        "pattern": "attempt_7_cap5_contract_20260726|workspace_attempt7",
    },
    {
        "benchmark": "TPC-H",
        "machine": "node01",
        "target": "anonymous@192.0.2.3",
        "root": "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726",
        "pattern": "attempt48_global_replay_cap5_20260726|run_tpch_weighted_point_attempt48",
    },
    {
        "benchmark": "TPC-C",
        "machine": "compute00/disk2",
        "target": JUMP,
        "root": "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726",
        "pattern": "attempt12_plan_cluster_fallback_20260726|pg5-live-tpcc-a12-uncertainty",
    },
    {
        "benchmark": "XP",
        "machine": "compute00/disk1",
        "target": JUMP,
        "root": "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726",
        "pattern": "attempt39_fresh_from_accepted3_20260726|xp_round2_eta_plan_scan.py",
    },
]


REMOTE_PROBE = r'''
import json
import re
import subprocess
from pathlib import Path

root = Path(__ROOT__)
pattern = __PATTERN__

def run(args):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=8).stdout.strip()

processes = run(["pgrep", "-af", pattern])
process_lines = [line for line in processes.splitlines()
                 if "pgrep -af" not in line and "monitor_5min" not in line]

accepted = None
counter_path = None
counter_candidates = []
if root.exists():
    counter_candidates = sorted(
        [p for p in root.rglob("*.txt")
         if "accepted" in p.name.lower() and "counter" in p.name.lower()],
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
for path in counter_candidates:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except Exception:
        continue
    accepted = value
    counter_path = str(path)
    break

events = []
event_path = None
if root.exists():
    event_candidates = sorted(
        [p for p in root.rglob("*.jsonl")
         if any(token in p.name.lower() for token in ("history", "progress", "event"))],
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    if event_candidates:
        event_path = event_candidates[0]
        try:
            lines = event_path.read_text(encoding="utf-8", errors="replace").splitlines()[-800:]
        except Exception:
            lines = []
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict) and item.get("event"):
                events.append(item)

latest = events[-1] if events else {}
outer_round = None
candidate = None
replay_total = 5
for item in reversed(events):
    if outer_round is None:
        outer_round = item.get("round_index", item.get("outer_round"))
    if candidate is None:
        candidate = item.get("candidate_index", item.get("probe_index", item.get("index")))
    if accepted is None:
        accepted = item.get("accepted_shrink_count", item.get("accepted_shrinks"))
    if outer_round is not None and candidate is not None and accepted is not None:
        break

event_name = str(latest.get("event") or "no_event")
error_tokens = ("error", "failed", "exception", "technical_failure", "fatal")
blocker = "-"
for item in reversed(events[-80:]):
    name = str(item.get("event") or "").lower()
    if any(token in name for token in error_tokens):
        blocker = str(item.get("error") or item.get("reason") or item.get("event"))[:120]
        break

print(json.dumps({
    "alive": bool(process_lines),
    "pid": process_lines[0].split(maxsplit=1)[0] if process_lines else None,
    "outer_round": outer_round,
    "accepted": accepted,
    "stage": event_name,
    "replay": f"{candidate or 0}/{replay_total}",
    "location": str(event_path or counter_path or root),
    "evidence": str(latest.get("timestamp") or latest.get("time") or "-")[:40],
    "blocker": blocker,
}, ensure_ascii=True))
'''


def ssh_command(lane: dict[str, str], encoded: str) -> list[str]:
    remote = f"echo {encoded} | base64 -d | python3 -"
    if lane["target"] == JUMP:
        return [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            JUMP, remote,
        ]
    return [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new", "-J", JUMP,
        lane["target"], remote,
    ]


def probe(lane: dict[str, str]) -> dict[str, object]:
    source = REMOTE_PROBE.replace("__ROOT__", repr(lane["root"]))
    source = source.replace("__PATTERN__", repr(lane["pattern"]))
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    try:
        proc = subprocess.run(
            ssh_command(lane, encoded),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        payload = json.loads(lines[-1]) if lines else {}
        if proc.returncode != 0 or not isinstance(payload, dict):
            raise RuntimeError(proc.stdout.strip() or f"ssh exit {proc.returncode}")
        return payload
    except Exception as error:
        return {
            "alive": False,
            "pid": None,
            "outer_round": None,
            "accepted": None,
            "stage": "monitor_error",
            "replay": "-/5",
            "location": lane["root"],
            "evidence": "-",
            "blocker": str(error)[:120],
        }


def render() -> None:
    print(f"\nSnapshot: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print("| benchmark | machine | alive/PID | outer | accepted | stage | replay | location | evidence | blocker |")
    print("|---|---|---|---:|---:|---|---:|---|---|---|")
    for lane in LANES:
        result = probe(lane)
        alive = f"yes/{result.get('pid')}" if result.get("alive") else "no/-"
        values = [
            lane["benchmark"], lane["machine"], alive,
            result.get("outer_round") or "-", result.get("accepted") if result.get("accepted") is not None else "-",
            result.get("stage") or "-", result.get("replay") or "-/5",
            result.get("location") or "-", result.get("evidence") or "-",
            str(result.get("blocker") or "-").replace("|", "/"),
        ]
        print("| " + " | ".join(str(value) for value in values) + " |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    while True:
        render()
        if args.once:
            return
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
