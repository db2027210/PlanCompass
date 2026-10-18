#!/usr/bin/env python3
"""Measure the same-SSD HyBench XP concurrency knee at 1/4/8/12/16 instances.

Every PostgreSQL container has an independent PGDATA, port, physical-core set,
memory limit, and HyBench client directory.  The controller is fail-closed and
supports resuming only completed waves from a matching manifest.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
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
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO, Tuple


IMAGE = "sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458"
DEFAULT_SEED_PGDATA = Path(
    "/disk1/wxr-data/hyxp_lab/hyxp_multi_instance_lab/20260808_v1/golden_pgdata"
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
LOCK_PATH = Path("/tmp/hybench_xp_same_ssd_scale_experiment.lock")

DEFAULT_COUNTS = (1, 4, 8, 12, 16)
MEMORY_BYTES = 30_404_000_000
DB_CPU_QUOTA = 10.0
DB_PHYSICAL_CPUS = 10
CLIENT_PHYSICAL_CPUS = 4
PORT_BASE = 59700
FATAL_PATTERNS = ("File not found", "NullPointerException", "OutOfMemoryError")
QPS_PATTERN = re.compile(r"XP-QPS is\s+([0-9.]+)")
TPS_PATTERN = re.compile(r"XP-TPS is\s+([0-9.]+)")

ProcessInfo = Tuple[Any, TextIO, Path]
MonitorInfo = Tuple[Any, TextIO, bool]


class ExperimentError(RuntimeError):
    """Fail-closed experiment error."""


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def run(
    command: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    output: Optional[TextIO] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    log("RUN " + " ".join(command))
    return subprocess.run(
        list(command),
        check=check,
        universal_newlines=True,
        stdout=subprocess.PIPE if capture else output,
        stderr=subprocess.STDOUT if capture or output is not None else None,
        timeout=timeout,
    )


def sudo_docker(*arguments: str, **kwargs: Any) -> subprocess.CompletedProcess:
    return run(("sudo", "-n", "docker", *arguments), **kwargs)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_counts(raw: str) -> Tuple[int, ...]:
    try:
        counts = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("counts must be comma-separated integers") from exc
    if not counts or counts[0] != 1 or any(item < 1 or item > 16 for item in counts):
        raise argparse.ArgumentTypeError("counts must start with 1 and stay in range 1..16")
    if len(set(counts)) != len(counts):
        raise argparse.ArgumentTypeError("counts must not contain duplicates")
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("plan", "preflight", "init", "run", "summarize", "all"))
    parser.add_argument("--tag", default="20260808_scale_v1")
    parser.add_argument("--counts", type=parse_counts, default=DEFAULT_COUNTS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup-minutes", type=int, default=1)
    parser.add_argument("--measurement-minutes", type=int, default=10)
    parser.add_argument("--cooldown-seconds", type=int, default=30)
    parser.add_argument("--seed-pgdata", type=Path, default=DEFAULT_SEED_PGDATA)
    parser.add_argument("--max-host-load", type=float, default=32.0)
    parser.add_argument("--allow-busy-host", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


class ScaleExperiment:
    def __init__(self, args: argparse.Namespace) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.tag):
            raise ExperimentError("tag contains unsupported characters")
        if args.repeats < 1 or args.repeats > 3:
            raise ExperimentError("repeats must be in range 1..3")
        if args.warmup_minutes < 1 or args.measurement_minutes < 1:
            raise ExperimentError("workload durations must be positive")
        if args.cooldown_seconds < 0:
            raise ExperimentError("cooldown must not be negative")

        self.args = args
        self.counts = tuple(args.counts)
        self.max_count = max(self.counts)
        self.root = Path("/disk1/hyxp_same_ssd_scale_lab") / args.tag
        suffix = re.sub(r"[^A-Za-z0-9]", "", args.tag).lower()
        self.lanes = tuple(f"i{item:02d}" for item in range(1, self.max_count + 1))
        self.containers = {
            lane: f"hyxp_scale_{suffix}_{lane}" for lane in self.lanes
        }
        self.ports = {
            lane: PORT_BASE + item for item, lane in enumerate(self.lanes, start=1)
        }
        self.pgdata = {lane: self.root / "pgdata" / lane for lane in self.lanes}
        self.config_dir = self.root / "config"

    @staticmethod
    def cpu_allocation(item: int) -> Dict[str, Any]:
        """Allocate distinct physical cores, leaving 16 cores free per NUMA node."""
        node = (item - 1) % 2
        slot = (item - 1) // 2
        base = node * 128 + slot * (DB_PHYSICAL_CPUS + CLIENT_PHYSICAL_CPUS)
        db_cpus = tuple(range(base, base + DB_PHYSICAL_CPUS))
        client_cpus = tuple(
            range(base + DB_PHYSICAL_CPUS, base + DB_PHYSICAL_CPUS + CLIENT_PHYSICAL_CPUS)
        )
        return {
            "node": node,
            "db_cpus": db_cpus,
            "client_cpus": client_cpus,
            "db_cpuset": ",".join(str(cpu) for cpu in db_cpus),
            "client_cpuset": ",".join(str(cpu) for cpu in client_cpus),
        }

    def count_orders(self) -> Tuple[Tuple[int, ...], ...]:
        ascending = self.counts
        reverse = tuple(reversed(self.counts))
        rotated = self.counts[1:] + self.counts[:1]
        choices = (ascending, reverse, rotated)
        return tuple(choices[item % len(choices)] for item in range(self.args.repeats))

    def manifest(self) -> Dict[str, Any]:
        allocations = {
            lane: self.cpu_allocation(item)
            for item, lane in enumerate(self.lanes, start=1)
        }
        return {
            "schema_version": 1,
            "tag": self.args.tag,
            "root": str(self.root),
            "seed_pgdata": str(self.args.seed_pgdata),
            "image": IMAGE,
            "hybench_home": str(HYBENCH_HOME),
            "counts": list(self.counts),
            "count_orders": [list(order) for order in self.count_orders()],
            "repeats": self.args.repeats,
            "warmup_minutes": self.args.warmup_minutes,
            "measurement_minutes": self.args.measurement_minutes,
            "cooldown_seconds": self.args.cooldown_seconds,
            "containers": self.containers,
            "ports": self.ports,
            "allocations": allocations,
            "memory_bytes_per_container": MEMORY_BYTES,
            "db_cpu_quota": DB_CPU_QUOTA,
            "io_limit": "unlimited",
        }

    def print_plan(self) -> None:
        print(json.dumps(self.manifest(), indent=2, ensure_ascii=False), flush=True)

    def acquire_lock(self) -> TextIO:
        handle = LOCK_PATH.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ExperimentError(f"another scale experiment owns {LOCK_PATH}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} tag={self.args.tag}\n")
        handle.flush()
        return handle

    def sudo_test(self, flag: str, path: Path) -> bool:
        return run(
            ("sudo", "-n", "test", flag, str(path)), check=False, capture=True
        ).returncode == 0

    def preflight(self) -> None:
        log("scale preflight started")
        for executable in ("sudo", "docker", "java", "iostat", "taskset", "timeout"):
            if shutil.which(executable) is None:
                raise ExperimentError(f"missing executable: {executable}")
        run(("sudo", "-n", "true"))
        for required in (HYBENCH_HOME, STATEMENTS, WARMUP_SOURCE, MEASUREMENT_SOURCE):
            if not required.exists():
                raise ExperimentError(f"missing required path: {required}")
        if not self.sudo_test("-f", self.args.seed_pgdata / "PG_VERSION"):
            raise ExperimentError(f"seed PGDATA is invalid: {self.args.seed_pgdata}")

        cpu_count = os.cpu_count() or 0
        if cpu_count < 512:
            raise ExperimentError(f"expected at least 512 logical CPUs, got {cpu_count}")
        all_cpus: List[int] = []
        for item in range(1, self.max_count + 1):
            allocation = self.cpu_allocation(item)
            all_cpus.extend(allocation["db_cpus"])
            all_cpus.extend(allocation["client_cpus"])
        if len(all_cpus) != len(set(all_cpus)):
            raise ExperimentError("CPU allocation overlaps between lanes")

        available_memory = int(
            run(("awk", "/MemAvailable/ {print $2 * 1024}", "/proc/meminfo"), capture=True).stdout.strip()
        )
        required_memory = self.max_count * MEMORY_BYTES + 64 * 1024**3
        if available_memory < required_memory:
            raise ExperimentError(
                f"insufficient memory: need {required_memory}, available {available_memory}"
            )

        seed_bytes = int(
            run(("sudo", "-n", "du", "-sb", str(self.args.seed_pgdata)), capture=True).stdout.split()[0]
        )
        required_disk = int(seed_bytes * self.max_count * 1.15) + 50 * 1024**3
        free_disk = shutil.disk_usage(Path("/disk1")).free
        if free_disk < required_disk:
            raise ExperimentError(
                f"insufficient /disk1 space: need {required_disk}, free {free_disk}"
            )

        load_one = os.getloadavg()[0]
        if load_one > self.args.max_host_load and not self.args.allow_busy_host:
            raise ExperimentError(
                f"host is not quiet: load1={load_one:.1f} exceeds {self.args.max_host_load:.1f}"
            )
        process_table = run(("ps", "-eo", "user=,pid=,pcpu=,args="), capture=True).stdout
        if "com.hybench.HyBench" in process_table:
            raise ExperimentError("another HyBench process is running")

        for port in self.ports.values():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise ExperimentError(f"port already in use: {port}") from exc
            finally:
                sock.close()

        existing = set(
            sudo_docker("ps", "-a", "--format", "{{.Names}}", capture=True).stdout.splitlines()
        )
        conflicts = sorted(existing.intersection(self.containers.values()))
        if conflicts and not self.args.resume:
            raise ExperimentError(f"scale containers already exist: {conflicts}; use --resume")
        log(f"scale preflight passed: load1={load_one:.1f}, seed={seed_bytes} bytes")

    def ensure_root(self) -> None:
        manifest_path = self.root / "manifest.json"
        if self.root.exists():
            if not self.args.resume:
                raise ExperimentError("scale root exists; choose another tag or use --resume")
            if not manifest_path.is_file():
                raise ExperimentError("scale root has no manifest")
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            # JSON serializes tuples as arrays.  Normalize the in-memory
            # manifest through JSON before comparing it with the saved file.
            expected = json.loads(json.dumps(self.manifest()))
            if existing != expected:
                raise ExperimentError("resume manifest does not match")
            return
        run(("sudo", "-n", "mkdir", "-p", str(self.root)))
        run(("sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(self.root)))
        atomic_json(manifest_path, self.manifest())

    def copy_pgdata(self, lane: str) -> None:
        destination = self.pgdata[lane]
        marker = self.root / "copy_markers" / f"{lane}.complete"
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        run(("sudo", "-n", "mkdir", "-p", str(destination)))
        run(("sudo", "-n", "cp", "-a", f"{self.args.seed_pgdata}/.", str(destination)))
        if not self.sudo_test("-f", destination / "PG_VERSION"):
            raise ExperimentError(f"copied PGDATA is incomplete: {destination}")
        stale_pid = destination / "postmaster.pid"
        if self.sudo_test("-f", stale_pid):
            run(("sudo", "-n", "mv", str(stale_pid), str(destination / "postmaster.pid.seed_stale")))
        marker.write_text(f"completed_at={time.time()}\n", encoding="utf-8")

    def write_props(self, lane: str, source: Path, kind: str, minutes: int) -> Path:
        destination = self.config_dir / f"{lane}_{kind}.props"
        text = source.read_text(encoding="utf-8")
        text = re.sub(
            r"127\.0\.0\.1:\d+", f"127.0.0.1:{self.ports[lane]}", text
        )
        text = re.sub(r"(?m)^xpRunMins=\d+$", f"xpRunMins={minutes}", text)
        text = text.replace(
            "ApplicationName=resource-adaptive-matrix",
            f"ApplicationName=hyxp-scale-{lane}",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        return destination

    def validate_props(self, path: Path, lane: str, minutes: int) -> None:
        content = path.read_text(encoding="utf-8")
        required = (
            "xtpclient=16", "xapclient=6", f"xpRunMins={minutes}",
            f"127.0.0.1:{self.ports[lane]}/hybench_sf10x",
        )
        if any(item not in content for item in required):
            raise ExperimentError(f"invalid XP configuration: {path}")
        if re.search(r"(?m)^ap_query_(ids|weights|sequence)=", content):
            raise ExperimentError(f"query-selection customization is forbidden: {path}")

    def create_containers(self) -> None:
        existing = set(
            sudo_docker("ps", "-a", "--format", "{{.Names}}", capture=True).stdout.splitlines()
        )
        for item, lane in enumerate(self.lanes, start=1):
            if self.containers[lane] in existing:
                continue
            allocation = self.cpu_allocation(item)
            sudo_docker(
                "create", "--name", self.containers[lane], "--network", "host",
                "--cpuset-cpus", allocation["db_cpuset"],
                "--cpuset-mems", str(allocation["node"]),
                "--cpus", str(DB_CPU_QUOTA),
                "--memory", str(MEMORY_BYTES), "--memory-swap", str(MEMORY_BYTES),
                "--shm-size", "8g", "--pids-limit", "1024",
                "-e", "POSTGRES_PASSWORD=postgres",
                "-v", f"{self.pgdata[lane]}:/var/lib/postgresql/data",
                IMAGE, "postgres", "-c", f"port={self.ports[lane]}",
                "-c", "listen_addresses=*", "-c",
                "shared_preload_libraries=pg_stat_statements",
            )

    def initialize(self) -> None:
        self.ensure_root()
        initialized = self.root / "initialized.json"
        if initialized.exists():
            log("scale initialization already complete")
            return
        for lane in self.lanes:
            log(f"copy PGDATA {lane}/{self.max_count}")
            self.copy_pgdata(lane)
            warmup = self.write_props(lane, WARMUP_SOURCE, "warmup", self.args.warmup_minutes)
            measure = self.write_props(
                lane, MEASUREMENT_SOURCE, "measure", self.args.measurement_minutes
            )
            self.validate_props(warmup, lane, self.args.warmup_minutes)
            self.validate_props(measure, lane, self.args.measurement_minutes)
        self.create_containers()
        atomic_json(initialized, {"initialized_at": time.time(), "manifest": self.manifest()})
        log("scale initialization complete")

    def active_lanes(self, count: int) -> Tuple[str, ...]:
        return self.lanes[:count]

    def stop_all(self) -> None:
        sudo_docker("stop", *self.containers.values(), check=False, capture=True)

    def wait_ready(self, lane: str) -> None:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = sudo_docker(
                "exec", self.containers[lane], "pg_isready", "-h", "127.0.0.1",
                "-p", str(self.ports[lane]), check=False, capture=True,
            )
            if result.returncode == 0:
                return
            time.sleep(2)
        raise ExperimentError(f"PostgreSQL did not become ready: {lane}")

    def psql(self, lane: str, database: str, sql: str, output: Optional[TextIO] = None) -> None:
        sudo_docker(
            "exec", self.containers[lane], "psql", "-X", "-v", "ON_ERROR_STOP=1",
            "-U", "postgres", "-p", str(self.ports[lane]), "-d", database,
            "-P", "pager=off", "-c", sql, output=output,
        )

    def reset_database(self, lane: str) -> None:
        self.psql(
            lane, "postgres",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname='hybench_sf10x' AND pid <> pg_backend_pid();",
        )
        # PostgreSQL executes multiple statements passed through one psql -c as
        # a single transaction.  DROP DATABASE is forbidden in a transaction,
        # so it must be sent in its own psql invocation.
        self.psql(
            lane, "postgres",
            "DROP DATABASE IF EXISTS hybench_sf10x WITH (FORCE);",
        )
        self.psql(
            lane, "postgres",
            "CREATE DATABASE hybench_sf10x WITH TEMPLATE hybench_sf10x_base OWNER postgres;",
        )
        self.psql(
            lane, "hybench_sf10x",
            "CREATE EXTENSION IF NOT EXISTS pg_stat_statements; CHECKPOINT;",
        )

    def run_parallel(self, function: Any, lanes: Iterable[str], label: str) -> None:
        lane_list = list(lanes)
        with ThreadPoolExecutor(max_workers=len(lane_list)) as executor:
            futures = {lane: executor.submit(function, lane) for lane in lane_list}
            failures = {}
            for lane, future in futures.items():
                try:
                    future.result()
                except Exception as exc:
                    failures[lane] = str(exc)
        if failures:
            raise ExperimentError(f"parallel {label} failures: {failures}")

    def prepare_runtime(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("lib", "conf", "Data_10x"):
            source = HYBENCH_HOME / name
            destination = directory / name
            if destination.is_symlink():
                if destination.resolve() != source.resolve():
                    raise ExperimentError(f"wrong runtime link: {destination}")
            elif destination.exists():
                raise ExperimentError(f"runtime resource is not a link: {destination}")
            else:
                destination.symlink_to(source, target_is_directory=True)

    def start_workloads(
        self, lanes: Iterable[str], wave_dir: Path, kind: str
    ) -> Dict[str, ProcessInfo]:
        processes: Dict[str, ProcessInfo] = {}
        for item, lane in enumerate(self.lanes, start=1):
            if lane not in lanes:
                continue
            directory = wave_dir / lane / kind
            self.prepare_runtime(directory)
            console = (directory / "console.log").open("w", encoding="utf-8")
            allocation = self.cpu_allocation(item)
            config = self.config_dir / f"{lane}_{kind}.props"
            command = (
                "taskset", "-c", allocation["client_cpuset"], "java", "-cp",
                f"{directory}/lib/*:{directory}", "com.hybench.HyBench", "-t", "runxp",
                "-c", str(config), "-f", str(STATEMENTS),
            )
            process = subprocess.Popen(
                command, cwd=directory, stdout=console, stderr=subprocess.STDOUT,
                universal_newlines=True, start_new_session=True,
            )
            processes[lane] = (process, console, directory)
        return processes

    def validate_log(self, directory: Path) -> Dict[str, float]:
        console = directory / "console.log"
        content = console.read_text(encoding="utf-8", errors="replace")
        fatal = [pattern for pattern in FATAL_PATTERNS if pattern in content]
        if fatal:
            raise ExperimentError(f"fatal HyBench patterns in {directory}: {fatal}")
        if "XP Workload is done" not in content:
            raise ExperimentError(f"HyBench did not finish in {directory}")
        qps = QPS_PATTERN.findall(content)
        tps = TPS_PATTERN.findall(content)
        if not qps or not tps:
            raise ExperimentError(f"missing final QPS/TPS in {directory}")
        return {"qps": float(qps[-1]), "tps": float(tps[-1])}

    def wait_workloads(
        self, processes: Mapping[str, ProcessInfo], timeout_seconds: int
    ) -> Dict[str, Dict[str, float]]:
        deadline = time.monotonic() + timeout_seconds
        returncodes: Dict[str, int] = {}
        try:
            while len(returncodes) < len(processes):
                if time.monotonic() >= deadline:
                    raise ExperimentError("HyBench wave timed out")
                for lane, (process, _, _) in processes.items():
                    if lane not in returncodes and process.poll() is not None:
                        returncodes[lane] = int(process.returncode)
                time.sleep(1)
        finally:
            for process, console, _ in processes.values():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                console.close()
        failures = {lane: code for lane, code in returncodes.items() if code != 0}
        if failures:
            raise ExperimentError(f"HyBench process failures: {failures}")
        return {
            lane: self.validate_log(directory)
            for lane, (_, _, directory) in processes.items()
        }

    def snapshot(self, lane: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(f"timestamp={time.time()}\n")
            self.psql(
                lane, "hybench_sf10x",
                "SELECT datname,xact_commit,xact_rollback,blks_read,blks_hit,temp_files,"
                "temp_bytes,blk_read_time,blk_write_time,deadlocks FROM pg_stat_database "
                "WHERE datname='hybench_sf10x'; SELECT wal_records,wal_fpi,wal_bytes,"
                "wal_buffers_full,wal_write,wal_sync,wal_write_time,wal_sync_time "
                "FROM pg_stat_wal;",
                output=handle,
            )

    def start_monitor(
        self, command: Sequence[str], output_path: Path, sudo_stop: bool = False
    ) -> MonitorInfo:
        handle = output_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            list(command), stdout=handle, stderr=subprocess.STDOUT,
            universal_newlines=True, start_new_session=True,
        )
        return process, handle, sudo_stop

    def stop_monitor(self, monitor: MonitorInfo) -> None:
        process, handle, sudo_stop = monitor
        if process.poll() is None:
            if sudo_stop:
                run(
                    ("sudo", "-n", "kill", "-TERM", "--", f"-{process.pid}"),
                    check=False, capture=True,
                )
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                run(
                    ("sudo", "-n", "kill", "-KILL", "--", f"-{process.pid}"),
                    check=False, capture=True,
                )
                process.wait(timeout=15)
        handle.close()

    def capture_docker_inspect(self, lanes: Sequence[str], output_path: Path) -> None:
        """Capture container metadata for the active lanes."""
        with output_path.open("w", encoding="utf-8") as handle:
            sudo_docker(
                "inspect", *(self.containers[lane] for lane in lanes), output=handle
            )

    @staticmethod
    def parse_iostat(path: Path) -> Dict[str, float]:
        samples: List[Tuple[float, float, float, float, float]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if len(fields) == 21 and fields[0] == "nvme1n1":
                samples.append(
                    (float(fields[2]), float(fields[8]), float(fields[5]),
                     float(fields[11]), float(fields[20]))
                )
        if not samples:
            raise ExperimentError(f"no nvme1n1 samples in {path}")
        return {
            "read_kib_s_mean": statistics.mean(item[0] for item in samples),
            "write_kib_s_mean": statistics.mean(item[1] for item in samples),
            "read_await_ms_mean": statistics.mean(item[2] for item in samples),
            "write_await_ms_mean": statistics.mean(item[3] for item in samples),
            "util_percent_mean": statistics.mean(item[4] for item in samples),
            "util_percent_max": max(item[4] for item in samples),
            "samples": float(len(samples)),
        }

    def archive_incomplete(self, wave_dir: Path) -> None:
        if wave_dir.exists() and any(wave_dir.iterdir()):
            archived = wave_dir.with_name(
                wave_dir.name + ".incomplete." + time.strftime("%Y%m%dT%H%M%S")
            )
            wave_dir.rename(archived)

    def run_wave(self, count: int, repeat: int) -> None:
        wave_dir = self.root / "results" / f"N{count:02d}_rep{repeat:02d}"
        if (wave_dir / "completed.json").exists():
            log(f"skip completed count={count} repeat={repeat}")
            return
        self.archive_incomplete(wave_dir)
        (wave_dir / "monitor").mkdir(parents=True)
        lanes = self.active_lanes(count)
        started_at = time.time()
        log(f"wave start count={count} repeat={repeat}")

        self.stop_all()
        sudo_docker("start", *(self.containers[lane] for lane in lanes))
        self.run_parallel(self.wait_ready, lanes, "readiness")
        self.run_parallel(self.reset_database, lanes, "database reset")
        if self.args.cooldown_seconds:
            log(f"cooldown {self.args.cooldown_seconds}s")
            time.sleep(self.args.cooldown_seconds)

        warmup = self.start_workloads(lanes, wave_dir, "warmup")
        self.wait_workloads(warmup, self.args.warmup_minutes * 60 + 300)
        self.run_parallel(
            lambda lane: self.snapshot(lane, wave_dir / lane / "db_before.txt"),
            lanes, "before snapshot",
        )

        monitor_duration = self.args.measurement_minutes * 60 + 300
        iostat_monitor = self.start_monitor(
            ("timeout", str(monitor_duration), "iostat", "-x", "-d", "-y", "1", "nvme1n1"),
            wave_dir / "monitor" / "iostat.log",
        )
        docker_monitor = self.start_monitor(
            (
                "sudo", "-n", "docker", "stats", "--format",
                "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.BlockIO}}",
                *(self.containers[lane] for lane in lanes),
            ),
            wave_dir / "monitor" / "docker_stats.csv", sudo_stop=True,
        )
        self.capture_docker_inspect(
            lanes, wave_dir / "monitor" / "docker_inspect.json"
        )
        try:
            launch_started = time.time()
            measurement = self.start_workloads(lanes, wave_dir, "measure")
            launch_finished = time.time()
            metrics = self.wait_workloads(
                measurement, self.args.measurement_minutes * 60 + 300
            )
        finally:
            self.stop_monitor(iostat_monitor)
            self.stop_monitor(docker_monitor)

        self.run_parallel(
            lambda lane: self.snapshot(lane, wave_dir / lane / "db_after.txt"),
            lanes, "after snapshot",
        )
        io_metrics = self.parse_iostat(wave_dir / "monitor" / "iostat.log")
        self.stop_all()
        atomic_json(
            wave_dir / "completed.json",
            {
                "count": count,
                "repeat": repeat,
                "lanes": list(lanes),
                "started_at": started_at,
                "finished_at": time.time(),
                "launch_skew_seconds": launch_finished - launch_started,
                "metrics": metrics,
                "io": io_metrics,
            },
        )
        log(f"wave complete count={count} repeat={repeat}")

    def run_experiment(self) -> None:
        if not (self.root / "initialized.json").exists():
            raise ExperimentError("scale experiment is not initialized")
        atomic_json(self.root / "runtime_manifest.json", self.manifest())
        try:
            for repeat, order in enumerate(self.count_orders(), start=1):
                for count in order:
                    self.run_wave(count, repeat)
        finally:
            self.stop_all()
        self.summarize()

    def summarize(self) -> None:
        records: Dict[int, List[Dict[str, Any]]] = {count: [] for count in self.counts}
        for completed in sorted((self.root / "results").glob("N*_rep*/completed.json")):
            value = json.loads(completed.read_text(encoding="utf-8"))
            if value["count"] in records:
                records[value["count"]].append(value)

        baseline_count = self.counts[0]
        baseline_by_repeat = {
            item["repeat"]: item for item in records.get(baseline_count, [])
        }
        summary: Dict[str, Any] = {"tag": self.args.tag, "counts": {}, "upper_limit": None}
        previous_total_qps: Optional[float] = None
        previous_total_tps: Optional[float] = None
        valid_counts: List[int] = []
        still_below_limit = True
        first_failure: Optional[Dict[str, Any]] = None
        for count in self.counts:
            waves = records[count]
            item: Dict[str, Any] = {"valid_repeats": len(waves)}
            if not waves:
                summary["counts"][str(count)] = item
                if still_below_limit:
                    still_below_limit = False
                    first_failure = {"count": count, "reasons": ["missing completed waves"]}
                continue
            per_wave = []
            for wave in waves:
                qps_values = [float(value["qps"]) for value in wave["metrics"].values()]
                tps_values = [float(value["tps"]) for value in wave["metrics"].values()]
                baseline = baseline_by_repeat.get(wave["repeat"])
                baseline_qps = None
                baseline_tps = None
                if baseline:
                    first = next(iter(baseline["metrics"].values()))
                    baseline_qps = float(first["qps"])
                    baseline_tps = float(first["tps"])
                per_wave.append(
                    {
                        "repeat": wave["repeat"],
                        "per_instance_qps": statistics.median(qps_values),
                        "per_instance_tps": statistics.median(tps_values),
                        "total_qps": sum(qps_values),
                        "total_tps": sum(tps_values),
                        "qps_retention": statistics.median(qps_values) / baseline_qps if baseline_qps else None,
                        "tps_retention": statistics.median(tps_values) / baseline_tps if baseline_tps else None,
                        "io_util_mean": float(wave["io"]["util_percent_mean"]),
                        "io_util_max": float(wave["io"]["util_percent_max"]),
                    }
                )
            numeric_keys = (
                "per_instance_qps", "per_instance_tps", "total_qps", "total_tps",
                "qps_retention", "tps_retention", "io_util_mean", "io_util_max",
            )
            for key in numeric_keys:
                values = [float(wave[key]) for wave in per_wave if wave[key] is not None]
                item[key + "_median"] = statistics.median(values) if values else None
            item["per_repeat"] = per_wave
            if previous_total_qps is not None:
                item["marginal_qps_gain"] = item["total_qps_median"] / previous_total_qps - 1
                item["marginal_tps_gain"] = item["total_tps_median"] / previous_total_tps - 1
            previous_total_qps = item["total_qps_median"]
            previous_total_tps = item["total_tps_median"]
            reasons = []
            if len(waves) != self.args.repeats:
                reasons.append("incomplete repeats")
            if (item["qps_retention_median"] or 0) < 0.90:
                reasons.append("QPS retention below 90%")
            if (item["tps_retention_median"] or 0) < 0.90:
                reasons.append("TPS retention below 90%")
            if item["io_util_mean_median"] >= 85.0:
                reasons.append("SSD mean utilization reached 85%")
            if count != 1 and (
                item.get("marginal_qps_gain", 0) < 0.10
                or item.get("marginal_tps_gain", 0) < 0.10
            ):
                reasons.append("marginal total-throughput gain below 10%")
            item["limit_failure_reasons"] = reasons
            if still_below_limit and not reasons:
                valid_counts.append(count)
            elif still_below_limit:
                still_below_limit = False
                first_failure = {"count": count, "reasons": reasons}
            summary["counts"][str(count)] = item
        summary["upper_limit"] = max(valid_counts) if valid_counts else None
        summary["first_failure"] = first_failure
        atomic_json(self.root / "summary.json", summary)

        lines = [
            "# HyBench XP  SSD ", "",
            "|  |  |  QPS |  TPS |  QPS |  TPS | QPS  | TPS  | SSD  |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for count in self.counts:
            item = summary["counts"][str(count)]
            def display(key: str, percent: bool = False) -> str:
                value = item.get(key)
                if value is None:
                    return "—"
                return f"{value * 100:.1f}%" if percent else f"{value:.2f}"
            lines.append(
                f"| {count} | {item['valid_repeats']} | {display('per_instance_qps_median')} | "
                f"{display('per_instance_tps_median')} | {display('total_qps_median')} | "
                f"{display('total_tps_median')} | {display('qps_retention_median', True)} | "
                f"{display('tps_retention_median', True)} | {display('io_util_mean_median')}% |"
            )
        lines.extend(
            (
                "", f"`{summary['upper_limit']}`",
                f"`{summary['first_failure']}`", "",
            )
        )
        (self.root / "summary.md").write_text("\n".join(lines), encoding="utf-8")
        log(f"scale summary written: {self.root / 'summary.md'}")


def main() -> int:
    args = parse_args()
    experiment = ScaleExperiment(args)
    if args.phase == "plan":
        experiment.print_plan()
        return 0
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
        ExperimentError, OSError, subprocess.CalledProcessError,
        subprocess.TimeoutExpired, KeyboardInterrupt,
    ) as exc:
        log(f"FAILED: {exc}")
        experiment.stop_all()
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
