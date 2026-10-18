#!/usr/bin/env python3
"""Run a HyBench XP same-SSD concurrency sweep on remote hosts.

The default CPU layout uses one exclusive physical core for PostgreSQL and one
for the HyBench client per lane.  The ``shared-smt`` layout gives each lane one
exclusive physical core and places PostgreSQL and HyBench on its two sibling
threads, allowing larger sweeps without sharing a physical core between lanes.
PGDATA is restored offline from the frozen SF10x seed before every wave.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
from typing import Any, Dict, List, Mapping, Optional, Sequence, TextIO, Tuple
import zipfile

import run_hybench_xp_same_ssd_scale_experiment as base


DEFAULT_COUNTS = (1, 4, 8, 12, 16)
MEMORY_BYTES = 4 * 1024**3
DISK_HEADROOM_BYTES = 20 * 1024**3
PORT_BASE = 59700


def parse_remote_counts(raw: str) -> Tuple[int, ...]:
    try:
        counts = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("counts must be comma-separated integers") from exc
    if not counts or any(item < 1 or item > 64 for item in counts):
        raise argparse.ArgumentTypeError("counts must stay in range 1..64")
    if tuple(sorted(counts)) != counts or len(set(counts)) != len(counts):
        raise argparse.ArgumentTypeError("counts must be unique and ascending")
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("plan", "preflight", "init", "run", "summarize", "all"))
    parser.add_argument("--tag", default="20260809_remote_scale_v1")
    parser.add_argument("--target-mount", type=Path, required=True)
    parser.add_argument("--device", required=True, help="iostat device name, for example nvme2n1")
    parser.add_argument("--seed-pgdata", type=Path, required=True)
    parser.add_argument("--hybench-assets", type=Path, required=True)
    parser.add_argument("--image", default="postgres:17")
    parser.add_argument(
        "--java-bin",
        default=os.environ.get("HYXP_JAVA_BIN", "java"),
        help="Java 16+ executable used by the HyBench client",
    )
    parser.add_argument("--counts", type=parse_remote_counts, default=DEFAULT_COUNTS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup-minutes", type=int, default=1)
    parser.add_argument("--measurement-minutes", type=int, default=10)
    parser.add_argument("--cooldown-seconds", type=int, default=30)
    parser.add_argument("--memory-gib", type=float, default=4.0)
    parser.add_argument(
        "--memory-bytes",
        type=int,
        help="Exact container memory limit; overrides --memory-gib when provided",
    )
    parser.add_argument("--db-physical-cores", type=int, default=1)
    parser.add_argument("--client-physical-cores", type=int, default=1)
    parser.add_argument("--db-cpu-quota", type=float, default=1.0)
    parser.add_argument("--max-host-load", type=float, default=16.0)
    parser.add_argument(
        "--cpu-layout", choices=("exclusive-pair", "shared-smt"), default="exclusive-pair"
    )
    parser.add_argument(
        "--restore-copy-mode", choices=("copy", "reflink"), default="copy"
    )
    parser.add_argument("--allow-busy-host", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def docker(*arguments: str, **kwargs: Any) -> subprocess.CompletedProcess:
    return base.run(("docker", *arguments), **kwargs)


def parse_cpu_list(raw: str) -> List[int]:
    values: List[int] = []
    for part in raw.strip().split(","):
        if not part:
            continue
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            values.extend(range(start, end + 1))
        else:
            values.append(int(part))
    return values


class RemoteScaleExperiment(base.ScaleExperiment):
    """Scale experiment with dynamic physical-core placement and direct Docker access."""

    def __init__(self, args: argparse.Namespace) -> None:
        if args.memory_gib <= 0 or (args.memory_bytes is not None and args.memory_bytes <= 0):
            raise base.ExperimentError("container memory limit must be positive")
        if args.db_physical_cores <= 0 or args.client_physical_cores <= 0:
            raise base.ExperimentError("physical core counts must be positive")
        if args.db_cpu_quota <= 0:
            raise base.ExperimentError("db-cpu-quota must be positive")
        if args.cpu_layout == "shared-smt" and (
            args.db_physical_cores != 1 or args.client_physical_cores != 1
        ):
            raise base.ExperimentError(
                "shared-smt requires one database core and one client core declaration"
            )
        self.memory_bytes = (
            int(args.memory_bytes)
            if args.memory_bytes is not None
            else int(args.memory_gib * 1024**3)
        )
        self.db_physical_cores = int(args.db_physical_cores)
        self.client_physical_cores = int(args.client_physical_cores)
        self.db_cpu_quota = float(args.db_cpu_quota)
        self.target_mount = args.target_mount.resolve()
        self.device = args.device[5:] if args.device.startswith("/dev/") else args.device
        self.assets = args.hybench_assets.resolve()
        self.jar = self.assets / "HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"
        self.statements = self.assets / "stmt_postgres.toml"
        self.image = args.image
        self.java_bin = args.java_bin
        lib_dir = os.environ.get("HYXP_LIB_DIR")
        self.lib_dir = Path(lib_dir).resolve() if lib_dir else None
        conf_dir = os.environ.get("HYXP_CONF_DIR")
        if conf_dir:
            self.conf_dir = Path(conf_dir).resolve()
        elif self.lib_dir is not None:
            self.conf_dir = self.lib_dir.parent / "conf"
        else:
            self.conf_dir = None

        base.IMAGE = self.image
        base.MEMORY_BYTES = self.memory_bytes
        base.DB_CPU_QUOTA = self.db_cpu_quota
        base.DB_PHYSICAL_CPUS = self.db_physical_cores
        base.CLIENT_PHYSICAL_CPUS = self.client_physical_cores
        base.PORT_BASE = PORT_BASE
        base.LOCK_PATH = Path(f"/tmp/hybench_xp_remote_ssd_scale_{socket.gethostname()}.lock")
        base.sudo_docker = docker

        super().__init__(args)
        self.root = self.target_mount / "hyxp_same_ssd_remote" / args.tag
        self.pgdata = {lane: self.root / "pgdata" / lane for lane in self.lanes}
        self.config_dir = self.root / "config"
        self._allocations = self._build_allocations()

    def _physical_cores_by_node(self) -> Dict[int, List[int]]:
        sibling_groups: Dict[Tuple[int, ...], int] = {}
        for topology in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/topology/thread_siblings_list")):
            siblings = tuple(parse_cpu_list(topology.read_text(encoding="utf-8")))
            if siblings:
                sibling_groups.setdefault(siblings, min(siblings))

        nodes: Dict[int, List[int]] = {}
        for node_path in sorted(Path("/sys/devices/system/node").glob("node[0-9]*")):
            node = int(node_path.name[4:] if node_path.name.startswith("node") else node_path.name)
            node_cpus = set(parse_cpu_list((node_path / "cpulist").read_text(encoding="utf-8")))
            nodes[node] = sorted(cpu for cpu in sibling_groups.values() if cpu in node_cpus)
        if not nodes:
            nodes[0] = sorted(sibling_groups.values())
        return {node: cpus for node, cpus in nodes.items() if cpus}

    def _smt_cores_by_node(self) -> Dict[int, List[Tuple[int, ...]]]:
        sibling_groups = set()
        for topology in sorted(
            Path("/sys/devices/system/cpu").glob("cpu[0-9]*/topology/thread_siblings_list")
        ):
            siblings = tuple(parse_cpu_list(topology.read_text(encoding="utf-8")))
            if siblings:
                sibling_groups.add(siblings)

        nodes: Dict[int, List[Tuple[int, ...]]] = {}
        for node_path in sorted(Path("/sys/devices/system/node").glob("node[0-9]*")):
            node = int(node_path.name[4:] if node_path.name.startswith("node") else node_path.name)
            node_cpus = set(parse_cpu_list((node_path / "cpulist").read_text(encoding="utf-8")))
            nodes[node] = sorted(
                (siblings for siblings in sibling_groups if siblings[0] in node_cpus),
                key=lambda siblings: siblings[0],
            )
        if not nodes:
            nodes[0] = sorted(sibling_groups, key=lambda siblings: siblings[0])
        return {node: cores for node, cores in nodes.items() if cores}

    def _build_allocations(self) -> Dict[int, Dict[str, Any]]:
        if self.args.cpu_layout == "shared-smt":
            by_node = self._smt_cores_by_node()
            node_ids = sorted(by_node)
            allocations: Dict[int, Dict[str, Any]] = {}
            used_by_node = {node: 0 for node in node_ids}
            for item in range(1, self.max_count + 1):
                node = node_ids[(item - 1) % len(node_ids)]
                offset = used_by_node[node]
                if offset >= len(by_node[node]):
                    raise base.ExperimentError(
                        f"node{node} lacks an exclusive SMT core for lane {item}"
                    )
                siblings = by_node[node][offset]
                if len(siblings) < 2:
                    raise base.ExperimentError(
                        f"physical core {siblings} has fewer than two online threads"
                    )
                db_cpu, client_cpu = siblings[:2]
                used_by_node[node] += 1
                allocations[item] = {
                    "node": node,
                    "physical_core_threads": siblings,
                    "db_cpus": (db_cpu,),
                    "client_cpus": (client_cpu,),
                    "db_cpuset": str(db_cpu),
                    "client_cpuset": str(client_cpu),
                }
            return allocations

        by_node = self._physical_cores_by_node()
        node_ids = sorted(by_node)
        allocations: Dict[int, Dict[str, Any]] = {}
        used_by_node = {node: 0 for node in node_ids}
        for item in range(1, self.max_count + 1):
            node = node_ids[(item - 1) % len(node_ids)]
            offset = used_by_node[node]
            cores_per_lane = self.db_physical_cores + self.client_physical_cores
            if offset + cores_per_lane > len(by_node[node]):
                raise base.ExperimentError(
                    f"node{node} lacks {cores_per_lane} exclusive physical cores for lane {item}"
                )
            db_cpus = tuple(by_node[node][offset:offset + self.db_physical_cores])
            client_start = offset + self.db_physical_cores
            client_cpus = tuple(
                by_node[node][client_start:client_start + self.client_physical_cores]
            )
            used_by_node[node] += cores_per_lane
            allocations[item] = {
                "node": node,
                "db_cpus": db_cpus,
                "client_cpus": client_cpus,
                "db_cpuset": ",".join(str(cpu) for cpu in db_cpus),
                "client_cpuset": ",".join(str(cpu) for cpu in client_cpus),
            }
        return allocations

    def cpu_allocation(self, item: int) -> Dict[str, Any]:
        return self._allocations[item]

    def manifest(self) -> Dict[str, Any]:
        value = super().manifest()
        profile = (
            f"remote-exclusive-{self.db_physical_cores}dbcore-"
            f"{self.client_physical_cores}clientcore-{self.memory_bytes}bytes"
        )
        if self.args.cpu_layout == "shared-smt":
            profile = (
                "remote-gt16-1physicalcore-smt-split-"
                f"{self.args.memory_gib:g}g"
            )
        value.update(
            {
                "schema_version": 2,
                "profile": profile,
                "root": str(self.root),
                "target_mount": str(self.target_mount),
                "device": self.device,
                "seed_pgdata": str(self.args.seed_pgdata.resolve()),
                "hybench_assets": str(self.assets),
                "hybench_jar": str(self.jar),
                "statements": str(self.statements),
                "java_bin": str(Path(shutil.which(self.java_bin) or self.java_bin).resolve()),
                "image": self.image,
                "memory_bytes_per_container": self.memory_bytes,
                "db_cpu_quota": self.db_cpu_quota,
                "db_physical_cores_per_container": self.db_physical_cores,
                "client_physical_cores_per_lane": self.client_physical_cores,
                "reset_policy": "offline full PGDATA restore before every wave",
            }
        )
        if self.args.cpu_layout != "exclusive-pair":
            value["cpu_layout"] = self.args.cpu_layout
        if self.args.restore_copy_mode != "copy":
            value["restore_copy_mode"] = self.args.restore_copy_mode
        value.pop("hybench_home", None)
        return value

    def sudo_test(self, flag: str, path: Path) -> bool:
        return base.run(("test", flag, str(path)), check=False, capture=True).returncode == 0

    def validate_java_runtime(self) -> Tuple[int, int]:
        """validate java runtime."""
        version_result = base.run(
            (self.java_bin, "-version"), check=False, capture=True
        )
        version_output = "\n".join(
            value for value in (version_result.stdout, version_result.stderr) if value
        )
        if version_result.returncode != 0:
            raise base.ExperimentError(
                f"Java version check failed for {self.java_bin}: {version_output.strip()}"
            )
        match = re.search(r'version\s+"(?:1\.)?(\d+)', version_output)
        if match is None:
            raise base.ExperimentError(
                f"cannot parse Java feature version from: {version_output.strip()}"
            )
        java_feature = int(match.group(1))

        with zipfile.ZipFile(str(self.jar)) as archive:
            class_data = archive.read("com/hybench/HyBench.class")
        if len(class_data) < 8 or class_data[:4] != b"\xca\xfe\xba\xbe":
            raise base.ExperimentError("invalid HyBench main class header")
        class_major = int.from_bytes(class_data[6:8], byteorder="big")
        minimum_java_feature = class_major - 44
        if java_feature < minimum_java_feature:
            raise base.ExperimentError(
                f"incompatible Java {java_feature}: HyBench class version {class_major} "
                f"requires Java {minimum_java_feature}+"
            )
        return java_feature, class_major

    def preflight(self) -> None:
        base.log("remote scale preflight started")
        for executable in ("docker", "iostat", "taskset", "timeout", "findmnt"):
            if shutil.which(executable) is None:
                raise base.ExperimentError(f"missing executable: {executable}")
        if shutil.which(self.java_bin) is None:
            raise base.ExperimentError(f"missing Java executable: {self.java_bin}")
        if self.lib_dir is not None and not (self.lib_dir / "HyBench-1.0-SNAPSHOT.jar").is_file():
            raise base.ExperimentError(f"invalid HyBench lib directory: {self.lib_dir}")
        if self.lib_dir is not None and (
            self.conf_dir is None or not (self.conf_dir / "log4j2.properties").is_file()
        ):
            raise base.ExperimentError(f"invalid HyBench conf directory: {self.conf_dir}")
        docker("info", capture=True)
        docker("image", "inspect", self.image, capture=True)

        for required in (
            self.target_mount,
            self.args.seed_pgdata,
            self.jar,
            self.statements,
            self.assets / "Data_10x/Related_transfer_bids",
            self.assets / "Data_10x/Related_checking_bids",
        ):
            if not required.exists():
                raise base.ExperimentError(f"missing required path: {required}")
        java_feature, class_major = self.validate_java_runtime()
        if not (self.args.seed_pgdata / "PG_VERSION").is_file():
            raise base.ExperimentError(f"invalid frozen PGDATA: {self.args.seed_pgdata}")
        if not os.access(self.target_mount, os.W_OK):
            raise base.ExperimentError(f"target mount is not writable: {self.target_mount}")

        all_cpus: List[int] = []
        for allocation in self._allocations.values():
            all_cpus.extend(allocation["db_cpus"])
            all_cpus.extend(allocation["client_cpus"])
        if len(all_cpus) != len(set(all_cpus)):
            raise base.ExperimentError("physical CPU allocation overlaps between lanes")

        mem_available = int(
            base.run(("awk", "/MemAvailable/ {print $2 * 1024}", "/proc/meminfo"), capture=True)
            .stdout.strip()
        )
        mem_required = self.max_count * self.memory_bytes + 16 * 1024**3
        if mem_available < mem_required:
            raise base.ExperimentError(
                f"insufficient memory: need {mem_required}, available {mem_available}"
            )

        seed_bytes = int(
            base.run(("du", "-sb", str(self.args.seed_pgdata)), capture=True).stdout.split()[0]
        )
        completed_copies = 0
        if self.args.resume:
            marker_dir = self.root / "copy_markers"
            completed_copies = sum(
                1 for lane in self.lanes if (marker_dir / f"{lane}.complete").is_file()
            )
        missing_copies = self.max_count - completed_copies
        if self.args.restore_copy_mode == "reflink":
            if self.args.seed_pgdata.stat().st_dev != self.target_mount.stat().st_dev:
                raise base.ExperimentError("reflink requires seed PGDATA on the target filesystem")
            disk_required = missing_copies * 256 * 1024**2 + DISK_HEADROOM_BYTES
        else:
            disk_required = seed_bytes * missing_copies + DISK_HEADROOM_BYTES
        disk_free = shutil.disk_usage(self.target_mount).free
        if disk_free < disk_required:
            raise base.ExperimentError(
                f"insufficient disk: need {disk_required}, free {disk_free}"
            )

        load_one = os.getloadavg()[0]
        if load_one > self.args.max_host_load and not self.args.allow_busy_host:
            raise base.ExperimentError(
                f"host is not quiet: load1={load_one:.1f} exceeds {self.args.max_host_load:.1f}"
            )
        process_table = base.run(("ps", "-eo", "user=,pid=,pcpu=,args="), capture=True).stdout
        if "com.hybench.HyBench" in process_table:
            raise base.ExperimentError("another HyBench process is running")
        for port in self.ports.values():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise base.ExperimentError(f"port already in use: {port}") from exc
            finally:
                sock.close()

        existing = set(docker("ps", "-a", "--format", "{{.Names}}", capture=True).stdout.splitlines())
        conflicts = sorted(existing.intersection(self.containers.values()))
        if conflicts and not self.args.resume:
            raise base.ExperimentError(f"containers already exist: {conflicts}; use --resume")
        base.log(
            f"remote preflight passed: load1={load_one:.1f}, seed={seed_bytes}, "
            f"disk_free={disk_free}, logical_cpus={len(all_cpus)} reserved, "
            f"java={java_feature}, hybench_class={class_major}"
        )

    def ensure_root(self) -> None:
        manifest_path = self.root / "manifest.json"
        if self.root.exists():
            if not self.args.resume:
                raise base.ExperimentError("experiment root exists; choose another tag or use --resume")
            if not manifest_path.is_file():
                raise base.ExperimentError("experiment root has no manifest")
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = json.loads(json.dumps(self.manifest()))
            if existing != expected:
                raise base.ExperimentError("resume manifest does not match")
            return
        self.root.mkdir(parents=True)
        base.atomic_json(manifest_path, self.manifest())

    def restore_pgdata(self, lane: str) -> None:
        destination = self.pgdata[lane]
        if destination.parent != self.root / "pgdata":
            raise base.ExperimentError(f"unsafe PGDATA destination: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        if self.args.restore_copy_mode == "reflink":
            source_relative = self.args.seed_pgdata.resolve().relative_to(self.target_mount)
            destination_relative = destination.relative_to(self.target_mount)
            script = (
                "source=$1; dest=$2; "
                "find \"$dest\" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; "
                "cp -a --reflink=always \"$source/.\" \"$dest/\"; "
                "rm -f \"$dest/postmaster.pid\" \"$dest/standby.signal\"; "
                "chown -R postgres:postgres \"$dest\"; "
                "chmod -R u+rwX \"$dest\""
            )
            docker(
                "run", "--rm", "--network", "none",
                "-v", f"{self.target_mount}:/target",
                self.image, "sh", "-ceu", script, "sh",
                f"/target/{source_relative}", f"/target/{destination_relative}",
            )
        else:
            script = (
                "find /dest -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; "
                "cp -a /source/. /dest/; "
                "rm -f /dest/postmaster.pid /dest/standby.signal; "
                "chown -R postgres:postgres /dest; "
                "chmod -R u+rwX /dest"
            )
            docker(
                "run", "--rm", "--network", "none",
                "-v", f"{self.args.seed_pgdata.resolve()}:/source:ro",
                "-v", f"{destination}:/dest", self.image, "sh", "-ceu", script,
            )
        validation = docker(
            "run", "--rm", "--network", "none", "-v", f"{destination}:/dest:ro",
            self.image, "test", "-f", "/dest/PG_VERSION", check=False, capture=True,
        )
        if validation.returncode != 0:
            raise base.ExperimentError(f"PGDATA restore failed: {destination}")

    def copy_pgdata(self, lane: str) -> None:
        marker = self.root / "copy_markers" / f"{lane}.complete"
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        self.restore_pgdata(lane)
        marker.write_text("complete\n", encoding="utf-8")

    def write_props(self, lane: str, source: Path, kind: str, minutes: int) -> Path:
        _ = source
        destination = self.config_dir / f"{lane}_{kind}.props"
        application = f"hyxp-remote-{socket.gethostname()}-{lane}"
        url = (
            f"jdbc:postgresql://127.0.0.1:{self.ports[lane]}/hybench_sf10x"
            f"?sslmode=disable&ApplicationName={application}&reWriteBatchedInserts=true"
            "&options=-c%20statement_timeout%3D700000"
        )
        content = f"""sf=10x
