#!/usr/bin/env python3
"""Guarded v124 Memory/AP continuation entrypoint.

This script intentionally does not use the matrix runner. It first runs the
external HYXP replay-safety guard and only starts parameter_optimizer.py when
the guard proves replay is safe. Starting requires explicit --start.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "pg_bonect_phase2_dbqps_amp_binary_top20_prune_v124_20260702_145218"
RUN_DIR = PROJECT_ROOT / "history" / "pg_benchmark_bottleneck_matrix" / RUN_ID
CONFIG_PATH = (
    PROJECT_ROOT
    / "config"
    / "generated"
    / "pg_benchmark_bottleneck_matrix"
    / RUN_ID
    / "memory"
    / "hybench_ap.json"
)
EXTERNAL_JSONL = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_sequence_full_tmux_20260702_002109/memory_binary_search_hybenchxp.jsonl"
)
KNOWN_PID_FILE = RUN_DIR / "replay_noise_guard_known_external_pids.json"
GUARD_JSON = RUN_DIR / "replay_noise_guard_external_hyxp_status_latest.json"
GUARD_MD = RUN_DIR / "replay_noise_guard_external_hyxp_status_latest.md"
STATUS_JSON = RUN_DIR / "v124_guarded_continue_status_latest.json"
STATUS_MD = RUN_DIR / "v124_guarded_continue_status_latest.md"
START_PID_FILE = RUN_DIR / "v124_postfix_optimizer.pid"
START_LOG = RUN_DIR / "memory" / "hybench_ap_postfix_optimizer_stdout.log"
DEFAULT_PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def process_alive(pid: Any) -> bool:
    try:
        parsed = int(pid)
    except Exception:
        return False
    if parsed <= 0:
        return False
    try:
        os.kill(parsed, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def pid_matches_same_run_optimizer(args: argparse.Namespace, pid: int) -> Tuple[bool, str, str]:
    """Verify a pidfile PID is really this run's parameter_optimizer process."""
    if not process_alive(pid):
        return False, "", "pid_not_alive"
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    except Exception as exc:
        return False, "", f"cmdline_unreadable:{type(exc).__name__}"
    if "parameter_optimizer.py" not in cmd:
        return False, cmd[:900], "cmdline_not_parameter_optimizer"
    cwd_path = ""
    try:
        cwd_path = os.readlink(f"/proc/{pid}/cwd")
    except Exception:
        cwd_path = ""
    config_text = str(args.config)
    run_dir_text = str(args.run_dir)
    project_text = str(PROJECT_ROOT)
    if (
        RUN_ID in cmd
        or config_text in cmd
        or run_dir_text in cmd
        or cwd_path == project_text
        or (project_text in cmd and "hybench_ap.json" in cmd)
    ):
        return True, cmd[:900], "same_run_parameter_optimizer_pidfile"
    return False, cmd[:900], "parameter_optimizer_not_same_run"


