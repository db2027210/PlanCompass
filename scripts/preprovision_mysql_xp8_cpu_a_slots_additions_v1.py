#!/usr/bin/env python3
"""Pre-provision exact XP8 w5-w8 additions without reading Algorithm-A evidence."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v5 as reset8


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6"
    / "xp_effective_resource_successor.json"
)
LEGACY = (
    HISTORY / "four_scene_live_rebind_v26_20260811T143500p0800"
    / "prepared_binding.json"
)
JOB = HISTORY / "v502_to_b_job_mixed_four_worker_authority_v71_20260827/prepared_binding.json"
TPCH = HISTORY / "v503_to_b_tpch_mixed_four_worker_authority_v71_20260827/prepared_binding.json"
TARGETS = {
    *(f"xp_cpu_w{i}" for i in range(5, 9)),
    *(f"xp_io_w{i}" for i in range(5, 9)),
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if HISTORY.resolve() not in output.parents or output.exists() or output.is_symlink():
        raise RuntimeError("fresh output beneath formal history required")
    freeze = load(FREEZE)
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
    }
    if (
        freeze.get("schema_version") != "mysql_xp_eight_worker_cpu_a_slots_successor_v6"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or len(frozen) != 16
        or not TARGETS <= set(frozen)
    ):
        raise RuntimeError("XP8 addition freeze differs")
    legacy: dict[str, dict[str, Any]] = {}
    for path in (LEGACY, JOB, TPCH):
        for row in load(path).get("workers") or []:
            if row.get("container_id") and row.get("ready") is not False:
                legacy[str(row["worker_id"])] = copy.deepcopy(dict(row))
    reset8.install_for_workers(frozen)
    impl = reset8.base._impl
    output.mkdir(parents=True, mode=0o700)
    stage = output / "new_worker_resets"
    quarantine = output / "discard_quarantine"
    additions: dict[str, dict[str, Any]] = {}
    removed_invalid: list[dict[str, Any]] = []

    # Keep Docker create/start operations sequential. This is management-plane
    # throttling only; C scenes remain independent and continue running.
    for worker_id in sorted(TARGETS):
        worker = frozen[worker_id]
        source_id = str(worker.get("reassigned_from_completed_scene_worker_id") or "")
        prior = copy.deepcopy(legacy.get(source_id) or {})
        unused = worker.get("slot_source_kind") == "mixed" and source_id in {
            "xp_io_w3", "xp_io_w4",
        }
        if not prior and unused:
            prior = copy.deepcopy(worker)
        if str(prior.get("container") or "") != str(worker["container"]):
            raise RuntimeError(f"{worker_id} predecessor container differs")
        existing = impl["_docker_run"](
            ["docker", "ps", "-aq", "--filter", f"name=^{worker['container']}$"],
            timeout=120,
        ).stdout.strip()
        if existing:
            inspected = impl["_docker_json"]("inspect", str(worker["container"]))
            state = inspected.get("State") or {}
            labels = (inspected.get("Config") or {}).get("Labels") or {}
            mounts = {
                str(row.get("Destination") or ""): str(row.get("Source") or "")
                for row in inspected.get("Mounts") or []
            }
            if (
                labels.get("ra.owner") != "mysql_four_scene_v5"
                or str((inspected.get("HostConfig") or {}).get("CpusetCpus") or "")
                != str(worker["database_cpuset"])
                or mounts.get("/var/lib/mysql") != str(worker["runtime_root"] + "/datadir")
            ):
                raise RuntimeError(f"{worker_id} existing physical slot differs")
            if (
                state.get("Running") is True
                and labels.get("ra.worker_id") == worker_id
                and labels.get("ra.scenario") == worker["scene"]
            ):
                defaults = Path(mounts["/run/ra-credentials"]) / "selected.cnf"
                benchmark = Path(mounts["/run/ra-credentials"]) / "benchmark.cnf"
                tables = impl["_host_mysql"](
                    worker, defaults,
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='hybench_sf10x';", timeout=60,
                ).stdout.strip()
                if tables != "8" or not benchmark.is_file():
                    raise RuntimeError(f"{worker_id} existing XP template differs")
                row = {
                    **prior, **worker, "ready": True,
                    "container_id": str(inspected["Id"]),
                    "container_pid": int(state.get("Pid") or 0),
                    "datadir": mounts["/var/lib/mysql"],
                    "credential_defaults": str(defaults),
                    "benchmark_defaults": str(benchmark),
                    "database": "hybench_sf10x", "table_count": 8,
                    "kernel_cgroup_resources": impl["prep"]._kernel_resource_readback(
                        inspected, worker
                    ),
                    "blkio_authority": impl["prep"]._enforce_blkio(
                        inspected, freeze, worker
                    ),
                    "preprovisioned_target_reused": True,
                    "algorithm_a_candidate_or_qps_evidence_used": False,
                    "passwords_emitted": False,
                }
                worker_output = stage / worker_id
                worker_output.mkdir(parents=True, exist_ok=False)
                evidence = worker_output / "reset_worker.json"
                atomic(evidence, row)
                row["worker_evidence_path"] = str(evidence)
                row["worker_evidence_sha256"] = sha(evidence)
                additions[worker_id] = row
                continue
            if state.get("Running") is not False or int(state.get("Pid") or 0) != 0:
                raise RuntimeError(f"{worker_id} invalid predecessor is not stopped")
            removed_invalid.append({
                "worker_id": worker_id, "container": worker["container"],
                "container_id": inspected["Id"],
                "old_label_worker_id": labels.get("ra.worker_id"),
                "old_label_scene": labels.get("ra.scenario"),
                "datadir_preserved": mounts.get("/var/lib/mysql"),
            })
            impl["_docker_run"](["docker", "rm", str(worker["container"])], timeout=120)
        prior_id = str(prior.get("container_id") or "")
        prior_cgroup = Path(str((prior.get("blkio_authority") or {}).get("cgroup_path") or ""))
        expected_cgroup = Path("/sys/fs/cgroup/blkio/docker-limited") / prior_id
        runtime_root = Path(str(worker["runtime_root"]))
        if (
            len(prior_id) != 64
            or prior_cgroup != expected_cgroup
            or prior_cgroup.exists()
            or not (runtime_root / "datadir").is_dir()
            or not (runtime_root / "credentials").is_dir()
        ):
            raise RuntimeError(f"{worker_id} absent predecessor recovery differs")
        additions[worker_id] = impl["_prepare_fresh_xp"](
            freeze, worker, prior, quarantine, stage,
            True, "formal_point", True, False, False, True,
        )

    if set(additions) != TARGETS or any(row.get("ready") is not True for row in additions.values()):
        raise RuntimeError("XP8 addition set differs")
    binding = output / "additions_binding.json"
    atomic(binding, {
        "schema_version": "mysql_xp8_cpu_a_slots_additions_preprovision_v1",
        "status": "PASS", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "worker_ids": sorted(additions),
        "workers": [additions[key] for key in sorted(additions)],
        "physical_additions_per_scene": 4,
        "algorithm_a_candidate_injection": False,
        "algorithm_a_qps_used_for_generation_or_ranking": False,
        "algorithm_a_parameter_ranges_used": False,
        "algorithm_a_role": "independent_posthoc_check_only",
        "candidate_measurements_reused": 0,
        "accepted_or_rejected_state_credit_changed": False,
        "removed_invalid_stopped_containers": removed_invalid,
        "workload_started": False,
    })
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "worker_ids": sorted(additions),
        "removed_invalid_count": len(removed_invalid),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