db=postgreSQL
classname=org.postgresql.Driver
classname_ap=org.postgresql.Driver
username=postgres
password=CHANGE_ME
username_ap=postgres
password_ap=CHANGE_ME
url={url}
url_tp={url}
url_ap={url}
at1_percent=40
at2_percent=30
at3_percent=15
at4_percent=5
at5_percent=7
at6_percent=3
tpclient=32
apclient=8
xtpclient=16
xapclient=6
fresh_interval=20
apRunMins={minutes}
tpRunMins={minutes}
xpRunMins={minutes}
risk_rate=0.1
power_alpha=0.1
apround=1
random_seed=4508334
dist=zipfian
contention_num=10000
"""
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        return destination

    def create_containers(self) -> None:
        existing = set(docker("ps", "-a", "--format", "{{.Names}}", capture=True).stdout.splitlines())
        for item, lane in enumerate(self.lanes, start=1):
            if self.containers[lane] in existing:
                continue
            allocation = self.cpu_allocation(item)
            docker(
                "create", "--name", self.containers[lane], "--network", "host",
                "--cpuset-cpus", allocation["db_cpuset"], "--cpuset-mems", str(allocation["node"]),
                "--cpus", str(self.db_cpu_quota), "--memory", str(self.memory_bytes),
                "--memory-swap", str(self.memory_bytes),
                "--shm-size", "8g", "--pids-limit", "1024", "--stop-timeout", "30",
                "-e", "POSTGRES_PASSWORD=CHANGE_ME",
                "-v", f"{self.pgdata[lane]}:/var/lib/postgresql/data",
                self.image, "postgres", "-c", f"port={self.ports[lane]}",
                "-c", "listen_addresses=*", "-c", "shared_preload_libraries=pg_stat_statements",
            )

    def reset_database(self, lane: str) -> None:
        _ = lane

    def prepare_runtime(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        sources = {"Data_10x": self.assets / "Data_10x"}
        if self.lib_dir is not None:
            sources["lib"] = self.lib_dir
        if self.conf_dir is not None:
            sources["conf"] = self.conf_dir
        for name, source in sources.items():
            destination = directory / name
            if destination.is_symlink():
                if destination.resolve() != source.resolve():
                    raise base.ExperimentError(f"wrong runtime link: {destination}")
            elif destination.exists():
                raise base.ExperimentError(f"runtime path is not a link: {destination}")
            else:
                destination.symlink_to(source, target_is_directory=True)

    def start_workloads(
        self, lanes: Sequence[str], wave_dir: Path, kind: str
    ) -> Dict[str, base.ProcessInfo]:
        processes: Dict[str, base.ProcessInfo] = {}
        for item, lane in enumerate(self.lanes, start=1):
            if lane not in lanes:
                continue
            directory = wave_dir / lane / kind
            self.prepare_runtime(directory)
            console = (directory / "console.log").open("w", encoding="utf-8")
            allocation = self.cpu_allocation(item)
            if self.lib_dir is not None:
                classpath = f"{directory}/lib/*:{directory}"
            else:
                classpath = f"{self.jar}:{directory}"
            command = (
                "taskset", "-c", allocation["client_cpuset"], self.java_bin, "-cp", classpath,
                "com.hybench.HyBench", "-t", "runxp", "-c",
                str(self.config_dir / f"{lane}_{kind}.props"), "-f", str(self.statements),
            )
            process = subprocess.Popen(
                command, cwd=directory, stdout=console, stderr=subprocess.STDOUT,
                universal_newlines=True, start_new_session=True,
            )
            processes[lane] = (process, console, directory)
        return processes

    def start_monitor(
        self, command: Sequence[str], output_path: Path, sudo_stop: bool = False
    ) -> base.MonitorInfo:
        direct = tuple(command)
        if direct[:3] == ("sudo", "-n", "docker"):
            direct = direct[2:]
        if "iostat" in direct and direct[-1:] == ("nvme1n1",):
            direct = direct[:-1] + (self.device,)
        return super().start_monitor(direct, output_path, sudo_stop=False)

    def stop_monitor(self, monitor: base.MonitorInfo) -> None:
        process, handle, _ = monitor
        super().stop_monitor((process, handle, False))

    def capture_docker_inspect(self, lanes: Sequence[str], output_path: Path) -> None:
        """Capture Docker metadata without requiring passwordless sudo."""
        with output_path.open("w", encoding="utf-8") as handle:
            docker("inspect", *(self.containers[lane] for lane in lanes), output=handle)

    def parse_iostat(self, path: Path) -> Dict[str, float]:
        samples: List[Tuple[float, float, float, float, float]] = []
        header: Dict[str, int] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if fields and fields[0] in {"Device", "Device:"}:
                header = {name: index for index, name in enumerate(fields)}
                continue
            if not fields or fields[0] != self.device or not header:
                continue

            def metric(*names: str) -> float:
                for name in names:
                    if name in header and header[name] < len(fields):
                        return float(fields[header[name]])
                raise base.ExperimentError(f"missing iostat columns {names} in {path}")

            samples.append(
                (
                    metric("rkB/s", "rKB/s"),
                    metric("wkB/s", "wKB/s"),
                    metric("r_await"),
                    metric("w_await"),
                    metric("%util"),
                )
            )
        if not samples:
            raise base.ExperimentError(f"no {self.device} samples in {path}")
        return {
            "read_kib_s_mean": statistics.mean(item[0] for item in samples),
            "write_kib_s_mean": statistics.mean(item[1] for item in samples),
            "read_await_ms_mean": statistics.mean(item[2] for item in samples),
            "write_await_ms_mean": statistics.mean(item[3] for item in samples),
            "util_percent_mean": statistics.mean(item[4] for item in samples),
            "util_percent_max": max(item[4] for item in samples),
            "samples": float(len(samples)),
        }

    def run_wave(self, count: int, repeat: int) -> None:
        wave_dir = self.root / "results" / f"N{count:02d}_rep{repeat:02d}"
        if (wave_dir / "completed.json").exists():
            base.log(f"skip completed count={count} repeat={repeat}")
            return
        self.stop_all()
        lanes = self.active_lanes(count)
        base.log(f"offline PGDATA restore before count={count} repeat={repeat}")
        with ThreadPoolExecutor(max_workers=min(4, len(lanes))) as executor:
            futures = [executor.submit(self.restore_pgdata, lane) for lane in lanes]
            for future in futures:
                future.result()
        super().run_wave(count, repeat)


def main() -> int:
    args = parse_args()
    experiment = RemoteScaleExperiment(args)
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
        base.ExperimentError, OSError, subprocess.CalledProcessError,
        subprocess.TimeoutExpired, KeyboardInterrupt,
    ) as exc:
        base.log(f"FAILED: {exc}")
        experiment.stop_all()
        return 1
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