def same_run_optimizer_rows_from_lines(
    args: argparse.Namespace,
    lines: List[str],
    *,
    reason: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    config_text = str(args.config)
    run_dir_text = str(args.run_dir)
    project_text = str(PROJECT_ROOT)
    current_pid = os.getpid()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == current_pid:
            continue
        cmd = parts[1]
        if "parameter_optimizer.py" not in cmd:
            continue
        cwd_path = ""
        try:
            cwd_path = os.readlink(f"/proc/{pid}/cwd")
        except Exception:
            cwd_path = ""
        if not (
            RUN_ID in cmd
            or config_text in cmd
            or run_dir_text in cmd
            or cwd_path == project_text
            or (
                project_text in cmd
                and "hybench_ap.json" in cmd
            )
        ):
            continue
        rows.append({
            "pid": pid,
            "cmd": cmd[:900],
            "cwd": cwd_path,
            "reason": reason,
        })
    return rows


def fallback_scan_same_run_optimizer_processes(
    args: argparse.Namespace,
    *,
    primary_status: str,
    primary_error: str = "",
) -> Dict[str, Any]:
    procfs_result = procfs_scan_same_run_optimizer_processes(
        args,
        reason=f"same_run_parameter_optimizer_process_procfs_after_{primary_status}",
    )
    if bool(procfs_result.get("reliable", False)):
        procfs_result["primary_status"] = primary_status
        procfs_result["primary_error"] = primary_error
        return procfs_result
    timeout_s = float(args.optimizer_scan_timeout_sec)
    try:
        completed = subprocess.run(
            ["pgrep", "-af", "parameter_optimizer.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": f"{primary_status}_fallback_timeout",
            "reliable": False,
            "timeout_s": timeout_s,
            "primary_error": primary_error,
            "rows": [],
        }
    except Exception as exc:
        return {
            "status": f"{primary_status}_fallback_error",
            "reliable": False,
            "timeout_s": timeout_s,
            "primary_error": primary_error,
            "fallback_error": type(exc).__name__,
            "rows": [],
        }
    if completed.returncode not in (0, 1):
        return {
            "status": f"{primary_status}_fallback_nonzero_exit",
            "reliable": False,
            "returncode": completed.returncode,
            "timeout_s": timeout_s,
            "primary_error": primary_error,
            "rows": [],
        }
    return {
        "status": f"ok_pgrep_after_{primary_status}",
        "reliable": True,
        "timeout_s": timeout_s,
        "primary_error": primary_error,
        "rows": same_run_optimizer_rows_from_lines(
            args,
            completed.stdout.splitlines(),
            reason="same_run_parameter_optimizer_process_pgrep_fallback",
        ),
    }


def procfs_scan_same_run_optimizer_processes(
    args: argparse.Namespace,
    *,
    reason: str,
) -> Dict[str, Any]:
    timeout_s = float(args.optimizer_scan_timeout_sec)
    started_at = time.monotonic()
    rows: List[Dict[str, Any]] = []
    config_text = str(args.config)
    run_dir_text = str(args.run_dir)
    project_text = str(PROJECT_ROOT)
    current_pid = os.getpid()
    try:
        names = os.listdir("/proc")
    except Exception as exc:
        return {
            "status": "procfs_error",
            "reliable": False,
            "timeout_s": timeout_s,
            "error": type(exc).__name__,
            "rows": [],
        }
    for name in names:
        if time.monotonic() - started_at > timeout_s:
            return {
                "status": "procfs_timeout",
                "reliable": False,
                "timeout_s": timeout_s,
                "rows": rows,
            }
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == current_pid:
            continue
        try:
            raw_cmd = Path("/proc") / name / "cmdline"
            cmd = raw_cmd.read_bytes().replace(b"\0", b" ").decode(
                "utf-8",
                errors="replace",
            ).strip()
        except Exception:
            continue
        if "parameter_optimizer.py" not in cmd:
            continue
        cwd_path = ""
        try:
            cwd_path = os.readlink(str(Path("/proc") / name / "cwd"))
        except Exception:
            cwd_path = ""
        if not (
            RUN_ID in cmd
            or config_text in cmd
            or run_dir_text in cmd
            or cwd_path == project_text
            or (project_text in cmd and "hybench_ap.json" in cmd)
        ):
            continue
        rows.append({
            "pid": pid,
            "cmd": cmd[:900],
            "cwd": cwd_path,
            "reason": reason,
        })
    return {
        "status": "ok_procfs",
        "reliable": True,
        "timeout_s": timeout_s,
        "rows": rows,
    }


def scan_same_run_optimizer_processes(args: argparse.Namespace) -> Dict[str, Any]:
    procfs_result = procfs_scan_same_run_optimizer_processes(
        args,
        reason="same_run_parameter_optimizer_process_procfs",
    )
    if bool(procfs_result.get("reliable", False)):
        return procfs_result
    timeout_s = float(args.optimizer_scan_timeout_sec)
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return fallback_scan_same_run_optimizer_processes(args, primary_status="timeout")
    except Exception as exc:
        return fallback_scan_same_run_optimizer_processes(
            args,
            primary_status="error",
            primary_error=type(exc).__name__,
        )
    if completed.returncode != 0:
        return fallback_scan_same_run_optimizer_processes(
            args,
            primary_status="nonzero_exit",
            primary_error=str(completed.returncode),
        )
    rows = same_run_optimizer_rows_from_lines(
        args,
        completed.stdout.splitlines(),
        reason="same_run_parameter_optimizer_process",
    )
    return {
        "status": "ok",
        "reliable": True,
        "timeout_s": timeout_s,
        "rows": rows,
    }


def build_guard_cmd(args: argparse.Namespace) -> List[str]:
    return [
        args.guard_python,
        str(PROJECT_ROOT / "shell" / "bonect_phase2_external_hyxp_status.py"),
        "--jsonl",
        str(args.external_jsonl),
        "--run-dir",
        str(args.run_dir),
        "--scan-proc",
        "--known-external-pid-file",
        str(args.known_pid_file),
        "--json-output",
        str(args.guard_json),
        "--md-output",
        str(args.guard_md),
        "--require-replay-safe",
        "--process-scan-timeout-sec",
        str(args.external_scan_timeout_sec),
    ]


def run_guard(args: argparse.Namespace) -> Tuple[int, Dict[str, Any], str]:
    cmd = build_guard_cmd(args)
    completed = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=float(args.guard_timeout_sec),
    )
    payload = load_json(args.guard_json)
    if not isinstance(payload, dict):
        payload = {}
    return completed.returncode, payload, completed.stdout[-4000:]


def vm_target_config(args: argparse.Namespace) -> Tuple[str, int]:
    payload = load_json(args.config)
    if not isinstance(payload, dict):
        return "", 0
    postgres = payload.get("postgres") or {}
    if not isinstance(postgres, dict):
        return "", 0
    host = str(postgres.get("host") or "")
    try:
        port = int(postgres.get("port") or 0)
    except Exception:
        port = 0
    return host, port


def guard_allows_start(args: argparse.Namespace, guard_rc: int, guard_payload: Dict[str, Any]) -> bool:
    if guard_rc == 0 and bool(guard_payload.get("replay_safe")):
        return True
    if not bool(args.allow_external_busy):
        return False
    effective = guard_payload.get("effective_wait_state") or {}
    if not isinstance(effective, dict):
        return False
    if effective.get("state") not in {"external_busy", "external_scan_inconclusive"}:
        return False
    host, port = vm_target_config(args)
    return host == "192.0.2.8" and port == 25432


def live_run_pid_rows(
    args: argparse.Namespace,
    guard_payload: Dict[str, Any],
    optimizer_scan: Dict[str, Any],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    pid_status = guard_payload.get("run_dir_pid_status") or {}
    if isinstance(pid_status, dict):
        for name, row in pid_status.items():
            if not (isinstance(row, dict) and row.get("live_for_run")):
                continue
            pid = row.get("pid")
            if pid is None:
                continue
            try:
                parsed_pid = int(pid)
            except Exception:
                continue
            matches, cmd, reason = pid_matches_same_run_optimizer(args, parsed_pid)
            if matches:
                item = dict(row)
                item["pid_file"] = name
                item["cmd"] = cmd
                item["reason"] = reason
                rows.append(item)
    start_pid = None
    if START_PID_FILE.exists():
        try:
            start_pid = int(START_PID_FILE.read_text(encoding="utf-8").strip().splitlines()[0])
        except Exception:
            start_pid = None
    if start_pid and process_alive(start_pid):
        matches, cmd, reason = pid_matches_same_run_optimizer(args, start_pid)
        if matches:
            rows.append({
                "pid_file": str(START_PID_FILE),
                "pid": start_pid,
                "live_for_run": True,
                "reason": reason,
                "cmd": cmd,
            })
    for row in optimizer_scan.get("rows") or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["live_for_run"] = True
        item["pid_file"] = "ps_eww_scan"
        rows.append(item)
    return rows


def make_status(
    *,
    args: argparse.Namespace,
    guard_rc: int,
    guard_payload: Dict[str, Any],
    guard_output_tail: str,
    optimizer_scan: Dict[str, Any],
    action: str,
    reason: str,
    optimizer_pid: int = 0,
    wait_attempt: int = 1,
    wait_elapsed_s: float = 0.0,
    next_poll_after_s: float = 0.0,
    safe_confirmations_completed: int = 0,
) -> Dict[str, Any]:
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_id": RUN_ID,
        "run_dir": str(args.run_dir),
        "config_path": str(args.config),
        "guard_rc": guard_rc,
        "guard_replay_safe": guard_payload.get("replay_safe"),
        "guard_allows_start": guard_allows_start(args, guard_rc, guard_payload),
        "allow_external_busy": bool(args.allow_external_busy),
        "vm_target": {
            "postgres_host": vm_target_config(args)[0],
            "postgres_port": vm_target_config(args)[1],
        },
        "guard_state": guard_payload.get("effective_wait_state"),
        "external_process_count": guard_payload.get("external_process_count"),
        "external_process_scan_status": guard_payload.get("external_process_scan_status"),
        "external_process_count_reliable": guard_payload.get("external_process_count_reliable"),
        "known_external_pid_status": [
            {
                "pid": row.get("pid"),
                "alive": row.get("alive"),
                "cmd_matches_external_hyxp": row.get("cmd_matches_external_hyxp"),
                "child_count": row.get("child_count"),
            }
            for row in (guard_payload.get("known_external_pid_status") or [])
            if isinstance(row, dict)
        ],
        "external_progress": {
            "current_round": (guard_payload.get("external") or {}).get("current_round"),
            "current_round_validated": (
                guard_payload.get("external") or {}
            ).get("current_round_validated"),
            "completed_rounds": (guard_payload.get("external") or {}).get("completed_rounds"),
            "jsonl_mtime": (guard_payload.get("external") or {}).get("jsonl_mtime"),
        },
        "same_run_optimizer_scan": optimizer_scan,
        "live_run_pid_rows": live_run_pid_rows(args, guard_payload, optimizer_scan),
        "action": action,
        "reason": reason,
        "optimizer_pid": optimizer_pid or None,
        "wait_enabled": bool(args.wait),
        "wait_attempt": wait_attempt,
        "wait_elapsed_s": round(float(wait_elapsed_s), 3),
        "poll_interval_s": float(args.poll_sec),
        "max_wait_s": float(args.max_wait_sec),
        "next_poll_after_s": round(float(next_poll_after_s), 3),
        "start_requires_explicit_flag": True,
        "safe_confirmations_required_for_start": (
            max(1, int(args.safe_confirmations)) if bool(args.start) else 1
        ),
        "safe_confirmations_completed": int(safe_confirmations_completed),
        "safe_confirmation_delay_s": float(args.safe_confirmation_delay_sec),
        "guarded_start_command": [
            "python3",
            str(PROJECT_ROOT / "shell" / "bonect_phase2_v124_guarded_continue.py"),
            "--start",
        ],
        "guarded_wait_start_command": [
            "python3",
            str(PROJECT_ROOT / "shell" / "bonect_phase2_v124_guarded_continue.py"),
            "--wait",
            "--poll-sec",
            str(float(args.poll_sec)),
            "--start",
        ],
        "start_command": [
            str(args.python_executable),
            str(PROJECT_ROOT / "parameter_optimizer.py"),
        ],
        "optimizer_log": str(args.optimizer_log),
        "guard_output_tail": guard_output_tail,
    }


def write_status(args: argparse.Namespace, status: Dict[str, Any]) -> None:
    atomic_write_text(args.status_json, json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# v124 guarded continuation status",
        "",
        f"- generated_at: `{status.get('generated_at')}`",
        f"- action: `{status.get('action')}`",
        f"- reason: `{status.get('reason')}`",
        f"- wait_enabled: `{status.get('wait_enabled')}`",
        f"- wait_attempt: `{status.get('wait_attempt')}`",
        f"- wait_elapsed_s: `{status.get('wait_elapsed_s')}`",
        f"- safe_confirmations_required_for_start: `{status.get('safe_confirmations_required_for_start')}`",
        f"- safe_confirmations_completed: `{status.get('safe_confirmations_completed')}`",
        f"- safe_confirmation_delay_s: `{status.get('safe_confirmation_delay_s')}`",
        f"- guard_rc: `{status.get('guard_rc')}`",
        f"- guard_replay_safe: `{status.get('guard_replay_safe')}`",
        f"- guard_allows_start: `{status.get('guard_allows_start')}`",
        f"- allow_external_busy: `{status.get('allow_external_busy')}`",
        f"- vm_target: `{status.get('vm_target')}`",
        f"- guard_state: `{status.get('guard_state')}`",
        f"- external_process_count: `{status.get('external_process_count')}`",
        f"- external_process_scan_status: `{status.get('external_process_scan_status')}`",
        f"- external_process_count_reliable: `{status.get('external_process_count_reliable')}`",
        f"- external_progress: `{status.get('external_progress')}`",
        f"- optimizer_pid: `{status.get('optimizer_pid')}`",
        f"- optimizer_log: `{status.get('optimizer_log')}`",
        f"- same_run_optimizer_scan: `{status.get('same_run_optimizer_scan')}`",
        "",
        "## Guarded Start Command",
        "",
        "```bash",
        " ".join(status.get("guarded_start_command") or []),
        "```",
        "",
        "## Guarded Wait Start Command",
        "",
        "```bash",
        " ".join(status.get("guarded_wait_start_command") or []),
        "```",
        "",
        "## Underlying Optimizer Command",
        "",
        "```bash",
        " ".join(status.get("start_command") or []),
        "```",
    ]
    atomic_write_text(args.status_md, "\n".join(lines) + "\n")


def start_optimizer(args: argparse.Namespace) -> int:
    args.optimizer_log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["OPTIMIZER_CONFIG"] = str(args.config)
    if bool(args.allow_external_busy):
        env["BONECT_ALLOW_EXTERNAL_BUSY_FOR_VM"] = "1"
    log_fp = args.optimizer_log.open("a", encoding="utf-8")
    try:
        log_fp.write(
            "\n"
            f"=== guarded continuation start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
        )
        log_fp.flush()
        proc = subprocess.Popen(
            [str(args.python_executable), str(PROJECT_ROOT / "parameter_optimizer.py")],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
    finally:
        log_fp.close()
    time.sleep(2.0)
    exit_code = proc.poll()
    if exit_code is not None:
        raise RuntimeError(f"optimizer_exited_immediately:{exit_code}")
    matches, cmd, reason = pid_matches_same_run_optimizer(args, int(proc.pid))
    if not matches:
        raise RuntimeError(f"optimizer_pid_validation_failed:{proc.pid}:{reason}:{cmd[:200]}")
    atomic_write_text(START_PID_FILE, f"{proc.pid}\n")
    return int(proc.pid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--external-jsonl", type=Path, default=EXTERNAL_JSONL)
    parser.add_argument("--known-pid-file", type=Path, default=KNOWN_PID_FILE)
    parser.add_argument("--guard-json", type=Path, default=GUARD_JSON)
    parser.add_argument("--guard-md", type=Path, default=GUARD_MD)
    parser.add_argument("--status-json", type=Path, default=STATUS_JSON)
    parser.add_argument("--status-md", type=Path, default=STATUS_MD)
    parser.add_argument("--optimizer-log", type=Path, default=START_LOG)
    parser.add_argument("--python-executable", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--guard-python", default=sys.executable)
    parser.add_argument("--guard-timeout-sec", type=float, default=90.0)
    parser.add_argument("--external-scan-timeout-sec", type=float, default=8.0)
    parser.add_argument("--optimizer-scan-timeout-sec", type=float, default=8.0)
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Keep polling until the guard is safe. Does not start unless --start is also set.",
    )
    parser.add_argument("--poll-sec", type=float, default=60.0)
    parser.add_argument(
        "--max-wait-sec",
        type=float,
        default=0.0,
        help="Maximum wait time for --wait. 0 means unlimited.",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Start parameter_optimizer.py after all guards pass. Default only checks readiness.",
    )
    parser.add_argument(
        "--allow-external-busy",
        action="store_true",
        help=(
            "Allow start when the only replay guard failure is an external HYXP process, "
            "but only for the v124 VM target 192.0.2.8:25432. Same-run optimizer "
            "guards still apply."
        ),
    )
    parser.add_argument(
        "--safe-confirmations",
        type=int,
        default=2,
        help=(
            "Number of consecutive replay-safe guard checks required before --start launches. "
            "Only applies when --start is set."
        ),
    )
    parser.add_argument(
        "--safe-confirmation-delay-sec",
        type=float,
        default=30.0,
        help="Delay between consecutive replay-safe guard checks before --start launches.",
    )
    args = parser.parse_args()

    started_at = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        elapsed = time.monotonic() - started_at
        guard_rc, guard_payload, guard_output_tail = run_guard(args)
        optimizer_scan = scan_same_run_optimizer_processes(args)
        live_rows = live_run_pid_rows(args, guard_payload, optimizer_scan)
        if not guard_allows_start(args, guard_rc, guard_payload):
            return_code = 2
            status = make_status(
                args=args,
                guard_rc=guard_rc,
                guard_payload=guard_payload,
                guard_output_tail=guard_output_tail,
                optimizer_scan=optimizer_scan,
                action="blocked",
                reason="external_replay_safe_guard_not_safe",
                wait_attempt=attempt,
                wait_elapsed_s=elapsed,
                next_poll_after_s=args.poll_sec if args.wait else 0.0,
            )
            waitable = True
        elif not bool(optimizer_scan.get("reliable", False)):
            return_code = 4
            status = make_status(
                args=args,
                guard_rc=guard_rc,
                guard_payload=guard_payload,
                guard_output_tail=guard_output_tail,
                optimizer_scan=optimizer_scan,
                action="blocked",
                reason="same_run_optimizer_scan_inconclusive",
                wait_attempt=attempt,
                wait_elapsed_s=elapsed,
                next_poll_after_s=args.poll_sec if args.wait else 0.0,
            )
            waitable = True
        elif live_rows:
            status = make_status(
                args=args,
                guard_rc=guard_rc,
                guard_payload=guard_payload,
                guard_output_tail=guard_output_tail,
                optimizer_scan=optimizer_scan,
                action="blocked",
                reason="same_run_optimizer_already_alive",
                wait_attempt=attempt,
                wait_elapsed_s=elapsed,
            )
            write_status(args, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 3
        elif not args.start:
            status = make_status(
                args=args,
                guard_rc=guard_rc,
                guard_payload=guard_payload,
                guard_output_tail=guard_output_tail,
                optimizer_scan=optimizer_scan,
                action="ready",
                reason="guard_passed_start_requires_explicit_start_flag",
                wait_attempt=attempt,
                wait_elapsed_s=elapsed,
                safe_confirmations_completed=1,
            )
            write_status(args, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        else:
            confirmations_required = max(1, int(args.safe_confirmations))
            confirmations_completed = 1
            while confirmations_completed < confirmations_required:
                time.sleep(max(0.0, float(args.safe_confirmation_delay_sec)))
                guard_rc, guard_payload, guard_output_tail = run_guard(args)
                optimizer_scan = scan_same_run_optimizer_processes(args)
                live_rows = live_run_pid_rows(args, guard_payload, optimizer_scan)
                if not guard_allows_start(args, guard_rc, guard_payload):
                    status = make_status(
                        args=args,
                        guard_rc=guard_rc,
                        guard_payload=guard_payload,
                        guard_output_tail=guard_output_tail,
                        optimizer_scan=optimizer_scan,
                        action="blocked",
                        reason="safe_confirmation_guard_failed",
                        wait_attempt=attempt,
                        wait_elapsed_s=time.monotonic() - started_at,
                        safe_confirmations_completed=confirmations_completed,
                    )
                    write_status(args, status)
                    print(json.dumps(status, ensure_ascii=False, indent=2))
                    return 2
                if not bool(optimizer_scan.get("reliable", False)):
                    status = make_status(
                        args=args,
                        guard_rc=guard_rc,
                        guard_payload=guard_payload,
                        guard_output_tail=guard_output_tail,
                        optimizer_scan=optimizer_scan,
                        action="blocked",
                        reason="safe_confirmation_same_run_optimizer_scan_inconclusive",
                        wait_attempt=attempt,
                        wait_elapsed_s=time.monotonic() - started_at,
                        safe_confirmations_completed=confirmations_completed,
                    )
                    write_status(args, status)
                    print(json.dumps(status, ensure_ascii=False, indent=2))
                    return 4
                if live_rows:
                    status = make_status(
                        args=args,
                        guard_rc=guard_rc,
                        guard_payload=guard_payload,
                        guard_output_tail=guard_output_tail,
                        optimizer_scan=optimizer_scan,
                        action="blocked",
                        reason="safe_confirmation_same_run_optimizer_already_alive",
                        wait_attempt=attempt,
                        wait_elapsed_s=time.monotonic() - started_at,
                        safe_confirmations_completed=confirmations_completed,
                    )
                    write_status(args, status)
                    print(json.dumps(status, ensure_ascii=False, indent=2))
                    return 3
                confirmations_completed += 1
            try:
                optimizer_pid = start_optimizer(args)
            except Exception as exc:
                status = make_status(
                    args=args,
                    guard_rc=guard_rc,
                    guard_payload=guard_payload,
                    guard_output_tail=guard_output_tail,
                    optimizer_scan=scan_same_run_optimizer_processes(args),
                    action="blocked",
                    reason=f"optimizer_start_failed:{type(exc).__name__}:{str(exc)[:240]}",
                    wait_attempt=attempt,
                    wait_elapsed_s=time.monotonic() - started_at,
                    safe_confirmations_completed=confirmations_completed,
                )
                write_status(args, status)
                print(json.dumps(status, ensure_ascii=False, indent=2))
                return 5
            status = make_status(
                args=args,
                guard_rc=guard_rc,
                guard_payload=guard_payload,
                guard_output_tail=guard_output_tail,
                optimizer_scan=optimizer_scan,
                action="started",
                reason="guard_passed_optimizer_started",
                optimizer_pid=optimizer_pid,
                wait_attempt=attempt,
                wait_elapsed_s=elapsed,
                safe_confirmations_completed=confirmations_completed,
            )
            write_status(args, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0

        if (
            not args.wait
            or not waitable
            or (args.max_wait_sec > 0.0 and elapsed >= args.max_wait_sec)
        ):
            status["next_poll_after_s"] = 0.0
            write_status(args, status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return return_code
        write_status(args, status)
        print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)
        time.sleep(max(1.0, float(args.poll_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
