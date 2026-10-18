"""Run a guarded full-factorial grid of dynamic MySQL system variables.

The runner only targets an explicitly labelled owned container, snapshots every
variable before the first mutation, restores after every point and again on
exit, and writes the same replay artifacts consumed by the standard exporter.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
from itertools import product
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


ALLOWED_VARIABLES = {
    "innodb_buffer_pool_size", "innodb_old_blocks_pct",
    "innodb_spin_wait_delay", "innodb_sync_spin_loops",
    "innodb_thread_concurrency", "innodb_thread_sleep_delay",
    "innodb_adaptive_max_sleep_delay", "innodb_concurrency_tickets",
    "innodb_io_capacity",
    "innodb_io_capacity_max", "innodb_flush_neighbors",
    "innodb_flush_log_at_trx_commit", "sync_binlog",
    "temptable_max_ram", "max_heap_table_size", "tmp_table_size",
    "range_optimizer_max_mem_size", "max_seeks_for_key",
    "join_buffer_size", "sort_buffer_size", "read_rnd_buffer_size",
}
WORKLOAD_FACTORS = {"workload_clients"}
ALLOWED_FACTORS = ALLOWED_VARIABLES | WORKLOAD_FACTORS


@contextmanager
def _docker_api_lock():
    """Serialize this experiment's Docker API calls on a congested daemon."""
    path = Path("/tmp/resource_adaptive_mysql_grid_docker_api.lock")
    handle = path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _acquire_target_lock(container: str, output: Path):
    safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in container)
    lock_path = Path("/tmp") / f"resource_adaptive_mysql_grid_{safe_name}.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError(f"another full-grid runner already owns container {container}")
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps({"pid": os.getpid(), "output": str(output)}) + "\n")
    handle.flush()
    return handle


def load_config(path: Path | str) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"target", "factors", "workload"}
    if not isinstance(cfg, dict) or required - set(cfg):
        raise ValueError(f"config missing {sorted(required - set(cfg or {}))}")
    target = cfg["target"]
    for key in ("container", "required_label", "host", "port", "pid", "user", "password", "database"):
        if key not in target:
            raise ValueError(f"target missing {key}")
    if not str(target["required_label"]).startswith("resource-adaptive-mysql.owner="):
        raise ValueError("target requires the dedicated ownership label")
    factors = cfg["factors"]
    if not isinstance(factors, dict) or not factors or set(factors) - ALLOWED_FACTORS:
        raise ValueError("factors contain no values or an unreviewed variable")
    for name, levels in factors.items():
        if not isinstance(levels, list) or len(levels) < 2 or len(set(levels)) != len(levels):
            raise ValueError(f"{name} needs at least two distinct levels")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 for value in levels):
            raise ValueError(f"{name} levels must be non-negative numbers")
    baseline = cfg.get("baseline_values", {})
    if set(baseline) - set(factors):
        raise ValueError("baseline_values must be a subset of factors")
    workload = cfg["workload"]
    if not isinstance(workload.get("argv"), list) or not workload["argv"]:
        raise ValueError("workload.argv must be a non-empty argv list")
    return cfg


def build_points(cfg: Mapping[str, Any]) -> list[dict[str, int | float]]:
    names = list(cfg["factors"])
    rows = [dict(zip(names, values)) for values in product(*(cfg["factors"][name] for name in names))]
    if cfg.get("randomize_order", True):
        random.Random(int(cfg.get("seed", 20260718))).shuffle(rows)
    return rows


