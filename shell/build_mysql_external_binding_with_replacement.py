#!/usr/bin/env python3
"""Build an audited external binding after replacing one XP worker."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from shell.run_mysql_four_scene_external_baselines import canonical_sha, load


CLIENT_CPUSETS = {
    "job_w1": "376-383",
    "job_w2": "400-407",
    "tpch_w1": "128-131,180-183",
    "tpch_w2": "408-415",
    "xp_cpu_w1": "288-295",
    "xp_cpu_w2": "296-303",
    "xp_io_w1": "304-311",
    "xp_io_w2": "312-319",
}
SEMANTIC_KEYS = (
    "worker_id",
    "scene",
    "container",
    "host_port",
    "database_cpuset",
    "database_cpuset_mems",
    "client_cpuset",
    "client_cpuset_mems",
    "cpu_quota_cores",
    "memory_bytes",
    "pids_max",
    "io_read_bps",
    "io_write_bps",
    "innodb_buffer_pool_bytes",
    "datadir",
)
FROZEN_REPLACEMENT_KEYS = (
    "container",
    "host_port",
    "database_cpuset",
    "database_cpuset_mems",
    "client_cpuset",
    "client_cpuset_mems",
    "datadir",
    "credential_defaults",
    "credential_benchmark",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-binding", type=Path, required=True)
    parser.add_argument("--replacement", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base_path = args.base_binding.resolve()
    replacement_path = args.replacement.resolve()
    output = args.output.resolve()
    if output.exists() or output.with_suffix(output.suffix + ".sha256").exists():
        raise RuntimeError("replacement binding output exists")
    binding = copy.deepcopy(load(base_path))
    replacement_doc = load(replacement_path)
    if (
        replacement_doc.get("status") != "PASS"
        or replacement_doc.get("workload_started") is not False
        or replacement_doc.get("old_container_retained_as_failure_evidence")
        is not True
    ):
        raise RuntimeError("replacement authority differs")
    replacement = copy.deepcopy(replacement_doc["replacement_worker"])
    if replacement.get("worker_id") != "xp_cpu_w1":
        raise RuntimeError("replacement worker differs")

    root_workers = {
        str(row["worker_id"]): row for row in binding["workers"]
    }
    root_workers["xp_cpu_w1"] = replacement
    for worker_id, client_cpuset in CLIENT_CPUSETS.items():
        root_workers[worker_id]["client_cpuset"] = client_cpuset
        root_workers[worker_id]["client_cpuset_mems"] = "0"
    binding["workers"] = [
        root_workers[row["worker_id"]] for row in binding["workers"]
    ]

    frozen_workers = {
        str(row["worker_id"]): row for row in binding["freeze"]["workers"]
    }
    frozen_replacement = frozen_workers["xp_cpu_w1"]
    for key in FROZEN_REPLACEMENT_KEYS:
        frozen_replacement[key] = replacement[key]
    for worker_id, client_cpuset in CLIENT_CPUSETS.items():
        frozen_workers[worker_id]["client_cpuset"] = client_cpuset
        frozen_workers[worker_id]["client_cpuset_mems"] = "0"
    binding["freeze"]["workers"] = [
        frozen_workers[row["worker_id"]]
        for row in binding["freeze"]["workers"]
    ]

    binding["client_affinity_rebind"] = {
        **dict(binding.get("client_affinity_rebind") or {}),
        "replacement_reason": replacement_doc["reason"],
        "replacement_authority": str(replacement_path),
        "replacement_authority_sha256": sha256(replacement_path),
        "old_container_retained_as_failure_evidence": True,
        "replacement_worker": "xp_cpu_w1",
        "restart_policy": "fresh_baseline_new_campaign_no_result_inheritance",
    }
    binding["runtime_root_mixed_worker_replacement"] = True
    binding["external_reset_runtime_root_policy"] = (
        "verified_per_worker_datadir_parent"
    )
    binding["worker_semantic_sha256"] = canonical_sha(
        [
            {key: worker[key] for key in SEMANTIC_KEYS}
            for worker in binding["workers"]
        ]
    )
    atomic_json(output, binding)
    checksum = sha256(output)
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    checksum_path.write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(output, flush=True)
    print(checksum, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
