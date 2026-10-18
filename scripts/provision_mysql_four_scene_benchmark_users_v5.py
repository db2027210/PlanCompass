#!/usr/bin/env python3
"""Rotate or verify host-bridge benchmark users for frozen v12 workers."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREPARED = (
    ROOT / "history/mysql_four_scene_20260809/four_scene_live_rebind_v19_20260810T143100p0800"
    / "prepared_binding.json"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260810_v12"
    / "four_scene_effective_resource_successor.json"
)
BENCHMARK_USER = "ra_benchmark"
FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v12"
OUTPUT_SCHEMA = "mysql_four_scene_benchmark_accounts_v12"
AUTHORITY_VERSION = "v12"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _defaults(user: str, password: str) -> str:
    return (
        "[client]\n"
        f"user={user}\n"
        f"password={password}\n"
        "protocol=tcp\nssl-mode=DISABLED\nget-server-public-key=TRUE\n"
    )


def _atomic_private(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _run_worker(
    worker: dict[str, Any], output_root: Path, verify_existing: bool,
    rotate_all: bool,
) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    if re.fullmatch(r"[a-z0-9_]+", worker_id) is None:
        raise RuntimeError(f"unsafe worker id: {worker_id!r}")
    container = str(worker["container"])
    root_defaults = Path(str(worker["credential_defaults"]))
    benchmark_defaults = root_defaults.with_name("benchmark.cnf")
    if benchmark_defaults.exists() and not verify_existing and not rotate_all:
        raise RuntimeError(f"{worker_id} benchmark credential already exists")
    if verify_existing and not benchmark_defaults.is_file():
        raise RuntimeError(f"{worker_id} existing benchmark credential is absent")
    # Docker's control socket can be temporarily slow while several XP lanes
    # finish large datadir sync/removal work.  Treat that as control-plane
    # latency, not as a failed worker identity.
    inspected = json.loads(subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True,
        check=True, timeout=300,
    ).stdout)[0]
    if (
        inspected.get("Id") != worker.get("container_id")
        or (inspected.get("State") or {}).get("Running") is not True
        or (inspected.get("Config") or {}).get("Labels", {}).get("ra.owner")
        != "mysql_four_scene_v5"
    ):
        raise RuntimeError(f"{worker_id} live container identity differs")
    processlist = subprocess.run(
        [
            "mysql", f"--defaults-file={root_defaults}", "--host=127.0.0.1",
            f"--port={worker['host_port']}", "--connect-timeout=10", "-NBe",
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' AND USER<>'event_scheduler'",
        ],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    if processlist != "0":
        raise RuntimeError(f"{worker_id} still has non-system client sessions")

    database = str(worker["database"])
    if not verify_existing:
        root_password = secrets.token_hex(32)
        benchmark_password = secrets.token_hex(32)
        sql = (
            "CREATE USER IF NOT EXISTS 'ra_benchmark'@'%' "
            f"IDENTIFIED WITH caching_sha2_password BY '{benchmark_password}'; "
            "ALTER USER 'ra_benchmark'@'%' "
            f"IDENTIFIED WITH caching_sha2_password BY '{benchmark_password}'; "
            f"GRANT ALL PRIVILEGES ON `{database}`.* TO 'ra_benchmark'@'%'; "
            "ALTER USER 'root'@'%' "
            f"IDENTIFIED WITH caching_sha2_password BY '{root_password}'; "
            "ALTER USER 'root'@'localhost' "
            f"IDENTIFIED WITH caching_sha2_password BY '{root_password}'; "
            "FLUSH PRIVILEGES;"
        )
        changed = subprocess.run(
            [
                "mysql", f"--defaults-file={root_defaults}", "--host=127.0.0.1",
                f"--port={worker['host_port']}", "--connect-timeout=10", "-NB",
            ],
            input=sql, capture_output=True, text=True, check=False, timeout=60,
        )
        if changed.returncode != 0:
            raise RuntimeError(
                f"{worker_id} account transaction failed: {changed.stderr[-1000:]}"
            )
        _atomic_private(root_defaults, _defaults("root", root_password))
        _atomic_private(benchmark_defaults, _defaults(BENCHMARK_USER, benchmark_password))
    internal = subprocess.run(
        [
            "mysql", f"--defaults-file={root_defaults}", "--host=127.0.0.1",
            f"--port={worker['host_port']}", "--connect-timeout=10", "-NBe",
            "SELECT 1",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )
    external = subprocess.run(
        [
            "mysql", f"--defaults-file={benchmark_defaults}",
            "--host=127.0.0.1", f"--port={worker['host_port']}",
            "--connect-timeout=5", f"--database={database}", "-NBe",
            "SELECT 1,USER(),CURRENT_USER(),DATABASE()",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if internal.returncode != 0 or external.returncode != 0:
        raise RuntimeError(
            f"{worker_id} rotated credential verification failed: "
            f"internal={internal.stderr[-500:]!r} external={external.stderr[-500:]!r}"
        )
    fields = external.stdout.strip().split("\t")
    if len(fields) != 4 or fields[0] != "1" or fields[2] != "ra_benchmark@%":
        raise RuntimeError(f"{worker_id} benchmark account readback differs")
    row = {
        "worker_id": worker_id,
        "scene": worker["scene"],
        "container": container,
        "container_id": worker["container_id"],
        "database": database,
        "benchmark_user": BENCHMARK_USER,
        "benchmark_defaults": str(benchmark_defaults),
        "benchmark_defaults_mode": oct(benchmark_defaults.stat().st_mode & 0o777),
        "root_defaults_rotated": str(root_defaults),
        "external_user": fields[1],
        "external_current_user": fields[2],
        "external_database": fields[3],
        "passwords_emitted": False,
        "account_mutation_performed_in_this_run": not verify_existing,
        "ready": True,
    }
    worker_output = output_root / worker_id
    worker_output.mkdir(parents=True, exist_ok=True)
    evidence = worker_output / "benchmark_user.json"
    evidence.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    row["evidence_path"] = str(evidence)
    row["evidence_sha256"] = _sha(evidence)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--rotate-all", action="store_true")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    if args.verify_existing and args.rotate_all:
        parser.error("--verify-existing and --rotate-all are mutually exclusive")
    prepared = _load(PREPARED)
    freeze = _load(FREEZE)
    if (
        freeze.get("schema_version") != FREEZE_SCHEMA
        or prepared.get("status") != "PREPARED"
        or prepared.get("workload_started") is not False
        or prepared.get("fresh_r0_complete") is not False
        or prepared.get("formal_outer_started") is not False
        or prepared.get("freeze_sha256") != _sha(FREEZE)
        or len(prepared.get("workers") or []) != 8
    ):
        raise RuntimeError(f"{AUTHORITY_VERSION} benchmark-user authority differs")
    output_root = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output_root.exists():
        raise RuntimeError(f"fresh benchmark-user output already exists: {output_root}")
    output_root.mkdir(parents=True, mode=0o700)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="mysql-v5-users") as pool:
        futures = {
            pool.submit(
                _run_worker, worker, output_root, args.verify_existing, args.rotate_all
            ): worker
            for worker in prepared["workers"]
        }
        for future in as_completed(futures):
            worker = futures[future]
            try:
                row = future.result()
                results.append(row)
                print(json.dumps({"worker_id": row["worker_id"], "ready": True}), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker["worker_id"],
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)
    results.sort(key=lambda row: row["worker_id"])
    document = {
        "schema_version": OUTPUT_SCHEMA,
        "status": "PASS" if not failures and len(results) == 8 else "FAILED_CLOSED",
        "created_at": _now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "prepared_binding_path": str(PREPARED),
        "prepared_binding_sha256": _sha(PREPARED),
        "freeze_path": str(FREEZE),
        "freeze_sha256": _sha(FREEZE),
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": _sha(Path(__file__).resolve()),
        "workers": results,
        "failures": failures,
        "root_credentials_rotated_after_local_diagnostic_exposure": True,
        "passwords_emitted": False,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output_root / "benchmark_accounts_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{_sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": document["status"], "ready": len(results),
        "failed": len(failures), "binding": str(binding),
    }), flush=True)
    return 0 if document["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
