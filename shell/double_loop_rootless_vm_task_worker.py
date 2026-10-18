#!/usr/bin/env python3
"""Run one formal matrix task in a user-owned rootless QEMU pool slot."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATABASES = {
    "hybench_ap": ("hybench_sf10x", "hybench_sf10x_base"),
    "hybench_tp": ("hybench_sf10x", "hybench_sf10x_base"),
    "hybench_xp": ("hybench_sf10x", "hybench_sf10x_base"),
    "job": ("imdbload", "template_imdbload"),
    "tpch": ("benchbase_tpch", "template_benchbase_tpch"),
    "tpcc": ("benchbase_tpcc", "template_benchbase_tpcc"),
}
DEFAULT_PROVISION_MAX_PARALLEL = 2
DEFAULT_SCHEDULER_CONFIG = ROOT / "config/double_loop_rootless_vm_scheduler_20260718.json"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def refresh_effective_profile(
    spec: Dict[str, Any],
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Refresh a launch profile from the current scheduler configuration.

    A long-lived scheduler keeps its parsed configuration in memory.  Formal
    workers therefore re-read the profile at the execution boundary so a
    corrected workload setting is not shadowed by a stale launch spec.  The
    original and effective values are returned for durable per-attempt audit.
    """
    source_value = spec.get("scheduler_config_path") or os.environ.get(
        "DOUBLE_LOOP_SCHEDULER_CONFIG",
        str(DEFAULT_SCHEDULER_CONFIG),
    )
    source = config_path or Path(str(source_value))
    original = spec.get("profile")
    audit: Dict[str, Any] = {
        "config_path": str(source.resolve()),
        "profile_key": f"{spec.get('target')}:{spec.get('benchmark')}",
        "original_profile": original,
        "refreshed": False,
    }
    try:
        config = json.loads(source.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        audit["refresh_error"] = f"{type(exc).__name__}: {exc}"
        audit["effective_profile"] = original
        return audit
    profiles = config.get("profiles") if isinstance(config, dict) else None
    current = profiles.get(audit["profile_key"]) if isinstance(profiles, dict) else None
    if not isinstance(current, dict):
        audit["refresh_error"] = "current scheduler profile is missing or invalid"
        audit["effective_profile"] = original
        return audit
    required = {"cpu_cores", "memory_mib", "read_mb", "write_mb"}
    missing = sorted(required - set(current))
    if missing:
        audit["refresh_error"] = f"current scheduler profile misses fields: {missing}"
        audit["effective_profile"] = original
        return audit
    effective = dict(current)
    task_profile_overrides = (
        config.get("task_profile_overrides") if isinstance(config, dict) else None
    )
    task_override = (
        task_profile_overrides.get(str(spec.get("task_id")))
        if isinstance(task_profile_overrides, dict)
        else None
    )
    if task_override is not None:
        if not isinstance(task_override, dict):
            audit["refresh_error"] = "task profile override is invalid"
            audit["effective_profile"] = original
            return audit
        effective.update(task_override)
        audit["task_profile_override"] = task_override
    spec["profile"] = effective
    audit["effective_profile"] = effective
    audit["refreshed"] = effective != original
    return audit


def process_active(pid: Any) -> bool:
    try:
        status = Path(f"/proc/{int(pid)}/status").read_text(
            encoding="utf-8", errors="replace"
        )
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError):
        return False
    state_line = next((line for line in status.splitlines() if line.startswith("State:")), "")
    parts = state_line.split()
    return len(parts) >= 2 and parts[1].upper() not in {"Z", "X"}


def run(command: List[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed exit={completed.returncode}: {' '.join(command)}\n{completed.stdout}"
        )
    return completed


def _read_cpuset_allowed(pid: int) -> str:
    try:
        for line in Path(f"/proc/{int(pid)}/status").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line.startswith("Cpus_allowed_list:"):
                return line.split(":", 1)[1].strip()
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError):
        return ""
    return ""


