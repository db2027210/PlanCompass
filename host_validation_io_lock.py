"""Cross-process reader/writer lock for storage-sensitive DB validation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


SCENARIOS = {"cpu", "memory", "io", "none"}
LOCK_MODES = {"shared", "exclusive"}

# Process-local reentrancy: nested shared under shared (or exclusive under exclusive)
# must not re-enter the FIFO ticket queue.  Otherwise a holder that opens a second
# shared scope while an exclusive waiter is queued deadlocks with writer preference.
_HELD_BY_PID: Dict[Tuple[str, int], Dict[str, Any]] = {}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _linux_process_start_ticks(pid: int) -> Optional[int]:
    try:
        stat_text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        fields_after_comm = stat_text[stat_text.rfind(")") + 2 :].split()
        return int(fields_after_comm[19])
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def _ticket_is_live(ticket_path: Path) -> bool:
    try:
        payload = json.loads(ticket_path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
        expected_start_ticks = int(payload["process_start_ticks"])
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return _linux_process_start_ticks(pid) == expected_start_ticks


def _cpu_total_and_iowait_jiffies() -> Tuple[int, int]:
    fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    if len(fields) < 6 or fields[0] != "cpu":
        raise RuntimeError("cannot parse aggregate CPU statistics")
    values = [int(value) for value in fields[1:]]
    return sum(values), values[4]


def _disk_busy_milliseconds(device: str) -> int:
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 13 or fields[2] != device:
            continue
        # Linux diskstats field 13 is milliseconds spent doing I/O.  The first
        # three tokens are major, minor and device name, respectively.
        return int(fields[12])
    raise RuntimeError(f"disk device {device!r} is absent from /proc/diskstats")


def _available_memory_gib() -> float:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "MemAvailable:":
            return float(fields[1]) / (1024.0 * 1024.0)
    raise RuntimeError("MemAvailable is absent from /proc/meminfo")


def _host_pair_admission_snapshot(admission: Mapping[str, Any]) -> Dict[str, Any]:
    """Sample the host before a second shared DB replay is admitted.

    Only a CPU+Memory pair uses this check.  The first replay keeps normal
    exclusive-equivalent behaviour; admitting the second one is conditional
    on the shared ``/disk2`` device being genuinely quiet.
    """
    device = str(admission.get("disk_device") or "nvme2n1")
    interval_s = min(5.0, max(0.1, float(admission.get("sample_interval_s") or 1.0)))
    before_total, before_iowait = _cpu_total_and_iowait_jiffies()
    before_busy = _disk_busy_milliseconds(device)
    time.sleep(interval_s)
    after_total, after_iowait = _cpu_total_and_iowait_jiffies()
    after_busy = _disk_busy_milliseconds(device)
    elapsed_ms = interval_s * 1000.0
    total_delta = after_total - before_total
    cpu_iowait_pct = (
        100.0 * (after_iowait - before_iowait) / total_delta
        if total_delta > 0
        else float("inf")
    )
    disk_util_pct = max(0.0, min(100.0, 100.0 * (after_busy - before_busy) / elapsed_ms))
    available_memory_gib = _available_memory_gib()
    max_disk_util_pct = float(admission.get("max_disk_util_pct") or 55.0)
    max_cpu_iowait_pct = float(admission.get("max_cpu_iowait_pct") or 3.0)
    min_available_memory_gib = float(admission.get("min_available_memory_gib") or 500.0)
    failures: List[str] = []
    if disk_util_pct > max_disk_util_pct:
        failures.append("disk_util_above_limit")
    if cpu_iowait_pct > max_cpu_iowait_pct:
        failures.append("cpu_iowait_above_limit")
    if available_memory_gib < min_available_memory_gib:
        failures.append("available_memory_below_limit")
    return {
        "sample_interval_s": interval_s,
        "disk_device": device,
        "disk_util_pct": disk_util_pct,
        "max_disk_util_pct": max_disk_util_pct,
        "cpu_iowait_pct": cpu_iowait_pct,
        "max_cpu_iowait_pct": max_cpu_iowait_pct,
        "available_memory_gib": available_memory_gib,
        "min_available_memory_gib": min_available_memory_gib,
        "passed": not failures,
        "failures": failures,
    }


@contextmanager
def host_validation_io_lock(
    lock_path: Path,
    scenario: str,
    *,
    timeout_s: float,
    mode: Optional[str] = None,
    scope: str = "restore_and_measurement",
    paired_shared_admission: Optional[Mapping[str, Any]] = None,
) -> Iterable[Dict[str, Any]]:
    """Acquire a FIFO, writer-preferring host-storage reader/writer lock.

    Nested acquisitions by the same process are re-entrant for compatible modes
    (shared under shared/exclusive, exclusive under exclusive).  This preserves
    writer preference across processes while preventing the classic deadlock:
    shared holder opens a second shared scope while an exclusive waiter is
    queued behind the first ticket.
    """
    normalized = str(scenario).strip().lower()
    if normalized not in SCENARIOS:
        raise ValueError(f"unsupported validation-I/O lock scenario: {scenario!r}")
    # Default: only IO validation takes exclusive (storage-sensitive, writer).
    # Memory/CPU/none share the lock so multi-lane formals can progress in
    # parallel without the classic deadlock:
    #   cpu=shared + io=exclusive-waiter + memory=exclusive-waiter → stall.
    # Callers that need exclusive memory (e.g. seed rebase) pass mode=explicitly.
    lock_mode = str(
        mode or ("exclusive" if normalized == "io" else "shared")
    ).strip().lower()
    if lock_mode not in LOCK_MODES:
        raise ValueError(f"unsupported validation-I/O lock mode: {lock_mode!r}")
    lock_path = lock_path.resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    queue_dir = Path(f"{lock_path}.queue")
    queue_dir.mkdir(parents=True, exist_ok=True)
    admission = dict(paired_shared_admission or {})
    admission_enabled = bool(admission.get("enabled", False))
    required_healthy_samples = max(
        1,
        int(admission.get("required_consecutive_healthy_samples") or 1),
    )
    healthy_pair_samples = 0
    last_admission: Dict[str, Any] = {
        "enabled": admission_enabled,
        "required_for_second_shared_lane": False,
        "passed": True,
        "reason": "not_required",
    }

    pid = os.getpid()
    process_start_ticks = _linux_process_start_ticks(pid)
    if process_start_ticks is None:
        raise RuntimeError(f"cannot identify validation-I/O lock process pid={pid}")
    started = time.time()
    deadline = started + max(1.0, float(timeout_s))
    held_key = (str(lock_path), pid)
    held = _HELD_BY_PID.get(held_key)
    if held is not None:
        held_mode = str(held.get("mode") or "")
        compatible = (
            lock_mode == "shared" and held_mode in LOCK_MODES
        ) or (lock_mode == "exclusive" and held_mode == "exclusive")
        if not compatible:
            raise RuntimeError(
                "host validation-I/O lock mode upgrade is not supported: "
                f"held={held_mode} requested={lock_mode} path={lock_path}"
            )
        held["depth"] = int(held.get("depth") or 1) + 1
        try:
            yield {
                "status": "acquired_reentrant",
                "path": str(lock_path),
                "scenario": normalized,
                "mode": lock_mode,
                "scope": str(scope),
                "wait_s": 0.0,
                "queue_policy": "process_local_reentrant",
                "queue_ticket": None,
                "reentrant_depth": held["depth"],
                "paired_shared_admission": {
                    "enabled": admission_enabled,
                    "passed": True,
                    "reason": "reentrant",
                },
            }
        finally:
            held["depth"] = int(held.get("depth") or 1) - 1
            if held["depth"] <= 0:
                _HELD_BY_PID.pop(held_key, None)
        return

    token = hashlib.sha256(
        f"{normalized}:{lock_mode}:{scope}".encode("utf-8")
    ).hexdigest()[:10]
    ticket_name = f"{time.time_ns():020d}_{pid}_{token}.ticket"
    ticket_path = queue_dir / ticket_name
    pending_path = queue_dir / f".{ticket_name}.pending"
    _write_json(
        pending_path,
        {
            "scenario": normalized,
            "mode": lock_mode,
            "scope": str(scope),
            "pid": pid,
            "process_start_ticks": process_start_ticks,
            "queued_at_epoch_s": started,
            "lock_path": str(lock_path),
        },
    )
    os.replace(pending_path, ticket_path)

    with lock_path.open("a+", encoding="utf-8") as handle:
        acquired = False
        try:
            while not acquired:
                live_tickets: List[Tuple[Path, Dict[str, Any]]] = []
                for candidate in queue_dir.glob("*.ticket"):
                    if candidate != ticket_path and not _ticket_is_live(candidate):
                        try:
                            candidate.unlink()
                        except FileNotFoundError:
                            pass
                        continue
                    try:
                        payload = json.loads(candidate.read_text(encoding="utf-8"))
                    except (FileNotFoundError, OSError, json.JSONDecodeError):
                        continue
                    live_tickets.append((candidate, payload))
                live_tickets.sort(key=lambda item: item[0].name)
                earlier = [
                    payload
                    for candidate, payload in live_tickets
                    if candidate.name < ticket_name
                ]
                # Writer preference across processes.  Tickets owned by this pid
                # that are already ahead do not re-block a nested request (the
                # reentrant path above is the primary escape; this is a belt).
                earlier_foreign = [
                    item
                    for item in earlier
                    if int(item.get("pid") or -1) != pid
                ]
                queue_allows = (
                    not earlier_foreign
                    if lock_mode == "exclusive"
                    else not any(
                        item.get("mode") == "exclusive" for item in earlier_foreign
                    )
                )
                earlier_shared = any(item.get("mode") == "shared" for item in earlier)
                admission_allows = True
                if lock_mode == "shared" and admission_enabled and earlier_shared:
                    try:
                        last_admission = _host_pair_admission_snapshot(admission)
                    except Exception as exc:
                        last_admission = {
                            "enabled": True,
                            "required_for_second_shared_lane": True,
                            "passed": False,
                            "reason": "host_load_sample_failed",
                            "failure_reason": f"{type(exc).__name__}: {exc}",
                        }
                    else:
                        last_admission["enabled"] = True
                        last_admission["required_for_second_shared_lane"] = True
                        last_admission["required_healthy_samples"] = required_healthy_samples
                        last_admission["consecutive_healthy_samples"] = (
                            healthy_pair_samples + 1
                            if last_admission.get("passed")
                            else 0
                        )
                    if last_admission.get("passed"):
                        healthy_pair_samples += 1
                    else:
                        healthy_pair_samples = 0
                    admission_allows = healthy_pair_samples >= required_healthy_samples
                    last_admission["passed"] = admission_allows
                    if not admission_allows and not last_admission.get("failures"):
                        last_admission["failures"] = ["healthy_sample_streak_not_reached"]
                elif lock_mode == "shared" and admission_enabled:
                    last_admission = {
                        "enabled": True,
                        "required_for_second_shared_lane": False,
                        "passed": True,
                        "reason": "first_shared_lane",
                    }
                if queue_allows:
                    operation = (
                        fcntl.LOCK_EX if lock_mode == "exclusive" else fcntl.LOCK_SH
                    )
                    if admission_allows:
                        try:
                            fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                            acquired = True
                        except BlockingIOError:
                            pass
                if acquired:
                    break
                if time.time() >= deadline:
                    raise RuntimeError(
                        "host validation-I/O lock timeout: "
                        f"scenario={normalized} mode={lock_mode} path={lock_path} "
                        f"timeout_s={timeout_s}"
                    )
                time.sleep(0.5)
            result = {
                "status": "acquired",
                "path": str(lock_path),
                "scenario": normalized,
                "mode": lock_mode,
                "scope": str(scope),
                "wait_s": time.time() - started,
                "queue_policy": (
                    "fifo_writer_preference_process_start_verified_reentrant_safe"
                ),
                "queue_ticket": ticket_name,
                "paired_shared_admission": last_admission,
            }
            _HELD_BY_PID[held_key] = {
                "mode": lock_mode,
                "depth": 1,
                "scenario": normalized,
            }
            try:
                yield result
            finally:
                held_now = _HELD_BY_PID.get(held_key)
                if held_now is not None:
                    held_now["depth"] = int(held_now.get("depth") or 1) - 1
                    if held_now["depth"] <= 0:
                        _HELD_BY_PID.pop(held_key, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                ticket_path.unlink()
            except FileNotFoundError:
                pass
