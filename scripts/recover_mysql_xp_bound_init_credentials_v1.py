#!/usr/bin/env python3
"""Resynchronise host defaults with the init SQL bound into a live XP task."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v5 as reset


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
ALLOWED_WORKERS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id", required=True, choices=sorted(ALLOWED_WORKERS))
    parser.add_argument("--init-pid", required=True, type=int)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{64}", args.container_id) is None:
        parser.error("full lowercase container id required")
    freeze = load(FREEZE)
    workers = {
        str(row["worker_id"]): dict(row)
        for row in freeze.get("workers") or []
        if row.get("worker_id") in ALLOWED_WORKERS
    }
    worker = workers[args.worker_id]
    runtime_root = Path(str(freeze["storage"]["runtime_root"])) / args.worker_id
    credentials = runtime_root / "credentials"
    selected = credentials / "selected.cnf"
    benchmark = credentials / "benchmark.cnf"
    host_init = credentials / "reset-init.sql"
    proc_root = Path(f"/proc/{args.init_pid}/root")
    proc_cgroup = Path(f"/proc/{args.init_pid}/cgroup")
    proc_cmdline = Path(f"/proc/{args.init_pid}/cmdline")
    proc_root_present = subprocess.run(
        ["sudo", "-n", "test", "-d", str(proc_root)], timeout=10,
    ).returncode == 0
    cgroup_text = subprocess.run(
        ["sudo", "-n", "cat", str(proc_cgroup)], capture_output=True,
        text=True, check=True, timeout=10,
    ).stdout
    cmdline = subprocess.run(
        ["sudo", "-n", "cat", str(proc_cmdline)], capture_output=True,
        check=True, timeout=10,
    ).stdout
    if (
        not str(runtime_root).startswith("/disk2/")
        or not proc_root_present
        or args.container_id not in cgroup_text
        or b"docker-init" not in cmdline
    ):
        raise RuntimeError("live container init identity differs")
    bound_init = proc_root / "run/ra-init/reset.sql"
    completed = subprocess.run(
        ["sudo", "-n", "cat", str(bound_init)],
        capture_output=True, text=True, check=True, timeout=20,
    )
    init_text = completed.stdout
    root_matches = re.findall(
        r"ALTER USER 'root'@'(?:%|localhost)' IDENTIFIED WITH "
        r"caching_sha2_password BY '([0-9a-f]{64})';",
        init_text,
    )
    benchmark_matches = re.findall(
        rf"ALTER USER '{re.escape(reset.BENCHMARK_USER)}'@'%' IDENTIFIED WITH "
        r"caching_sha2_password BY '([0-9a-f]{64})';",
        init_text,
    )
    if len(root_matches) != 2 or len(set(root_matches)) != 1 or len(benchmark_matches) != 1:
        raise RuntimeError("bound init credential grammar differs")
    bound_sha256 = hashlib.sha256(init_text.encode("utf-8")).hexdigest()
    host_init_sha256_before = subprocess.run(
        ["sudo", "-n", "sha256sum", str(host_init)], capture_output=True,
        text=True, check=True, timeout=10,
    ).stdout.split()[0]
    reset._write_private(selected, reset.prep._defaults(root_matches[0]))
    reset._write_private(
        benchmark, reset._benchmark_defaults(benchmark_matches[0]),
    )
    subprocess.run(
        ["sudo", "-n", "cp", "--no-preserve=mode,ownership", str(bound_init),
         str(host_init)],
        check=True, timeout=20,
    )
    subprocess.run(
        ["sudo", "-n", "chown", "999:999", str(host_init)],
        check=True, timeout=10,
    )
    subprocess.run(
        ["sudo", "-n", "chmod", "0400", str(host_init)],
        check=True, timeout=10,
    )
    host_init_sha256_after = subprocess.run(
        ["sudo", "-n", "sha256sum", str(host_init)], capture_output=True,
        text=True, check=True, timeout=10,
    ).stdout.split()[0]
    if host_init_sha256_after != bound_sha256:
        raise RuntimeError("host init resynchronisation differs")
    root_readback = reset._host_mysql(
        worker, selected, "SELECT 1,@@global.innodb_fast_shutdown", timeout=30,
    ).stdout.strip()
    benchmark_readback = reset._host_mysql(
        worker, benchmark, "SELECT 1", timeout=30,
    ).stdout.strip()
    if root_readback != "1\t1" or benchmark_readback != "1":
        raise RuntimeError("credential resynchronisation readback differs")
    output = args.output.expanduser().resolve()
    if output.exists():
        raise RuntimeError(f"fresh output required: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic(output, {
        "schema_version": "mysql_xp_bound_init_credential_recovery_v1",
        "status": "PASS_RECOVERY_ONLY",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "worker_id": args.worker_id,
        "container_id": args.container_id,
        "container_init_pid": args.init_pid,
        "bound_init_sha256": bound_sha256,
        "host_init_sha256_before": host_init_sha256_before,
        "host_init_sha256_after": host_init_sha256_after,
        "host_init_resynchronised": True,
        "host_init_bound_inode_mismatch_observed": bound_sha256 != host_init_sha256_before,
        "selected_defaults_sha256": sha(selected),
        "benchmark_defaults_sha256": sha(benchmark),
        "root_readback": root_readback,
        "benchmark_readback": benchmark_readback,
        "password_material_recorded": False,
        "workload_started": False,
        "accepted_rounds_mutation": False,
    })
    print(json.dumps({"status": "PASS", "worker_id": args.worker_id, "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