def apply_qemu_cpuset(pid: int, cpuset: str) -> Dict[str, Any]:
    """Pin a rootless QEMU process to its exclusive host bank.

    Prevents multi-VM CPU oversubscribe that stalls guest IO submission and
    produces host-side IO queueing under concurrent matrix load.
    """
    wanted = str(cpuset or "").strip()
    result: Dict[str, Any] = {
        "pid": int(pid),
        "cpuset_cpus": wanted,
        "before": _read_cpuset_allowed(pid),
    }
    if not wanted:
        result["status"] = "skipped_empty"
        return result
    commands = [
        ["taskset", "-cp", wanted, str(int(pid))],
        ["sudo", "-n", "taskset", "-cp", wanted, str(int(pid))],
    ]
    last_output = ""
    for command in commands:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        last_output = completed.stdout.strip()
        after = _read_cpuset_allowed(pid)
        result["after"] = after
        result["via"] = " ".join(command[:2])
        if after == wanted or (
            completed.returncode == 0 and wanted in after and after.count(",") <= wanted.count(",") + 2
        ):
            # Exact match preferred; tolerate equivalent expanded forms by
            # comparing membership when the kernel rewrites the list.
            if after == wanted:
                result["status"] = "ok"
                return result
            # Fall through to membership compare below.
        if after:
            def _expand(value: str) -> set:
                out = set()
                for part in value.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if "-" in part:
                        lo, hi = part.split("-", 1)
                        out.update(range(int(lo), int(hi) + 1))
                    else:
                        out.add(int(part))
                return out

            if _expand(after) == _expand(wanted):
                result["status"] = "ok"
                return result
    result["status"] = "failed"
    result["detail"] = last_output
    raise RuntimeError(
        f"failed to pin qemu pid={pid} to cpuset={wanted!r}: before={result.get('before')!r} "
        f"after={result.get('after')!r} detail={last_output!r}"
    )


