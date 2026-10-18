#!/usr/bin/env python3
"""Watch the active B6 formal batch; on finish audit and optionally supersede.

Stays inside the fixed-protocol framework: never overwrites finished dirs; if the
current batch fails acceptance with timeout-class errors, write a new protocol
with a higher validation-timeout (and refreshed mysqld PID) and launch it.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "goal_20260720"
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
BATCH_RUNNER = GOAL / "run_fixed_protocol_batch.py"
VERIFY = GOAL / "verify_formal_batch.py"
MONITOR = GOAL / "update_goal_monitor_status.py"
STATUS = GOAL / "recovery_schedule_status.json"
CONTAINER = "ra_mysql_goal_hybench_xp"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_monitor() -> None:
    subprocess.run([str(PYTHON), str(MONITOR)], check=False, cwd=str(ROOT))


def host_mysqld_pid(container: str = CONTAINER) -> int:
    top = subprocess.run(
        ["docker", "top", container, "-eo", "pid,comm"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in top.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "mysqld":
            return int(parts[0])
    raise RuntimeError(f"mysqld pid not found in docker top for {container}")


def ensure_container() -> int:
    subprocess.run(["docker", "start", CONTAINER], check=False, capture_output=True, text=True)
    for _ in range(60):
        ping = subprocess.run(
            ["docker", "exec", CONTAINER, "mysqladmin", "ping", "-uroot", "-p123456", "--silent"],
            check=False,
            capture_output=True,
            text=True,
        )
        if ping.returncode == 0:
            return host_mysqld_pid()
        time.sleep(2)
    raise RuntimeError("MySQL container did not become ready")


def audit_batch(batch: Path, *, min_delta: float, max_delta: float, required: int) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(PYTHON), str(VERIFY),
            "--batch-dir", str(batch),
            "--min-delta-pp", str(min_delta),
            "--max-delta-pp", str(max_delta),
            "--required-passes", str(required),
            "--write",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if completed.returncode != 0:
        return {
            "acceptance_met": False,
            "error": (completed.stderr or completed.stdout or "audit_failed")[-2000:],
        }
    return json.loads(completed.stdout)


def timeout_failures(batch: Path) -> bool:
    for failure in batch.glob("attempt_*/run_failure.json"):
        data = load_json(failure) or {}
        err = str(data.get("error") or "") + str(data.get("error_type") or "")
        if "TimeoutError" in err or "workload exceeded" in err:
            return True
    return False


def supersede_protocol(base_protocol: Path, *, new_timeout: int, mysqld_pid: int) -> Path:
    data = json.loads(base_protocol.read_text(encoding="utf-8"))
    old_id = str(data["protocol_id"])
    # B6_io_explicitcap_n2v3_... -> n2v4
    if "n2v" in old_id:
        head, _, tail = old_id.rpartition("n2v")
        version = ""
        rest = tail
        i = 0
        while i < len(rest) and rest[i].isdigit():
            version += rest[i]
            i += 1
        next_v = int(version or "1") + 1
        new_id = f"{head}n2v{next_v}{rest[i:]}"
    else:
        new_id = old_id + "_retry"
    exp = str(data.get("exp_id") or "B6U")
    if exp[-1].isdigit():
        # bump trailing digits
        prefix = exp.rstrip("0123456789")
        num = exp[len(prefix):]
        exp = f"{prefix}{int(num) + 1}"
    else:
        exp = exp + "2"
    data["protocol_id"] = new_id
    data["exp_id"] = exp
    data["output_dir"] = f"goal_20260720/formal_batches/{new_id}"
    data["supersedes_batch"] = old_id
    data["supersession_reason"] = (
        f"Automatic recovery: predecessor {old_id} finished without acceptance; "
        f"raising validation-timeout to {new_timeout}s and refreshing mysqld-pid."
    )
    args = list(data["runner_args"])
    def set_flag(flag: str, value: str) -> None:
        if flag in args:
            args[args.index(flag) + 1] = value
        else:
            args.extend([flag, value])
    set_flag("--exp-id", exp)
    set_flag("--mysqld-pid", str(mysqld_pid))
    set_flag("--validation-timeout", str(new_timeout))
    data["runner_args"] = args
    out = GOAL / "protocols" / f"{new_id}.json"
    if out.exists():
        raise FileExistsError(out)
    write_json(out, data)
    return out


def launch_protocol(protocol: Path) -> int:
    log = GOAL / "formal_batches" / f"{protocol.stem}.batch.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [str(PYTHON), str(BATCH_RUNNER), "--protocol", str(protocol)],
        cwd=str(ROOT),
        stdout=log.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return proc.pid


def update_recovery(active_batch: Path, protocol: Path, audit: dict[str, Any], pid: int | None = None) -> None:
    current = load_json(STATUS) or {"policy": "one_successor_max_per_endpoint", "items": {}}
    items = current.setdefault("items", {})
    row = items.setdefault("B6", {})
    row.update({
        "active_protocol": str(protocol),
        "active_output": str(active_batch),
        "active_acceptance": audit.get("acceptance_met"),
        "active_audit": str(active_batch / "acceptance_audit.json"),
        "updated_at_utc": utc_now(),
    })
    if pid is not None:
        row["active_pid"] = pid
    current["updated_at_utc"] = utc_now()
    write_json(STATUS, current)


def find_active_b6() -> Path | None:
    root = GOAL / "formal_batches"
    unfinished = []
    for path in sorted(root.glob("B6_*")):
        if not path.is_dir():
            continue
        if (path / "batch_summary.json").is_file():
            continue
        unfinished.append(path)
    if unfinished:
        return unfinished[-1]
    # latest finished
    finished = sorted(
        [p for p in root.glob("B6_*") if p.is_dir() and (p / "batch_summary.json").is_file()],
        key=lambda p: p.stat().st_mtime,
    )
    return finished[-1] if finished else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-s", type=float, default=120.0)
    parser.add_argument("--max-supersedes", type=int, default=2)
    parser.add_argument("--min-delta-pp", type=float, default=7.0)
    parser.add_argument("--max-delta-pp", type=float, default=12.0)
    parser.add_argument("--required-passes", type=int, default=1)
    parser.add_argument("--next-timeout", type=int, default=1200)
    args = parser.parse_args()

    supersedes = 0
    while True:
        refresh_monitor()
        batch = find_active_b6()
        if batch is None:
            time.sleep(args.poll_s)
            continue
        summary = batch / "batch_summary.json"
        if not summary.is_file():
            # still running
            time.sleep(args.poll_s)
            continue
        audit = audit_batch(
            batch,
            min_delta=args.min_delta_pp,
            max_delta=args.max_delta_pp,
            required=args.required_passes,
        )
        proto_path = batch / "protocol.json"
        update_recovery(batch, proto_path if proto_path.is_file() else batch, audit)
        refresh_monitor()
        if audit.get("acceptance_met"):
            print(json.dumps({"event": "b6_accepted", "batch": str(batch), "audit": audit}, ensure_ascii=False), flush=True)
            return 0
        if supersedes >= args.max_supersedes:
            print(json.dumps({"event": "b6_failed_no_more_supersedes", "batch": str(batch), "audit": audit}, ensure_ascii=False), flush=True)
            return 1
        try:
            pid = ensure_container()
            base = GOAL / "protocols" / f"{batch.name}.json"
            if not base.is_file():
                base = proto_path
            # Timeout path: raise validation-timeout. Quality path (formal JSON
            # present but acceptance fail): reuse system-knobs calibrated
            # protocol template if available, else same knobs with higher timeout.
            if timeout_failures(batch):
                new_protocol = supersede_protocol(base, new_timeout=args.next_timeout, mysqld_pid=pid)
                reason = "timeout"
            else:
                quality = GOAL / "protocols" / "B6_io_systemknobs_n2_20260720.json"
                src = quality if quality.is_file() else base
                data = json.loads(src.read_text(encoding="utf-8"))
                # New id for quality retry so finished dirs are never overwritten.
                new_id = f"B6_io_systemknobs_n2v{supersedes + 1}_20260720"
                while (GOAL / "protocols" / f"{new_id}.json").exists() or (
                    GOAL / "formal_batches" / new_id
                ).exists():
                    supersedes += 1
                    new_id = f"B6_io_systemknobs_n2v{supersedes + 1}_20260720"
                data["protocol_id"] = new_id
                data["exp_id"] = f"B6C{supersedes + 1}"
                data["output_dir"] = f"goal_20260720/formal_batches/{new_id}"
                data["predecessor_batch"] = batch.name
                data["revision_rationale"] = (
                    f"Quality recovery after {batch.name} finished with formal "
                    f"results but acceptance_met=false (QPS/drop targets). "
                    f"System-knob calibration + validation-timeout={args.next_timeout}."
                )
                args_list = list(data["runner_args"])

                def set_flag(flag: str, value: str) -> None:
                    if flag in args_list:
                        args_list[args_list.index(flag) + 1] = value
                    else:
                        args_list.extend([flag, value])

                set_flag("--exp-id", data["exp_id"])
                set_flag("--mysqld-pid", str(pid))
                set_flag("--validation-timeout", str(args.next_timeout))
                data["runner_args"] = args_list
                new_protocol = GOAL / "protocols" / f"{new_id}.json"
                write_json(new_protocol, data)
                reason = "quality"
            launch_pid = launch_protocol(new_protocol)
            supersedes += 1
            update_recovery(
                GOAL / "formal_batches" / new_protocol.stem,
                new_protocol,
                {"acceptance_met": False, "launched": True, "reason": reason},
                pid=launch_pid,
            )
            print(json.dumps({
                "event": "b6_supersede_launched",
                "reason": reason,
                "protocol": str(new_protocol),
                "pid": launch_pid,
                "mysqld_pid": pid,
                "timeout": args.next_timeout,
            }, ensure_ascii=False), flush=True)
            refresh_monitor()
        except Exception as exc:  # keep watching; surface error
            print(json.dumps({"event": "b6_supersede_error", "error": str(exc)}, ensure_ascii=False), flush=True)
            return 3
        time.sleep(args.poll_s)


if __name__ == "__main__":
    raise SystemExit(main())
