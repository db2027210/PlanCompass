#!/usr/bin/env python3
"""Queue strict four-VM A, B, then C experiments without disturbing other workloads."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.watch_vm_host_disk_conflicts import snapshot as docker_snapshot  # noqa: E402


TARGETS = ("cpu", "memory", "io", "none")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def event_payload(sequence_root: Path, event: str, **extra: Any) -> Dict[str, Any]:
    return {
        "event": event,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "sequence_root": str(sequence_root),
        **extra,
    }


def record(sequence_root: Path, event: str, **extra: Any) -> Dict[str, Any]:
    payload = event_payload(sequence_root, event, **extra)
    atomic_write(
        sequence_root / "sequence_status.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    append_jsonl(sequence_root / "sequence_history.jsonl", payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return payload


def disk_device() -> Tuple[str, str]:
    proc = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "-T", "/disk1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    device_name = Path(proc.stdout.strip()).name
    device_number = Path(f"/sys/class/block/{device_name}/dev").read_text(encoding="utf-8").strip()
    return device_name, device_number


def diskstats(device_name: str) -> Tuple[int, int, int]:
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 13 and fields[2] == device_name:
            return int(fields[5]), int(fields[9]), int(fields[12])
    raise RuntimeError(f"diskstats device missing: {device_name}")


def conflicting_processes(substring: str) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    own_pid = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own_pid:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if substring in cmdline:
            matches.append({"pid": int(entry.name), "cmdline": cmdline.strip()})
    return sorted(matches, key=lambda row: row["pid"])


def sample_disk(
    *,
    device_name: str,
    device_number: str,
    sample_sec: float,
    max_read_mib_s: float,
    max_write_mib_s: float,
    max_util_pct: float,
    max_docker_mib_s: float,
    conflict_process_substring: str,
) -> Dict[str, Any]:
    process_conflicts = conflicting_processes(conflict_process_substring)
    disk_before = diskstats(device_name)
    docker_before = docker_snapshot(device_number)
    started = time.monotonic()
    time.sleep(max(1.0, sample_sec))
    elapsed = max(1e-6, time.monotonic() - started)
    disk_after = diskstats(device_name)
    docker_after = docker_snapshot(device_number)
    read_mib_s = (disk_after[0] - disk_before[0]) * 512.0 / 1048576.0 / elapsed
    write_mib_s = (disk_after[1] - disk_before[1]) * 512.0 / 1048576.0 / elapsed
    util_pct = (disk_after[2] - disk_before[2]) / (elapsed * 1000.0) * 100.0
    docker_rates: List[Dict[str, Any]] = []
    for container_id, value in docker_after.items():
        previous = docker_before.get(container_id) or value
        rate = max(0.0, float(value["bytes"] - previous["bytes"]) / 1048576.0 / elapsed)
        docker_rates.append({
            "name": value.get("name"),
            "mib_s": rate,
            "mounts": value.get("mounts") or [],
        })
    docker_rates.sort(key=lambda row: (-row["mib_s"], str(row["name"])))
    docker_conflicts = [row for row in docker_rates if row["mib_s"] > max_docker_mib_s]
    reasons: List[str] = []
    if process_conflicts:
        reasons.append("conflicting_optimizer_process_active")
    if read_mib_s > max_read_mib_s:
        reasons.append("disk_read_above_limit")
    if write_mib_s > max_write_mib_s:
        reasons.append("disk_write_above_limit")
    if util_pct > max_util_pct:
        reasons.append("disk_util_above_limit")
    if docker_conflicts:
        reasons.append("docker_io_above_limit")
    return {
        "quiet": not reasons,
        "reasons": reasons,
        "sample_sec": elapsed,
        "device_name": device_name,
        "device_number": device_number,
        "read_mib_s": read_mib_s,
        "write_mib_s": write_mib_s,
        "util_pct": util_pct,
        "max_read_mib_s": max_read_mib_s,
        "max_write_mib_s": max_write_mib_s,
        "max_util_pct": max_util_pct,
        "max_docker_mib_s": max_docker_mib_s,
        "process_conflicts": process_conflicts,
        "docker_rates": docker_rates,
        "docker_conflicts": docker_conflicts,
    }


def wait_for_disk_idle(
    *,
    sequence_root: Path,
    stage: str,
    args: argparse.Namespace,
    device_name: str,
    device_number: str,
) -> None:
    consecutive = 0
    while consecutive < args.quiet_samples:
        try:
            sample = sample_disk(
                device_name=device_name,
                device_number=device_number,
                sample_sec=args.disk_sample_sec,
                max_read_mib_s=args.max_read_mib_s,
                max_write_mib_s=args.max_write_mib_s,
                max_util_pct=args.max_util_pct,
                max_docker_mib_s=args.max_docker_mib_s,
                conflict_process_substring=args.conflict_process_substring,
            )
        except Exception as exc:
            sample = {"quiet": False, "reasons": ["sampling_failed"], "error": f"{type(exc).__name__}: {exc}"}
        consecutive = consecutive + 1 if sample.get("quiet") else 0
        record(
            sequence_root,
            "waiting_for_disk_idle",
            stage=stage,
            quiet_consecutive=consecutive,
            quiet_required=args.quiet_samples,
            sample=sample,
        )
        if consecutive < args.quiet_samples:
            time.sleep(max(1.0, args.poll_sec))


def tmux_session_exists(session: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", session],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def launch_strategy(
    *,
    sequence_root: Path,
    strategy: str,
    attempt: int,
    args: argparse.Namespace,
) -> Optional[Tuple[Path, str]]:
    suffix = {
        "A": "a_top1",
        "B": f"b_scorev2_topk{args.score_b_topk}",
        "C": f"c_real_resource_noqps_topk{args.score_b_topk}",
        "NESTED": f"nested_resource_semantic_topk{args.score_b_topk}",
    }[strategy]
    run_id = f"hyxp_four_vm_{suffix}_{args.sequence_id}_attempt{attempt:02d}"
    run_root = args.runs_root / run_id
    session = f"hyxp-{strategy.lower()}-{args.sequence_id[-13:]}-{attempt:02d}"
    env = os.environ.copy()
    env.update({
        "EXPERIMENT_STRATEGY": strategy,
        "SCORE_B_TOPK": str(args.score_b_topk),
        "RUN_ID": run_id,
        "RUN_ROOT": str(run_root),
        "SESSION": session,
    })
    process = subprocess.run(
        ["bash", str(args.launcher)],
        cwd=str(ROOT),
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    record(
        sequence_root,
        "strategy_launch_result",
        strategy=strategy,
        attempt=attempt,
        run_root=str(run_root),
        session=session,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )
    if process.returncode != 0:
        return None
    return run_root, session


def wait_for_run(
    *,
    sequence_root: Path,
    strategy: str,
    attempt: int,
    run_root: Path,
    session: str,
    poll_sec: float,
) -> str:
    while True:
        markers = sorted(path.name for path in run_root.glob("INVALIDATED_*.md") if path.is_file())
        watcher = load_json(run_root / "completion_audit_watcher_status.json") or {}
        audit = load_json(run_root / "convergence_completion_audit.json") or {}
        finals: Dict[str, Any] = {}
        for target in TARGETS:
            final = load_json(run_root / target / f"final_for_{target}.json") or {}
            finals[target] = {
                "converged": final.get("converged"),
                "stop_reason": final.get("stop_reason"),
                "completed_round_count": final.get("completed_round_count"),
            }
        if watcher.get("event") == "completion_audit_watcher_complete" and audit.get("complete") is True:
            record(
                sequence_root,
                "strategy_complete",
                strategy=strategy,
                attempt=attempt,
                run_root=str(run_root),
                session=session,
                finals=finals,
                audit_complete=True,
            )
            return "complete"
        if markers:
            record(
                sequence_root,
                "strategy_invalidated",
                strategy=strategy,
                attempt=attempt,
                run_root=str(run_root),
                session=session,
                invalidation_markers=markers,
                finals=finals,
            )
            return "invalidated"
        record(
            sequence_root,
            "strategy_running",
            strategy=strategy,
            attempt=attempt,
            run_root=str(run_root),
            session=session,
            tmux_session_exists=tmux_session_exists(session),
            watcher_event=watcher.get("event"),
            audit_complete=audit.get("complete"),
            finals=finals,
        )
        time.sleep(max(5.0, poll_sec))


def run_strategy_until_complete(
    *,
    sequence_root: Path,
    strategy: str,
    args: argparse.Namespace,
    device_name: str,
    device_number: str,
) -> Path:
    attempt = 0
    while args.max_attempts <= 0 or attempt < args.max_attempts:
        attempt += 1
        wait_for_disk_idle(
            sequence_root=sequence_root,
            stage=f"before_{strategy}",
            args=args,
            device_name=device_name,
            device_number=device_number,
        )
        launched = launch_strategy(sequence_root=sequence_root, strategy=strategy, attempt=attempt, args=args)
        if launched is None:
            time.sleep(max(5.0, args.retry_sec))
            continue
        run_root, session = launched
        outcome = wait_for_run(
            sequence_root=sequence_root,
            strategy=strategy,
            attempt=attempt,
            run_root=run_root,
            session=session,
            poll_sec=args.run_poll_sec,
        )
        if outcome == "complete":
            return run_root
        time.sleep(max(5.0, args.retry_sec))
    raise RuntimeError(f"strategy {strategy} exhausted max attempts={args.max_attempts}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sequence-id",
        default=f"v2_{time.strftime('%Y%m%d_%H%M%S')}",
    )
    parser.add_argument(
        "--sequence-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_vm/runs"),
    )
    parser.add_argument(
        "--launcher",
        type=Path,
        default=ROOT / "shell/run_hybenchxp_four_vm_recall_full_convergence.sh",
    )
    parser.add_argument(
        "--comparison-script",
        type=Path,
        default=ROOT / "shell/compare_hybenchxp_four_vm_ab.py",
    )
    parser.add_argument("--score-b-topk", type=int, default=20)
    parser.add_argument("--disk-sample-sec", type=float, default=5.0)
    parser.add_argument("--poll-sec", type=float, default=30.0)
    parser.add_argument("--run-poll-sec", type=float, default=60.0)
    parser.add_argument("--retry-sec", type=float, default=60.0)
    parser.add_argument("--quiet-samples", type=int, default=10)
    parser.add_argument("--max-read-mib-s", type=float, default=5.0)
    parser.add_argument("--max-write-mib-s", type=float, default=5.0)
    parser.add_argument("--max-util-pct", type=float, default=10.0)
    parser.add_argument("--max-docker-mib-s", type=float, default=1.0)
    parser.add_argument("--max-attempts", type=int, default=0, help="0 retries indefinitely")
    parser.add_argument(
        "--conflict-process-substring",
        default="hyxp_io_mild_fullsearch_disk1x2",
    )
    args = parser.parse_args()
    if args.score_b_topk <= 1:
        parser.error("--score-b-topk must be greater than one")
    if args.quiet_samples <= 0:
        parser.error("--quiet-samples must be positive")
    args.runs_root = args.runs_root.resolve()
    args.launcher = args.launcher.resolve()
    args.comparison_script = args.comparison_script.resolve()
    sequence_root = (
        args.sequence_root.resolve()
        if args.sequence_root is not None
        else args.runs_root / "ab_sequences" / args.sequence_id
    )
    sequence_root.mkdir(parents=True, exist_ok=True)
    history_path = sequence_root / "sequence_history.jsonl"
    if history_path.exists() and history_path.stat().st_size > 0:
        raise RuntimeError(f"sequence root already has history: {sequence_root}")
    device_name, device_number = disk_device()
    record(
        sequence_root,
        "sequence_start",
        sequence_id=args.sequence_id,
        launcher=str(args.launcher),
        comparison_script=str(args.comparison_script),
        score_b_topk=args.score_b_topk,
        order=["A", "B", "C"],
        disk_device_name=device_name,
        disk_device_number=device_number,
        quiet_samples=args.quiet_samples,
        conflict_process_substring=args.conflict_process_substring,
    )
    try:
        a_root = run_strategy_until_complete(
            sequence_root=sequence_root,
            strategy="A",
            args=args,
            device_name=device_name,
            device_number=device_number,
        )
        b_root = run_strategy_until_complete(
            sequence_root=sequence_root,
            strategy="B",
            args=args,
            device_name=device_name,
            device_number=device_number,
        )
        c_root = run_strategy_until_complete(
            sequence_root=sequence_root,
            strategy="C",
            args=args,
            device_name=device_name,
            device_number=device_number,
        )
        comparison_dir = sequence_root / "comparison"
        comparison = subprocess.run(
            [
                sys.executable,
                str(args.comparison_script),
                str(a_root),
                str(b_root),
                str(c_root),
                str(comparison_dir),
            ],
            cwd=str(ROOT),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if comparison.returncode != 0:
            raise RuntimeError(
                f"comparison failed rc={comparison.returncode}: {comparison.stderr.strip()}"
            )
        record(
            sequence_root,
            "sequence_complete",
            a_run_root=str(a_root),
            b_run_root=str(b_root),
            c_run_root=str(c_root),
            comparison_dir=str(comparison_dir),
            comparison_stdout=comparison.stdout,
        )
        return 0
    except BaseException as exc:
        record(sequence_root, "sequence_failed", error=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
