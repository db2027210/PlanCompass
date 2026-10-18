#!/usr/bin/env python3
"""Watch a Bonect phase-2 run for startup/runtime evidence.

This script is intentionally read-only with respect to benchmark execution. It
does not start or kill HyBench/Bonect jobs; it only records runner/supervisor,
optimizer-history, verifier, and control-decision evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "pg_bonect_phase2_dbqps_amp_binary_top20_prune_v122_20260702_025943"
DEFAULT_HISTORY_ROOT = PROJECT_ROOT / "history" / "pg_benchmark_bottleneck_matrix"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "report" / "bonect"


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _alive(pid: int | None) -> bool:
    return bool(pid and Path(f"/proc/{pid}").exists())


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    yield item
    except FileNotFoundError:
        return


def _event_summary(history_paths: List[Path]) -> Dict[str, Any]:
    counts: Counter[str] = Counter()
    latest_by_event: Dict[str, Dict[str, Any]] = {}
    for path in history_paths:
        for item in _iter_jsonl(path):
            event = str(item.get("event") or "")
            if not event:
                continue
            counts[event] += 1
            latest_by_event[event] = item
    interesting = [
        "baseline_runtime_resource_summary",
        "baseline_top_sql",
        "candidate_surrogate_probe_start",
        "candidate_surrogate_probe_end",
        "bruteforce_initial_topk_replay_pruned",
        "bruteforce_initial_topk_replay_start",
        "bruteforce_replay_candidate_pruned",
        "small_loop_ranking_goal_summary",
        "final_feedback_best_primary_by_db_qps_updated",
        "final_feedback_round_complete",
    ]
    return {
        "history_file_count": len(history_paths),
        "history_paths": [str(path) for path in history_paths],
        "event_counts": dict(counts),
        "interesting_event_counts": {name: counts.get(name, 0) for name in interesting},
        "latest_interesting_events": {
            name: latest_by_event.get(name)
            for name in interesting
            if name in latest_by_event
        },
    }


def _path_info(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _list_files(paths: List[Path]) -> List[Dict[str, Any]]:
    items = []
    for path in sorted(paths):
        info = _path_info(path)
        info["path"] = str(path)
        items.append(info)
    return items


def _run_verifier(args: argparse.Namespace) -> Dict[str, Any]:
    verifier_json = Path(args.verifier_json_output)
    verifier_md = Path(args.verifier_md_output)
    cmd = [
        "python3",
        "shell/verify_bonect_phase2_run.py",
        "--run-id",
        args.run_id,
        "--history-root",
        args.history_root,
        "--bottleneck",
        args.bottleneck,
        "--benchmark",
        args.benchmark,
        "--launcher-pid",
        str(args.launcher_pid or ""),
        "--json-output",
        str(verifier_json),
        "--md-output",
        str(verifier_md),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(10, int(args.verifier_timeout_sec)),
            check=False,
        )
        result = {
            "command": cmd,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
            "json_output": str(verifier_json),
            "md_output": str(verifier_md),
        }
        if verifier_json.exists():
            try:
                data = json.loads(verifier_json.read_text(encoding="utf-8"))
            except Exception as exc:
                data = {"_error": str(exc)}
            result["summary"] = {
                "phase2_ready_for_claim": data.get("phase2_ready_for_claim"),
                "required_checks": data.get("required_checks"),
                "surrogate_backprop": data.get("surrogate_backprop"),
            }
        return result
    except Exception as exc:
        return {"command": cmd, "error": f"{type(exc).__name__}: {exc}"}


def _build_snapshot(args: argparse.Namespace) -> Dict[str, Any]:
    run_dir = Path(args.history_root) / args.run_id
    bottleneck_dir = run_dir / args.bottleneck
    launcher_pid = _read_pid(run_dir / "launcher.pid") or args.launcher_pid
    runner_pid = _read_pid(run_dir / "runner.pid")
    supervisor_pid = _read_pid(run_dir / "supervisor.pid")
    history_paths = sorted(bottleneck_dir.glob(f"{args.benchmark}_optimizer_history_*.jsonl"))
    control_json = sorted(run_dir.glob("control_decision_*.json"))
    control_md = sorted(run_dir.glob("control_decision_*.md"))
    event_summary = _event_summary(history_paths)
    snapshot = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_id": args.run_id,
        "run_dir": str(run_dir),
        "bottleneck": args.bottleneck,
        "benchmark": args.benchmark,
        "launcher_pid": launcher_pid,
        "launcher_alive": _alive(launcher_pid),
        "runner_pid": runner_pid,
        "runner_alive": _alive(runner_pid),
        "supervisor_pid": supervisor_pid,
        "supervisor_alive": _alive(supervisor_pid),
        "files": {
            "launcher_pid": _path_info(run_dir / "launcher.pid"),
            "runner_pid": _path_info(run_dir / "runner.pid"),
            "supervisor_pid": _path_info(run_dir / "supervisor.pid"),
            "runner_log": _path_info(run_dir / "runner.log"),
            "supervisor_log": _path_info(run_dir / "supervisor.log"),
            "startup_status_snapshot": _path_info(run_dir / "startup_status_snapshot.log"),
            "startup_verifier": _path_info(run_dir / "startup_verifier.log"),
            "launch_wait": _path_info(run_dir / "launch_wait.log"),
        },
        "control_decision_json_count": len(control_json),
        "control_decision_md_count": len(control_md),
        "control_decision_json": _list_files(control_json),
        "control_decision_md": _list_files(control_md),
        "event_summary": event_summary,
        "action_required": bool(control_json or control_md),
    }
    if args.run_verifier:
        snapshot["verifier"] = _run_verifier(args)
    return snapshot


def _signature(snapshot: Dict[str, Any]) -> Tuple[Any, ...]:
    interesting = snapshot.get("event_summary", {}).get("interesting_event_counts", {})
    return (
        snapshot.get("launcher_pid"),
        snapshot.get("launcher_alive"),
        snapshot.get("runner_pid"),
        snapshot.get("runner_alive"),
        snapshot.get("supervisor_pid"),
        snapshot.get("supervisor_alive"),
        snapshot.get("control_decision_json_count"),
        snapshot.get("control_decision_md_count"),
        tuple(sorted(interesting.items())),
    )


def _write_outputs(snapshot: Dict[str, Any], args: argparse.Namespace) -> None:
    json_path = Path(args.json_output)
    md_path = Path(args.md_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# Bonect Phase-2 Startup Watch: {snapshot['run_id']}",
        "",
        f"- generated_at: `{snapshot['generated_at']}`",
        f"- run_dir: `{snapshot['run_dir']}`",
        f"- launcher_pid: `{snapshot.get('launcher_pid')}` alive=`{snapshot.get('launcher_alive')}`",
        f"- runner_pid: `{snapshot.get('runner_pid')}` alive=`{snapshot.get('runner_alive')}`",
        f"- supervisor_pid: `{snapshot.get('supervisor_pid')}` alive=`{snapshot.get('supervisor_alive')}`",
        f"- control_decision_json_count: `{snapshot.get('control_decision_json_count')}`",
        f"- control_decision_md_count: `{snapshot.get('control_decision_md_count')}`",
        f"- action_required: `{snapshot.get('action_required')}`",
        "",
        "## Interesting Events",
    ]
    for name, count in snapshot.get("event_summary", {}).get("interesting_event_counts", {}).items():
        lines.append(f"- `{name}`: `{count}`")
    verifier = snapshot.get("verifier") or {}
    if verifier:
        summary = verifier.get("summary") or {}
        lines.extend(
            [
                "",
                "## Verifier",
                f"- returncode: `{verifier.get('returncode')}`",
                f"- phase2_ready_for_claim: `{summary.get('phase2_ready_for_claim')}`",
                f"- json_output: `{verifier.get('json_output')}`",
                f"- md_output: `{verifier.get('md_output')}`",
            ]
        )
        checks = summary.get("required_checks") or {}
        for name, ok in checks.items():
            lines.append(f"- `{name}`: `{ok}`")
    if snapshot.get("control_decision_json"):
        lines.extend(["", "## Control Decision JSON"])
        for item in snapshot["control_decision_json"]:
            lines.append(f"- `{item['path']}` mtime=`{item.get('mtime')}` size=`{item.get('size')}`")
    if snapshot.get("control_decision_md"):
        lines.extend(["", "## Control Decision Markdown"])
        for item in snapshot["control_decision_md"]:
            lines.append(f"- `{item['path']}` mtime=`{item.get('mtime')}` size=`{item.get('size')}`")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--history-root", default=str(DEFAULT_HISTORY_ROOT))
    parser.add_argument("--bottleneck", default="memory")
    parser.add_argument("--benchmark", default="hybench_ap")
    parser.add_argument("--launcher-pid", type=int, default=0)
    parser.add_argument("--json-output", default=str(DEFAULT_REPORT_DIR / "phase2_v122_startup_watch_latest.json"))
    parser.add_argument("--md-output", default=str(DEFAULT_REPORT_DIR / "phase2_v122_startup_watch_latest.md"))
    parser.add_argument("--verifier-json-output", default=str(DEFAULT_REPORT_DIR / "phase2_v122_watch_verification_latest.json"))
    parser.add_argument("--verifier-md-output", default=str(DEFAULT_REPORT_DIR / "phase2_v122_watch_verification_latest.md"))
    parser.add_argument("--verifier-timeout-sec", type=int, default=60)
    parser.add_argument("--run-verifier", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-sec", type=float, default=60.0)
    parser.add_argument("--timeout-sec", type=float, default=0.0)
    parser.add_argument("--pid-output", default="")
    args = parser.parse_args()

    if args.pid_output:
        pid_path = Path(args.pid_output)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    deadline = time.time() + args.timeout_sec if args.timeout_sec > 0 else None
    last_signature: Tuple[Any, ...] | None = None
    while True:
        snapshot = _build_snapshot(args)
        sig = _signature(snapshot)
        if sig != last_signature or not Path(args.json_output).exists():
            _write_outputs(snapshot, args)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2), flush=True)
            last_signature = sig
        if not args.watch:
            break
        if deadline is not None and time.time() >= deadline:
            break
        time.sleep(max(5.0, float(args.poll_sec)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
