#!/usr/bin/env python3
"""Prepare immutable-enough lane evidence for the authorized JOB baseline."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
from typing import Any, Dict, List


CAMPAIGN_ID = "pg5_common_outer_bgallowed_20260722_v1"
LANE_ID = "job_node02"
LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
PORT = 64302
CPUSET = "12-17,48-53"
CPUSET_MEMS = "0"
SQL_NAMES = ("1a.sql", "5a.sql", "9a.sql", "12a.sql", "16a.sql", "20a.sql", "26a.sql", "33a.sql")
SQL_WEIGHTS = (10, 10, 10, 10, 10, 10, 30, 10)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: List[str]) -> str:
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    resolved = LANE_ROOT.resolve()
    resolved.relative_to(Path("/mnt/nvme2n1"))
    if run(["findmnt", "-T", str(resolved), "-n", "-o", "TARGET"]) != "/mnt/nvme2n1":
        raise RuntimeError("lane root is not on /mnt/nvme2n1")
    manifest_root = resolved / "manifest"
    manifest_root.mkdir(parents=True, exist_ok=True)
    sql_root = resolved / "assets" / "job_sql"
    template_root = resolved / "template" / "job"
    pgdata = template_root / "pgdata"
    required = [template_root / "template_manifest.json", pgdata / "PG_VERSION", pgdata / "global" / "pg_control"]
    required.extend(sql_root / name for name in SQL_NAMES)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"missing lane inputs: {missing}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", PORT))
    docker_root = run(["docker", "info", "--format", "{{.DockerRootDir}}"])
    Path(docker_root).resolve().relative_to(Path("/mnt/nvme2n1"))
    captured_at = utc_now()
    background = {
        "captured_at_utc": captured_at,
        "background_load_allowed": True,
        "host_idle_gate_waived_by_user": True,
        "loadavg": Path("/proc/loadavg").read_text(encoding="utf-8").strip(),
        "meminfo_sha256": sha256_file(Path("/proc/meminfo")),
        "process_snapshot": run(["ps", "-eo", "user,pid,psr,pcpu,pmem,etimes,args", "--sort=-pcpu"]),
        "docker_snapshot": run(["docker", "ps", "-a", "--no-trunc"]),
        "docker_root": docker_root,
        "continuous_evidence": [
            str(resolved / "background" / "prelaunch_mpstat.log"),
            str(resolved / "background" / "prelaunch_iostat.log"),
            str(resolved / "background" / "prelaunch_vmstat.log"),
            str(resolved / "background" / "runtime_mpstat.log"),
            str(resolved / "background" / "runtime_iostat.log"),
            str(resolved / "background" / "runtime_vmstat.log"),
        ],
    }
    campaign = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "engine": "postgresql17",
        "included_benchmarks": ["hybench_ap", "job", "tpch", "tpcc", "hybench_xp"],
        "lane_id": LANE_ID,
        "host": "node02",
        "host_ipv4": "192.0.2.4",
        "background_load_allowed": True,
        "background_load_recorded": True,
        "external_tasks_may_not_be_stopped_or_overwritten": True,
        "created_at_utc": captured_at,
        "execution_scope": "shared baseline R0 only; optimization methods require the new common-outer protocol",
        "method_order_after_shared_baseline": ["experience_table_all_subset_sgd", "random", "brute"],
        "common_outer": {
            "shared_baseline": True,
            "serial_methods_per_lane": True,
            "accepted_shrink_target": 10,
            "sgd_first_round_real_replays": 5,
            "sgd_first_round_must_not_expand_to_10": True,
            "outer_shrinks_only_after_acceptance": True,
        },
    }
    lane = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "lane_id": LANE_ID,
        "benchmark": "job",
        "target_bottleneck": "memory",
        "host": "node02",
        "host_ipv4": "192.0.2.4",
        "lane_root": str(resolved),
        "storage_mount": "/mnt/nvme2n1",
        "storage_device": run(["findmnt", "-T", str(resolved), "-n", "-o", "SOURCE"]),
        "docker_root": docker_root,
        "container_name": "ra_pg5_job_node02_v1",
        "host_port": PORT,
        "cpuset_cpus": CPUSET,
        "cpuset_mems": CPUSET_MEMS,
        "cpu_quota_cores": 3,
        "memory_bytes": 6442450944,
        "memory_swap_bytes": 6442450944,
        "io_read_bps": 1205862400,
        "io_write_bps": 1205862400,
        "pids_limit": 512,
        "paths": {
            "data_template": str(pgdata),
            "work": str(resolved / "work" / "runtime"),
            "logs": str(resolved / "logs"),
            "history": str(resolved / "history"),
            "tmp": str(resolved / "tmp"),
            "socket": str(resolved / "socket" / "postgresql"),
            "lease": str(resolved / "lease" / "job_lane.lock"),
        },
        "background_load_allowed": True,
        "background_snapshot": background,
    }
    workload = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "lane_id": LANE_ID,
        "benchmark": "job",
        "formal_name": "JOB-8-mixed-26a-weighted",
        "database": "imdb_job",
        "clients": 1,
        "query_sequence": list(SQL_NAMES),
        "query_weights": list(SQL_WEIGHTS),
        "query_sha256": {name: sha256_file(sql_root / name) for name in SQL_NAMES},
        "warmup_seconds": 60,
        "measurement_hard_max_seconds": 600,
        "online_early_stop": "six consecutive stable 30-second windows after at least 180 seconds",
        "baseline_id": "R0",
    }
    source_template = json.loads((template_root / "template_manifest.json").read_text(encoding="utf-8"))
    template = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "lane_id": LANE_ID,
        "benchmark": "job",
        "adoption_mode": source_template.get("provision_mode", "lane-local PostgreSQL 17 JOB template"),
        "source_template_id": source_template.get("template_id"),
        "source_freeze_id": source_template.get("freeze_id"),
        "source_campaign_id": source_template.get("global_campaign_id"),
        "template_manifest_sha256": sha256_file(template_root / "template_manifest.json"),
        "pg_control_sha256": sha256_file(pgdata / "global" / "pg_control"),
        "expected_pg_control_sha256": source_template["build_runtime"]["pg_control_sha256_after_clean_shutdown"],
        "pg_version": (pgdata / "PG_VERSION").read_text(encoding="utf-8").strip(),
        "database": source_template.get("database"),
        "row_counts": source_template.get("row_counts"),
        "template_read_only": not os.access(pgdata, os.W_OK),
        "path": str(pgdata),
    }
    if template["pg_control_sha256"] != template["expected_pg_control_sha256"]:
        raise RuntimeError("copied template pg_control checksum mismatch")
    documents = {
        "campaign_manifest.json": campaign,
        "lane_manifest.json": lane,
        "workload_manifest.json": workload,
        "template_adoption_manifest.json": template,
        "method_handoff_manifest.json": {
            "schema_version": 1,
            "campaign_id": CAMPAIGN_ID,
            "lane_id": LANE_ID,
            "shared_baseline_id": "R0",
            "method_order": ["experience_table_all_subset_sgd", "random", "brute"],
            "serial_execution_required": True,
            "common_outer_state_shared_after_baseline": True,
            "experience_table_all_subset_sgd": {
                "k": 7,
                "all_subset_candidate_count": 127,
                "first_round_real_replays": 5,
                "first_round_real_replays_must_not_expand_to": 10,
                "accepted_shrink_target": 10,
                "outer_interval_shrinks_only_after_acceptance": True,
            },
            "random": {"uses_shared_outer": True},
            "brute": {"uses_shared_outer": True},
            "old_p24_10_candidate_runner_may_be_used_as_formal_method": False,
            "baseline_launcher": str(resolved / "launch" / "launch_job_node02.sh"),
            "post_baseline_launcher": str(resolved / "launch" / "launch_methods_after_baseline.sh"),
        },
    }
    for name, value in documents.items():
        atomic_json(manifest_root / name, value)
    checksum_lines = []
    for path in sorted(manifest_root.glob("*.json")):
        checksum_lines.append(f"{sha256_file(path)}  {path.name}")
    (manifest_root / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "prepared", "lane_root": str(resolved), "manifests": sorted(documents)}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
