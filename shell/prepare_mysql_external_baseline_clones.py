#!/usr/bin/env python3
"""Prepare eight isolated MySQL workers for external-baseline campaigns.

The live A/B/C datadirs are never copied.  Each worker receives a full physical
copy of the immutable scene template, the authoritative resource envelope, and
a fresh container/port/CPU binding.  The command is fail-closed and only
creates a previously absent output root.
"""

from __future__ import annotations

import argparse
import configparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Dict, Iterable, Mapping


MYSQL_ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FREEZE = (
    MYSQL_ROOT
    / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
SOURCE_BINDING = (
    MYSQL_ROOT
    / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v26_20260811T143500p0800"
    / "prepared_binding.json"
)
FORMAL_TARGET_BINDING = (
    MYSQL_ROOT
    / "history/mysql_four_scene_20260809"
    / "v301_to_four_scene_two_worker_live_authority_20260826"
    / "prepared_binding.json"
)
DEFAULT_RUNTIME_ROOT = Path(
    "/disk2/resource_adaptive_mysql_external_baselines_20260826/independent_workers_v1"
)
DEFAULT_BINDING = Path(
    "/home/anonymous/program/resource_adaptive/config/formal/"
    "mysql_external_baseline_independent_workers_20260826_v1.json"
)
ORPHANED_QUARANTINE_AUDIT = (
    PROJECT_ROOT / "config/formal/"
    "mysql_external_baseline_orphaned_quarantine_archive_20260826_v1.json"
)
IMAGE = "mysql:8.0"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
EXTERNAL_OWNER = "mysql_external_baseline_disk1_v1"
CONTAINER_PREFIX = "ra_pgssd_mysql_baseline_"
DEVICES_BY_RUNTIME_MOUNT = {
    "/disk1": "/dev/nvme1n1",
    "/disk2": "/dev/nvme2n1",
}


# CPU-set widths, NUMA nodes, and quotas remain identical to the source
# authority.  Only logical CPU IDs change.  Every logical ID is unique across
# the new database and client sets.
CPU_BINDINGS: Dict[str, Dict[str, str]] = {
    "job_w1": {"database_cpuset": "320-327", "client_cpuset": "376-383"},
    "job_w2": {"database_cpuset": "328-335", "client_cpuset": "400-407"},
    "tpch_w1": {"database_cpuset": "388-399,494-497", "client_cpuset": "128-131,180-183"},
    "tpch_w2": {"database_cpuset": "436-451", "client_cpuset": "408-415"},
    "xp_cpu_w1": {"database_cpuset": "336-351", "client_cpuset": "288-295"},
    # Keep the eight-core client allocation on NUMA node 0 while avoiding both
    # the formal MySQL target CPUs and the independent GPTuner lane on 296-303.
    "xp_cpu_w2": {"database_cpuset": "352-359,368-375", "client_cpuset": "112-119"},
    "xp_io_w1": {"database_cpuset": "416-423,452-455", "client_cpuset": "304-311"},
    "xp_io_w2": {"database_cpuset": "500-511", "client_cpuset": "312-319"},
}
PORTS = {
    "job_w1": 65411,
    "job_w2": 65412,
    "tpch_w1": 65421,
    "tpch_w2": 65422,
    "xp_cpu_w1": 65431,
    "xp_cpu_w2": 65432,
    "xp_io_w1": 65441,
    "xp_io_w2": 65442,
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def option_password(path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8") as handle:
        parser.read_file(handle)
    password = parser.get("client", "password", fallback="")
    if not password:
        raise RuntimeError(f"private option file lacks password: {path}")
    return password


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def write_private_init(credentials: Path, scene: str) -> Path:
    root_password = option_password(credentials / "selected.cnf")
    benchmark_password = option_password(credentials / "benchmark.cnf")
    database = {
        "job_memory": "imdb_job",
        "tpch_sf4_memory": "tpch",
        "hybench_xp_cpu": "hybench_sf10x",
        "hybench_xp_io": "hybench_sf10x",
    }[scene]
    init_path = credentials / "reset-init.sql"
    temporary = credentials / f".reset-init.sql.{os.getpid()}.tmp"
    temporary.write_text(
        "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY {sql_string(root_password)};\n"
        "ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY "
        f"{sql_string(root_password)};\n"
        "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;\n"
        "CREATE USER IF NOT EXISTS 'ra_benchmark'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY {sql_string(benchmark_password)};\n"
        "ALTER USER 'ra_benchmark'@'%' IDENTIFIED WITH caching_sha2_password BY "
        f"{sql_string(benchmark_password)};\n"
        f"GRANT ALL PRIVILEGES ON `{database}`.* TO 'ra_benchmark'@'%';\n"
        "FLUSH PRIVILEGES;\n",
        encoding="utf-8",
    )
    os.replace(temporary, init_path)
    return init_path


def run(argv: Iterable[str], *, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), check=True, text=True, capture_output=True, timeout=timeout
    )


def sudo(argv: Iterable[str], *, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    return run(["sudo", "-n", *argv], timeout=timeout)


def cpu_set(specification: str) -> set[int]:
    values: set[int] = set()
    for part in specification.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lower, upper = (int(value) for value in part.split("-", 1))
            if upper < lower:
                raise RuntimeError(f"invalid CPU range: {part}")
            values.update(range(lower, upper + 1))
        else:
            values.add(int(part))
    return values


def inspect_container(name: str) -> Dict[str, Any]:
    completed = run(["docker", "inspect", name], timeout=60)
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError(f"Docker inspect shape differs: {name}")
    return rows[0]


def enforce_blkio(
    inspected: Mapping[str, Any], freeze: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    container_id = str(inspected.get("Id") or "")
    pid = int((inspected.get("State") or {}).get("Pid") or 0)
    if len(container_id) != 64 or pid <= 1:
        raise RuntimeError(f"{worker['worker_id']}: live container identity differs")
    cgroup = Path("/sys/fs/cgroup/blkio/docker-limited") / container_id
    tasks = cgroup / "tasks"
    if cgroup.is_symlink() or not cgroup.is_dir() or not tasks.is_file():
        raise RuntimeError(f"{worker['worker_id']}: blkio cgroup differs")
    device = str(freeze["storage"]["device_major_minor"])
    paths = {
        "read": cgroup / "blkio.throttle.read_bps_device",
        "write": cgroup / "blkio.throttle.write_bps_device",
    }
    rates = {
        "read": int(worker["io_read_bps"]),
        "write": int(worker["io_write_bps"]),
    }
    for direction, path in paths.items():
        completed = subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=f"{device} {rates[direction]}\n",
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"{worker['worker_id']}: {direction} blkio write failed"
            )
    readback: Dict[str, int | None] = {}
    for direction, path in paths.items():
        matches = [
            int(parts[1])
            for line in path.read_text(encoding="ascii").splitlines()
            if len(parts := line.split()) == 2 and parts[0] == device
        ]
        readback[direction] = matches[0] if len(matches) == 1 else None
    if readback != rates:
        raise RuntimeError(f"{worker['worker_id']}: blkio readback differs")
    return {
        "cgroup_version": "v1",
        "cgroup_path": str(cgroup),
        "device_major_minor": device,
        "read_bps": rates["read"],
        "write_bps": rates["write"],
        "read_throttle_file": str(paths["read"]),
        "write_throttle_file": str(paths["write"]),
        "docker_hostconfig_blkio_not_authoritative": True,
    }


def existing_running_cpuset() -> Dict[int, list[str]]:
    ids = run(["docker", "ps", "-q"], timeout=60).stdout.split()
    owners: Dict[int, list[str]] = {}
    for container_id in ids:
        inspected = inspect_container(container_id)
        specification = str((inspected.get("HostConfig") or {}).get("CpusetCpus") or "")
        if not specification:
            continue
        name = str(inspected.get("Name") or "").lstrip("/")
        for cpu in cpu_set(specification):
            owners.setdefault(cpu, []).append(name)
    return owners


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def wait_mysql(worker: Mapping[str, Any], timeout_seconds: float = 300) -> None:
    deadline = time.monotonic() + timeout_seconds
    defaults = str(worker["credential_defaults"])
    while time.monotonic() < deadline:
        completed = subprocess.run(
            [
                "mysql",
                f"--defaults-file={defaults}",
                "--host=127.0.0.1",
                f"--port={worker['host_port']}",
                "--connect-timeout=5",
                "-NBe",
                "SELECT 1",
            ],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if completed.returncode == 0 and completed.stdout.strip() == "1":
            return
        time.sleep(2)
    raise RuntimeError(f"{worker['worker_id']}: MySQL did not become ready")


def mysql(worker: Mapping[str, Any], sql: str) -> str:
    completed = run(
        [
            "mysql",
            f"--defaults-file={worker['credential_defaults']}",
            "--host=127.0.0.1",
            f"--port={worker['host_port']}",
            "--connect-timeout=10",
            "-NBe",
            sql,
        ],
        timeout=90,
    )
    return completed.stdout.strip()


def copy_worker(
    source: Mapping[str, Any], freeze: Mapping[str, Any], runtime_root: Path
) -> Dict[str, Any]:
    worker_id = str(source["worker_id"])
    scene = str(source["scene"])
    worker_root = runtime_root / worker_id
    datadir = worker_root / "datadir"
    credentials = worker_root / "credentials"
    template = Path(str(freeze["templates"][scene])).resolve(strict=True)
    source_credentials = Path(str(source["credential_defaults"])).parent.resolve(strict=True)
    if (
        not str(template).startswith("/disk2/")
        or not str(worker_root).startswith(("/disk1/", "/disk2/"))
    ):
        raise RuntimeError(f"{worker_id}: path scope differs")
    sudo(["mkdir", "-p", str(datadir), str(credentials)], timeout=60)
    started = time.monotonic()
    sudo(
        ["cp", "-a", "--reflink=never", f"{template}/.", str(datadir)],
        timeout=1800,
    )
    sudo(
        ["cp", "-a", "--reflink=never", f"{source_credentials}/.", str(credentials)],
        timeout=120,
    )
    sudo(
        ["rm", "-f", str(datadir / "mysql.sock"), str(datadir / "mysqld.sock"), str(datadir / "mysqld.pid")],
        timeout=30,
    )
    sudo(["chown", "-R", "999:999", str(datadir)], timeout=600)
    sudo(
        ["find", str(datadir), "-type", "d", "-exec", "chmod", "0750", "{}", "+"],
        timeout=600,
    )
    sudo(
        ["find", str(datadir), "-type", "f", "-exec", "chmod", "0640", "{}", "+"],
        timeout=600,
    )
    # Keep client-readable option files private to the experiment owner.
    sudo(["chown", f"{os.getuid()}:{os.getgid()}", str(worker_root)], timeout=30)
    sudo(["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(credentials)], timeout=60)
    sudo(["chmod", "0700", str(worker_root), str(credentials)], timeout=30)
    for credential in credentials.glob("*.cnf"):
        sudo(["chmod", "0600", str(credential)], timeout=30)
    reset_init = write_private_init(credentials, scene)
    sudo(["chown", "999:999", str(reset_init)], timeout=30)
    sudo(["chmod", "0400", str(reset_init)], timeout=30)
    return {
        "worker_id": worker_id,
        "template": str(template),
        "template_sha_scope": "full_physical_copy_from_immutable_template",
        "datadir": str(datadir),
        "credentials": str(credentials),
        "copy_seconds": time.monotonic() - started,
    }


def adopt_existing_copy(
    source: Mapping[str, Any], freeze: Mapping[str, Any], runtime_root: Path
) -> Dict[str, Any]:
    """Validate and normalize copies left by a pre-container failed attempt."""
    worker_id = str(source["worker_id"])
    scene = str(source["scene"])
    worker_root = runtime_root / worker_id
    datadir = worker_root / "datadir"
    credentials = worker_root / "credentials"
    template = Path(str(freeze["templates"][scene])).resolve(strict=True)
    required = (datadir / "ibdata1", datadir / "mysql.ibd", credentials / "selected.cnf", credentials / "benchmark.cnf")
    directory_checks = all(
        subprocess.run(
            ["sudo", "-n", "test", "-d", str(path)],
            check=False,
            timeout=30,
        ).returncode == 0
        for path in (datadir, credentials)
    )
    file_checks = all(
        subprocess.run(
            ["sudo", "-n", "test", "-f", str(path)],
            check=False,
            timeout=30,
        ).returncode == 0
        for path in required
    )
    if not directory_checks or not file_checks:
        raise RuntimeError(f"{worker_id}: resumable physical copy is incomplete")
    sudo(["chown", "-R", "999:999", str(datadir)], timeout=600)
    sudo(
        ["find", str(datadir), "-type", "d", "-exec", "chmod", "0750", "{}", "+"],
        timeout=600,
    )
    sudo(
        ["find", str(datadir), "-type", "f", "-exec", "chmod", "0640", "{}", "+"],
        timeout=600,
    )
    sudo(["chown", f"{os.getuid()}:{os.getgid()}", str(worker_root)], timeout=30)
    sudo(["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(credentials)], timeout=60)
    sudo(["chmod", "0700", str(worker_root), str(credentials)], timeout=30)
    for name in ("selected.cnf", "benchmark.cnf"):
        sudo(["chmod", "0600", str(credentials / name)], timeout=30)
    reset_init = write_private_init(credentials, scene)
    sudo(["chown", "999:999", str(reset_init)], timeout=30)
    sudo(["chmod", "0400", str(reset_init)], timeout=30)
    return {
        "worker_id": worker_id,
        "template": str(template),
        "template_sha_scope": "full_physical_copy_from_immutable_template",
        "datadir": str(datadir),
        "credentials": str(credentials),
        "copy_seconds": 0.0,
        "copy_resumed_after_precontainer_permission_failure": True,
    }


def start_worker(
    worker: Dict[str, Any],
    freeze: Mapping[str, Any],
    *,
    device: str | None = None,
) -> Dict[str, Any]:
    name = str(worker["container"])
    datadir = Path(str(worker["datadir"]))
    credentials = Path(str(worker["credential_defaults"])).parent
    io_device = device or str(freeze["storage"]["device"])
    argv = [
        "docker", "run", "-d", "--init", "--name", name,
        # Use a dedicated owner so unrelated MySQL campaign cleanup cannot
        # select these disk1 external-baseline workers by production labels.
        "--label", f"ra.owner={EXTERNAL_OWNER}",
        "--label", "ra.external_baseline_plane=disk1_v2",
        "--label", f"ra.scenario={worker['scene']}",
        "--label", f"ra.worker_id={worker['worker_id']}",
        "--label", "ra.data_plane_origin=immutable_template_clone",
        "--cpus", str(worker["cpu_quota_cores"]),
        "--cpuset-cpus", str(worker["database_cpuset"]),
        "--cpuset-mems", str(worker["database_cpuset_mems"]),
        "--memory", str(worker["memory_bytes"]),
        "--memory-swap", str(worker["memory_bytes"]),
        "--memory-swappiness", "0",
        "--pids-limit", str(worker["pids_max"]),
        "--device-read-bps", f"{io_device}:{worker['io_read_bps']}",
        "--device-write-bps", f"{io_device}:{worker['io_write_bps']}",
        "-p", f"127.0.0.1:{worker['host_port']}:3306",
        "-v", f"{datadir}:/var/lib/mysql:rw",
        "-v", f"{credentials}:/run/ra-credentials:ro",
    ]
    xp = str(worker["scene"]).startswith("hybench_xp_")
    reset_init = credentials / "reset-init.sql"
    if not reset_init.is_file():
        raise RuntimeError(f"{worker['worker_id']}: private reset init is missing")
    argv.extend(["-v", f"{reset_init}:/run/ra-init/reset.sql:ro"])
    argv.extend([
        IMAGE,
        "mysqld",
        "--init-file=/run/ra-init/reset.sql",
        "--persisted-globals-load=OFF",
        "--bind-address=0.0.0.0",
        f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
        *( ["--innodb-use-native-aio=OFF", "--innodb-flush-method=fsync"]
           if xp else ["--innodb-flush-method=O_DIRECT"] ),
        "--innodb-flush-log-at-trx-commit=2",
        "--skip-log-bin",
        "--max-connections=128",
        "--performance-schema=ON",
    ])
    run(argv, timeout=300)
    wait_mysql(worker)
    mysql(
        worker,
        "UPDATE mysql.server_cost SET cost_value=NULL; "
        "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
    )
    inspected = inspect_container(name)
    host = inspected.get("HostConfig") or {}
    expected = {
        "CpusetCpus": worker["database_cpuset"],
        "CpusetMems": worker["database_cpuset_mems"],
        "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(worker["memory_bytes"]),
        "MemorySwappiness": 0,
        "PidsLimit": int(worker["pids_max"]),
    }
    differences = {
        key: {"expected": value, "actual": host.get(key)}
        for key, value in expected.items() if host.get(key) != value
    }
    if differences:
        raise RuntimeError(f"{worker['worker_id']}: Docker envelope differs: {differences}")
    schema = {
        "job_memory": ("imdb_job", 1),
        "tpch_sf4_memory": ("tpch", 8),
        "hybench_xp_cpu": ("hybench_sf10x", 8),
        "hybench_xp_io": ("hybench_sf10x", 8),
    }
    database, minimum_tables = schema[str(worker["scene"])]
    table_count = int(mysql(
        worker,
        "SELECT COUNT(*) FROM information_schema.tables "
        f"WHERE table_schema='{database}'",
    ))
    if table_count < minimum_tables:
        raise RuntimeError(f"{worker['worker_id']}: schema coverage differs")
    worker.update({
        "container_id": inspected["Id"],
        "container_pid": (inspected.get("State") or {}).get("Pid"),
        "mysql_version": mysql(worker, "SELECT @@version"),
        "schema_table_count": table_count,
        "default_cost_readback": mysql(
            worker,
            "SELECT COUNT(*) FROM mysql.server_cost WHERE cost_value IS NULL",
        ),
        "blkio_authority": enforce_blkio(inspected, freeze, worker),
    })
    return worker


def refresh_runtime_authority(binding_path: Path) -> None:
    binding = load(binding_path)
    freeze = binding["freeze"]
    workers = [dict(worker) for worker in binding["workers"]]
    for worker in workers:
        inspected = inspect_container(str(worker["container"]))
        if (inspected.get("State") or {}).get("Running") is not True:
            raise RuntimeError(f"{worker['worker_id']}: container is not running")
        worker["container_id"] = inspected["Id"]
        worker["container_pid"] = (inspected.get("State") or {}).get("Pid")
        worker["blkio_authority"] = enforce_blkio(inspected, freeze, worker)
    frozen_by_id = {
        str(worker["worker_id"]): dict(worker)
        for worker in freeze["workers"]
    }
    for worker in workers:
        frozen_by_id[str(worker["worker_id"])]["blkio_authority"] = dict(
            worker["blkio_authority"]
        )
    freeze["workers"] = [
        frozen_by_id[str(worker["worker_id"])] for worker in workers
    ]
    binding["workers"] = workers
    binding["freeze"] = freeze
    binding["runtime_authority_refreshed_at"] = now()
    binding["blkio_authority_current_container_ids"] = True
    atomic(binding_path, binding)


def start_existing_and_refresh_runtime_authority(binding_path: Path) -> None:
    """Start only the eight sealed external workers and refresh live cgroups."""
    binding = load(binding_path)
    if (
        binding.get("schema_version")
        != "mysql_external_baseline_independent_workers_v1"
        or binding.get("status") != "PASS"
        or binding.get("immutable_templates_only") is not True
        or binding.get("live_datadirs_copied") is not False
    ):
        raise RuntimeError("independent external worker binding differs")
    workers = [dict(worker) for worker in binding.get("workers") or []]
    actual = {str(worker.get("container")) for worker in workers}
    expected = actual
    names_match_workers = all(
        str(worker.get("container") or "").startswith(
            f"{CONTAINER_PREFIX}{worker['worker_id']}"
        )
        and str(worker.get("container") or "").endswith("_20260826")
        for worker in workers
    )
    worker_ids_match = {str(worker.get("worker_id")) for worker in workers} == set(
        CPU_BINDINGS
    )
    if (
        len(expected) != 8
        or len(workers) != 8
        or not names_match_workers
        or not worker_ids_match
    ):
        raise RuntimeError("external worker container allowlist differs")
    def start_and_wait(worker: Mapping[str, Any]) -> str:
        inspected = inspect_container(str(worker["container"]))
        if (inspected.get("State") or {}).get("Running") is not True:
            run(["docker", "start", str(worker["container"])], timeout=300)
        wait_mysql(worker, timeout_seconds=300)
        return str(worker["worker_id"])

    with ThreadPoolExecutor(
        max_workers=8, thread_name_prefix="mysql-external-start"
    ) as pool:
        futures = [pool.submit(start_and_wait, worker) for worker in workers]
        for future in as_completed(futures):
            future.result()
    refresh_runtime_authority(binding_path)


def recreate_existing_and_refresh_runtime_authority(binding_path: Path) -> None:
    """Recreate only container objects while preserving all eight datadirs."""
    binding = load(binding_path)
    if (
        binding.get("schema_version")
        != "mysql_external_baseline_independent_workers_v1"
        or binding.get("status") != "PASS"
        or binding.get("immutable_templates_only") is not True
        or binding.get("live_datadirs_copied") is not False
    ):
        raise RuntimeError("independent external worker binding differs")
    freeze = dict(binding["freeze"])
    workers = [dict(worker) for worker in binding.get("workers") or []]
    actual = {str(worker.get("container")) for worker in workers}
    expected = actual
    names_match_workers = all(
        str(worker.get("container") or "").startswith(
            f"{CONTAINER_PREFIX}{worker['worker_id']}"
        )
        and str(worker.get("container") or "").endswith("_20260826")
        for worker in workers
    )
    worker_ids_match = {str(worker.get("worker_id")) for worker in workers} == set(
        CPU_BINDINGS
    )
    if (
        len(expected) != 8
        or len(workers) != 8
        or not names_match_workers
        or not worker_ids_match
    ):
        raise RuntimeError("external worker container allowlist differs")
    # A prior fail-closed campaign may have moved a worker root while leaving
    # its sealed option files in another worker of the same scene (or in the
    # canonical runtime-root copy). Restore only those private option files,
    # then validate every credential before deleting any container.
    runtime_root = Path(str(binding["runtime_root"]))
    credential_dirs_by_scene: Dict[str, list[Path]] = {}
    for worker in workers:
        scene = str(worker["scene"])
        credential_dirs_by_scene.setdefault(scene, []).append(
            Path(str(worker["credential_defaults"])).parent
        )
        canonical = runtime_root / str(worker["worker_id"]) / "credentials"
        if canonical not in credential_dirs_by_scene[scene]:
            credential_dirs_by_scene[scene].append(canonical)
    for worker in workers:
        scene = str(worker["scene"])
        credentials = Path(str(worker["credential_defaults"])).parent
        credentials.mkdir(parents=True, exist_ok=True)
        for name in ("selected.cnf", "benchmark.cnf"):
            target = credentials / name
            if target.is_file():
                continue
            donor = next(
                (
                    directory / name
                    for directory in credential_dirs_by_scene[scene]
                    if (directory / name).is_file()
                ),
                None,
            )
            if donor is None:
                raise RuntimeError(
                    f"{worker['worker_id']}: no sealed same-scene donor for {name}"
                )
            temporary = credentials / f".{name}.{os.getpid()}.tmp"
            shutil.copy2(donor, temporary)
            os.replace(temporary, target)
            sudo(["chmod", "0600", str(target)], timeout=30)

    # Older sealed bindings may also predate reset-init.sql. Rebuild this
    # derived credential before container deletion.
    for worker in workers:
        credentials = Path(str(worker["credential_defaults"])).parent
        reset_init = write_private_init(credentials, str(worker["scene"]))
        sudo(["chown", "999:999", str(reset_init)], timeout=30)
        sudo(["chmod", "0400", str(reset_init)], timeout=30)
    # Remove only the eight sealed container objects. Their bind-mounted
    # datadirs and credentials are intentionally outside Docker storage.
    existing_names = set(
        run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            timeout=60,
        ).stdout.splitlines()
    )
    for name in sorted(expected & existing_names):
        run(["docker", "rm", "-f", name], timeout=300)
    recreated: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(
        max_workers=8, thread_name_prefix="mysql-external-recreate"
    ) as pool:
        futures = {
            pool.submit(start_worker, worker, freeze): str(worker["worker_id"])
            for worker in workers
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            recreated[worker_id] = future.result()
    workers = [recreated[str(worker["worker_id"])] for worker in workers]
    frozen_by_id = {
        str(worker["worker_id"]): dict(worker)
        for worker in freeze.get("workers") or []
    }
    for worker in workers:
        worker_id = str(worker["worker_id"])
        frozen_by_id[worker_id].update({
            key: value for key, value in worker.items()
            if key not in {"mysql_version", "schema_table_count", "default_cost_readback"}
        })
    freeze["workers"] = [
        frozen_by_id[str(worker["worker_id"])] for worker in workers
    ]
    binding["workers"] = workers
    binding["freeze"] = freeze
    binding["runtime_authority_refreshed_at"] = now()
    binding["container_objects_recreated_at"] = now()
    binding["datadirs_reused_without_copy"] = True
    binding["blkio_authority_current_container_ids"] = True
    atomic(binding_path, binding)


def archive_orphaned_gptuner_baseline_quarantine(binding_path: Path) -> None:
    """Recoverably archive the pre-workload r3 quarantine collision."""
    binding = load(binding_path)
    if (
        binding.get("schema_version")
        != "mysql_external_baseline_independent_workers_v1"
        or binding.get("status") != "PASS"
    ):
        raise RuntimeError("independent external worker binding differs")
    runtime_root = Path(str(binding["runtime_root"])).resolve(strict=True)
    quarantine = (
        runtime_root / "quarantine/gptuner/gptuner_baseline"
    )
    archive = quarantine.with_name(
        "gptuner_baseline.failed_preworkload_r3_r5_20260826"
    )
    failed_states = [
        Path(
            "/disk2/resource_adaptive_mysql_external_baselines_20260826/"
            "20260826_gptuner_aqetuner_four_r3/gptuner/state.json"
        ),
        Path(
            "/disk2/resource_adaptive_mysql_external_baselines_20260826/"
            "20260826_gptuner_aqetuner_four_r5/gptuner/state.json"
        ),
    ]
    state_receipts: list[Dict[str, Any]] = []
    for state_path in failed_states:
        state = load(state_path)
        if (
            state.get("status") != "FAILED_CLOSED"
            or int(state.get("physical_workload_invocations") or 0) != 0
            or int(state.get("valid_replays") or 0) != 0
            or int(state.get("technical_failures") or 0) != 0
        ):
            raise RuntimeError("failed pre-workload state authority differs")
        state_receipts.append({
            "path": str(state_path),
            "sha256": sha(state_path),
            "status": state["status"],
            "error": state.get("error"),
            "physical_workload_invocations": 0,
        })
    if ORPHANED_QUARANTINE_AUDIT.exists():
        prior = load(ORPHANED_QUARANTINE_AUDIT)
        if (
            prior.get("status") != "PASS"
            or prior.get("archive") != str(archive)
            or quarantine.exists()
            or not archive.is_dir()
        ):
            raise RuntimeError("orphaned quarantine archive evidence differs")
        return
    if (
        not quarantine.is_dir()
        or quarantine.is_symlink()
        or archive.exists()
        or not str(quarantine).startswith("/disk2/")
        or quarantine.parent.name != "gptuner"
    ):
        raise RuntimeError("orphaned quarantine source authority differs")
    live_receipts: list[Dict[str, Any]] = []
    for worker in binding.get("workers") or []:
        if not str(worker.get("worker_id")).startswith("xp_"):
            continue
        inspected = inspect_container(str(worker["container"]))
        mounts = {
            str(row.get("Destination")): str(row.get("Source"))
            for row in inspected.get("Mounts") or []
        }
        live_datadir = str(worker["datadir"])
        if (
            inspected.get("Id") != worker.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or mounts.get("/var/lib/mysql") != live_datadir
            or live_datadir.startswith(str(quarantine) + "/")
        ):
            raise RuntimeError(
                f"{worker['worker_id']}: live mount archive gate differs"
            )
        live_receipts.append({
            "worker_id": worker["worker_id"],
            "container": worker["container"],
            "container_id": inspected["Id"],
            "live_datadir": live_datadir,
        })
    if len(live_receipts) != 4:
        raise RuntimeError("XP live worker archive gate differs")
    measured = sudo(["du", "-sb", str(quarantine)], timeout=600)
    bytes_before_archive = int(measured.stdout.split()[0])
    sudo(["mv", "--", str(quarantine), str(archive)], timeout=600)
    if quarantine.exists() or not archive.is_dir():
        raise RuntimeError("orphaned quarantine archive rename failed")
    document = {
        "schema_version": "mysql_external_orphaned_quarantine_archive_v1",
        "status": "PASS",
        "created_at": now(),
        "source": str(quarantine),
        "archive": str(archive),
        "bytes": bytes_before_archive,
        "recoverable": True,
        "deleted": False,
        "live_mounts_moved": False,
        "failed_states": state_receipts,
        "live_workers": live_receipts,
        "binding_path": str(binding_path),
        "binding_sha256_before_archive": sha(binding_path),
    }
    atomic(ORPHANED_QUARANTINE_AUDIT, document)
    binding["orphaned_quarantine_archive"] = {
        "path": str(ORPHANED_QUARANTINE_AUDIT),
        "sha256": sha(ORPHANED_QUARANTINE_AUDIT),
        "archive": str(archive),
        "recoverable": True,
    }
    atomic(binding_path, binding)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--binding-output", type=Path, default=DEFAULT_BINDING)
    parser.add_argument("--copy-workers", type=int, default=4)
    parser.add_argument("--resume-copies", action="store_true")
    parser.add_argument("--refresh-runtime-authority", action="store_true")
    parser.add_argument(
        "--start-existing-and-refresh-runtime-authority",
        action="store_true",
    )
    parser.add_argument(
        "--recreate-existing-and-refresh-runtime-authority",
        action="store_true",
    )
    parser.add_argument(
        "--archive-orphaned-gptuner-baseline-quarantine",
        action="store_true",
    )
    args = parser.parse_args()
    runtime_root = args.runtime_root.resolve()
    binding_output = args.binding_output.resolve()
    if args.refresh_runtime_authority:
        if (
            args.resume_copies
            or args.start_existing_and_refresh_runtime_authority
            or args.recreate_existing_and_refresh_runtime_authority
            or args.archive_orphaned_gptuner_baseline_quarantine
        ):
            parser.error("refresh-runtime-authority and resume-copies are exclusive")
        refresh_runtime_authority(binding_output)
        print(binding_output, flush=True)
        return 0
    if args.start_existing_and_refresh_runtime_authority:
        if (
            args.resume_copies
            or args.recreate_existing_and_refresh_runtime_authority
            or args.archive_orphaned_gptuner_baseline_quarantine
        ):
            parser.error("start-existing and resume-copies are exclusive")
        start_existing_and_refresh_runtime_authority(binding_output)
        print(binding_output, flush=True)
        return 0
    if args.recreate_existing_and_refresh_runtime_authority:
        if args.resume_copies or args.archive_orphaned_gptuner_baseline_quarantine:
            parser.error("recreate-existing and resume-copies are exclusive")
        recreate_existing_and_refresh_runtime_authority(binding_output)
        print(binding_output, flush=True)
        return 0
    if args.archive_orphaned_gptuner_baseline_quarantine:
        if args.resume_copies:
            parser.error("archive-orphaned and resume-copies are exclusive")
        archive_orphaned_gptuner_baseline_quarantine(binding_output)
        print(ORPHANED_QUARANTINE_AUDIT, flush=True)
        return 0
    runtime_mount = next(
        (
            mount
            for mount in DEVICES_BY_RUNTIME_MOUNT
            if str(runtime_root).startswith(mount + "/")
        ),
        None,
    )
    if runtime_mount is None:
        parser.error("runtime root must be beneath /disk1 or /disk2")
    device = DEVICES_BY_RUNTIME_MOUNT[runtime_mount]
    device_major_minor_path = (
        Path("/sys/class/block") / Path(device).name / "dev"
    )
    if not device_major_minor_path.is_file():
        raise RuntimeError(f"runtime block-device authority differs: {device}")
    device_major_minor = device_major_minor_path.read_text(
        encoding="ascii"
    ).strip()
    if not re.fullmatch(r"\d+:\d+", device_major_minor):
        raise RuntimeError("runtime block-device major:minor differs")
    if binding_output.exists():
        raise RuntimeError("binding output already exists")
    if runtime_root.exists() != args.resume_copies:
        raise RuntimeError(
            "runtime-root existence differs from --resume-copies intent"
        )
    if not 1 <= args.copy_workers <= 8:
        parser.error("copy-workers must be in [1, 8]")
    freeze = load(SOURCE_FREEZE)
    source_binding = load(SOURCE_BINDING)
    source_workers = {
        str(row["worker_id"]): dict(row) for row in source_binding["workers"]
    }
    if set(source_workers) != set(CPU_BINDINGS) or set(PORTS) != set(source_workers):
        raise RuntimeError("source worker set differs")
    local_image = json.loads(run(["docker", "image", "inspect", IMAGE]).stdout)[0]
    if local_image.get("Id") != IMAGE_ID:
        raise RuntimeError("local MySQL image identity differs")
    if not Path(device).exists():
        raise RuntimeError(f"runtime block device differs: {device}")
    target_cpu_owners: Dict[int, list[str]] = {}
    for worker_id, binding in CPU_BINDINGS.items():
        for role in ("database_cpuset", "client_cpuset"):
            for cpu in cpu_set(binding[role]):
                target_cpu_owners.setdefault(cpu, []).append(f"{worker_id}:{role}")
    overlaps = {cpu: owners for cpu, owners in target_cpu_owners.items() if len(owners) != 1}
    if overlaps:
        raise RuntimeError(f"new worker CPU sets overlap: {overlaps}")
    formal = load(FORMAL_TARGET_BINDING)
    formal_cpus = set(
        int(cpu)
        for cpu in formal["rolling_dynamic_noninterference_gate"]
        ["actual_target_cpu_scope_release"]["target_cpus"]
    )
    formal_overlaps = sorted(set(target_cpu_owners) & formal_cpus)
    if formal_overlaps:
        raise RuntimeError(
            f"new worker CPU sets overlap formal MySQL target: {formal_overlaps}"
        )
    running = existing_running_cpuset()
    external_overlaps = {
        cpu: running[cpu] for cpu in target_cpu_owners if cpu in running
    }
    if external_overlaps:
        raise RuntimeError(f"new CPU sets overlap running containers: {external_overlaps}")
    for worker_id, port in PORTS.items():
        if not port_is_free(port):
            raise RuntimeError(f"{worker_id}: host port is not free: {port}")
        name = f"{CONTAINER_PREFIX}{worker_id}_20260826"
        exists = run(
            ["docker", "ps", "-aq", "--filter", f"name=^{name}$"], timeout=30
        ).stdout.strip()
        if exists:
            raise RuntimeError(f"target container already exists: {name}")
    runtime_root.mkdir(parents=True, mode=0o700, exist_ok=args.resume_copies)
    copied: Dict[str, Dict[str, Any]] = {}
    copy_callback = adopt_existing_copy if args.resume_copies else copy_worker
    with ThreadPoolExecutor(max_workers=args.copy_workers, thread_name_prefix="mysql-template-copy") as pool:
        futures = {
            pool.submit(copy_callback, source_workers[worker_id], freeze, runtime_root): worker_id
            for worker_id in sorted(source_workers)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            copied[worker_id] = future.result()
            print(
                f"{'adopted' if args.resume_copies else 'copied'} {worker_id}",
                flush=True,
            )
    runtime_freeze = dict(freeze)
    runtime_freeze["storage"] = {
        **dict(freeze["storage"]),
        "device": device,
        "device_major_minor": device_major_minor,
        "mount": runtime_mount,
        "runtime_root": str(runtime_root),
        "copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "independent_external_baseline_data_plane": True,
        "runtime_root_inherited_from_v4_before_any_workload": False,
        "runtime_root_migrated_to_pg_ssd_before_any_workload": (
            runtime_mount == "/disk1"
        ),
    }
    workers: list[Dict[str, Any]] = []
    for worker_id in sorted(source_workers):
        worker = dict(source_workers[worker_id])
        worker.update(CPU_BINDINGS[worker_id])
        worker["host_port"] = PORTS[worker_id]
        worker["container"] = f"{CONTAINER_PREFIX}{worker_id}_20260826"
        worker["datadir"] = copied[worker_id]["datadir"]
        credentials = copied[worker_id]["credentials"]
        worker["credential_defaults"] = str(Path(credentials) / "selected.cnf")
        worker["credential_benchmark"] = str(Path(credentials) / "benchmark.cnf")
        worker["copy_seconds"] = copied[worker_id]["copy_seconds"]
        workers.append(start_worker(worker, runtime_freeze, device=device))
        print(f"started {worker_id} on {worker['host_port']}", flush=True)
    custom_freeze = dict(runtime_freeze)
    custom_freeze["workers"] = [
        {
            key: value for key, value in worker.items()
            if key not in {"container_id", "container_pid", "mysql_version", "schema_table_count", "default_cost_readback"}
        }
        for worker in workers
    ]
    authority = {
        "schema_version": "mysql_external_baseline_independent_workers_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_freeze": str(SOURCE_FREEZE),
        "source_freeze_sha256": sha(SOURCE_FREEZE),
        "source_binding": str(SOURCE_BINDING),
        "source_binding_sha256": sha(SOURCE_BINDING),
        "runtime_root": str(runtime_root),
        "immutable_templates_only": True,
        "live_datadirs_copied": False,
        "copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "image": IMAGE,
        "image_id": IMAGE_ID,
        "device": device,
        "resource_caps_equal_source": True,
        "cpuset_width_equal_source": True,
        "logical_cpu_sets_pairwise_disjoint": True,
        "logical_cpu_sets_disjoint_from_running_container_cpusets": True,
        "ports_pairwise_distinct_and_loopback_only": True,
        "client_affinity_rebind": {
            "formal_target_binding": str(FORMAL_TARGET_BINDING),
            "formal_target_binding_sha256": sha(FORMAL_TARGET_BINDING),
            "formal_target_cpu_scope_excluded": True,
            "running_container_logical_cpuset_overlap": False,
            "reason": (
                "exclude the live formal MySQL target CPU scope and all "
                "independently running container CPU scopes"
            ),
        },
        "workers": workers,
        "freeze": custom_freeze,
        "worker_semantic_sha256": canonical_sha([
            {
                key: worker[key] for key in (
                    "worker_id", "scene", "container", "host_port",
                    "database_cpuset", "database_cpuset_mems", "client_cpuset",
                    "client_cpuset_mems", "cpu_quota_cores", "memory_bytes",
                    "pids_max", "io_read_bps", "io_write_bps",
                    "innodb_buffer_pool_bytes", "datadir",
                )
            }
            for worker in workers
        ]),
    }
    atomic(binding_output, authority)
    print(binding_output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
