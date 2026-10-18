#!/usr/bin/env python3
"""Continuous host resource guard for anonymous experiment / VM / Docker workloads.

Samples host CPU, memory, IO-block pressure, and used capacity of /disk1, /disk2,
and /disk3. On breach, selects anonymous-owned experiment processes, QEMU/KVM VMs,
and experiment Docker containers and stops them (or dry-runs the intended acts).

Default mode is dry-run: evaluate and log only. Pass --live to send stop/kill.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pwd
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


# ---------------------------------------------------------------------------
# Defaults (overridable via CLI / env)
# ---------------------------------------------------------------------------

DEFAULT_INTERVAL_S = 5.0
DEFAULT_CPU_BUSY_PCT = 95.0
DEFAULT_MEM_USED_PCT = 95.0
DEFAULT_MEM_AVAILABLE_FLOOR_BYTES = 8 * 1024**3  # 8 GiB on ~1 TiB hosts
DEFAULT_IOWAIT_PCT = 40.0
DEFAULT_DISK_UTIL_PCT = 90.0
DEFAULT_DISK_USED_PCT = 90.0  # breach when used% > this
DEFAULT_DISK_PATHS = ("/disk1", "/disk2", "/disk3")
DEFAULT_CPU_SAMPLE_S = 0.5
DEFAULT_IO_SAMPLE_S = 0.5
DEFAULT_STOP_TIMEOUT_S = 3.0
DEFAULT_OWNER = "anonymous"

# Experiment process cmdline markers (owned by DEFAULT_OWNER).
# Prefer concrete runner scripts over broad path fragments so interactive
# shells / goal monitors under the repo are not selected.
EXPERIMENT_CMDLINE_MARKERS = (
    "double_loop_rootless_vm_task_worker",
    "run_double_loop_sgd_scenarios",
    "run_mysql_gd_matrix",
    "experiment_matrix_mysql",
    "mysql_system_variable_grid_runner",
    "mysql_double_loop_grid_runner",
    "mysql_experiment_runner",
    "mysql_test_orchestrator",
    "mysql_benchmark_runner",
    "mysql_hybench_runner",
    "mysql_job_runner",
    "tpcc_cpu_factorial_runner",
    "binary_plan_resource_tuner",
    "run_benchmark_capture",
    "rootless_vm_task_worker",
    "run_fixed_protocol_batch",
    "resource_adaptive_experiment_matrix",
    "resource_adaptive_double_loop",
    "bruteforce_pg17",
    "HyBench",
    "com.hybench",
    "benchbase.jar",
    "benchbase-mysql",
    "benchbase-postgres",
    "/hybench ",
    " hybench ",
    "bash hybench",
    "hybench -t ",
)

# Never kill these even when cmdline path mentions experiment trees.
EXPERIMENT_CMDLINE_EXCLUDES = (
    "host_resource_guard",
    "codex_goal_watchdog",
    "update_goal_monitor",
    "watch_b6_recovery",
    "continuous_monitor",
    "GROK_AGENT",
    "grok-goal-",
    "app-server",
    "vscode-server",
    "cursor-server",
)

# Safe interactive / infra processes never killed solely by comm name.
SAFE_COMM_EXACT = frozenset(
    {
        "sshd",
        "ssh",
        "sftp-server",
        "systemd",
        "(sd-pam)",
        "dbus-daemon",
        "pulseaudio",
        "node",
        "code",
        "code-server",
        "grok",
        "codex",
        "MainThread",
        "cursor",
        "sleep",
        "tail",
        "less",
        "vim",
        "nvim",
        "emacs",
        "htop",
        "btop",
        "top",
        "ps",
        "docker",
        "containerd",
        "dockerd",
    }
)

# Docker name / image heuristics for anonymous experiment containers.
DOCKER_EXPERIMENT_NAME_RE = re.compile(
    r"(?i)("
    r"ra[_-]?mysql|"
    r"ra[_-]?pg|"
    r"ra[_-]?rootless|"
    r"resource[_-]?adaptive|"
    r"mysql[_-]?gd|"
    r"mysql[_-]?goal|"
    r"gptuner[_-]?mysql|"
    r"hybench|"
    r"benchbase.*mysql|"
    r"tpcc.*mysql|"
    r"tpch.*mysql"
    r")"
)

QEMU_COMM_PREFIXES = ("qemu-system-", "qemu-kvm")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    cpu_busy_pct: float = DEFAULT_CPU_BUSY_PCT
    mem_used_pct: float = DEFAULT_MEM_USED_PCT
    mem_available_floor_bytes: int = DEFAULT_MEM_AVAILABLE_FLOOR_BYTES
    iowait_pct: float = DEFAULT_IOWAIT_PCT
    disk_util_pct: float = DEFAULT_DISK_UTIL_PCT
    disk_used_pct: float = DEFAULT_DISK_USED_PCT
    disk_paths: Tuple[str, ...] = DEFAULT_DISK_PATHS


@dataclass
class MetricSnapshot:
    """Host resource sample used by breach decision."""

    ts: float
    cpu_busy_pct: float
    mem_used_pct: float
    mem_available_bytes: int
    mem_total_bytes: int
    iowait_pct: float
    disk_util_avg_pct: float
    disk_used_pct: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_log_dict(self) -> dict:
        return {
            "ts": self.ts,
            "cpu_busy_pct": round(self.cpu_busy_pct, 2),
            "mem_used_pct": round(self.mem_used_pct, 2),
            "mem_available_bytes": self.mem_available_bytes,
            "mem_total_bytes": self.mem_total_bytes,
            "iowait_pct": round(self.iowait_pct, 2),
            "disk_util_avg_pct": round(self.disk_util_avg_pct, 2),
            "disk_used_pct": {k: round(v, 2) for k, v in self.disk_used_pct.items()},
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class KillTarget:
    """A anonymous-scoped workload candidate for stop/kill."""

    kind: str  # "process" | "qemu" | "docker"
    identity: str  # pid as str, or docker id/name
    name: str
    owner: str
    reason_match: str = ""

    def key(self) -> Tuple[str, str]:
        return self.kind, self.identity


@dataclass
class ActResult:
    target: KillTarget
    action: str  # "would_stop" | "stop" | "kill" | "skip" | "error"
    detail: str = ""
    dry_run: bool = True


# ---------------------------------------------------------------------------
# Pure decision logic (testable without real host overload)
# ---------------------------------------------------------------------------


def evaluate_breach(snapshot: MetricSnapshot, thresholds: Thresholds) -> List[str]:
    """Return ordered reason codes for any OBJECTIVE breach conditions.

    Reason codes:
      - cpu_saturated
      - memory_over_limit
      - io_blocked
      - disk_over_limit:<path>  (used% > thresholds.disk_used_pct)
    """
    reasons: List[str] = []
    if snapshot.cpu_busy_pct >= thresholds.cpu_busy_pct:
        reasons.append("cpu_saturated")
    mem_over = snapshot.mem_used_pct >= thresholds.mem_used_pct
    floor_breached = (
        snapshot.mem_available_bytes >= 0
        and snapshot.mem_available_bytes < thresholds.mem_available_floor_bytes
        and snapshot.mem_total_bytes > thresholds.mem_available_floor_bytes
    )
    if mem_over or floor_breached:
        reasons.append("memory_over_limit")
    if (
        snapshot.iowait_pct >= thresholds.iowait_pct
        or snapshot.disk_util_avg_pct >= thresholds.disk_util_pct
    ):
        reasons.append("io_blocked")
    for path in thresholds.disk_paths:
        used = snapshot.disk_used_pct.get(path)
        if used is not None and used > thresholds.disk_used_pct:
            reasons.append(f"disk_over_limit:{path}")
    return reasons


def is_monitor_self(target: KillTarget, monitor_pid: int) -> bool:
    if target.kind in {"process", "qemu"}:
        try:
            return int(target.identity) == int(monitor_pid)
        except (TypeError, ValueError):
            return False
    return False


def select_kill_targets(
    candidates: Sequence[KillTarget],
    *,
    owner: str = DEFAULT_OWNER,
    monitor_pid: int,
    allowed_kinds: Optional[Set[str]] = None,
) -> List[KillTarget]:
    """Filter to anonymous-owned experiment/VM/docker targets; drop monitor and others."""
    allowed = allowed_kinds or {"process", "qemu", "docker"}
    selected: List[KillTarget] = []
    seen: Set[Tuple[str, str]] = set()
    for target in candidates:
        if target.kind not in allowed:
            continue
        if target.owner and target.owner != owner:
            continue
        if is_monitor_self(target, monitor_pid):
            continue
        key = target.key()
        if key in seen:
            continue
        seen.add(key)
        selected.append(target)
    return selected


def decide_and_act(
    snapshot: MetricSnapshot,
    thresholds: Thresholds,
    candidates: Sequence[KillTarget],
    *,
    owner: str,
    monitor_pid: int,
    dry_run: bool,
    act_fn: Callable[[KillTarget, bool], ActResult],
) -> Tuple[List[str], List[KillTarget], List[ActResult]]:
    """Full sample→decide→act path used by the loop and by tests.

    Act is invoked only when there is at least one breach reason.
    """
    reasons = evaluate_breach(snapshot, thresholds)
    targets = select_kill_targets(
        candidates, owner=owner, monitor_pid=monitor_pid
    )
    results: List[ActResult] = []
    if not reasons:
        return reasons, targets, results
    for target in targets:
        results.append(act_fn(target, dry_run))
    return reasons, targets, results


# ---------------------------------------------------------------------------
# Metric sampling (side effects)
# ---------------------------------------------------------------------------


def _read_proc_stat_cpu() -> Tuple[int, int, int]:
    """Return (total_jiffies, idle_jiffies, iowait_jiffies) for aggregate cpu."""
    with open("/proc/stat", "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("cpu "):
                parts = line.split()
                # user nice system idle iowait irq softirq steal guest guest_nice
                vals = [int(x) for x in parts[1:]]
                while len(vals) < 8:
                    vals.append(0)
                idle = vals[3]
                iowait = vals[4]
                total = sum(vals[:8])  # exclude guest double-count fields if present
                return total, idle, iowait
    raise RuntimeError("could not parse /proc/stat cpu line")


def _read_meminfo() -> Tuple[int, int]:
    """Return (mem_total_bytes, mem_available_bytes)."""
    total = available = 0
    with open("/proc/meminfo", "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
    if total <= 0:
        raise RuntimeError("could not parse MemTotal from /proc/meminfo")
    return total, available


def _read_diskstats() -> Dict[str, Tuple[int, int]]:
    """Return device -> (io_ticks_ms, weighted) from /proc/diskstats field 13.

    Field layout (Linux): #13 is time spent doing I/Os (ms) — used for util%.
    We key by base disk names only (nvme0n1, sda, …), skipping partitions when
    a parent disk is present would be ideal, but averaging all disks with
    non-zero activity is enough for host pressure.
    """
    out: Dict[str, Tuple[int, int]] = {}
    with open("/proc/diskstats", "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            # Skip loop/ram/dm helpers that are not the bulk data disks.
            if name.startswith(("loop", "ram", "dm-")):
                continue
            io_ticks = int(parts[12])  # field 13 (0-indexed 12)
            out[name] = (io_ticks, int(parts[13]) if len(parts) > 13 else 0)
    return out


def _disk_used_percent(path: str) -> Optional[float]:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    if total <= 0:
        return None
    free = st.f_bavail * st.f_frsize
    used = total - free
    return 100.0 * used / total


def sample_metrics(
    *,
    disk_paths: Sequence[str] = DEFAULT_DISK_PATHS,
    cpu_sample_s: float = DEFAULT_CPU_SAMPLE_S,
    io_sample_s: float = DEFAULT_IO_SAMPLE_S,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> MetricSnapshot:
    """Sample host CPU, memory, IO util, and named disk used%."""
    notes: List[str] = []
    t0, idle0, iowait0 = _read_proc_stat_cpu()
    ds0 = _read_diskstats()
    sample_window = max(cpu_sample_s, io_sample_s, 0.05)
    sleep_fn(sample_window)
    t1, idle1, iowait1 = _read_proc_stat_cpu()
    ds1 = _read_diskstats()

    dt = max(t1 - t0, 1)
    idle_d = max(idle1 - idle0, 0)
    iowait_d = max(iowait1 - iowait0, 0)
    busy = max(0.0, 100.0 * (1.0 - idle_d / dt))
    iowait_pct = 100.0 * iowait_d / dt

    # Device util ≈ Δio_ticks / (sample_ms). Cap at 100 per device, then average
    # devices that had any activity or are among known data disks.
    sample_ms = sample_window * 1000.0
    utils: List[float] = []
    for name, (ticks1, _) in ds1.items():
        ticks0 = ds0.get(name, (0, 0))[0]
        delta = max(ticks1 - ticks0, 0)
        util = min(100.0, 100.0 * delta / sample_ms) if sample_ms > 0 else 0.0
        if util > 0.0 or name in {"nvme1n1", "nvme2n1", "sda", "sda1", "nvme0n1"}:
            utils.append(util)
    disk_util_avg = sum(utils) / len(utils) if utils else 0.0

    mem_total, mem_avail = _read_meminfo()
    mem_used_pct = 100.0 * (mem_total - mem_avail) / mem_total if mem_total else 0.0

    disk_used: Dict[str, float] = {}
    for path in disk_paths:
        pct = _disk_used_percent(path)
        if pct is None:
            notes.append(f"disk_unreadable:{path}")
        else:
            disk_used[path] = pct

    return MetricSnapshot(
        ts=time.time(),
        cpu_busy_pct=busy,
        mem_used_pct=mem_used_pct,
        mem_available_bytes=mem_avail,
        mem_total_bytes=mem_total,
        iowait_pct=iowait_pct,
        disk_util_avg_pct=disk_util_avg,
        disk_used_pct=disk_used,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Target discovery (side effects)
# ---------------------------------------------------------------------------


def _username_for_uid(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _read_proc_owner(pid: int) -> Optional[str]:
    try:
        st = os.stat(f"/proc/{pid}")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    return _username_for_uid(st.st_uid)


def _read_proc_comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _read_proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, PermissionError):
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _pid_or_ancestor_matches(pid: int, patterns: Sequence[str]) -> bool:
    """Return true when this process or one of its parents carries a guard tag."""
    wanted = tuple(item for item in patterns if item)
    if not wanted:
        return False
    seen: Set[int] = set()
    current = pid
    for _ in range(32):
        if current <= 1 or current in seen:
            break
        seen.add(current)
        if any(item in _read_proc_cmdline(current) for item in wanted):
            return True
        try:
            stat = Path(f"/proc/{current}/stat").read_text(encoding="utf-8")
            fields = stat[stat.rfind(")") + 2 :].split()
            current = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return False


def _cmdline_looks_like_experiment(cmdline: str, comm: str) -> bool:
    text = f"{comm} {cmdline}"
    if any(ex in text for ex in EXPERIMENT_CMDLINE_EXCLUDES):
        return False
    # Interactive agent shells often embed paths; never treat pure shells as
    # experiment runners unless they clearly wrap hybench/benchbase/timeout of one.
    if comm in {"zsh", "bash", "sh", "fish"} and not any(
        m in text
        for m in (
            "hybench",
            "benchbase",
            "run_mysql_gd_matrix",
            "double_loop",
            "mysql_experiment_runner",
            "mysql_hybench_runner",
            "mysql_benchmark_runner",
            "binary_plan_resource_tuner",
            "run_benchmark_capture",
            "run_fixed_protocol_batch",
        )
    ):
        return False
    return any(marker in text for marker in EXPERIMENT_CMDLINE_MARKERS)


def _is_qemu(comm: str, cmdline: str) -> bool:
    if any(comm.startswith(p) for p in QEMU_COMM_PREFIXES):
        return True
    if "qemu-system-" in cmdline or "/qemu-system-" in cmdline:
        return True
    return False


def discover_process_and_qemu_targets(
    *,
    owner: str = DEFAULT_OWNER,
    monitor_pid: int,
    exclude_cmdline: Sequence[str] = (),
) -> List[KillTarget]:
    """Scan /proc for anonymous-owned experiment processes and QEMU VMs."""
    found: List[KillTarget] = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == monitor_pid:
            continue
        if _pid_or_ancestor_matches(pid, exclude_cmdline):
            continue
        owner_name = _read_proc_owner(pid)
        if owner_name != owner:
            continue
        comm = _read_proc_comm(pid)
        cmdline = _read_proc_cmdline(pid)
        if _is_qemu(comm, cmdline):
            name = comm or f"qemu:{pid}"
            # Prefer -name value when present.
            m = re.search(r"-name\s+(\S+)", cmdline)
            if m:
                name = m.group(1)
            found.append(
                KillTarget(
                    kind="qemu",
                    identity=str(pid),
                    name=name,
                    owner=owner_name,
                    reason_match="qemu",
                )
            )
            continue
        if comm in SAFE_COMM_EXACT and not _cmdline_looks_like_experiment(cmdline, comm):
            continue
        if _cmdline_looks_like_experiment(cmdline, comm):
            short = (cmdline[:160] + "…") if len(cmdline) > 160 else cmdline
            found.append(
                KillTarget(
                    kind="process",
                    identity=str(pid),
                    name=short or comm or str(pid),
                    owner=owner_name,
                    reason_match="experiment_cmdline",
                )
            )
    return found


def discover_docker_targets(
    *,
    owner: str = DEFAULT_OWNER,
    docker_ps_fn: Optional[Callable[[], List[dict]]] = None,
    exclude_names: Sequence[str] = (),
) -> List[KillTarget]:
    """List experiment-looking Docker containers via docker CLI.

    Docker daemon ownership is not per-user; we scope by name/image heuristics
    matching anonymous resource-adaptive experiment containers. Foreign stacks
    (overleaf, redis generic, etc.) are excluded by the name regex.
    """
    rows: List[dict]
    if docker_ps_fn is not None:
        rows = docker_ps_fn()
    else:
        rows = _docker_ps_json()
    found: List[KillTarget] = []
    for row in rows:
        name = str(row.get("Names") or row.get("names") or row.get("name") or "")
        # docker ps --format json may use comma-joined names
        name = name.split(",")[0].lstrip("/")
        if any(pattern and pattern in name for pattern in exclude_names):
            continue
        image = str(row.get("Image") or row.get("image") or "")
        cid = str(row.get("ID") or row.get("Id") or row.get("id") or name)
        blob = f"{name} {image}"
        if not DOCKER_EXPERIMENT_NAME_RE.search(blob):
            continue
        found.append(
            KillTarget(
                kind="docker",
                identity=cid,
                name=name or cid,
                owner=owner,
                reason_match="docker_experiment_name",
            )
        )
    return found


def _docker_ps_json() -> List[dict]:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.warning("docker ps failed: %s", exc)
        return []
    if proc.returncode != 0:
        logging.warning("docker ps rc=%s: %s", proc.returncode, proc.stderr.strip())
        return []
    rows: List[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def discover_all_targets(
    *,
    owner: str = DEFAULT_OWNER,
    monitor_pid: int,
    docker_ps_fn: Optional[Callable[[], List[dict]]] = None,
    exclude_cmdline: Sequence[str] = (),
    exclude_docker_names: Sequence[str] = (),
) -> List[KillTarget]:
    targets = discover_process_and_qemu_targets(
        owner=owner, monitor_pid=monitor_pid, exclude_cmdline=exclude_cmdline
    )
    targets.extend(discover_docker_targets(
        owner=owner, docker_ps_fn=docker_ps_fn, exclude_names=exclude_docker_names
    ))
    return targets


# ---------------------------------------------------------------------------
# Act path (side effects; injectable for tests)
# ---------------------------------------------------------------------------


def default_act(
    target: KillTarget,
    dry_run: bool,
    *,
    stop_timeout_s: float = DEFAULT_STOP_TIMEOUT_S,
    signal_fn: Callable[[int, int], None] = os.kill,
    docker_stop_fn: Optional[Callable[[str, float], Tuple[bool, str]]] = None,
) -> ActResult:
    """Stop/kill one target, or record would_stop in dry-run."""
    if dry_run:
        return ActResult(
            target=target,
            action="would_stop",
            detail=f"dry-run {target.kind} {target.identity} ({target.name})",
            dry_run=True,
        )
    if target.kind == "docker":
        stopper = docker_stop_fn or _docker_stop
        ok, detail = stopper(target.identity, stop_timeout_s)
        return ActResult(
            target=target,
            action="stop" if ok else "error",
            detail=detail,
            dry_run=False,
        )
    # process / qemu: SIGTERM then brief wait then SIGKILL
    try:
        pid = int(target.identity)
    except ValueError:
        return ActResult(
            target=target, action="error", detail="bad pid", dry_run=False
        )
    try:
        signal_fn(pid, signal.SIGTERM)
    except ProcessLookupError:
        return ActResult(
            target=target, action="stop", detail="already gone", dry_run=False
        )
    except PermissionError as exc:
        return ActResult(
            target=target, action="error", detail=f"SIGTERM denied: {exc}", dry_run=False
        )
    deadline = time.time() + max(0.1, stop_timeout_s)
    while time.time() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return ActResult(
                target=target, action="stop", detail="SIGTERM", dry_run=False
            )
        time.sleep(0.1)
    try:
        signal_fn(pid, signal.SIGKILL)
        return ActResult(
            target=target, action="kill", detail="SIGKILL after timeout", dry_run=False
        )
    except ProcessLookupError:
        return ActResult(
            target=target, action="stop", detail="gone before SIGKILL", dry_run=False
        )
    except PermissionError as exc:
        return ActResult(
            target=target, action="error", detail=f"SIGKILL denied: {exc}", dry_run=False
        )


def _docker_stop(identity: str, timeout_s: float) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["docker", "stop", "-t", str(max(1, int(timeout_s))), identity],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s + 10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker stop failed: {exc}"
    if proc.returncode == 0:
        return True, (proc.stdout or identity).strip()
    return False, (proc.stderr or proc.stdout or f"rc={proc.returncode}").strip()


# ---------------------------------------------------------------------------
# Loop / CLI
# ---------------------------------------------------------------------------


def format_snapshot_line(snapshot: MetricSnapshot, reasons: Sequence[str]) -> str:
    disks = " ".join(
        f"{p}={snapshot.disk_used_pct.get(p, float('nan')):.1f}%"
        for p in sorted(snapshot.disk_used_pct)
    )
    reason_s = ",".join(reasons) if reasons else "none"
    return (
        f"cpu={snapshot.cpu_busy_pct:.1f}% "
        f"mem_used={snapshot.mem_used_pct:.1f}% "
        f"mem_avail_GiB={snapshot.mem_available_bytes / 1024**3:.1f} "
        f"iowait={snapshot.iowait_pct:.1f}% "
        f"disk_util_avg={snapshot.disk_util_avg_pct:.1f}% "
        f"disks[{disks}] "
        f"breach={reason_s}"
    )


def run_once(
    *,
    thresholds: Thresholds,
    owner: str,
    monitor_pid: int,
    dry_run: bool,
    force_breach: bool = False,
    sample_fn: Optional[Callable[[], MetricSnapshot]] = None,
    discover_fn: Optional[Callable[[], List[KillTarget]]] = None,
    act_fn: Optional[Callable[[KillTarget, bool], ActResult]] = None,
    stop_timeout_s: float = DEFAULT_STOP_TIMEOUT_S,
    exclude_cmdline: Sequence[str] = (),
    exclude_docker_names: Sequence[str] = (),
) -> int:
    """One sample → decide → (optional) act cycle. Returns 0."""
    sample = sample_fn or (
        lambda: sample_metrics(disk_paths=thresholds.disk_paths)
    )
    discover = discover_fn or (
        lambda: discover_all_targets(
            owner=owner, monitor_pid=monitor_pid,
            exclude_cmdline=exclude_cmdline,
            exclude_docker_names=exclude_docker_names,
        )
    )
    actor = act_fn or (
        lambda t, d: default_act(t, d, stop_timeout_s=stop_timeout_s)
    )

    snapshot = sample()
    if force_breach:
        # Artificially force a breach while keeping real metrics in the log.
        snapshot.notes.append("force_breach")
        # Bump CPU so evaluate_breach trips without mutating thresholds.
        snapshot.cpu_busy_pct = max(snapshot.cpu_busy_pct, thresholds.cpu_busy_pct + 1.0)

    candidates = discover()
    reasons, targets, results = decide_and_act(
        snapshot,
        thresholds,
        candidates,
        owner=owner,
        monitor_pid=monitor_pid,
        dry_run=dry_run,
        act_fn=actor,
    )

    logging.info("snapshot %s", format_snapshot_line(snapshot, reasons))
    logging.info("snapshot_json %s", json.dumps(snapshot.to_log_dict(), sort_keys=True))
    if reasons:
        logging.warning(
            "BREACH reasons=%s dry_run=%s selected=%d",
            reasons,
            dry_run,
            len(targets),
        )
        for t in targets:
            logging.warning(
                "selected kind=%s id=%s name=%s owner=%s match=%s",
                t.kind,
                t.identity,
                t.name,
                t.owner,
                t.reason_match,
            )
        for r in results:
            logging.warning(
                "act action=%s kind=%s id=%s detail=%s",
                r.action,
                r.target.kind,
                r.target.identity,
                r.detail,
            )
    else:
        logging.info(
            "all-clear candidates=%d selected_if_breach=%d dry_run=%s",
            len(candidates),
            len(targets),
            dry_run,
        )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--once",
        action="store_true",
        help="sample once and exit (verification / short run)",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("HOST_GUARD_INTERVAL", DEFAULT_INTERVAL_S)),
        help=f"loop interval seconds (default {DEFAULT_INTERVAL_S})",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="actually stop/kill targets (default is dry-run safe mode)",
    )
    p.add_argument(
        "--force-breach",
        action="store_true",
        help="force breach decision after real sampling (for dry-run drills)",
    )
    p.add_argument("--owner", default=os.environ.get("HOST_GUARD_OWNER", DEFAULT_OWNER))
    p.add_argument(
        "--cpu-busy-pct",
        type=float,
        default=float(os.environ.get("HOST_GUARD_CPU_BUSY_PCT", DEFAULT_CPU_BUSY_PCT)),
    )
    p.add_argument(
        "--mem-used-pct",
        type=float,
        default=float(os.environ.get("HOST_GUARD_MEM_USED_PCT", DEFAULT_MEM_USED_PCT)),
    )
    p.add_argument(
        "--mem-available-floor-gib",
        type=float,
        default=float(
            os.environ.get(
                "HOST_GUARD_MEM_AVAILABLE_FLOOR_GIB",
                DEFAULT_MEM_AVAILABLE_FLOOR_BYTES / 1024**3,
            )
        ),
        help="memory available floor in GiB (default 8)",
    )
    p.add_argument(
        "--iowait-pct",
        type=float,
        default=float(os.environ.get("HOST_GUARD_IOWAIT_PCT", DEFAULT_IOWAIT_PCT)),
    )
    p.add_argument(
        "--disk-util-pct",
        type=float,
        default=float(os.environ.get("HOST_GUARD_DISK_UTIL_PCT", DEFAULT_DISK_UTIL_PCT)),
    )
    p.add_argument(
        "--disk-used-pct",
        type=float,
        default=float(os.environ.get("HOST_GUARD_DISK_USED_PCT", DEFAULT_DISK_USED_PCT)),
        help="breach when disk used%% is STRICTLY greater than this (default 90)",
    )
    p.add_argument(
        "--disk-path",
        action="append",
        dest="disk_paths",
        default=None,
        help="disk mount to watch; repeatable (default /disk1 /disk2 /disk3)",
    )
    p.add_argument(
        "--stop-timeout",
        type=float,
        default=DEFAULT_STOP_TIMEOUT_S,
        help="seconds to wait after SIGTERM / docker stop -t",
    )
    p.add_argument(
        "--exclude-cmdline", action="append", default=[],
        help="do not act on a process tree whose cmdline contains this tag; repeatable",
    )
    p.add_argument(
        "--exclude-docker-name", action="append", default=[],
        help="do not stop a container whose name contains this value; repeatable",
    )
    p.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="optional extra log file path",
    )
    return p


def configure_logging(log_file: Optional[Path]) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def thresholds_from_args(args: argparse.Namespace) -> Thresholds:
    paths = tuple(args.disk_paths) if args.disk_paths else DEFAULT_DISK_PATHS
    return Thresholds(
        cpu_busy_pct=args.cpu_busy_pct,
        mem_used_pct=args.mem_used_pct,
        mem_available_floor_bytes=int(args.mem_available_floor_gib * 1024**3),
        iowait_pct=args.iowait_pct,
        disk_util_pct=args.disk_util_pct,
        disk_used_pct=args.disk_used_pct,
        disk_paths=paths,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.log_file)
    thresholds = thresholds_from_args(args)
    dry_run = not args.live
    monitor_pid = os.getpid()
    logging.info(
        "host_resource_guard start dry_run=%s once=%s interval=%.2fs owner=%s "
        "thresholds=%s force_breach=%s pid=%s",
        dry_run,
        args.once,
        args.interval,
        args.owner,
        asdict(thresholds),
        args.force_breach,
        monitor_pid,
    )
    if dry_run:
        logging.info("SAFE MODE: dry-run (no stop/kill signals). Pass --live to act.")
    else:
        logging.warning("LIVE MODE: will stop/kill anonymous experiment/qemu/docker targets on breach")

    def cycle() -> int:
        return run_once(
            thresholds=thresholds,
            owner=args.owner,
            monitor_pid=monitor_pid,
            dry_run=dry_run,
            force_breach=args.force_breach,
            stop_timeout_s=args.stop_timeout,
            exclude_cmdline=args.exclude_cmdline,
            exclude_docker_names=args.exclude_docker_name,
        )

    if args.once:
        return cycle()

    if args.interval < 0.2:
        logging.error("interval too small: %s", args.interval)
        return 2
    while True:
        try:
            cycle()
        except Exception:  # noqa: BLE001 — never die the loop on sample errors
            logging.exception("cycle failed; continuing")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
