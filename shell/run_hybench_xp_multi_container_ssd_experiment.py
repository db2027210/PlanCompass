#!/usr/bin/env python3
"""Run the HyBench XP same-SSD versus split-SSD interference experiment.

The script is intentionally fail-closed.  It never deletes the source cluster,
never shares a PGDATA directory between containers, and only stops containers
whose names are derived from this experiment's tag.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO, Tuple


SOURCE_CONTAINER = "pg5v1_hyxp_compute00_59630_w1"
SOURCE_PGDATA = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/worker/w1/pgdata"
)
IMAGE = (
    "sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458"
)
HYBENCH_HOME = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/"
    "hybench_runtime/HyBench-2024-master"
)
STATEMENTS = HYBENCH_HOME / "conf/stmt_postgres.toml"
BASE_CONFIG = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/configs"
)
WARMUP_SOURCE = BASE_CONFIG / "hybench_warmup_60s.props"
MEASUREMENT_SOURCE = BASE_CONFIG / "hybench_measurement_180_600s.props"
LOCK_PATH = Path("/tmp/hybench_xp_multi_container_ssd_experiment.lock")

FORBIDDEN_XP_KEYS = {
    "ap_query_ids",
    "ap_query_weights",
    "ap_query_sequence",
    "ap_query_sequence_limit",
}

CASE_ORDERS = (
    ("A_ALONE", "B_SAME_ALONE", "SAME_PARALLEL", "B_SPLIT_ALONE", "SPLIT_PARALLEL"),
    ("SPLIT_PARALLEL", "B_SPLIT_ALONE", "SAME_PARALLEL", "B_SAME_ALONE", "A_ALONE"),
    ("B_SAME_ALONE", "SAME_PARALLEL", "A_ALONE", "SPLIT_PARALLEL", "B_SPLIT_ALONE"),
)

CASE_LANES: Mapping[str, Tuple[str, ...]] = {
    "A_ALONE": ("a",),
    "B_SAME_ALONE": ("b_same",),
    "SAME_PARALLEL": ("a", "b_same"),
    "B_SPLIT_ALONE": ("b_split",),
    "SPLIT_PARALLEL": ("a", "b_split"),
}

QPS_PATTERN = re.compile(r"Current\s+\d+/\d+\s+time\s+AP QPS is\s+([0-9.]+)")
TPS_PATTERN = re.compile(r"Current\s+\d+/\d+\s+time\s+TP TPS is\s+([0-9.]+)")
FATAL_HYBENCH_PATTERNS = (
    "File not found",
    "NullPointerException",
    "OutOfMemoryError",
)

WorkloadProcess = Tuple[subprocess.Popen[str], TextIO, Path]


class ExperimentError(RuntimeError):
    """A fail-closed experiment error."""


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def run(
    command: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    output: Optional[TextIO] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    log("RUN " + " ".join(command))
    return subprocess.run(
        list(command),
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else output,
        stderr=subprocess.STDOUT if capture or output is not None else None,
        timeout=timeout,
    )


def sudo_docker(*arguments: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return run(("sudo", "-n", "docker", *arguments), **kwargs)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=("preflight", "init", "run", "summarize", "all"),
    )
    parser.add_argument("--tag", default="20260808_v1")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup-minutes", type=int, default=1)
    parser.add_argument("--measurement-minutes", type=int, default=10)
    parser.add_argument(
        "--cap-profile",
        choices=("low", "64m", "unlimited"),
        default="low",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse a matching initialized root and skip completed cases.",
    )
    return parser.parse_args()


class Experiment:
    def __init__(self, args: argparse.Namespace) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.tag):
            raise ExperimentError("tag may contain only letters, digits, dot, underscore, dash")
        if args.repeats < 1:
            raise ExperimentError("repeats must be positive")
        if args.warmup_minutes < 1 or args.measurement_minutes < 1:
            raise ExperimentError("workload durations must be positive")

        self.args = args
        self.root = Path("/disk1/hyxp_multi_instance_lab") / args.tag
        self.root2 = Path("/disk2/hyxp_multi_instance_lab") / args.tag
        self.golden = self.root / "golden_pgdata"
        self.pgdata = {
            "a": self.root / "pgdata_a",
            "b_same": self.root / "pgdata_b_same",
            "b_split": self.root2 / "pgdata_b_split",
        }
        suffix = re.sub(r"[^A-Za-z0-9]", "", args.tag).lower()
        self.containers = {
            "a": f"hyxp_ssd_a_{suffix}",
            "b_same": f"hyxp_ssd_bsame_{suffix}",
            "b_split": f"hyxp_ssd_bsplit_{suffix}",
        }
        self.ports = {"a": 59631, "b_same": 59632, "b_split": 59632}
        self.db_cpus = {
            "a": "32-51,288-307",
            "b_same": "64-83,320-339",
            "b_split": "64-83,320-339",
        }
        self.client_cpus = {
            "a": "84-91,340-347",
            "b_same": "92-99,348-355",
            "b_split": "92-99,348-355",
        }
        self.devices = {"a": "/dev/nvme1n1", "b_same": "/dev/nvme1n1", "b_split": "/dev/nvme2n1"}
        self.device_numbers = {"a": "259:5", "b_same": "259:5", "b_split": "259:6"}
        self.config = {
            "a_warmup": self.root / "config/a_warmup.props",
            "a_measure": self.root / "config/a_measure.props",
            "b_warmup": self.root / "config/b_warmup.props",
            "b_measure": self.root / "config/b_measure.props",
        }

    def manifest(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "tag": self.args.tag,
            "root": str(self.root),
            "root2": str(self.root2),
            "source_container": SOURCE_CONTAINER,
            "source_pgdata": str(SOURCE_PGDATA),
            "image": IMAGE,
            "hybench_home": str(HYBENCH_HOME),
            "statements": str(STATEMENTS),
            "repeats": self.args.repeats,
            "warmup_minutes": self.args.warmup_minutes,
            "measurement_minutes": self.args.measurement_minutes,
            "cap_profile": self.args.cap_profile,
            "containers": self.containers,
            "ports": self.ports,
            "db_cpus": self.db_cpus,
            "client_cpus": self.client_cpus,
            "devices": self.devices,
            "device_numbers": self.device_numbers,
            "case_orders": [list(order) for order in CASE_ORDERS],
        }

    def acquire_lock(self) -> TextIO:
        handle = LOCK_PATH.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ExperimentError(f"another experiment owns {LOCK_PATH}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} tag={self.args.tag}\n")
        handle.flush()
        return handle

    def preflight(self) -> None:
        log("preflight started")
        for executable in ("sudo", "docker", "java", "iostat", "taskset", "timeout"):
            if shutil.which(executable) is None:
                raise ExperimentError(f"missing executable: {executable}")
        run(("sudo", "-n", "true"))

        for path in (SOURCE_PGDATA, HYBENCH_HOME, STATEMENTS, WARMUP_SOURCE, MEASUREMENT_SOURCE):
            if not path.exists():
                raise ExperimentError(f"missing required path: {path}")

        if Path("/disk1").stat().st_dev == Path("/disk2").stat().st_dev:
            raise ExperimentError("/disk1 and /disk2 are not independent filesystems")
        for disk in (Path("/disk1"), Path("/disk2")):
            free = shutil.disk_usage(disk).free
            if free < 100 * 1024**3:
                raise ExperimentError(f"less than 100 GiB free on {disk}")

        source = sudo_docker(
            "inspect",
            SOURCE_CONTAINER,
            "--format",
            "{{.State.Status}} {{.State.Pid}} {{.Image}}",
            capture=True,
        ).stdout.strip()
        parts = source.split()
        if len(parts) != 3 or parts[0] != "exited" or parts[1] != "0":
            raise ExperimentError(
                f"source container must be fully stopped for an offline copy; got: {source}"
            )
        if parts[2] != IMAGE:
            raise ExperimentError(f"source image changed: {parts[2]}")

        for port in (59631, 59632):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise ExperimentError(f"port {port} is already in use") from exc
            finally:
                sock.close()

        existing = sudo_docker(
            "ps", "-a", "--format", "{{.Names}}", capture=True
        ).stdout.splitlines()
        conflicts = sorted(set(existing).intersection(self.containers.values()))
        if conflicts and not self.args.resume:
            raise ExperimentError(f"lab containers already exist: {conflicts}; use --resume")

        process_table = run(
            ("ps", "-eo", "pid=,args="), capture=True
        ).stdout
        source_processes = [
            line for line in process_table.splitlines()
            if str(SOURCE_PGDATA) in line
            and "run_hybench_xp_multi_container_ssd_experiment.py" not in line
        ]
        if source_processes:
            raise ExperimentError(
                "source PGDATA appears in a live process command line: "
                + " | ".join(source_processes)
            )
        sample = run(
            ("iostat", "-x", "-d", "-y", "1", "3", "nvme1n1", "nvme2n1"),
            capture=True,
            timeout=10,
        ).stdout
        preflight_dir = self.root if self.root.exists() else Path("/tmp")
        (preflight_dir / f"hyxp_ssd_preflight_{self.args.tag}.iostat").write_text(sample, encoding="utf-8")
        log("preflight passed")

    def ensure_new_or_matching_root(self) -> None:
        manifest_path = self.root / "manifest.json"
        if self.root.exists() or self.root2.exists():
            if not self.args.resume:
                raise ExperimentError("experiment root already exists; use a new tag or --resume")
            if not manifest_path.is_file():
                raise ExperimentError("resume root has no manifest")
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing != self.manifest():
                raise ExperimentError("resume manifest does not match requested experiment")
            return

        run(("sudo", "-n", "mkdir", "-p", str(self.root), str(self.root2)))
        run(
            (
                "sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}",
                str(self.root), str(self.root2),
            )
        )
        atomic_json(manifest_path, self.manifest())

    def sudo_test(self, flag: str, path: Path) -> bool:
        return run(
            ("sudo", "-n", "test", flag, str(path)),
            check=False,
            capture=True,
        ).returncode == 0

    def copy_pgdata(self, source: Path, destination: Path, marker: Path) -> None:
        if marker.exists():
            log(f"reuse completed PGDATA copy: {destination}")
            return
        run(("sudo", "-n", "mkdir", "-p", str(destination)))
        run(("sudo", "-n", "cp", "-a", f"{source}/.", str(destination)))
        if not self.sudo_test("-f", destination / "PG_VERSION"):
            raise ExperimentError(f"PGDATA copy has no PG_VERSION: {destination}")
        marker.write_text(f"completed_at={time.time()}\n", encoding="utf-8")

    def write_props(self, source: Path, destination: Path, port: int, app: str, minutes: int) -> None:
        text = source.read_text(encoding="utf-8")
        text = text.replace("127.0.0.1:59630", f"127.0.0.1:{port}")
        text = text.replace("ApplicationName=resource-adaptive-matrix", f"ApplicationName={app}")
        text = re.sub(r"(?m)^xpRunMins=\d+$", f"xpRunMins={minutes}", text)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")

    def validate_props(self, path: Path, expected_port: int, expected_minutes: int) -> None:
        values: Dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        forbidden = FORBIDDEN_XP_KEYS.intersection(values)
        if forbidden:
            raise ExperimentError(f"forbidden XP query-selection keys in {path}: {forbidden}")
        if int(values.get("xtpclient", "-1")) != 16 or int(values.get("xapclient", "-1")) != 6:
            raise ExperimentError(f"unexpected native XP client profile in {path}")
        if sum(int(values.get(f"at{i}_percent", "-1")) for i in range(1, 7)) != 100:
            raise ExperimentError(f"AT percentages do not sum to 100 in {path}")
        if int(values.get("xpRunMins", "-1")) != expected_minutes:
            raise ExperimentError(f"wrong xpRunMins in {path}")
        for key in ("url", "url_tp", "url_ap"):
            if f":{expected_port}/hybench_sf10x" not in values.get(key, ""):
                raise ExperimentError(f"wrong {key} in {path}")

    def initialize(self) -> None:
        self.ensure_new_or_matching_root()
        initialized = self.root / "initialized.json"
        if initialized.exists():
            log("initialization already complete")
            return

        golden_marker = self.root / ".golden_copy_complete"
        if not golden_marker.exists() and self.sudo_test("-f", self.golden / "PG_VERSION"):
            # A previous cp process returned successfully but validation was
            # interrupted by host-side permission checks.  Re-running cp -a is
            # safe and completes any missing files without touching the source.
            log("golden copy exists without completion marker; verifying by replaying cp -a")
        self.copy_pgdata(SOURCE_PGDATA, self.golden, golden_marker)
        stale_pid = self.golden / "postmaster.pid"
        if self.sudo_test("-f", stale_pid):
            run(
                (
                    "sudo", "-n", "mv", str(stale_pid),
                    str(self.golden / "postmaster.pid.source_stale"),
                )
            )
        if self.sudo_test("-e", self.golden / "standby.signal"):
            raise ExperimentError("source copy unexpectedly contains standby.signal")
        version = run(
            ("sudo", "-n", "cat", str(self.golden / "PG_VERSION")),
            capture=True,
        ).stdout.strip()
        if version != "17":
            raise ExperimentError("unexpected PostgreSQL major version")

        copy_markers = {
            "a": self.root / ".a_copy_complete",
            "b_same": self.root / ".b_same_copy_complete",
            "b_split": self.root2 / ".b_split_copy_complete",
        }
        for lane, destination in self.pgdata.items():
            self.copy_pgdata(self.golden, destination, copy_markers[lane])

        self.write_props(WARMUP_SOURCE, self.config["a_warmup"], 59631, "hyxp-lab-a", self.args.warmup_minutes)
        self.write_props(MEASUREMENT_SOURCE, self.config["a_measure"], 59631, "hyxp-lab-a", self.args.measurement_minutes)
        self.write_props(WARMUP_SOURCE, self.config["b_warmup"], 59632, "hyxp-lab-b", self.args.warmup_minutes)
        self.write_props(MEASUREMENT_SOURCE, self.config["b_measure"], 59632, "hyxp-lab-b", self.args.measurement_minutes)
        for key, path in self.config.items():
            self.validate_props(
                path,
                59631 if key.startswith("a_") else 59632,
                self.args.warmup_minutes if key.endswith("warmup") else self.args.measurement_minutes,
            )

        environment = self.root / "environment.txt"
        with environment.open("w", encoding="utf-8") as handle:
            for command in (
                ("date", "--iso-8601=seconds"),
                ("uname", "-a"),
                ("lsblk", "-o", "NAME,KNAME,TYPE,SIZE,ROTA,RO,MOUNTPOINT"),
                ("df", "-h", "/disk1", "/disk2"),
                ("sudo", "-n", "docker", "ps", "--format", "{{.Names}}\\t{{.Status}}"),
            ):
                run(command, output=handle)

        self.create_containers()
        atomic_json(
            initialized,
            {"initialized_at": time.time(), "manifest": self.manifest()},
        )
        log("initialization complete")

    def create_containers(self) -> None:
        existing = set(
            sudo_docker("ps", "-a", "--format", "{{.Names}}", capture=True).stdout.splitlines()
        )
        for lane in ("a", "b_same", "b_split"):
            name = self.containers[lane]
            if name in existing:
                continue
            command = [
                "create", "--name", name, "--network", "host",
                "--cpuset-cpus", self.db_cpus[lane], "--cpuset-mems", "0",
                "--cpus", "10.5", "--memory", "30404000000",
                "--memory-swap", "30404000000", "--shm-size", "8g",
                "--pids-limit", "1024",
                "-e", "POSTGRES_PASSWORD=postgres",
                "-v", f"{self.pgdata[lane]}:/var/lib/postgresql/data",
                IMAGE, "postgres", "-c", f"port={self.ports[lane]}",
                "-c", "listen_addresses=*", "-c",
                "shared_preload_libraries=pg_stat_statements",
            ]
            sudo_docker(*command)

    def expected_io_limits(self) -> Tuple[int, int]:
        if self.args.cap_profile == "unlimited":
            return 0, 0
        if self.args.cap_profile == "low":
            return 100000, 837500
        return 64 * 1024 * 1024, 64 * 1024 * 1024

    def cgroup_io_files(self, lane: str) -> Tuple[Path, Path]:
        container_id = sudo_docker(
            "inspect", "-f", "{{.Id}}", self.containers[lane], capture=True
        ).stdout.strip()
        root = Path("/sys/fs/cgroup/blkio/docker-limited") / container_id
        return (
            root / "blkio.throttle.read_bps_device",
            root / "blkio.throttle.write_bps_device",
        )

    def write_cgroup_limit(self, path: Path, value: str) -> None:
        log(f"WRITE {path}: {value}")
        subprocess.run(
            ("sudo", "-n", "tee", str(path)),
            input=value + "\n",
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )

    def apply_io_limits(
        self,
        lane: str,
        read_bps: int,
        write_bps: int,
    ) -> Dict[str, Any]:
        read_file, write_file = self.cgroup_io_files(lane)
        device_number = self.device_numbers[lane]
        self.write_cgroup_limit(read_file, f"{device_number} {read_bps}")
        self.write_cgroup_limit(write_file, f"{device_number} {write_bps}")

        def readback(path: Path) -> Dict[str, int]:
            output = run(
                ("sudo", "-n", "cat", str(path)), capture=True
            ).stdout
            result: Dict[str, int] = {}
            for line in output.splitlines():
                fields = line.split()
                if len(fields) == 2:
                    result[fields[0]] = int(fields[1])
            return result

        actual_read = readback(read_file).get(device_number, 0)
        actual_write = readback(write_file).get(device_number, 0)
        if actual_read != read_bps or actual_write != write_bps:
            raise ExperimentError(
                f"I/O contract readback mismatch for {lane}: "
                f"expected={read_bps}/{write_bps}, actual={actual_read}/{actual_write}"
            )
        return {
            "lane": lane,
            "device": self.devices[lane],
            "device_number": device_number,
            "read_bps": actual_read,
            "write_bps": actual_write,
            "read_file": str(read_file),
            "write_file": str(write_file),
        }

    def apply_io_contract(self, lane: str) -> Dict[str, Any]:
        return self.apply_io_limits(lane, *self.expected_io_limits())

    def stop_all(self) -> None:
        sudo_docker("stop", *self.containers.values(), check=False, capture=True)

    def wait_ready(self, lane: str, timeout_seconds: int = 120) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = sudo_docker(
                "exec", self.containers[lane], "pg_isready", "-h", "127.0.0.1",
                "-p", str(self.ports[lane]), check=False, capture=True,
            )
            if result.returncode == 0:
                return
            time.sleep(2)
        raise ExperimentError(f"PostgreSQL did not become ready: {lane}")

    def psql(self, lane: str, database: str, sql: str, *, output: Optional[TextIO] = None) -> None:
        sudo_docker(
            "exec", self.containers[lane], "psql", "-X", "-v", "ON_ERROR_STOP=1",
            "-U", "postgres", "-p", str(self.ports[lane]), "-d", database,
            "-P", "pager=off", "-c", sql, output=output,
        )

    def reset_database(self, lane: str) -> None:
        self.psql(
            lane,
            "postgres",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname IN ('hybench_sf10x','hybench_sf10x_base') "
            "AND pid <> pg_backend_pid();",
        )
        self.psql(
            lane,
            "postgres",
            "DROP DATABASE IF EXISTS hybench_sf10x WITH (FORCE);",
        )
        self.psql(
            lane,
            "postgres",
            "CREATE DATABASE hybench_sf10x WITH TEMPLATE hybench_sf10x_base OWNER postgres;",
        )
        self.psql(
            lane,
            "hybench_sf10x",
            "CREATE EXTENSION IF NOT EXISTS pg_stat_statements; CHECKPOINT;",
        )

    def snapshot(self, lane: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(f"timestamp={time.time()}\n")
            self.psql(
                lane,
                "hybench_sf10x",
                "SELECT datname,xact_commit,xact_rollback,blks_read,blks_hit,temp_files,"
                "temp_bytes,blk_read_time,blk_write_time,deadlocks FROM pg_stat_database "
                "WHERE datname='hybench_sf10x'; "
                "SELECT wal_records,wal_fpi,wal_bytes,wal_buffers_full,wal_write,wal_sync,"
                "wal_write_time,wal_sync_time FROM pg_stat_wal; "
                "SELECT backend_type,object,context,reads,writes,writebacks,extends,fsyncs,"
                "read_time,write_time,fsync_time FROM pg_stat_io ORDER BY 1,2,3;",
                output=handle,
            )

    def prepare_hybench_runtime(self, runtime_dir: Path) -> None:
        """Create an isolated HyBench working directory for one client run."""
        runtime_dir.mkdir(parents=True, exist_ok=True)
        for name in ("lib", "conf", "Data_10x", "Data_100x"):
            source = HYBENCH_HOME / name
            if not source.exists():
                if name == "Data_100x":
                    continue
                raise ExperimentError(f"missing HyBench runtime resource: {source}")
            destination = runtime_dir / name
            if destination.is_symlink():
                if destination.resolve() != source.resolve():
                    raise ExperimentError(f"wrong HyBench runtime link: {destination}")
                continue
            if destination.exists():
                raise ExperimentError(f"HyBench runtime path is not a symlink: {destination}")
            destination.symlink_to(source, target_is_directory=True)

    def workload_command(self, lane: str, config: Path, runtime_dir: Path) -> List[str]:
        return [
            "taskset", "-c", self.client_cpus[lane], "java", "-cp",
            f"{runtime_dir}/lib/*:{runtime_dir}", "com.hybench.HyBench",
            "-t", "runxp", "-c", str(config), "-f", str(STATEMENTS),
        ]

    def start_workloads(
        self,
        lanes: Iterable[str],
        output_root: Path,
        kind: str,
    ) -> Dict[str, WorkloadProcess]:
        processes: Dict[str, WorkloadProcess] = {}
        for lane in lanes:
            client = "a" if lane == "a" else "b"
            config = self.config[f"{client}_{kind}"]
            lane_dir = output_root / client / kind
            self.prepare_hybench_runtime(lane_dir)
            console = (lane_dir / "console.log").open("w", encoding="utf-8")
            process = subprocess.Popen(
                self.workload_command(lane, config, lane_dir),
                cwd=lane_dir,
                stdout=console,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            processes[lane] = (process, console, lane_dir)
        return processes

    def validate_workload_log(self, directory: Path) -> None:
        """Reject silent HyBench failures even when Java exits with status 0."""
        log_paths = [directory / "console.log", directory / "hybench.log"]
        existing = [path for path in log_paths if path.is_file()]
        if not existing:
            raise ExperimentError(f"no HyBench logs in {directory}")
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in existing
        )
        fatal = [pattern for pattern in FATAL_HYBENCH_PATTERNS if pattern in text]
        if fatal:
            raise ExperimentError(f"fatal HyBench log patterns in {directory}: {fatal}")
        if "Begin XP Workload" not in text:
            raise ExperimentError(f"HyBench XP did not start in {directory}")
        if not QPS_PATTERN.search(text) or not TPS_PATTERN.search(text):
            raise ExperimentError(f"HyBench produced no AP/TP progress windows in {directory}")

    def wait_workloads(
        self,
        processes: Mapping[str, WorkloadProcess],
        timeout_seconds: int,
    ) -> Dict[str, int]:
        deadline = time.monotonic() + timeout_seconds
        returncodes: Dict[str, int] = {}
        try:
            while len(returncodes) < len(processes):
                if time.monotonic() >= deadline:
                    raise ExperimentError("HyBench workload timed out")
                for lane, (process, _, _) in processes.items():
                    if lane not in returncodes:
                        code = process.poll()
                        if code is not None:
                            returncodes[lane] = code
                time.sleep(1)
        finally:
            for process, console, _ in processes.values():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                console.close()
        failures = {lane: code for lane, code in returncodes.items() if code != 0}
        if failures:
            raise ExperimentError(f"HyBench failures: {failures}")
        for _, _, directory in processes.values():
            self.validate_workload_log(directory)
        return returncodes

    def start_monitor(self, command: Sequence[str], path: Path) -> Tuple[subprocess.Popen[str], TextIO]:
        handle = path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            list(command), stdout=handle, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
        )
        return process, handle

    def stop_monitor(self, item: Tuple[subprocess.Popen[str], TextIO]) -> None:
        process, handle = item
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        handle.close()

    def parse_metrics(self, directory: Path) -> Dict[str, Any]:
        preferred = directory / "hybench.log"
        fallback = directory / "console.log"
        path = preferred if preferred.is_file() else fallback
        if not path.is_file():
            raise ExperimentError(f"no HyBench log files in {directory}")
        text = path.read_text(encoding="utf-8", errors="replace")
        qps = [float(item) for item in QPS_PATTERN.findall(text)]
        tps = [float(item) for item in TPS_PATTERN.findall(text)]
        if not qps or not tps:
            raise ExperimentError(f"missing AP QPS or TP TPS windows in {directory}")
        return {
            "ap_qps_mean": statistics.fmean(qps),
            "ap_qps_median": statistics.median(qps),
            "ap_qps_windows": len(qps),
            "tp_tps_mean": statistics.fmean(tps),
            "tp_tps_median": statistics.median(tps),
            "tp_tps_windows": len(tps),
        }

    def archive_incomplete(self, case_dir: Path) -> None:
        if not case_dir.exists() or not any(case_dir.iterdir()):
            return
        archived = case_dir.with_name(
            case_dir.name + ".incomplete." + time.strftime("%Y%m%dT%H%M%S")
        )
        case_dir.rename(archived)

    def run_case(self, case: str, repeat: int) -> None:
        case_dir = self.root / "results" / f"{case}_rep{repeat:02d}"
        if (case_dir / "completed.json").exists():
            log(f"skip completed {case} repeat {repeat}")
            return
        self.archive_incomplete(case_dir)
        case_dir.mkdir(parents=True)
        (case_dir / "monitor").mkdir()
        lanes = CASE_LANES[case]
        started_at = time.time()
        log(f"case start: {case} repeat={repeat} lanes={lanes}")

        self.stop_all()
        io_contracts = {}
        for lane in lanes:
            sudo_docker("start", self.containers[lane])
            # Database restoration copies the 10x template database and is not
            # part of the measured workload.  Temporarily remove throttling,
            # matching the established production restore wrapper, then apply
            # and verify the experiment contract before warmup begins.
            self.apply_io_limits(lane, 0, 0)
        for lane in lanes:
            self.wait_ready(lane)
            self.reset_database(lane)
            io_contracts[lane] = self.apply_io_contract(lane)

        warmup = self.start_workloads(lanes, case_dir, "warmup")
        self.wait_workloads(warmup, self.args.warmup_minutes * 60 + 180)

        for lane in lanes:
            client = "a" if lane == "a" else "b"
            self.snapshot(lane, case_dir / client / "db_before.txt")

        with (case_dir / "monitor/docker_inspect.json").open("w", encoding="utf-8") as handle:
            run(
                ("sudo", "-n", "docker", "inspect", *(self.containers[lane] for lane in lanes)),
                output=handle,
            )
        atomic_json(case_dir / "monitor/io_contract_readback.json", io_contracts)
        monitors = [
            self.start_monitor(
                ("iostat", "-x", "-d", "-y", "1", "nvme1n1", "nvme2n1"),
                case_dir / "monitor/iostat.log",
            ),
            self.start_monitor(
                (
                    "sudo", "-n", "docker", "stats", "--format",
                    "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.BlockIO}}",
                    *(self.containers[lane] for lane in lanes),
                ),
                case_dir / "monitor/docker_stats.csv",
            ),
        ]
        try:
            measurement = self.start_workloads(lanes, case_dir, "measure")
            returncodes = self.wait_workloads(
                measurement,
                self.args.measurement_minutes * 60 + 300,
            )
        finally:
            for monitor in monitors:
                self.stop_monitor(monitor)

        metrics: Dict[str, Any] = {}
        for lane in lanes:
            client = "a" if lane == "a" else "b"
            self.snapshot(lane, case_dir / client / "db_after.txt")
            metrics[client] = self.parse_metrics(case_dir / client / "measure")

        self.stop_all()
        atomic_json(
            case_dir / "completed.json",
            {
                "case": case,
                "repeat": repeat,
                "lanes": list(lanes),
                "started_at": started_at,
                "finished_at": time.time(),
                "returncodes": returncodes,
                "metrics": metrics,
            },
        )
        log(f"case complete: {case} repeat={repeat} metrics={metrics}")

    def run_experiment(self) -> None:
        if not (self.root / "initialized.json").exists():
            raise ExperimentError("experiment is not initialized")
        atomic_json(self.root / "runtime_manifest.json", self.manifest())
        try:
            for repeat in range(1, self.args.repeats + 1):
                order = CASE_ORDERS[(repeat - 1) % len(CASE_ORDERS)]
                for case in order:
                    self.run_case(case, repeat)
        finally:
            self.stop_all()
        self.summarize()

    def summarize(self) -> None:
        rows: Dict[str, List[Dict[str, Any]]] = {case: [] for case in CASE_LANES}
        for path in sorted((self.root / "results").glob("*/completed.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            rows[value["case"]].append(value)

        summary: Dict[str, Any] = {"tag": self.args.tag, "cases": {}}
        for case, records in rows.items():
            item: Dict[str, Any] = {"valid_repeats": len(records), "clients": {}}
            for client in ("a", "b"):
                client_records = [record["metrics"][client] for record in records if client in record["metrics"]]
                if client_records:
                    item["clients"][client] = {
                        key: statistics.median(float(record[key]) for record in client_records)
                        for key in ("ap_qps_mean", "tp_tps_mean")
                    }
            summary["cases"][case] = item

        def ratio(case: str, baseline: str, client: str, metric: str) -> Optional[float]:
            try:
                return (
                    summary["cases"][case]["clients"][client][metric]
                    / summary["cases"][baseline]["clients"][client][metric]
                )
            except (KeyError, ZeroDivisionError):
                return None

        summary["retention"] = {
            "same": {
                "a_ap": ratio("SAME_PARALLEL", "A_ALONE", "a", "ap_qps_mean"),
                "a_tp": ratio("SAME_PARALLEL", "A_ALONE", "a", "tp_tps_mean"),
                "b_ap": ratio("SAME_PARALLEL", "B_SAME_ALONE", "b", "ap_qps_mean"),
                "b_tp": ratio("SAME_PARALLEL", "B_SAME_ALONE", "b", "tp_tps_mean"),
            },
            "split": {
                "a_ap": ratio("SPLIT_PARALLEL", "A_ALONE", "a", "ap_qps_mean"),
                "a_tp": ratio("SPLIT_PARALLEL", "A_ALONE", "a", "tp_tps_mean"),
                "b_ap": ratio("SPLIT_PARALLEL", "B_SPLIT_ALONE", "b", "ap_qps_mean"),
                "b_tp": ratio("SPLIT_PARALLEL", "B_SPLIT_ALONE", "b", "tp_tps_mean"),
            },
        }
        atomic_json(self.root / "summary.json", summary)

        lines = [
            "# HyBench XP  SSD ",
            "",
            f"- tag: `{self.args.tag}`",
            f"- cap profile: `{self.args.cap_profile}`",
            f"- measurement: `{self.args.measurement_minutes} min`",
            "",
            "|  |  | A AP QPS | A TP TPS | B AP QPS | B TP TPS |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for case in CASE_LANES:
            item = summary["cases"][case]
            a = item["clients"].get("a", {})
            b = item["clients"].get("b", {})
            lines.append(
                f"| {case} | {item['valid_repeats']} | "
                f"{a.get('ap_qps_mean', '—')} | {a.get('tp_tps_mean', '—')} | "
                f"{b.get('ap_qps_mean', '—')} | {b.get('tp_tps_mean', '—')} |"
            )
        lines.extend(("", "## ", "", "```json", json.dumps(summary["retention"], indent=2), "```", ""))
        (self.root / "summary.md").write_text("\n".join(lines), encoding="utf-8")
        log(f"summary written: {self.root / 'summary.md'}")


def main() -> int:
    args = parse_args()
    experiment = Experiment(args)
    lock = experiment.acquire_lock()
    try:
        if args.phase in {"preflight", "all"}:
            experiment.preflight()
        if args.phase in {"init", "all"}:
            experiment.initialize()
        if args.phase in {"run", "all"}:
            experiment.run_experiment()
        if args.phase == "summarize":
            experiment.summarize()
    except (
        ExperimentError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        log(f"FAILED: {exc}")
        experiment.stop_all()
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