def _inspect_running_container_locally(target: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read metadata for the exact running PID without contacting dockerd.

    The Docker daemon on the shared host can be busy for minutes because other
    experiments are using it.  A running container's cgroup and root-owned
    metadata are sufficient for the initial ownership check and avoid turning
    unrelated container activity into a global launch bottleneck.
    """
    pid = int(target["pid"])
    try:
        cgroup_lines = (Path("/proc") / str(pid) / "cgroup").read_text().splitlines()
    except OSError:
        return None
    container_id = ""
    for line in cgroup_lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        candidate = parts[2].rstrip("/").rsplit("/", 1)[-1]
        if re.fullmatch(r"[0-9a-f]{64}", candidate):
            container_id = candidate
            break
    if not container_id:
        return None
    root = Path("/var/lib/docker/containers") / container_id
    documents: list[dict[str, Any]] = []
    for name in ("config.v2.json", "hostconfig.json"):
        completed = subprocess.run(
            ["sudo", "-n", "cat", str(root / name)], text=True,
            capture_output=True, timeout=10, check=False,
        )
        if completed.returncode:
            return None
        try:
            documents.append(json.loads(completed.stdout))
        except json.JSONDecodeError:
            return None
    item, host = documents
    item["HostConfig"] = host
    return item


def _verify_owner(cfg: Mapping[str, Any]) -> None:
    target = cfg["target"]
    item = _inspect_running_container_locally(target)
    if item is None:
        with _docker_api_lock():
            completed = subprocess.run(["docker", "inspect", str(target["container"])],
                                       text=True, capture_output=True, timeout=120, check=False)
        if completed.returncode:
            raise RuntimeError("cannot inspect target container")
        item = json.loads(completed.stdout)[0]
    key, value = str(target["required_label"]).split("=", 1)
    labels = item.get("Config", {}).get("Labels", {}) or {}
    bindings = item.get("HostConfig", {}).get("PortBindings", {}).get("3306/tcp", []) or []
    ports = {int(row["HostPort"]) for row in bindings if row.get("HostPort")}
    if (item.get("Name") or "").lstrip("/") != str(target["container"]):
        raise RuntimeError("target container name does not match PID cgroup")
    if labels.get(key) != value or int(target["port"]) not in ports or not item.get("State", {}).get("Running"):
        raise RuntimeError("target ownership, port binding, or running state mismatch")
    if int(item.get("State", {}).get("Pid", 0)) != int(target["pid"]):
        raise RuntimeError("target PID changed; refresh config before running")
    host = item.get("HostConfig", {})
    if "expected_nano_cpus" in target and int(host.get("NanoCpus", 0)) != int(target["expected_nano_cpus"]):
        raise RuntimeError("target CPU quota does not match config")
    if "expected_memory_bytes" in target and int(host.get("Memory", 0)) != int(target["expected_memory_bytes"]):
        raise RuntimeError("target memory limit does not match config")


def _inspect_owned(cfg: Mapping[str, Any]) -> dict[str, Any]:
    target = cfg["target"]
    with _docker_api_lock():
        completed = subprocess.run(["docker", "inspect", str(target["container"])],
                                   text=True, capture_output=True, timeout=120, check=False)
    if completed.returncode:
        raise RuntimeError("cannot inspect target container")
    item = json.loads(completed.stdout)[0]
    key, value = str(target["required_label"]).split("=", 1)
    if (item.get("Config", {}).get("Labels", {}) or {}).get(key) != value:
        raise RuntimeError("target ownership label changed")
    return item


def _mysql(cfg: Mapping[str, Any], sql: str) -> str:
    target = cfg["target"]
    env = dict(os.environ)
    env["MYSQL_PWD"] = str(target["password"])
    completed = subprocess.run(
        ["mysql", "--connect-timeout=5", "--batch", "--raw", "--skip-column-names",
         "-h", str(target["host"]), "-P", str(target["port"]), "-u", str(target["user"]),
         str(target["database"]), "-e", sql],
        text=True, capture_output=True, timeout=120, check=False, env=env,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "mysql command failed")
    return completed.stdout.strip()


def _wait_for_quiescence(cfg: Mapping[str, Any], *, timeout_s: float = 300,
                         stable_checks: int = 2) -> None:
    """Wait until no other connection remains attached to the target DB."""
    deadline = time.monotonic() + timeout_s
    consecutive_zeroes = 0
    while True:
        raw = _mysql(
            cfg,
            "SELECT COUNT(*) FROM information_schema.PROCESSLIST "
            "WHERE DB=DATABASE() AND ID<>CONNECTION_ID()",
        )
        count = int(raw)
        consecutive_zeroes = consecutive_zeroes + 1 if count == 0 else 0
        if consecutive_zeroes >= stable_checks:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"target database did not quiesce within {timeout_s}s; {count} sessions remain"
            )
        time.sleep(1)


def _snapshot(cfg: Mapping[str, Any]) -> dict[str, int | float]:
    names = [name for name in cfg["factors"] if name in ALLOWED_VARIABLES]
    if not names:
        return {}
    quoted = ",".join(repr(name) for name in names)
    output = _mysql(cfg, f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({quoted})")
    result: dict[str, int | float] = {}
    for line in output.splitlines():
        name, raw = line.split("\t", 1)
        value = float(raw) if "." in raw else int(raw)
        result[name] = value
    if set(result) != set(names):
        raise RuntimeError(f"incomplete variable snapshot: {result}")
    return result


def _apply(cfg: Mapping[str, Any], values: Mapping[str, int | float]) -> dict[str, int | float]:
    for name, value in values.items():
        if name in WORKLOAD_FACTORS:
            continue
        if name not in ALLOWED_VARIABLES:
            raise ValueError(name)
        _mysql(cfg, f"SET GLOBAL {name}={value}")
        if name == "innodb_buffer_pool_size":
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                actual_size = int(_mysql(cfg, "SELECT @@GLOBAL.innodb_buffer_pool_size"))
                resize_code = int(_mysql(
                    cfg,
                    "SELECT VARIABLE_VALUE FROM performance_schema.global_status "
                    "WHERE VARIABLE_NAME='Innodb_buffer_pool_resize_status_code'",
                ))
                if actual_size == int(value) and resize_code == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError(
                    f"buffer-pool resize timed out at {actual_size}, expected {int(value)}"
                )
    actual = _snapshot(cfg)
    actual.update({name: value for name, value in values.items() if name in WORKLOAD_FACTORS})
    # Some size variables round to an allocation chunk. The effective values
    # are authoritative and are persisted in the result params.
    return actual


def _restore_owned_blkio_limit(cfg: Mapping[str, Any]) -> None:
    """Reapply an explicitly configured limit after an owned-container restart."""
    target = cfg["target"]
    if "disk_bw_mb" not in target:
        return
    pid = int(target["pid"])
    device = str(target.get("blkio_device", "")).strip()
    if not device or not all(part.isdigit() for part in device.split(":", 1)):
        raise RuntimeError("invalid blkio_device for owned target")
    relative = None
    for line in (Path("/proc") / str(pid) / "cgroup").read_text().splitlines():
        _, controllers, path = line.split(":", 2)
        if "blkio" in controllers.split(","):
            relative = path
            break
    if not relative:
        raise RuntimeError("owned target has no blkio cgroup")
    cgroup = Path("/sys/fs/cgroup/blkio") / relative.lstrip("/")
    bytes_per_sec = round(float(target["disk_bw_mb"]) * 1024 * 1024)
    requested = f"{device} {bytes_per_sec}\n"
    for name in ("blkio.throttle.read_bps_device", "blkio.throttle.write_bps_device"):
        path = cgroup / name
        completed = subprocess.run(
            ["sudo", "-n", "tee", str(path)], input=requested,
            text=True, capture_output=True, timeout=30, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"cannot restore owned blkio limit: {completed.stderr.strip()}")
        actual = path.read_text().splitlines()
        if requested.strip() not in actual:
            raise RuntimeError(f"owned blkio limit verification failed: {path}")


def _owned_blkio_paths(cfg: Mapping[str, Any]) -> dict[str, Path]:
    target = cfg["target"]
    pid = int(target["pid"])
    relative = None
    for line in (Path("/proc") / str(pid) / "cgroup").read_text().splitlines():
        _, controllers, path = line.split(":", 2)
        if "blkio" in controllers.split(","):
            relative = path
            break
    if not relative:
        raise RuntimeError("owned target has no blkio cgroup")
    root = Path("/sys/fs/cgroup/blkio") / relative.lstrip("/")
    return {
        name: root / name
        for name in (
            "blkio.throttle.read_bps_device",
            "blkio.throttle.write_bps_device",
        )
    }


def _device_limit(path: Path, device: str) -> int | None:
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == device:
            return int(parts[1])
    return None


def _write_device_limit(path: Path, device: str, value: int | None) -> None:
    # A zero value removes a cgroup-v1 throttle entry.
    requested_value = 0 if value is None else int(value)
    requested = f"{device} {requested_value}\n"
    completed = subprocess.run(
        ["sudo", "-n", "tee", str(path)], input=requested,
        text=True, capture_output=True, timeout=30, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"cannot set owned blkio limit: {completed.stderr.strip()}")
    actual = _device_limit(path, device)
    if actual != value:
        raise RuntimeError(
            f"owned blkio limit verification failed: {path}; "
            f"expected {value}, got {actual}"
        )


@contextmanager
def owned_blkio_limit(cfg: Mapping[str, Any]):
    """Temporarily enforce and exactly restore an owned target's IO limit."""
    if not cfg.get("enforce_blkio_limit", False):
        yield
        return
    target = cfg["target"]
    device = str(target.get("blkio_device", "")).strip()
    if not re.fullmatch(r"\d+:\d+", device):
        raise RuntimeError("invalid blkio_device for owned target")
    paths = _owned_blkio_paths(cfg)
    previous = {name: _device_limit(path, device) for name, path in paths.items()}
    requested = round(float(target["disk_bw_mb"]) * 1024 * 1024)
    try:
        for path in paths.values():
            _write_device_limit(path, device, requested)
        yield
    finally:
        for name, path in paths.items():
            _write_device_limit(path, device, previous[name])


def _persist_restart_apply(cfg: dict[str, Any], values: Mapping[str, int | float]) -> dict[str, int | float]:
    """Persist one point, restart the owned container, refresh PID, and verify it."""
    _verify_owner(cfg)
    for name, value in values.items():
        if name in WORKLOAD_FACTORS:
            continue
        if name not in ALLOWED_VARIABLES:
            raise ValueError(name)
        _mysql(cfg, f"SET PERSIST {name}={value}")
    target = cfg["target"]
    evict_source: str | None = None
    if cfg.get("evict_owned_data_cache", False):
        item = _inspect_owned(cfg)
        sources = [
            str(mount.get("Source", ""))
            for mount in item.get("Mounts", [])
            if mount.get("Destination") == "/var/lib/mysql"
        ]
        owned_root = "/disk1/resource_adaptive_mysql_grid_20260718/workers/"
        if len(sources) != 1 or not sources[0].startswith(owned_root):
            raise RuntimeError("owned MySQL data mount is outside the disk1 worker root")
        evict_source = sources[0]
    with _docker_api_lock():
        if evict_source is None:
            restarted = subprocess.run(
                ["docker", "restart", "-t", "120", str(target["container"])],
                text=True, capture_output=True, timeout=600, check=False,
            )
        else:
            stopped = subprocess.run(
                ["docker", "stop", "-t", "120", str(target["container"])],
                text=True, capture_output=True, timeout=600, check=False,
            )
            if stopped.returncode:
                raise RuntimeError(stopped.stderr.strip() or "container stop failed")
            evictor = Path(__file__).with_name("tools") / "evict_tree_cache"
            evicted = subprocess.run(
                ["sudo", "-n", str(evictor), evict_source], text=True, capture_output=True,
                timeout=600, check=False,
            )
            if evicted.returncode:
                raise RuntimeError(evicted.stderr.strip() or "owned cache eviction failed")
            restarted = subprocess.run(
                ["docker", "start", str(target["container"])],
                text=True, capture_output=True, timeout=600, check=False,
            )
    if restarted.returncode:
        raise RuntimeError(restarted.stderr.strip() or "container restart failed")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            item = _inspect_owned(cfg)
            pid = int(item.get("State", {}).get("Pid", 0))
            if item.get("State", {}).get("Running") and pid > 0:
                target["pid"] = pid
                _restore_owned_blkio_limit(cfg)
                _mysql(cfg, "SELECT 1")
                _verify_owner(cfg)
                actual = _snapshot(cfg)
                actual.update({name: value for name, value in values.items()
                               if name in WORKLOAD_FACTORS})
                return actual
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        time.sleep(2)
    raise RuntimeError("owned MySQL did not become ready after restart")


def _last_metrics(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESOURCE_METRICS="):
            line = line.split("=", 1)[1]
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and {"cpu", "memory", "io", "qps"} <= set(value):
            return value
    raise RuntimeError("monitor emitted no complete metrics JSON")


def _run_captured_process_group(argv: Sequence[str], *, cwd: Path,
                                timeout_s: float) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        list(argv), cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except BaseException:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _cycle_cold_buffer_pool(cfg: Mapping[str, Any]) -> None:
    """Evict cached InnoDB pages without restarting the owned container."""
    low = int(cfg.get("cold_buffer_pool_bytes", 0))
    if low <= 0:
        return
    original = int(_mysql(cfg, "SELECT @@GLOBAL.innodb_buffer_pool_size"))
    if low >= original:
        raise RuntimeError("cold buffer-pool size must be below the active size")

    def wait_for(expected: int) -> None:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            actual = int(_mysql(cfg, "SELECT @@GLOBAL.innodb_buffer_pool_size"))
            code = int(_mysql(
                cfg,
                "SELECT VARIABLE_VALUE FROM performance_schema.global_status "
                "WHERE VARIABLE_NAME='Innodb_buffer_pool_resize_status_code'",
            ))
            if actual == expected and code == 0:
                return
            time.sleep(1)
        raise RuntimeError(f"buffer-pool resize timed out at {actual}, expected {expected}")

    _mysql(cfg, f"SET GLOBAL innodb_buffer_pool_size={low}")
    wait_for(low)
    _mysql(cfg, f"SET GLOBAL innodb_buffer_pool_size={original}")
    wait_for(original)


def _run_workload(cfg: Mapping[str, Any], output: Path,
                  params: Mapping[str, int | float]) -> tuple[dict[str, Any], float]:
    target, workload = cfg["target"], cfg["workload"]
    _cycle_cold_buffer_pool(cfg)
    format_values = {"output_dir": str(output), **params}
    child = [str(value).format_map(format_values) for value in workload["argv"]]
    argv = [
        sys.executable, str(Path(__file__).with_name("mysql_resource_monitor.py")),
        "--pid", str(target["pid"]), "--interval-sec", str(workload.get("interval_sec", 1)),
        "--warmup-samples", str(workload.get("warmup_samples", 5)), "--", *child,
    ]
    started = time.monotonic()
    completed = _run_captured_process_group(
        argv, cwd=Path(__file__).resolve().parent,
        timeout_s=float(workload.get("timeout_sec", 1200)),
    )
    elapsed = time.monotonic() - started
    output.mkdir(parents=True, exist_ok=True)
    (output / "monitor.stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (output / "monitor.stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    _wait_for_quiescence(
        cfg, timeout_s=float(cfg.get("quiesce_timeout_sec", 300))
    )
    if completed.returncode:
        raise RuntimeError(f"workload failed ({completed.returncode}); see {output}")
    return _last_metrics(completed.stdout), elapsed


def execute(cfg: Mapping[str, Any], output: Path) -> dict[str, Any]:
    # The target PID is deliberately refreshed after each owned-container
    # restart, so keep a private mutable copy of caller-owned configuration.
    cfg = json.loads(json.dumps(cfg))
    # Keep the descriptor alive for the whole function. CPython closes it and
    # releases the advisory lock on both normal return and exception unwind.
    target_lock = _acquire_target_lock(str(cfg["target"]["container"]), output)
    _verify_owner(cfg)
    _wait_for_quiescence(
        cfg, timeout_s=float(cfg.get("quiesce_timeout_sec", 300))
    )
    output.mkdir(parents=True, exist_ok=False)
    original = _snapshot(cfg)
    baseline_requested = dict(original)
    baseline_requested.update(cfg.get("baseline_values", {}))
    (output / "runtime_snapshot.json").write_text(json.dumps(original, indent=2) + "\n")
    points = build_points(cfg)
    rows: list[dict[str, Any]] = []
    seen_effective_points: set[tuple[tuple[str, int | float], ...]] = set()
    started = time.monotonic()
    restart_mode = bool(cfg.get("restart_between_points", False))
    baseline_after_points = bool(cfg.get("baseline_after_points", False))

    def capture_baseline(apply_point: Any) -> None:
        baseline_effective = apply_point(cfg, baseline_requested)
        baseline, _ = _run_workload(
            cfg, output / "workloads" / "baseline", baseline_effective)
        (output / "baseline.json").write_text(json.dumps({
            "params": baseline_effective, "resource": baseline,
            "score": float(baseline["qps"]),
        }, indent=2) + "\n")

    try:
        apply_point = _persist_restart_apply if restart_mode else _apply
        if not baseline_after_points:
            capture_baseline(apply_point)
        for index, requested in enumerate(points):
            try:
                effective = apply_point(cfg, requested)
                signature = tuple(sorted(effective.items()))
                if signature in seen_effective_points:
                    raise RuntimeError(
                        "distinct requested points collapse to duplicate effective "
                        f"values: {effective}"
                    )
                seen_effective_points.add(signature)
                metrics, elapsed = _run_workload(
                    cfg, output / "workloads" / f"point-{index:03d}", effective)
                row = {
                    "candidate_id": f"system_grid_{index}", "candidate_index": index,
                    "status": "ok", "params": effective, "requested_params": requested,
                    "actual_resource": metrics, "actual_score": float(metrics["qps"]),
                    "elapsed_sec": elapsed, "completed_at": datetime.now().isoformat(timespec="seconds"),
                    "source": "mysql_system_variable_full_factorial_real_replay",
                }
            finally:
                if not restart_mode:
                    _apply(cfg, original)
            rows.append(row)
            with (output / "full_replay_results.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        if baseline_after_points:
            capture_baseline(apply_point)
    finally:
        active_failure = sys.exc_info()[0] is not None
        try:
            if restart_mode:
                _persist_restart_apply(cfg, original)
            else:
                _apply(cfg, original)
        except Exception as restore_error:
            (output / "restore_error.json").write_text(json.dumps({
                "error": type(restore_error).__name__, "message": str(restore_error),
                "runtime_state_indeterminate": True,
            }, indent=2) + "\n")
            if not active_failure:
                raise
    summary = {
        "status": "completed", "full_grid_candidate_count": len(points),
        "unique_success_count": len(rows), "attempted_this_run": len(rows),
        "successes_this_run": len(rows), "failures_this_run": 0,
        "elapsed_sec": time.monotonic() - started,
        "scenario": cfg.get("scenario"), "benchmark": cfg.get("benchmark"),
        "endpoint": f"{cfg['target']['host']}:{cfg['target']['port']}",
    }
    (output / "full_replay_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(load_config(args.config), args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
