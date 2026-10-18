#!/usr/bin/env python3
"""Create eight fresh, isolated MySQL data planes for the v4 four-scene freeze."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_disk2_two_worker_20260809_v4"
    / "four_scene_resource_successor.json"
)
DEFAULT_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v4.json"
)
IMAGE = "mysql:8.0"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
WORKER_ID = re.compile(r"[a-z0-9_]+")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(argv: list[str], *, timeout: int = 600, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=True,
        timeout=timeout,
    )


def _docker_json(*argv: str) -> dict[str, Any]:
    value = json.loads(_run(["docker", *argv], timeout=30).stdout)
    if isinstance(value, list):
        if len(value) != 1 or not isinstance(value[0], dict):
            raise RuntimeError("docker JSON cardinality differs")
        return value[0]
    if not isinstance(value, dict):
        raise RuntimeError("docker JSON is not an object")
    return value


def _mounted_tree_bytes(path: Path) -> int:
    """Measure a protected datadir through Docker without weakening permissions."""
    output = _run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--cpus", "0.25", "--memory", "268435456",
            "--memory-swap", "268435456", "--pids-limit", "32",
            "-v", f"{path}:/tree:ro", "--entrypoint", "/usr/bin/du",
            IMAGE, "-sb", "/tree",
        ],
        timeout=600,
    ).stdout
    return int(output.split()[0])


def _host_swap_devices() -> list[str]:
    lines = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()
    return [line for line in lines[1:] if line.strip()]


def _validate_authority(freeze_path: Path, preflight_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    freeze_path = freeze_path.resolve(strict=True)
    preflight_path = preflight_path.resolve(strict=True)
    if freeze_path.is_symlink() or preflight_path.is_symlink():
        raise RuntimeError("freeze and preflight must be regular non-symlink files")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_resource_successor_v4"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or freeze.get("formal_launch_authorized") is not False
        or len(freeze.get("workers") or []) != 8
    ):
        raise RuntimeError("v4 four-scene freeze identity differs")
    if (
        preflight.get("schema_version") != "mysql_four_scene_runtime_preflight_v3"
        or preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or preflight.get("launch_performed") is not False
        or preflight.get("freeze_path") != str(freeze_path)
        or preflight.get("freeze_sha256") != _sha(freeze_path)
        or preflight.get("blockers") != []
        or float(preflight.get("observed_seconds") or 0) < 60.0
    ):
        raise RuntimeError("v4 runtime preflight authority differs or is not PASS")
    image = _docker_json("image", "inspect", IMAGE)
    if image.get("Id") != IMAGE_ID:
        raise RuntimeError("local MySQL image identity differs")
    return freeze, preflight


def _defaults(password: str) -> str:
    return (
        "[client]\nuser=root\npassword=" + password
        + "\nprotocol=tcp\nssl-mode=DISABLED\nget-server-public-key=TRUE\n"
    )


def _wait_mysql(container: str, defaults: str, timeout_seconds: int = 240) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = _docker_json("inspect", container).get("State") or {}
        if state.get("Running") is not True:
            raise RuntimeError(f"{container} exited before MySQL readiness")
        try:
            probe = subprocess.run(
                [
                    "docker", "exec", container, "mysql",
                    f"--defaults-file={defaults}", "--connect-timeout=3", "-NBe", "SELECT 1",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=8,
            )
        except subprocess.TimeoutExpired:
            time.sleep(1)
            continue
        if probe.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"{container} MySQL readiness timed out")


def _controller_path(
    pid: int, container_id: str, controller: str, mount: str
) -> Path:
    matches: list[str] = []
    for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines():
        hierarchy, controllers, relative = line.split(":", 2)
        if hierarchy and controller in controllers.split(","):
            matches.append(relative)
    if len(matches) != 1:
        raise RuntimeError(f"{controller} cgroup membership cardinality differs")
    relative = matches[0].lstrip("/")
    if relative != f"docker-limited/{container_id}":
        raise RuntimeError(f"{controller} cgroup path is outside exact docker-limited identity")
    path = Path("/sys/fs/cgroup") / mount / relative
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"{controller} cgroup directory is absent or unsafe")
    return path


def _target_device_rate(path: Path, device: str) -> int | None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"unsafe or absent cgroup file: {path}")
    found: list[int] = []
    for line in path.read_text(encoding="ascii").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == device:
            found.append(int(parts[1]))
    if len(found) > 1:
        raise RuntimeError(f"duplicate target-device rows in {path}")
    return found[0] if found else None


def _enforce_blkio(
    inspect: dict[str, Any], freeze: dict[str, Any], worker: dict[str, Any]
) -> dict[str, Any]:
    container_id = str(inspect.get("Id") or "")
    pid = int((inspect.get("State") or {}).get("Pid") or 0)
    if len(container_id) != 64 or pid <= 1:
        raise RuntimeError(f"{worker['worker_id']} live container identity differs")
    cgroup = _controller_path(pid, container_id, "blkio", "blkio")
    storage_by_worker = freeze.get("storage_by_worker") or {}
    storage = dict(
        storage_by_worker.get(str(worker["worker_id"])) or freeze["storage"]
    )
    device = str(storage["device_major_minor"])
    actual_device = os.stat(str(storage["device"])).st_rdev
    observed_device = f"{os.major(actual_device)}:{os.minor(actual_device)}"
    if observed_device != device:
        raise RuntimeError("frozen block-device major:minor differs")
    paths = {
        "read": cgroup / "blkio.throttle.read_bps_device",
        "write": cgroup / "blkio.throttle.write_bps_device",
    }
    rates = {
        "read": int(worker["io_read_bps"]),
        "write": int(worker["io_write_bps"]),
    }
    for direction, path in paths.items():
        result = subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=f"{device} {rates[direction]}\n",
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{worker['worker_id']} {direction} cgroup write failed: "
                f"{result.stderr.strip()}"
            )
    readback = {
        direction: _target_device_rate(path, device)
        for direction, path in paths.items()
    }
    if readback != rates:
        raise RuntimeError(f"{worker['worker_id']} cgroup blkio readback differs")
    return {
        "cgroup_version": "v1",
        "cgroup_path": str(cgroup),
        "device_major_minor": device,
        "read_bps": readback["read"],
        "write_bps": readback["write"],
        "read_throttle_file": str(paths["read"]),
        "write_throttle_file": str(paths["write"]),
        "docker_hostconfig_blkio_not_authoritative": True,
    }


def _kernel_resource_readback(
    inspect: dict[str, Any], worker: dict[str, Any]
) -> dict[str, Any]:
    container_id = str(inspect["Id"])
    pid = int((inspect.get("State") or {}).get("Pid") or 0)
    cpuset = _controller_path(pid, container_id, "cpuset", "cpuset")
    cpu = _controller_path(pid, container_id, "cpu", "cpu,cpuacct")
    memory = _controller_path(pid, container_id, "memory", "memory")
    pids = _controller_path(pid, container_id, "pids", "pids")
    values: dict[str, Any] = {
        "cpuset_cpus": (cpuset / "cpuset.cpus").read_text().strip(),
        "cpuset_mems": (cpuset / "cpuset.mems").read_text().strip(),
        "cpu_quota_us": int((cpu / "cpu.cfs_quota_us").read_text()),
        "cpu_period_us": int((cpu / "cpu.cfs_period_us").read_text()),
        "memory_limit_bytes": int((memory / "memory.limit_in_bytes").read_text()),
        "pids_max": int((pids / "pids.max").read_text()),
    }
    expected_quota = int(round(float(worker["cpu_quota_cores"]) * values["cpu_period_us"]))
    expected = {
        "cpuset_cpus": str(worker["database_cpuset"]),
        "cpuset_mems": str(worker["database_cpuset_mems"]),
        "cpu_quota_us": expected_quota,
        "memory_limit_bytes": int(worker["memory_bytes"]),
        "pids_max": int(worker["pids_max"]),
    }
    differences = {
        key: {"actual": values[key], "expected": value}
        for key, value in expected.items() if values[key] != value
    }
    if differences:
        raise RuntimeError(
            f"{worker['worker_id']} kernel cgroup resource differs: {differences}"
        )
    return values


def _prepare_worker(
    freeze: dict[str, Any], worker: dict[str, Any], output_root: Path
) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    if WORKER_ID.fullmatch(worker_id) is None:
        raise RuntimeError(f"unsafe worker id: {worker_id!r}")
    container = str(worker["container"])
    bootstrap = container + "_bootstrap"
    runtime_root = Path(freeze["storage"]["runtime_root"]) / worker_id
    datadir = runtime_root / "datadir"
    credentials = runtime_root / "credentials"
    template = Path(freeze["templates"][worker["scene"]]).resolve(strict=True)
    xp_profile = freeze.get("xp_innodb_io_profile") or {}
    is_xp = worker["scene"] in set(xp_profile.get("applies_to_scenes") or [])
    if is_xp and (
        xp_profile.get("container_init") is not True
        or xp_profile.get("innodb_use_native_aio") is not False
        or str(xp_profile.get("innodb_flush_method") or "").lower() != "fsync"
    ):
        raise RuntimeError(f"{worker_id} frozen XP I/O profile differs")
    if not str(template).startswith("/disk2/") or not str(runtime_root).startswith("/disk2/"):
        raise RuntimeError("template/runtime path is outside /disk2")
    if runtime_root.exists():
        raise RuntimeError(f"fresh runtime root already exists: {runtime_root}")
    existing = _run(
        ["docker", "ps", "-aq", "--filter", f"name=^{container}$"], timeout=30
    ).stdout.strip()
    if existing:
        raise RuntimeError(f"container name already exists: {container}")

    credentials.mkdir(parents=True, mode=0o700)
    datadir.mkdir(mode=0o700)
    password = secrets.token_hex(32)
    defaults = credentials / "selected.cnf"
    defaults.write_text(_defaults(password), encoding="utf-8")
    defaults.chmod(0o600)

    started = time.monotonic()
    first_cpu_segment = str(worker["database_cpuset"]).split(",", 1)[0]
    copy = _run(
        [
            "docker", "run", "--rm", "--network", "none", "--cpus", "1",
            "--cpuset-cpus", first_cpu_segment,
            "--cpuset-mems", str(worker["database_cpuset_mems"]),
            "--memory", "4294967296", "--memory-swap", "4294967296",
            "--pids-limit", "128",
            "-v", f"{template}:/template:ro", "-v", f"{datadir}:/copy:rw",
            "--entrypoint", "/bin/sh", IMAGE, "-c",
            "set -eu; cp -a --reflink=never /template/. /copy/; "
            "rm -f /copy/mysql.sock /copy/mysqld.sock /copy/mysqld.pid; "
            "chown -R mysql:mysql /copy; "
            "find /copy -type d -exec chmod 0750 {} +; "
            "find /copy -type f -exec chmod 0640 {} +",
        ],
        timeout=1800,
    )
    copy_seconds = time.monotonic() - started
    source_bytes = _mounted_tree_bytes(template)
    copied_bytes = _mounted_tree_bytes(datadir)
    if abs(copied_bytes - source_bytes) > 4 * 1024 * 1024:
        raise RuntimeError(f"{worker_id} copy byte size differs beyond metadata allowance")

    bootstrap_command = [
            "docker", "run", "-d", "--name", bootstrap, "--network", "none",
            "--cpus", "1", "--cpuset-cpus", first_cpu_segment,
            "--cpuset-mems", str(worker["database_cpuset_mems"]),
            "--memory", "2147483648", "--memory-swap", "2147483648",
            "--pids-limit", "128", "-v", f"{datadir}:/var/lib/mysql:rw",
            IMAGE, "mysqld", "--skip-grant-tables=ON", "--skip-networking=ON",
            "--innodb-buffer-pool-size=536870912",
            "--innodb-flush-method=fsync" if is_xp else "--innodb-flush-method=O_DIRECT",
            "--skip-log-bin", "--max-connections=32",
        ]
    if is_xp:
        bootstrap_command.append("--innodb-use-native-aio=OFF")
    _run(
        bootstrap_command,
        timeout=60,
    )
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        state = _docker_json("inspect", bootstrap).get("State") or {}
        if state.get("Running") is not True:
            raise RuntimeError(f"{bootstrap} exited before password normalization")
        try:
            probe = subprocess.run(
                ["docker", "exec", bootstrap, "mysqladmin", "-uroot", "ping", "--silent"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=8,
            )
        except subprocess.TimeoutExpired:
            time.sleep(1)
            continue
        if probe.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"{bootstrap} readiness timed out")
    sql = (
        "FLUSH PRIVILEGES; "
        f"CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{password}'; "
        f"ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{password}'; "
        f"ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password BY '{password}';"
    )
    _run(["docker", "exec", bootstrap, "mysql", "-uroot", "-NBe", sql], timeout=60)
    _run(["docker", "stop", "--time", "120", bootstrap], timeout=150)
    _run(["docker", "rm", bootstrap], timeout=30)

    owner_label = str(freeze.get("container_owner_label") or "mysql_four_scene_v4")
    docker_command = [
            "docker", "run", "-d",
            *(["--init"] if is_xp else []),
            "--name", container,
            "--label", f"ra.owner={owner_label}",
            "--label", f"ra.scenario={worker['scene']}",
            "--label", f"ra.worker_id={worker_id}",
            "--cpus", str(worker["cpu_quota_cores"]),
            "--cpuset-cpus", str(worker["database_cpuset"]),
            "--cpuset-mems", str(worker["database_cpuset_mems"]),
            "--memory", str(worker["memory_bytes"]),
            "--memory-swap", str(worker["memory_bytes"]),
            "--pids-limit", str(worker["pids_max"]),
            "--device-read-bps", f"{freeze['storage']['device']}:{worker['io_read_bps']}",
            "--device-write-bps", f"{freeze['storage']['device']}:{worker['io_write_bps']}",
            "-p", f"127.0.0.1:{worker['host_port']}:3306",
            "-v", f"{datadir}:/var/lib/mysql:rw",
            "-v", f"{credentials}:/run/ra-credentials:ro",
            IMAGE, "mysqld", "--persisted-globals-load=OFF", "--bind-address=0.0.0.0",
            f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
            "--innodb-flush-method=fsync" if is_xp else "--innodb-flush-method=O_DIRECT",
            "--innodb-flush-log-at-trx-commit=2",
            "--skip-log-bin", "--max-connections=128", "--performance-schema=ON",
        ]
    if is_xp:
        docker_command.append("--innodb-use-native-aio=OFF")
    _run(
        docker_command,
        timeout=60,
    )
    _wait_mysql(container, "/run/ra-credentials/selected.cnf")
    _run(
        [
            "docker", "exec", container, "mysql",
            "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
            "UPDATE mysql.server_cost SET cost_value=NULL; "
            "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
        ],
        timeout=60,
    )
    database = {
        "job_memory": "imdb_job",
        "tpch_sf4_memory": "tpch",
        "hybench_xp_cpu": "hybench_sf10x",
        "hybench_xp_io": "hybench_sf10x",
    }[worker["scene"]]
    query = _run(
        [
            "docker", "exec", container, "mysql",
            "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
            "SELECT @@version,@@global.innodb_buffer_pool_size; "
            f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{database}';",
        ],
        timeout=60,
    ).stdout.splitlines()
    if len(query) != 2:
        raise RuntimeError(f"{worker_id} MySQL/database readback cardinality differs")
    version, buffer_pool = query[0].split("\t")
    table_count = int(query[1])
    if int(buffer_pool) != int(worker["innodb_buffer_pool_bytes"]) or table_count <= 0:
        raise RuntimeError(f"{worker_id} buffer pool or schema readback differs")

    inspect = _docker_json("inspect", container)
    host = inspect.get("HostConfig") or {}
    expected_nano = int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000))
    expected = {
        "CpusetCpus": worker["database_cpuset"],
        "CpusetMems": worker["database_cpuset_mems"],
        "NanoCpus": expected_nano,
        "Memory": int(worker["memory_bytes"]),
        "PidsLimit": int(worker["pids_max"]),
    }
    differences = {
        key: {"actual": host.get(key), "expected": value}
        for key, value in expected.items() if host.get(key) != value
    }
    if differences:
        raise RuntimeError(f"{worker_id} effective Docker resource differs: {differences}")
    docker_memory_swap = int(host.get("MemorySwap") or 0)
    swap_devices = _host_swap_devices()
    if int(worker["swap_bytes"]) != 0:
        raise RuntimeError(f"{worker_id} v4 worker unexpectedly requests nonzero swap")
    if docker_memory_swap == -1:
        if swap_devices:
            raise RuntimeError(
                f"{worker_id} Docker cannot limit swap while host swap devices exist"
            )
    elif docker_memory_swap != int(worker["memory_bytes"]):
        raise RuntimeError(f"{worker_id} Docker MemorySwap readback differs")
    kernel_resources = _kernel_resource_readback(inspect, worker)
    blkio_authority = _enforce_blkio(inspect, freeze, worker)
    mounts = inspect.get("Mounts") or []
    mysql_mounts = [row for row in mounts if row.get("Destination") == "/var/lib/mysql"]
    if len(mysql_mounts) != 1 or mysql_mounts[0].get("Source") != str(datadir):
        raise RuntimeError(f"{worker_id} datadir bind readback differs")

    row = {
        "worker_id": worker_id,
        "scene": worker["scene"],
        "container": container,
        "container_id": inspect["Id"],
        "container_pid": (inspect.get("State") or {}).get("Pid"),
        "host_port": worker["host_port"],
        "runtime_root": str(runtime_root),
        "datadir": str(datadir),
        "credential_defaults": str(defaults),
        "template": str(template),
        "source_bytes": source_bytes,
        "copied_bytes_before_bootstrap": copied_bytes,
        "copy_mode": "cp_a_reflink_never",
        "copy_seconds": copy_seconds,
        "mysql_version": version,
        "database": database,
        "table_count": table_count,
        "effective_resources": {
            **expected,
            "DockerMemorySwap": docker_memory_swap,
            "HostSwapDevices": swap_devices,
            "EffectiveSwapBytes": 0,
        },
        "kernel_cgroup_resources": kernel_resources,
        "blkio_authority": blkio_authority,
        "io_read_bps": worker["io_read_bps"],
        "io_write_bps": worker["io_write_bps"],
        "innodb_buffer_pool_bytes": int(buffer_pool),
        "ready": True,
    }
    worker_output = output_root / worker_id
    worker_output.mkdir(parents=True, exist_ok=True)
    (worker_output / "prepared_worker.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--copy-workers", type=int, default=2)
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("run-id has unsafe characters or length")
    if not 1 <= args.copy_workers <= 2:
        parser.error("copy-workers must be 1 or 2 for the frozen same-SSD policy")
    freeze, preflight = _validate_authority(args.freeze, args.preflight)
    selected_workers = list(freeze["workers"])
    if args.worker_id is not None:
        selected_workers = [
            worker for worker in selected_workers
            if worker["worker_id"] == args.worker_id
        ]
        if len(selected_workers) != 1:
            raise RuntimeError("requested worker-id is not uniquely frozen")
    output_root = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output_root.exists():
        raise RuntimeError(f"fresh preparation output already exists: {output_root}")
    output_root.mkdir(parents=True, mode=0o700)
    lock_path = Path("/tmp/mysql_four_scene_v4_prepare.lock")
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another v4 preparation owns the lock") from error

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.copy_workers, thread_name_prefix="mysql-v4-copy") as pool:
        futures = {
            pool.submit(_prepare_worker, freeze, worker, output_root): worker
            for worker in selected_workers
        }
        for future in as_completed(futures):
            worker = futures[future]
            try:
                row = future.result()
                results.append(row)
                print(json.dumps({
                    "worker_id": row["worker_id"], "container": row["container"],
                    "ready": True, "copy_seconds": row["copy_seconds"],
                }), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker["worker_id"],
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)

    results.sort(key=lambda row: row["worker_id"])
    document = {
        "schema_version": "mysql_four_scene_prepared_data_planes_v4",
        "status": (
            "PREPARED" if not failures and len(results) == len(selected_workers)
            else "PREPARATION_FAILED_CLOSED"
        ),
        "created_at": _now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(args.freeze.resolve()),
        "freeze_sha256": _sha(args.freeze.resolve()),
        "preflight_path": str(args.preflight.resolve()),
        "preflight_sha256": _sha(args.preflight.resolve()),
        "preparation_source_path": str(Path(__file__).resolve()),
        "preparation_source_sha256": _sha(Path(__file__).resolve()),
        "preflight_formal_launch_authorized": preflight["formal_launch_authorized"],
        "selected_worker_ids": [worker["worker_id"] for worker in selected_workers],
        "workers": results,
        "failures": failures,
        "data_planes_started": len(results),
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {
            scene: 0 for scene in sorted({worker["scene"] for worker in selected_workers})
        },
    }
    binding = output_root / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{_sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": document["status"], "prepared": len(results),
        "failed": len(failures), "binding": str(binding),
    }), flush=True)
    return 0 if document["status"] == "PREPARED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