def resolve_slot_cpuset(spec: Dict[str, Any], config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Resolve exclusive cpuset for the assigned scheduler slot."""
    slot = spec.get("slot") if isinstance(spec.get("slot"), dict) else {}
    audit: Dict[str, Any] = {
        "slot_id": (slot or {}).get("slot_id") or spec.get("slot_id"),
        "from_spec": str((slot or {}).get("cpuset_cpus") or "").strip(),
        "from_config": "",
        "cpuset_cpus": "",
        "client_cpuset_cpus": str((slot or {}).get("client_cpuset_cpus") or "").strip(),
    }
    if audit["from_spec"]:
        audit["cpuset_cpus"] = audit["from_spec"]
        audit["source"] = "launch_spec.slot"
        return audit
    source = config_path or Path(
        os.environ.get("DOUBLE_LOOP_SCHEDULER_CONFIG", str(DEFAULT_SCHEDULER_CONFIG))
    )
    audit["config_path"] = str(source)
    try:
        config = json.loads(source.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        return audit
    slot_id = str(audit["slot_id"] or "")
    for row in config.get("slots") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("slot_id") or "") != slot_id:
            continue
        audit["from_config"] = str(row.get("cpuset_cpus") or "").strip()
        audit["cpuset_cpus"] = audit["from_config"]
        if not audit["client_cpuset_cpus"]:
            audit["client_cpuset_cpus"] = str(row.get("client_cpuset_cpus") or "").strip()
        audit["source"] = "scheduler_config.slots"
        break
    if not audit["cpuset_cpus"]:
        audit["error"] = f"no cpuset_cpus configured for slot {slot_id!r}"
    return audit


def acquire_provision_slot(task_root: Path, max_parallel: int) -> Tuple[Any, int]:
    """Limit concurrent overlay creation and cold guest boots across workers."""
    if max_parallel < 1:
        raise ValueError("provision_max_parallel must be positive")
    scheduler_root = task_root.parents[2]
    lock_root = scheduler_root / "control" / "provisioning_slots"
    lock_root.mkdir(parents=True, exist_ok=True)
    while True:
        for index in range(max_parallel):
            handle = (lock_root / f"slot_{index + 1}.lock").open("a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            print(
                f"acquired provisioning slot {index + 1}/{max_parallel}",
                flush=True,
            )
            return handle, index + 1
        time.sleep(2.0)


def release_provision_slot(handle: Any, index: Optional[int]) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
    print(f"released provisioning slot {index}", flush=True)


def qmp_execute(
    path: Path,
    command: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(5.0)
    try:
        client.connect(str(path))
        client.recv(65536)
        command_payload: Dict[str, Any] = {"execute": command}
        if arguments:
            command_payload["arguments"] = arguments
        for payload in ({"execute": "qmp_capabilities"}, command_payload):
            client.sendall((json.dumps(payload) + "\r\n").encode("utf-8"))
            response = json.loads(client.recv(65536).decode("utf-8", errors="replace"))
        return response
    finally:
        client.close()


def set_qemu_disk_throttle(
    qmp_path: Path,
    *,
    read_bps: int,
    write_bps: int,
) -> Dict[str, Any]:
    """Set the measurement throttle after a fast cold boot."""
    arguments = {
        "device": "drive0",
        "bps": 0,
        "bps_rd": int(read_bps),
        "bps_wr": int(write_bps),
        "iops": 0,
        "iops_rd": 0,
        "iops_wr": 0,
        "bps_max": 0,
        "bps_rd_max": int(read_bps),
        "bps_wr_max": int(write_bps),
        "bps_max_length": 1,
        "bps_rd_max_length": 1,
        "bps_wr_max_length": 1,
    }
    response = qmp_execute(qmp_path, "block_set_io_throttle", arguments)
    if not isinstance(response, dict) or response.get("error"):
        raise RuntimeError(f"failed to set QEMU measurement throttle: {response}")
    return {
        "status": "ok",
        "read_bps": int(read_bps),
        "write_bps": int(write_bps),
        "response": response.get("return"),
    }


def stop_vm(pid: Any, qmp_path: Path) -> None:
    if not process_active(pid):
        return
    # The QMP path is slot-scoped and therefore reused by the next attempt.
    # A slow previous worker can otherwise connect to the replacement VM's
    # socket and power down the new guest while it is entering validation.
    # The PID is attempt-local and was returned by this worker's own QEMU
    # launch, so signal only that exact process during cleanup.
    _ = qmp_path
    try:
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\0", b" ")
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError):
        return
    if b"qemu-system" not in command:
        raise RuntimeError(f"refusing to stop non-QEMU owned PID: pid={int(pid)}")
    os.kill(int(pid), 15)
    deadline = time.time() + 20.0
    while process_active(pid) and time.time() < deadline:
        time.sleep(0.5)
    if process_active(pid):
        # A QEMU stuck during guest shutdown must not retain a pool slot's
        # forwarded ports after the scheduler marks that slot idle.  The PID
        # comes from this task's private pidfile, so escalation cannot target
        # an unrelated VM.
        os.kill(int(pid), 9)
    deadline = time.time() + 10.0
    while process_active(pid) and time.time() < deadline:
        time.sleep(0.25)
    if process_active(pid):
        raise RuntimeError(f"owned QEMU process did not exit: pid={int(pid)}")


def qmp_boot_diagnostics(
    path: Optional[Path],
    screen_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return compact read-only QMP evidence for a guest that is still booting."""
    if path is None:
        return {}
    diagnostics: Dict[str, Any] = {}
    for command in ("query-status", "query-blockstats"):
        try:
            response = qmp_execute(path, command)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            diagnostics[command] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if not isinstance(response, dict):
            diagnostics[command] = response
            continue
        value = response.get("return")
        if command == "query-blockstats" and isinstance(value, list):
            compact: List[Dict[str, Any]] = []
            for row in value:
                if not isinstance(row, dict):
                    continue
                stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
                compact.append(
                    {
                        "device": row.get("device"),
                        "rd_bytes": stats.get("rd_bytes"),
                        "wr_bytes": stats.get("wr_bytes"),
                        "rd_operations": stats.get("rd_operations"),
                        "wr_operations": stats.get("wr_operations"),
                        "failed_rd_operations": stats.get("failed_rd_operations"),
                        "failed_wr_operations": stats.get("failed_wr_operations"),
                    }
                )
            diagnostics[command] = compact
        else:
            diagnostics[command] = value
    if screen_path is not None:
        try:
            response = qmp_execute(
                path,
                "screendump",
                {"filename": str(screen_path)},
            )
            diagnostics["screendump"] = {
                "path": str(screen_path),
                "response": response.get("return") if isinstance(response, dict) else response,
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            diagnostics["screendump"] = {
                "error": f"{type(exc).__name__}: {exc}",
                "path": str(screen_path),
            }
    return diagnostics


def wait_ready(
    spec: Dict[str, Any],
    vm_uuid: str,
    timeout: float = 900.0,
    qmp_path: Optional[Path] = None,
    qmp_screen_path: Optional[Path] = None,
) -> Dict[str, Any]:
    ssh_port = int(spec["slot"]["ssh_port"])
    pg_port = int(spec["slot"]["postgres_port"])
    deadline = time.time() + timeout
    last_output = ""
    last_qmp: Dict[str, Any] = {}
    next_qmp_sample = 0.0
    while time.time() < deadline:
        if time.time() >= next_qmp_sample:
            last_qmp = qmp_boot_diagnostics(qmp_path, qmp_screen_path)
            print(
                "boot QMP diagnostics: "
                + json.dumps(last_qmp, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            next_qmp_sample = time.time() + 60.0
        try:
            ssh = subprocess.run(
                [
                    "ssh", "-p", str(ssh_port), "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
                    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                    "anonymous@127.0.0.1",
                    "sudo -n cat /sys/class/dmi/id/product_uuid; "
                    "systemctl is-active ssh postgresql17-resource-adaptive",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # A heavily loaded host can accept SSH while the guest command is
            # still starved.  This is an ordinary not-ready sample inside the
            # bounded readiness window, not a terminal provisioning failure.
            last_output = "SSH readiness probe timed out after 15s"
            time.sleep(3.0)
            continue
        last_output = ssh.stdout.strip()
        if ssh.returncode == 0 and vm_uuid.lower() in last_output.lower():
            pg = subprocess.run(
                [
                    "psql",
                    f"host=127.0.0.1 port={pg_port} user=postgres password=CHANGE_ME "
                    "dbname=postgres connect_timeout=5",
                    "-Atqc", "select current_setting('server_version_num')::int >= 170000",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
                check=False,
            )
            if pg.returncode == 0 and pg.stdout.strip() == "t":
                return {"ssh": last_output, "postgres": pg.stdout.strip()}
            last_output = pg.stdout.strip()
        time.sleep(3.0)
    last_qmp = qmp_boot_diagnostics(qmp_path, qmp_screen_path) or last_qmp
    raise RuntimeError(
        "rootless VM did not become ready: "
        f"{last_output}; qmp={json.dumps(last_qmp, ensure_ascii=False, sort_keys=True)}"
    )


def newest_launch(root: Path) -> Path:
    paths = sorted(root.glob("formal_launch_*.json"), key=lambda path: path.stat().st_mtime)
    if not paths:
        raise RuntimeError(f"formal launcher produced no manifest under {root}")
    return paths[-1]


def quiesce_unrelated_database_services(ssh_port: int) -> Dict[str, Any]:
    """Stop non-PostgreSQL database daemons inside this owned experiment VM.

    Some reusable benchmark seeds contain MySQL as well as PostgreSQL.  On a
    deliberately memory-constrained PostgreSQL run, MySQL's boot retries can
    consume the headroom and trigger PostgreSQL OOM kills before the measured
    workload starts.  Masking those unrelated units in the disposable overlay
    removes that source of cross-database noise without changing the PG
    workload or optimizer.
    """
    remote = (
        "set -eu; "
        "for unit in mysql.service mariadb.service; do "
        "if systemctl list-unit-files \"$unit\" --no-legend 2>/dev/null "
        "| grep -q \"^$unit\"; then "
        "sudo -n systemctl disable --now \"$unit\" >/dev/null 2>&1 || true; "
        "sudo -n systemctl mask \"$unit\" >/dev/null 2>&1 || true; "
        "printf '%s enabled=%s active=%s\\n' \"$unit\" "
        "\"$(systemctl is-enabled \"$unit\" 2>/dev/null || true)\" "
        "\"$(systemctl is-active \"$unit\" 2>/dev/null || true)\"; "
        "fi; done"
    )
    completed = run(
        [
            "ssh", "-p", str(int(ssh_port)), "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "anonymous@127.0.0.1", remote,
        ],
        timeout=120,
    )
    return {
        "status": "ok",
        "services": [
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip() and not line.startswith("Warning:")
        ],
    }


def collect_guest_exit_diagnostics(ssh_port: int) -> Dict[str, Any]:
    """Capture read-only guest evidence before an owned VM is torn down."""
    remote = (
        "set +e; "
        "echo '### services'; "
        "systemctl is-active ssh postgresql17-resource-adaptive; "
        "echo '### postgres journal'; "
        "sudo -n journalctl -u postgresql17-resource-adaptive -n 160 --no-pager; "
        "echo '### kernel memory/crash events'; "
        "sudo -n dmesg | grep -Ei 'out of memory|oom|killed process|segfault|panic' | tail -n 100; "
        "echo '### memory'; free -m"
    )
    command = [
        "ssh", "-p", str(int(ssh_port)), "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "anonymous@127.0.0.1", remote,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
            check=False,
        )
        return {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "returncode": completed.returncode,
            "output": completed.stdout,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": f"{type(exc).__name__}: {exc}",
        }


def ensure_statement_features(spec: Dict[str, Any], vm_uuid: str) -> Dict[str, Any]:
    """Enable pg_stat_statements and copy it into the replay template."""
    pg_port = int(spec["slot"]["postgres_port"])
    ssh_port = int(spec["slot"]["ssh_port"])
    database, template = DATABASES[str(spec["benchmark"])]
    dsn = (
        f"host=127.0.0.1 port={pg_port} user=postgres password=CHANGE_ME "
        "dbname=postgres connect_timeout=10"
    )
    preload = run(
        ["psql", dsn, "-Atqc", "SHOW shared_preload_libraries"], timeout=30
    ).stdout.strip()
    restarted = False
    if "pg_stat_statements" not in {part.strip() for part in preload.split(",") if part.strip()}:
        run(
            [
                "psql", dsn, "-v", "ON_ERROR_STOP=1", "-c",
                "ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';",
            ],
            timeout=60,
        )
        run(
            [
                "ssh", "-p", str(ssh_port), "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "anonymous@127.0.0.1",
                "sudo -n systemctl restart postgresql17-resource-adaptive",
            ],
            timeout=180,
        )
        wait_ready(spec, vm_uuid, timeout=180.0)
        restarted = True
    database_dsn = dsn.replace("dbname=postgres", f"dbname={database}")
    extension_present = run(
        [
            "psql", database_dsn, "-Atqc",
            "SELECT count(*) FROM pg_extension WHERE extname='pg_stat_statements'",
        ],
        timeout=30,
    ).stdout.strip() == "1"
    template_recreated = False
    if not extension_present:
        run(
            [
                "psql", database_dsn, "-v", "ON_ERROR_STOP=1", "-c",
                "CREATE EXTENSION pg_stat_statements;",
            ],
            timeout=120,
        )
        for sql in (
            f"ALTER DATABASE {template} WITH IS_TEMPLATE false ALLOW_CONNECTIONS true;",
            f"DROP DATABASE {template};",
            f"CREATE DATABASE {template} WITH TEMPLATE {database} OWNER postgres;",
            f"ALTER DATABASE {template} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;",
        ):
            run(
                ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
                timeout=900,
            )
        template_recreated = True
    calls = run(
        ["psql", database_dsn, "-Atqc", "SELECT count(*) FROM pg_stat_statements"],
        timeout=60,
    ).stdout.strip()
    return {
        "shared_preload_libraries_before": preload,
        "postgres_restarted": restarted,
        "extension_preexisting": extension_present,
        "template_recreated": template_recreated,
        "pg_stat_statements_query_ok": calls.isdigit(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-spec", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.launch_spec.read_text(encoding="utf-8"))
    profile_audit = refresh_effective_profile(spec)
    started = time.time()
    result: Dict[str, Any] = {
        "task_id": spec["task_id"],
        "slot_id": spec["slot"]["slot_id"],
        "status": "failed",
        "started_at": started,
    }
    qemu_pid: Any = None
    provision_lock: Any = None
    provision_slot: Optional[int] = None
    work: Optional[Path] = None
    runtime_vm_root: Optional[Path] = None
    task_root: Optional[Path] = None
    ephemeral_work = False
    qmp_path = Path(
        f"/tmp/ra-rootless-{os.getuid()}-{spec['slot']['slot_id']}-{spec['task_id']}.qmp"
    )
    try:
        task_root = Path(spec["task_root"]).resolve()
        profile_audit["validated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json(task_root / "effective_profile_audit.json", profile_audit)
        vm_root = task_root / "rootless_vm"
        vm_root.mkdir(parents=True, exist_ok=True)
        runtime_root_value = spec.get("runtime_overlay_root")
        if runtime_root_value:
            runtime_root = Path(str(runtime_root_value)).resolve()
            runtime_vm_root = runtime_root / f"{spec['task_id']}-attempt_{int(spec['attempt'])}"
            runtime_vm_root.mkdir(parents=True, exist_ok=False)
            ephemeral_work = True
        else:
            runtime_vm_root = vm_root
        seed = Path(spec["seed_overlay"]).resolve()
        if not seed.is_file():
            raise FileNotFoundError(f"validated benchmark seed is missing: {seed}")
        work = runtime_vm_root / "work-overlay.qcow2"
        if work.exists():
            raise RuntimeError(f"refusing to overwrite existing task overlay: {work}")
        provision_lock, provision_slot = acquire_provision_slot(
            task_root,
            int(spec.get("provision_max_parallel") or DEFAULT_PROVISION_MAX_PARALLEL),
        )
        run(
            ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(seed), str(work)],
            timeout=120,
        )
        vm_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"resource-adaptive/{spec['task_id']}"))
        mac_tail = int(uuid.UUID(vm_uuid).hex[-2:], 16)
        mac = f"52:54:00:ce:18:{mac_tail:02x}"
        vm_name = f"ra-rootless-{spec['slot']['slot_id']}-{spec['task_id']}"
        pidfile = vm_root / "qemu.pid"
        serial = vm_root / "serial.log"
        boot_screen = vm_root / "boot-screen.ppm"
        profile = spec["profile"]
        cache_mode = str(profile.get("disk_cache_mode") or "writeback").strip().lower()
        if cache_mode not in {"writeback", "none"}:
            raise ValueError(f"unsupported rootless QEMU disk cache mode: {cache_mode!r}")
        if cache_mode == "none" and runtime_vm_root is not None:
            # ``cache=none`` uses O_DIRECT and cannot open a tmpfs qcow2
            # overlay.  Refuse to silently fall back to writeback, because this
            # path is used for physically comparable IO measurements.
            if os.stat(str(runtime_vm_root)).st_dev == os.stat("/dev/shm").st_dev:
                raise RuntimeError(
                    "direct IO cache=none requires a disk-backed runtime overlay, not /dev/shm"
                )
        read_bps = max(1, int(float(profile["read_mb"]) * 1024 * 1024))
        write_bps = max(1, int(float(profile["write_mb"]) * 1024 * 1024))
        boot_read_bps = max(
            read_bps,
            int(float(profile.get("boot_read_mb") or 800.0) * 1024 * 1024),
        )
        boot_write_bps = max(
            write_bps,
            int(float(profile.get("boot_write_mb") or 800.0) * 1024 * 1024),
        )
        qemu = [
            "qemu-system-x86_64", "-name", vm_name,
            "-machine", "pc-q35-4.2,accel=kvm", "-cpu", "host",
            "-m", str(int(profile["memory_mib"])),
            "-smp", f"{int(profile['cpu_cores'])},sockets=1,cores={int(profile['cpu_cores'])},threads=1",
            "-no-user-config", "-nodefaults", "-display", "none", "-daemonize",
            "-pidfile", str(pidfile), "-uuid", vm_uuid, "-boot", "strict=on",
            "-device", "pcie-root-port,port=0x8,chassis=1,id=pci.1,bus=pcie.0,multifunction=on,addr=0x1",
            "-device", "pcie-root-port,port=0x9,chassis=2,id=pci.2,bus=pcie.0,addr=0x1.0x1",
            "-device", "pcie-root-port,port=0xa,chassis=3,id=pci.3,bus=pcie.0,addr=0x1.0x2",
            "-drive",
            f"file={work},if=none,id=drive0,format=qcow2,cache={cache_mode},discard=unmap,"
            f"throttling.bps-read={boot_read_bps},throttling.bps-write={boot_write_bps}",
            "-device", "virtio-blk-pci,drive=drive0,bus=pci.3,addr=0x0,bootindex=1",
            "-netdev",
            f"user,id=net0,hostfwd=tcp:127.0.0.1:{int(spec['slot']['ssh_port'])}-:22,"
            f"hostfwd=tcp:127.0.0.1:{int(spec['slot']['postgres_port'])}-:25432",
            "-device", f"virtio-net-pci,netdev=net0,mac={mac},bus=pci.1,addr=0x0",
            "-object", "rng-random,id=objrng,filename=/dev/urandom",
            "-device", "virtio-rng-pci,rng=objrng",
            "-qmp", f"unix:{qmp_path},server,nowait", "-serial", f"file:{serial}",
        ]
        direct_boot = spec.get("direct_kernel_boot")
        if isinstance(direct_boot, dict):
            kernel = Path(str(direct_boot.get("kernel") or "")).resolve()
            initrd = Path(str(direct_boot.get("initrd") or "")).resolve()
            kernel_append = str(direct_boot.get("append") or "").strip()
            if not kernel.is_file():
                raise FileNotFoundError(f"direct boot kernel is missing: {kernel}")
            if not initrd.is_file():
                raise FileNotFoundError(f"direct boot initrd is missing: {initrd}")
            if not kernel_append:
                raise ValueError("direct boot kernel append string is empty")
            qemu.extend(
                [
                    "-kernel", str(kernel),
                    "-initrd", str(initrd),
                    "-append", kernel_append,
                ]
            )
        run(qemu, timeout=120)
        qemu_pid = int(pidfile.read_text(encoding="utf-8").strip())
        cpuset_audit = resolve_slot_cpuset(spec)
        if not cpuset_audit.get("cpuset_cpus"):
            raise RuntimeError(
                "rootless QEMU launch requires an exclusive slot cpuset to avoid "
                f"host CPU oversubscribe/IO pile-up: {cpuset_audit}"
            )
        pin_result = apply_qemu_cpuset(qemu_pid, str(cpuset_audit["cpuset_cpus"]))
        print(
            f"[cpu-pin] slot={cpuset_audit.get('slot_id')} "
            f"pid={qemu_pid} cpuset={cpuset_audit.get('cpuset_cpus')} "
            f"status={pin_result.get('status')} via={pin_result.get('via')}",
            flush=True,
        )
        readiness = wait_ready(
            spec,
            vm_uuid,
            qmp_path=qmp_path,
            # The pool is intentionally headless (no VGA/display device).
            # QMP screendump can retain the global QEMU lock until its failed
            # display capture returns, effectively freezing guest boot.  The
            # serial log plus status/block statistics provide complete boot
            # diagnostics without mutating or stalling the VM.
            qmp_screen_path=None,
        )
        release_provision_slot(provision_lock, provision_slot)
        provision_lock = None
        provision_slot = None
        ssh_port = int(spec["slot"]["ssh_port"])
        unrelated_services = quiesce_unrelated_database_services(ssh_port)
        statement_features = ensure_statement_features(spec, vm_uuid)
        # Prove the exact transport and privileged cache-drop path before a
        # formal runner is created.  A failure here is infrastructure-only and
        # cannot be confused with an optimizer baseline/candidate result.
        run(
            [
                "ssh", "-p", str(ssh_port), "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "anonymous@127.0.0.1",
                "sudo -n sync && sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches'",
            ],
            timeout=180,
        )
        # Keep the disposable guest at the boot bandwidth through extension,
        # template, restart, and initial cache-drop preparation.  Apply the
        # formal bottleneck only after all provisioning IO is complete so a
        # very low measurement write limit cannot turn a startup/shutdown
        # checkpoint into an infrastructure timeout.
        measurement_throttle = set_qemu_disk_throttle(
            qmp_path,
            read_bps=read_bps,
            write_bps=write_bps,
        )
        print(
            "[io-throttle] formal measurement limits applied "
            f"read_bps={read_bps} write_bps={write_bps}",
            flush=True,
        )
        mem = run(
            [
                "ssh", "-p", str(ssh_port), "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "anonymous@127.0.0.1", "awk '/^MemTotal:/ {print $2}' /proc/meminfo",
            ],
            timeout=30,
        )
        memory_capacity_gib = int(mem.stdout.strip().splitlines()[-1]) / (1024.0 * 1024.0)
        manifest = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "all_ready": True,
            "vm_count": 1,
            "rootless_pool": True,
            "vms": [{
                "name": vm_name,
                "exp_id": spec["exp_id"],
                "repeat_id": spec["repeat_id"],
                "benchmark": spec["benchmark"],
                "target": spec["target"],
                "ip": "127.0.0.1",
                "ssh_port": ssh_port,
                "postgres_port": int(spec["slot"]["postgres_port"]),
                "cpu": int(profile["cpu_cores"]),
                "memory_mib": int(profile["memory_mib"]),
                "memory_capacity_gib": memory_capacity_gib,
                "read_mb": float(profile["read_mb"]),
                "write_mb": float(profile["write_mb"]),
                "boot_read_mb": boot_read_bps / (1024.0 * 1024.0),
                "boot_write_mb": boot_write_bps / (1024.0 * 1024.0),
                "measurement_throttle": measurement_throttle,
                "seed_overlay": str(seed),
                "overlay": str(work),
                "mac": mac,
                "uuid": vm_uuid,
                "qemu_pid": qemu_pid,
                "slot_id": spec["slot"]["slot_id"],
                "cpuset_cpus": cpuset_audit.get("cpuset_cpus"),
                "client_cpuset_cpus": cpuset_audit.get("client_cpuset_cpus"),
                "cpu_pin": pin_result,
                "readiness": readiness,
                "unrelated_database_services": unrelated_services,
                "statement_features": statement_features,
            }],
        }
        write_json(task_root / "vm_manifest.json", manifest)
        launch = [
            sys.executable, "-u", str(ROOT / "shell/launch_double_loop_formal_matrix.py"),
            "--root", str(task_root), "--only", str(spec["task_id"]),
            "--attempt-tag", f"rootless_a{int(spec['attempt'])}",
            "--qmp-socket", str(qmp_path),
        ]
        launch.extend(str(item) for item in profile.get("launcher_args") or [])
        run(launch, timeout=300)
        launch_manifest = newest_launch(task_root)
        payload = json.loads(launch_manifest.read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        row = rows[0] if len(rows) == 1 else None
        if not isinstance(row, dict) or row.get("status") not in {"launched", "already_running"}:
            raise RuntimeError(f"formal launcher returned no running row: {row}")
        runner_pid = int(row["pid"])
        runner_exit_audit = row.get("runner_exit_audit")
        result.update({
            "status": "launched",
            "runner_pid": runner_pid,
            "run_dir": row["run_dir"],
            "runtime_dir": row["runtime_dir"],
            "vm_name": vm_name,
            "vm_ip": "127.0.0.1",
            "ssh_port": ssh_port,
            "postgres_port": int(spec["slot"]["postgres_port"]),
            "qemu_pid": qemu_pid,
            "launch_manifest": str(launch_manifest),
            "runner_exit_audit": runner_exit_audit,
        })
        write_json(args.result, result)
        while process_active(runner_pid):
            time.sleep(5.0)
        if task_root is not None:
            diagnostics = collect_guest_exit_diagnostics(ssh_port)
            write_json(task_root / "guest_exit_diagnostics.json", diagnostics)
            result["guest_exit_diagnostics"] = str(
                task_root / "guest_exit_diagnostics.json"
            )
        result["status"] = "finished"
        result["runner_finished_at"] = time.time()
    except Exception as exc:  # noqa: BLE001 - durable worker boundary
        result.update({
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        })
    finally:
        release_provision_slot(provision_lock, provision_slot)
        try:
            stop_vm(qemu_pid, qmp_path)
        except Exception as exc:  # noqa: BLE001 - retain primary result
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        if ephemeral_work and work is not None and runtime_vm_root is not None:
            try:
                work.unlink(missing_ok=True)
                runtime_vm_root.rmdir()
            except OSError as exc:
                result["overlay_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        result["worker_elapsed_s"] = time.time() - started
        write_json(args.result, result)
    return 0 if result.get("status") == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
