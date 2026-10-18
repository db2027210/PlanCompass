#!/usr/bin/env python3
"""Build one fresh, sealed MySQL TPC-H SF4 template on /disk2.

The builder never reuses or mutates the historical SF10 template.  It waits
for a 60-second clean non-interference window, loads all eight TPC-H tables,
validates the scaled row counts and post-load indexes, shuts MySQL down
cleanly, checks the requested 9-13 GB size band, then publishes a hash-bound
storage manifest.  It does not grant Algorithm C accepted-round credit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
TARGET_ROOT = Path("/disk2/resource_adaptive_mysql_tpch_sf4_20260809")
TEMPLATE_ROOT = TARGET_ROOT / "template"
DATADIR = TEMPLATE_ROOT / "datadir"
MANIFEST = TEMPLATE_ROOT / "storage_manifest.json"
CONTAINER = "ra_mysql_tpch_sf4_builder_20260809"
PORT = 65340
DB_CPUSET = "208-215,464-471"
CLIENT_CPUSET = "224-231,480-487"
CPUSET_MEMS = "1"
IMAGE_MEMORY = 6442450944
BUFFER_POOL = 4294967296
SCALE_FACTOR = 4
MINIMUM_BYTES = 9_000_000_000
MAXIMUM_BYTES = 13_000_000_000
BENCHBASE_JAR = Path("/home/anonymous/benchbase-mysql/benchbase.jar")
OUTPUT_BASE = (
    ROOT / "history/mysql_gradient_debug/mysql_tpch_sf4_disk2_rebuild_20260809"
)
LOCK = Path("/tmp/resource_adaptive_mysql_tpch_sf4_rebuild_20260809.lock")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _run(
    argv: Sequence[str], *, timeout: float = 120.0, input_text: str | None = None,
    check: bool = True, stdout: Any = subprocess.PIPE, stderr: Any = subprocess.PIPE,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), text=True, input=input_text, stdout=stdout, stderr=stderr,
        timeout=timeout, check=check, cwd=cwd,
    )


def _docker(*args: str, timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["docker", *args], timeout=timeout, check=check)


def _process_inventory() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    completed = _run(
        ["ps", "-eo", "pid=,ppid=,etimes=,args="], timeout=10, check=True
    )
    postgres: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    allowed_data_builders: list[dict[str, Any]] = []
    own = {os.getpid(), os.getppid()}
    patterns = (
        re.compile(r"\bjava\b.*\bbenchbase(?:\.jar)?\b", re.I),
        re.compile(r"\bpsql\b", re.I),
        re.compile(r"binary_plan_resource_tuner", re.I),
        re.compile(r"mysql_benchmark_command", re.I),
        re.compile(r"(?:com\.hybench\.HyBench|\bhybench\b.*-(?:t|c)\b)", re.I),
        re.compile(r"formal_job|formal_tpch", re.I),
    )
    for line in completed.stdout.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        pid, ppid, age, command = int(fields[0]), int(fields[1]), int(fields[2]), fields[3]
        row = {"pid": pid, "ppid": ppid, "age_seconds": age, "command": command}
        if re.search(r"(?:^|/)postgres(?:\s|$)|postgres:", command):
            postgres.append(row)
        if pid not in own and any(pattern.search(command) for pattern in patterns):
            if "rebuild_mysql_tpch_sf4_disk2.py" not in command:
                # This is a data build, not a performance measurement.  A
                # PostgreSQL BenchBase create/load whose artifacts live on
                # /disk1 may coexist with this MySQL /disk2 build.  Inventory
                # it explicitly, but do not treat it as a correctness risk.
                separate_disk_pg_build = (
                    "benchbase" in command.lower()
                    and "-b tpch" in command.lower()
                    and "--create=true" in command.lower()
                    and "--load=true" in command.lower()
                    and "/home/anonymous/program/resource_adaptive/" in command
                    and "/home/anonymous/program/resource_adaptive_mysql/" not in command
                )
                if separate_disk_pg_build:
                    allowed_data_builders.append({
                        **row,
                        "classification": "allowed_cross_device_data_build",
                        "mysql_target_device": "/dev/nvme2n1",
                        "mysql_target_mount": "/disk2",
                        "performance_measurement": False,
                    })
                else:
                    conflicts.append(row)
    return postgres, conflicts, allowed_data_builders


def _wait_for_clean_window(output: Path, *, clean_seconds: int = 60) -> dict[str, Any]:
    started = time.monotonic()
    clean_since: float | None = None
    samples: list[dict[str, Any]] = []
    while time.monotonic() - started < 21600:
        postgres, conflicts, allowed_data_builders = _process_inventory()
        now = time.monotonic()
        if conflicts:
            clean_since = None
        elif clean_since is None:
            clean_since = now
        clean_elapsed = 0.0 if clean_since is None else now - clean_since
        sample = {
            "at": _now(),
            "postgresql_inventory_count": len(postgres),
            "postgresql_mutations": 0,
            "conflicts": conflicts,
            "allowed_co_resident_data_builders": allowed_data_builders,
            "clean_elapsed_seconds": clean_elapsed,
        }
        samples.append(sample)
        _atomic_json(output / "non_interference_latest.json", sample)
        if clean_elapsed >= clean_seconds:
            result = {
                "schema_version": "mysql_tpch_sf4_build_non_interference_v1",
                "passed": True,
                "clean_window_seconds": clean_seconds,
                "completed_at": _now(),
                "sample_count": len(samples),
                "postgresql_inventory": postgres,
                "postgresql_mutations": 0,
                "allowed_co_resident_data_builders": allowed_data_builders,
                "performance_measurement": False,
                "samples": samples[-120:],
            }
            _atomic_json(output / "non_interference_gate.json", result)
            return result
        time.sleep(2)
    raise RuntimeError("six-hour non-interference wait expired")


def _wait_mysql(defaults: Path, timeout_s: int = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = _run(
            ["mysql", f"--defaults-file={defaults}", "-NBe", "SELECT 1"],
            timeout=8, check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "1":
            return
        time.sleep(2)
    raise RuntimeError("fresh SF4 MySQL loader did not become ready")


def _query(defaults: Path, sql: str) -> str:
    return _run(
        ["mysql", f"--defaults-file={defaults}", "-NBe", sql], timeout=300
    ).stdout.strip()


def _tree_hash(path: Path) -> str:
    result = _docker(
        "run", "--rm", "--network", "none", "-v", f"{path}:/tree:ro",
        "--entrypoint", "/bin/sh", IMAGE, "-c",
        "cd /tree && find . -xdev -type f -print0 | LC_ALL=C sort -z "
        "| xargs -0 sha256sum | sha256sum | cut -d ' ' -f1",
        timeout=3600,
    )
    value = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RuntimeError("invalid template tree SHA-256")
    return value


def _directory_bytes(path: Path) -> int:
    result = _docker(
        "run", "--rm", "--network", "none", "-v", f"{path}:/tree:ro",
        "--entrypoint", "/bin/sh", IMAGE, "-c", "du -sb /tree | cut -f1",
        timeout=300,
    )
    return int(result.stdout.strip())


def _render_config(output: Path, password: str) -> Path:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from mysql_workload_adapter import MySQLEndpoint, generate_benchbase_xml

    config = generate_benchbase_xml(
        "tpch",
        MySQLEndpoint("127.0.0.1", PORT, "tpch", "root", password),
        output / "benchbase_config",
        duration_s=900,
        terminals=8,
        scale_factor=SCALE_FACTOR,
    )
    config.chmod(0o600)
    return config


def _build(run_id: str) -> int:
    output = OUTPUT_BASE / run_id
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = output / "run_state.json"
    if state_path.exists():
        raise RuntimeError("run id already has state; refusing duplicate execution")

    def state(status: str, **extra: Any) -> None:
        _atomic_json(state_path, {
            "schema_version": "mysql_tpch_sf4_disk2_builder_state_v1",
            "status": status,
            "updated_at": _now(),
            "pid": os.getpid(),
            "run_id": run_id,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutations": 0,
            "accepted_rounds_credit": 0,
            **extra,
        })

    handle = LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("an SF4 rebuild already owns the lock") from exc
    try:
        state("WAITING_NON_INTERFERENCE")
        _wait_for_clean_window(output)
        state("NON_INTERFERENCE_PASSED_PREPARING")
        if TARGET_ROOT.exists() or MANIFEST.exists():
            raise RuntimeError("SF4 target already exists; refusing overwrite")
        if not BENCHBASE_JAR.is_file():
            raise FileNotFoundError(BENCHBASE_JAR)
        if Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]:
            raise RuntimeError("host swap must remain disabled")
        free_bytes = os.statvfs("/disk2").f_bavail * os.statvfs("/disk2").f_frsize
        if free_bytes < 80_000_000_000:
            raise RuntimeError("insufficient /disk2 headroom for build plus two copies")
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", PORT))
            except OSError as exc:
                raise RuntimeError(f"loader port {PORT} is in use") from exc
        if _docker("ps", "-aq", "--filter", f"name=^{CONTAINER}$").stdout.strip():
            raise RuntimeError("SF4 loader container name already exists")

        DATADIR.mkdir(parents=True, mode=0o700)
        credential_dir = TEMPLATE_ROOT / "credentials"
        credential_dir.mkdir(mode=0o700)
        password = secrets.token_hex(32)
        password_secret = credential_dir / "root_password"
        password_secret.write_text(password + "\n", encoding="utf-8")
        password_secret.chmod(0o600)
        defaults = output / "mysql.cnf"
        defaults.write_text(
            "[client]\nuser=root\npassword=" + password
            + f"\nhost=127.0.0.1\nport={PORT}\nprotocol=tcp\nssl-mode=DISABLED\n"
              "get-server-public-key\n",
            encoding="utf-8",
        )
        defaults.chmod(0o600)
        config = _render_config(output, password)
        state("MYSQL_INITIALIZING", config=str(config))
        container_id = _docker(
            "run", "-d", "--name", CONTAINER,
            "--label", "ra.owner=mysql_tpch_sf4_builder_v1",
            "--label", "ra.formal_scope=mysql_only",
            "--cpus", "7.5", "--cpuset-cpus", DB_CPUSET,
            "--cpuset-mems", CPUSET_MEMS,
            "--memory", str(IMAGE_MEMORY), "--memory-swap", str(IMAGE_MEMORY),
            "--pids-limit", "512", "-p", f"127.0.0.1:{PORT}:3306",
            "-v", f"{DATADIR}:/var/lib/mysql:rw",
            "-v", f"{password_secret}:/run/secrets/root_password:ro",
            "-e", "MYSQL_ROOT_PASSWORD_FILE=/run/secrets/root_password",
            "-e", "MYSQL_DATABASE=tpch", IMAGE,
            "mysqld", "--persisted-globals-load=OFF", "--bind-address=0.0.0.0",
            f"--innodb-buffer-pool-size={BUFFER_POOL}",
            "--innodb-flush-method=O_DIRECT", "--innodb-flush-log-at-trx-commit=2",
            "--innodb-redo-log-capacity=536870912", "--skip-log-bin",
            "--max-connections=96", "--performance-schema=ON",
            timeout=120,
        ).stdout.strip()
        _wait_mysql(defaults)
        state("BENCHBASE_CREATE_LOAD_RUNNING", container_id=container_id)
        (output / "benchbase").mkdir(mode=0o700)
        load_stdout = (output / "benchbase_load.stdout.log").open("w", encoding="utf-8")
        load_stderr = (output / "benchbase_load.stderr.log").open("w", encoding="utf-8")
        try:
            completed = _run(
                ["taskset", "-c", CLIENT_CPUSET, "java", "-Xmx8g", "-jar",
                 str(BENCHBASE_JAR), "-b", "tpch", "-c", str(config),
                 "--create=true", "--load=true", "--execute=false", "-d", str(output / "benchbase")],
                timeout=21600, stdout=load_stdout, stderr=load_stderr, check=False,
                cwd=BENCHBASE_JAR.parent,
            )
        finally:
            load_stdout.close()
            load_stderr.close()
        if completed.returncode != 0:
            raise RuntimeError(f"BenchBase SF4 load failed rc={completed.returncode}")

        state("VALIDATING_ROWS_AND_INDEXES")
        tables = ("customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier")
        counts = {
            table: int(_query(defaults, f"SELECT COUNT(*) FROM tpch.{table}"))
            for table in tables
        }
        exact = {
            "customer": 600000, "nation": 25, "orders": 6000000,
            "part": 800000, "partsupp": 3200000, "region": 5,
            "supplier": 40000,
        }
        differences = {
            table: {"actual": counts.get(table), "expected": expected}
            for table, expected in exact.items() if counts.get(table) != expected
        }
        if not 23_900_000 <= counts["lineitem"] <= 24_100_000:
            differences["lineitem"] = {
                "actual": counts["lineitem"], "expected_range": [23_900_000, 24_100_000]
            }
        if differences:
            raise RuntimeError("SF4 row count validation differs: " + json.dumps(differences))
        index_count = int(_query(
            defaults,
            "SELECT COUNT(DISTINCT TABLE_NAME,INDEX_NAME) FROM information_schema.statistics "
            "WHERE TABLE_SCHEMA='tpch'",
        ))
        if index_count < 8:
            raise RuntimeError(f"TPC-H post-load index inventory is incomplete: {index_count}")
        mysql_version = _query(defaults, "SELECT VERSION()")
        _run(
            ["mysqladmin", f"--defaults-file={defaults}", "shutdown"],
            timeout=180, check=True,
        )
        _docker("wait", CONTAINER, timeout=180)
        _docker("rm", CONTAINER, timeout=120)
        _docker(
            "run", "--rm", "--network", "none", "-v", f"{DATADIR}:/copy:rw",
            "--entrypoint", "/bin/sh", IMAGE, "-c",
            "rm -f /copy/mysql.sock /copy/mysqld.sock /copy/mysqld.pid; "
            "chown -R mysql:mysql /copy; find /copy -type d -exec chmod 0550 {} +; "
            "find /copy -type f -exec chmod 0440 {} +",
            timeout=600,
        )
        template_bytes = _directory_bytes(DATADIR)
        if not MINIMUM_BYTES <= template_bytes <= MAXIMUM_BYTES:
            raise RuntimeError(
                f"clean SF4 template size {template_bytes} is outside "
                f"[{MINIMUM_BYTES}, {MAXIMUM_BYTES}]"
            )
        tree_sha = _tree_hash(DATADIR)
        password_secret.unlink()
        credential_dir.rmdir()
        manifest = {
            "schema_version": "mysql_tpch_sf4_disk2_storage_v1",
            "status": "SEALED",
            "artifact_kind": "data_preparation_template",
            "performance_measurement": False,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutations": 0,
            "template_path": str(DATADIR),
            "device": "/dev/nvme2n1",
            "device_major_minor": "259:6",
            "filesystem": "ext4",
            "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
            "scale_factor": SCALE_FACTOR,
            "database": "tpch",
            "queries": "Q1-Q22",
            "row_counts": counts,
            "index_count": index_count,
            "template_bytes": template_bytes,
            "target_size_band_bytes": [MINIMUM_BYTES, MAXIMUM_BYTES],
            "template_tree_sha256": tree_sha,
            "tree_hash_algorithm": "sha256sum_regular_files_relative_v1",
            "clean_shutdown": True,
            "sealed_read_only": True,
            "mysql_version": mysql_version,
            "image": IMAGE,
            "benchbase_jar": str(BENCHBASE_JAR),
            "benchbase_jar_sha256": _sha(BENCHBASE_JAR),
            "builder": str(Path(__file__).resolve()),
            "builder_sha256": _sha(Path(__file__).resolve()),
            "built_at": _now(),
            "accepted_rounds_credit": 0,
        }
        _atomic_json(MANIFEST, manifest)
        manifest_sha = _sha(MANIFEST)
        (TEMPLATE_ROOT / "storage_manifest.json.sha256").write_text(
            f"{manifest_sha}  storage_manifest.json\n", encoding="utf-8"
        )
        state(
            "SEALED_SF4_TEMPLATE_READY",
            template=str(DATADIR), template_bytes=template_bytes,
            storage_manifest=str(MANIFEST), storage_manifest_sha256=manifest_sha,
            row_counts=counts, index_count=index_count,
        )
        return 0
    except Exception as exc:
        state("FAILED_ZERO_CREDIT", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.detach:
        if args.run_id:
            parser.error("--detach chooses its own run id")
        run_id = "tpch_sf4_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = OUTPUT_BASE / run_id
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output.mkdir(mode=0o700)
        stdout = (output / "supervisor.stdout.log").open("w", encoding="utf-8")
        stderr = (output / "supervisor.stderr.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--run-id", run_id],
            stdout=stdout, stderr=stderr, start_new_session=True,
        )
        stdout.close()
        stderr.close()
        _atomic_json(output / "detached_supervisor.json", {
            "run_id": run_id, "pid": process.pid, "started_at": _now(),
            "run_state": str(output / "run_state.json"),
        })
        print(json.dumps({
            "status": "DETACHED", "run_id": run_id, "pid": process.pid,
            "output": str(output),
        }))
        return 0
    if not args.run_id:
        parser.error("non-detached mode requires --run-id")
    return _build(args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
