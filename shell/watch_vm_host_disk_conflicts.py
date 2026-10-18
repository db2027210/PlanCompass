#!/usr/bin/env python3
"""Fail closed when non-VM Docker workloads use the VM image disk."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple


TARGETS = ("cpu", "memory", "io", "none")
DEFAULT_ALLOWED_QEMU_GUESTS = (
    "bonect-replay-v87-1",
    "bonect-replay-v87-2",
    "bonect-replay-v87-3",
    "bonect-replay-v87-4",
)
DEFAULT_LOCK_PATH = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_vm/"
    "FOUR_VM_EXCLUSIVE_DISK1.lock"
)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
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


def command(args: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def disk1_containers() -> Dict[str, Dict[str, Any]]:
    listed = command(["docker", "ps", "-q"])
    ids = [value for value in listed.stdout.splitlines() if value]
    if listed.returncode != 0 or not ids:
        return {}
    inspected = command(["docker", "inspect", *ids])
    if inspected.returncode != 0:
        raise RuntimeError(f"docker inspect failed: {inspected.stderr.strip()}")
    values = json.loads(inspected.stdout)
    result: Dict[str, Dict[str, Any]] = {}
    for value in values:
        mounts = value.get("Mounts") or []
        disk1_mounts = [
            str(item.get("Source"))
            for item in mounts
            if str(item.get("Source") or "").startswith("/disk1/")
        ]
        if not disk1_mounts:
            continue
        container_id = str(value.get("Id") or "")
        state = value.get("State") or {}
        result[container_id] = {
            "id": container_id,
            "name": str(value.get("Name") or "").lstrip("/"),
            "pid": int(state.get("Pid") or 0),
            "started_at": str(state.get("StartedAt") or ""),
            "mounts": disk1_mounts,
        }
    return result


def blkio_cgroup_path(pid: int) -> Optional[Path]:
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        hierarchy, controllers, relative = line.split(":", 2)
        _ = hierarchy
        if "blkio" in controllers.split(","):
            return Path("/sys/fs/cgroup/blkio") / relative.lstrip("/")
    return None


def container_disk_bytes(container: Dict[str, Any], device: str) -> int:
    cgroup = blkio_cgroup_path(int(container.get("pid") or 0))
    if cgroup is None:
        raise RuntimeError(f"blkio cgroup missing for {container.get('name')}")
    stats_path = cgroup / "blkio.throttle.io_service_bytes"
    total = 0
    for line in stats_path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == device and fields[1] in {"Read", "Write"}:
            total += int(fields[2])
    return total


def snapshot(device: str) -> Dict[str, Dict[str, Any]]:
    containers = disk1_containers()
    for value in containers.values():
        value["bytes"] = container_disk_bytes(value, device)
    return containers


def qemu_guest_name(cmdline: str) -> str:
    for token in cmdline.split():
        if token.startswith("guest="):
            return token.split("=", 1)[1].split(",", 1)[0]
    return ""


def qemu_disk1_processes() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if "qemu-system" not in cmdline or "/disk1/" not in cmdline:
            continue
        guest = qemu_guest_name(cmdline)
        if not guest:
            continue
        disk1_paths = sorted(
            {
                token.strip("\"',{}")
                for token in cmdline.split()
                if "/disk1/" in token
            }
        )
        result[guest] = {
            "guest": guest,
            "pid": int(entry.name),
            "disk1_paths": disk1_paths,
        }
    return result


def qemu_snapshot(device: str) -> Dict[str, Dict[str, Any]]:
    guests = qemu_disk1_processes()
    for value in guests.values():
        cgroup = blkio_cgroup_path(int(value["pid"]))
        if cgroup is None:
            raise RuntimeError(f"blkio cgroup missing for QEMU guest {value['guest']}")
        stats_path = cgroup / "blkio.throttle.io_service_bytes"
        total = 0
        for line in stats_path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[0] == device and fields[1] in {"Read", "Write"}:
                total += int(fields[2])
        value["bytes"] = total
        value["cgroup"] = str(cgroup)
    return guests


def all_converged(
    run_root: Path,
    targets: Tuple[str, ...] = TARGETS,
    stop_reasons: Tuple[str, ...] = ("epsilon_converged",),
) -> bool:
    for target in targets:
        path = run_root / target / f"final_for_{target}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        stop_reason = str(value.get("stop_reason") or "")
        if stop_reason not in stop_reasons:
            return False
        if stop_reason == "epsilon_converged" and value.get("converged") is not True:
            return False
    return True


def stop_run_processes(run_root: Path, session: str) -> None:
    """Freeze and kill an invalidated run before removing the guard's tmux session."""

    own_pid = os.getpid()
    own_pgid = os.getpgrp()

    def process_snapshot() -> Tuple[Dict[int, int], Dict[int, str]]:
        parents: Dict[int, int] = {}
        cmdlines: Dict[int, str] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                status_lines = (entry / "status").read_text(encoding="utf-8").splitlines()
                parent_line = next(line for line in status_lines if line.startswith("PPid:"))
                parents[pid] = int(parent_line.split()[1])
                cmdlines[pid] = (
                    (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                )
            except (OSError, StopIteration, ValueError):
                continue
        return parents, cmdlines

    def own_ancestors(parents: Dict[int, int]) -> set:
        result = {own_pid}
        current = own_pid
        while current in parents and parents[current] > 1 and parents[current] not in result:
            current = parents[current]
            result.add(current)
        return result

    def matching_processes() -> List[int]:
        parents, cmdlines = process_snapshot()
        excluded = own_ancestors(parents)
        matched = {
            pid
            for pid, cmdline in cmdlines.items()
            if pid not in excluded and str(run_root) in cmdline
        }
        changed = True
        while changed:
            changed = False
            for pid, parent in parents.items():
                if pid not in excluded and parent in matched and pid not in matched:
                    matched.add(pid)
                    changed = True
        return sorted(matched)

    def signal_processes(pids: List[int], sig: signal.Signals) -> None:
        groups = set()
        singles = set()
        for pid in pids:
            try:
                pgid = os.getpgid(pid)
            except ProcessLookupError:
                continue
            if pgid > 1 and pgid != own_pgid:
                groups.add(pgid)
            else:
                singles.add(pid)
        for pgid in sorted(groups):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                pass
        for pid in sorted(singles):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    # The tuner intentionally ignores TERM/HUP, so freeze first to prevent it
    # from launching a replacement validator during cleanup, then kill the
    # complete process groups. Repeat to close the /proc enumeration race.
    for _ in range(3):
        matched = matching_processes()
        if not matched:
            break
        signal_processes(matched, signal.SIGSTOP)
        matched = sorted(set(matched) | set(matching_processes()))
        signal_processes(matched, signal.SIGKILL)
        time.sleep(0.25)

    # This must be last because the guard itself lives in this tmux session and
    # may receive SIGHUP as soon as the session disappears.
    command(["tmux", "kill-session", "-t", session])


def release_lock(lock_path: Path, run_root: Path) -> None:
    try:
        contents = lock_path.read_text(encoding="utf-8")
    except OSError:
        return
    if f"run_root={run_root}" not in contents:
        return
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("session")
    parser.add_argument("--interval-sec", type=float, default=5.0)
    parser.add_argument("--max-docker-mib-s", type=float, default=1.0)
    parser.add_argument("--max-qemu-mib-s", type=float, default=1.0)
    parser.add_argument(
        "--allowed-qemu-guests",
        nargs="+",
        default=list(DEFAULT_ALLOWED_QEMU_GUESTS),
    )
    parser.add_argument("--device", default="259:6", help="major:minor for the /disk1 block device")
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=TARGETS,
        default=list(TARGETS),
        help="Only these target final artifacts are required before releasing the lock",
    )
    parser.add_argument(
        "--stop-reasons",
        nargs="+",
        default=["epsilon_converged"],
        help="Final stop reasons accepted as terminal for the selected targets",
    )
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=DEFAULT_LOCK_PATH,
    )
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    interval = max(1.0, args.interval_sec)
    allowed_qemu_guests = tuple(sorted(set(args.allowed_qemu_guests)))
    history_path = run_root / "host_disk_isolation_history.jsonl"
    try:
        previous = snapshot(args.device)
        previous_qemu = qemu_snapshot(args.device)
        missing_allowed = sorted(set(allowed_qemu_guests) - set(previous_qemu))
        if missing_allowed:
            raise RuntimeError(f"allowed QEMU guests missing at guard start: {missing_allowed}")
    except Exception as exc:
        failure_payload = {
            "event": "host_disk_guard_failure",
            "status": "conflict",
            "all_converged": False,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "device": args.device,
            "interval_sec": interval,
            "max_docker_mib_s": args.max_docker_mib_s,
            "max_qemu_mib_s": args.max_qemu_mib_s,
            "allowed_qemu_guests": list(allowed_qemu_guests),
            "error": f"{type(exc).__name__}: {exc}",
            "conflicts": [{"reason": "guard_failure", "error": str(exc)}],
        }
        marker = (
            "# Invalidated: host disk isolation guard failed\n\n"
            f"- Detected: `{failure_payload['updated_at']}`\n"
            f"- Error: `{type(exc).__name__}: {exc}`\n"
        )
        atomic_write(
            run_root / "host_disk_isolation_status.json",
            json.dumps(failure_payload, indent=2) + "\n",
        )
        append_jsonl(history_path, failure_payload)
        atomic_write(run_root / "INVALIDATED_HOST_DISK_GUARD_FAILURE.md", marker)
        release_lock(args.lock_path, run_root)
        stop_run_processes(run_root, args.session)
        return 2
    start_payload = {
        "event": "host_disk_guard_start",
        "status": "ok",
        "all_converged": False,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "device": args.device,
        "interval_sec": interval,
        "max_docker_mib_s": args.max_docker_mib_s,
        "max_qemu_mib_s": args.max_qemu_mib_s,
        "allowed_qemu_guests": list(allowed_qemu_guests),
        "tracked_containers": list(previous.values()),
        "tracked_qemu_guests": list(previous_qemu.values()),
        "conflicts": [],
    }
    atomic_write(run_root / "host_disk_isolation_status.json", json.dumps(start_payload, indent=2) + "\n")
    append_jsonl(history_path, start_payload)
    while True:
        time.sleep(interval)
        if all_converged(run_root, tuple(args.targets), tuple(args.stop_reasons)):
            epsilon_converged = all_converged(run_root, tuple(args.targets))
            payload = {
                "event": "host_disk_guard_complete",
                "status": "ok",
                "all_terminal": True,
                "all_converged": epsilon_converged,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "device": args.device,
                "max_docker_mib_s": args.max_docker_mib_s,
                "max_qemu_mib_s": args.max_qemu_mib_s,
                "allowed_qemu_guests": list(allowed_qemu_guests),
                "targets": list(args.targets),
                "accepted_stop_reasons": list(args.stop_reasons),
                "tracked_containers": list(previous.values()),
                "tracked_qemu_guests": list(previous_qemu.values()),
            }
            atomic_write(run_root / "host_disk_isolation_status.json", json.dumps(payload, indent=2) + "\n")
            append_jsonl(history_path, payload)
            release_lock(args.lock_path, run_root)
            return 0
        try:
            current = snapshot(args.device)
            current_qemu = qemu_snapshot(args.device)
        except Exception as exc:
            failure_payload = {
                "event": "host_disk_guard_failure",
                "status": "conflict",
                "all_converged": False,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "device": args.device,
                "interval_sec": interval,
                "max_docker_mib_s": args.max_docker_mib_s,
                "max_qemu_mib_s": args.max_qemu_mib_s,
                "allowed_qemu_guests": list(allowed_qemu_guests),
                "error": f"{type(exc).__name__}: {exc}",
                "conflicts": [{"reason": "guard_failure", "error": str(exc)}],
            }
            marker = (
                "# Invalidated: host disk isolation guard failed\n\n"
                f"- Detected: `{failure_payload['updated_at']}`\n"
                f"- Error: `{type(exc).__name__}: {exc}`\n"
            )
            atomic_write(
                run_root / "host_disk_isolation_status.json",
                json.dumps(failure_payload, indent=2) + "\n",
            )
            append_jsonl(history_path, failure_payload)
            atomic_write(run_root / "INVALIDATED_HOST_DISK_GUARD_FAILURE.md", marker)
            release_lock(args.lock_path, run_root)
            stop_run_processes(run_root, args.session)
            return 2
        rates: List[Dict[str, Any]] = []
        qemu_rates: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []
        for container_id, value in current.items():
            prior = previous.get(container_id)
            delta = max(0, int(value["bytes"]) - int((prior or value)["bytes"]))
            rate = delta / interval / 1048576
            item = {
                "id": container_id,
                "name": value["name"],
                "mounts": value["mounts"],
                "mib_s": rate,
            }
            rates.append(item)
            if prior is None:
                item["reason"] = "new_disk1_container_started"
                conflicts.append(item)
            elif value.get("started_at") != prior.get("started_at"):
                item["reason"] = "disk1_container_restarted"
                conflicts.append(item)
            elif rate > args.max_docker_mib_s:
                item["reason"] = "disk1_container_io_above_limit"
                conflicts.append(item)
        for guest, value in current_qemu.items():
            prior = previous_qemu.get(guest)
            delta = max(0, int(value["bytes"]) - int((prior or value)["bytes"]))
            rate = delta / interval / 1048576
            item = {
                "guest": guest,
                "pid": value["pid"],
                "disk1_paths": value["disk1_paths"],
                "mib_s": rate,
                "allowed": guest in allowed_qemu_guests,
            }
            qemu_rates.append(item)
            if guest in allowed_qemu_guests:
                if prior is None:
                    item["reason"] = "allowed_qemu_guest_started_after_guard"
                    conflicts.append(item)
                elif int(value["pid"]) != int(prior["pid"]):
                    item["reason"] = "allowed_qemu_guest_restarted"
                    conflicts.append(item)
            elif prior is None:
                item["reason"] = "non_target_disk1_qemu_started"
                conflicts.append(item)
            elif rate > args.max_qemu_mib_s:
                item["reason"] = "non_target_disk1_qemu_io_above_limit"
                conflicts.append(item)
        for guest in allowed_qemu_guests:
            if guest not in current_qemu:
                conflicts.append({
                    "guest": guest,
                    "mib_s": 0.0,
                    "allowed": True,
                    "reason": "allowed_qemu_guest_missing",
                })
        payload = {
            "event": "host_disk_guard_sample",
            "status": "conflict" if conflicts else "ok",
            "all_converged": False,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "device": args.device,
            "interval_sec": interval,
            "max_docker_mib_s": args.max_docker_mib_s,
            "max_qemu_mib_s": args.max_qemu_mib_s,
            "allowed_qemu_guests": list(allowed_qemu_guests),
            "rates": rates,
            "qemu_rates": qemu_rates,
            "conflicts": conflicts,
        }
        atomic_write(run_root / "host_disk_isolation_status.json", json.dumps(payload, indent=2) + "\n")
        append_jsonl(history_path, payload)
        if conflicts:
            lines = [
                "# Invalidated: runtime host disk conflict",
                "",
                f"- Detected: `{payload['updated_at']}`",
                f"- Device: `{args.device}`",
                f"- Limit: `{args.max_docker_mib_s} MiB/s` per Docker container",
                f"- Non-target QEMU limit: `{args.max_qemu_mib_s} MiB/s`",
                "- Conflicts:",
            ]
            lines.extend(
                f"  - `{item.get('name') or item.get('guest')}`: "
                f"{item['reason']}, {item['mib_s']:.6f} MiB/s"
                for item in conflicts
            )
            lines.append("")
            atomic_write(run_root / "INVALIDATED_RUNTIME_HOST_DISK_CONFLICT.md", "\n".join(lines))
            release_lock(args.lock_path, run_root)
            stop_run_processes(run_root, args.session)
            return 2
        previous = current
        previous_qemu = current_qemu


if __name__ == "__main__":
    raise SystemExit(main())
