#!/usr/bin/env python3
"""Recover the eight XP/I/O data planes before the Round-6 A resume.

This is a technical recovery only.  It creates no workload measurement and
grants no accepted/rejected optimizer credit.  The worker identities come
from the last complete eight-worker reset binding; every worker is reset from
the frozen native-XP template under the current mixed-SSD reset runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1"
    / "xp_effective_resource_successor.json"
)
PREPARED = (
    ROOT
    / "history/mysql_four_scene_20260809"
    / "v1051_a_io_round4_post_c_live_authority_normalized_20260828"
    / "prepared_binding.json"
)
SOURCE_RESET = (
    ROOT
    / "history/mysql_four_scene_20260809"
    / "v1056_to_a_io_round4_v14_resume_20260828_xp_io"
    / "round_006/wave_001/xp_resets"
    / "v1056_to_a_io_round4_v14_resume_20260828_xp_io_round_006_wave_001_attempt_01"
    / "reset_binding.json"
)
EXPECTED = {
    FREEZE: "327bf4b69ba540bc4787d55f31b9acd6bbd727937771f2bf67d99f0aee9cb044",
    PREPARED: "81bda0d7ed641002dcb3dfc67b401f75b261ec64ac208383987a8b386c80bd0c",
    SOURCE_RESET: "1ac5aa53e26fb68e70a3d0cf84a9bb112c570f8cf21afae62765fa79f82eab16",
}
WORKER_IDS = {f"xp_io_w{index}" for index in range(1, 9)}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_sources() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError(f"frozen recovery source differs: {path}")
    freeze = load(FREEZE)
    source = load(SOURCE_RESET)
    if (
        source.get("status") != "PASS"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("source reset authority is not mysql_only PASS")
    rows = list(source.get("workers") or [])
    workers = {str(row.get("worker_id")): dict(row) for row in rows}
    if len(rows) != 8 or set(workers) != WORKER_IDS:
        raise RuntimeError("source reset must bind exactly xp_io_w1..w8")
    for worker_id, row in workers.items():
        if (
            row.get("scene") != "hybench_xp_io"
            or int(row.get("accepted_or_rejected_round_credit") or 0) != 0
            or int(row.get("candidate_measurements_reused") or 0) != 0
        ):
            raise RuntimeError(f"technical-only source differs: {worker_id}")
    frozen_rows = {
        str(row.get("worker_id")): dict(row)
        for row in list(freeze.get("workers") or [])
        if str(row.get("worker_id")) in WORKER_IDS
    }
    if set(frozen_rows) != WORKER_IDS:
        raise RuntimeError("freeze does not contain all XP/I/O workers")
    for worker_id in sorted(WORKER_IDS):
        for field in ("container", "host_port", "scene"):
            if workers[worker_id].get(field) != frozen_rows[worker_id].get(field):
                raise RuntimeError(f"{worker_id} frozen {field} differs")
    return freeze, workers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"fresh recovery output required: {output}")
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "technical_recovery_state.json"
    freeze, workers = validate_sources()
    atomic(state_path, {
        "schema_version": "mysql_algorithm_a_xp_io_round6_data_plane_recovery_v1",
        "status": "RUNNING",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_io",
        "worker_ids": sorted(workers),
        "physical_worker_parallelism": 8,
        "docker_control_concurrency": 2,
        "accepted_or_rejected_round_credit": 0,
        "candidate_measurements_reused": 0,
        "source_paths": {str(path): digest for path, digest in EXPECTED.items()},
    })

    from scripts import mysql_async_xp_mixed_ssd_post_reset_settle_v1 as settle
    from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8

    reset8.install_for_scene(workers)
    reset8.configure(freeze=FREEZE, prepared=PREPARED)
    # ORIGINAL_RESET_XP resolves ``alg.reset`` dynamically.  Bind that exact
    # dispatch and its legacy worker-id table to the installed XP8 v6 runtime
    # before invoking it.  The old C-v2 implementation was originally loaded
    # with only xp_cpu/io_w1..w2 in this module-global table.
    settle.alg.reset = reset8
    settle.alg.XP_IDS = tuple(sorted(workers))
    cgroup_root = Path("/sys/fs/cgroup/blkio/docker-limited")
    absent: set[str] = set()
    stopped: set[str] = set()
    reaped_empty_cgroups: list[str] = []
    for worker_id in sorted(workers):
        row = workers[worker_id]
        container = str(row["container"])
        existing_id = str(reset8._container_id(container) or "")
        if not existing_id:
            prior_id = str(row.get("container_id") or "")
            prior_cgroup = cgroup_root / prior_id
            runtime_root = Path(str(row.get("runtime_root") or ""))
            if (
                len(prior_id) != 64
                or prior_cgroup.exists()
                or not (runtime_root / "datadir").is_dir()
                or not (runtime_root / "credentials").is_dir()
            ):
                raise RuntimeError(f"{worker_id} absent recovery proof differs")
            absent.add(worker_id)
            continue
        inspected = reset8._docker_json("inspect", container)
        current_id = str(inspected.get("Id") or "")
        state = dict(inspected.get("State") or {})
        labels = dict((inspected.get("Config") or {}).get("Labels") or {})
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        if (
            len(current_id) != 64
            or not current_id.startswith(existing_id)
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or mounts.get("/var/lib/mysql") != str(row.get("datadir") or "")
            or state.get("Restarting") is not False
            or state.get("Dead") is not False
        ):
            raise RuntimeError(f"{worker_id} current Docker identity differs")
        running = state.get("Running") is True and int(state.get("Pid") or 0) > 1
        exited = (
            state.get("Running") is False
            and state.get("Status") == "exited"
            and int(state.get("Pid") or 0) == 0
            and isinstance(state.get("ExitCode"), int)
        )
        if not running and not exited:
            raise RuntimeError(f"{worker_id} current Docker liveness differs")
        current_cgroup = cgroup_root / current_id
        if exited and current_cgroup.is_dir():
            tasks = current_cgroup / "tasks"
            processes = current_cgroup / "cgroup.procs"
            children = [path for path in current_cgroup.iterdir() if path.is_dir()]
            if (
                not tasks.is_file()
                or tasks.read_text(encoding="ascii").strip()
                or not processes.is_file()
                or processes.read_text(encoding="ascii").strip()
                or children
            ):
                raise RuntimeError(f"{worker_id} stopped cgroup is not empty")
            subprocess.run(
                ["sudo", "-n", "rmdir", str(current_cgroup)],
                capture_output=True, text=True, check=True, timeout=30,
            )
            if current_cgroup.exists():
                raise RuntimeError(f"{worker_id} empty cgroup reap failed")
            reaped_empty_cgroups.append(str(current_cgroup))
        if (running and not current_cgroup.is_dir()) or (exited and current_cgroup.exists()):
            raise RuntimeError(f"{worker_id} current cgroup liveness differs")
        row["container_id"] = current_id
        row["container_pid"] = int(state.get("Pid") or 0)
        authority = dict(row.get("blkio_authority") or {})
        authority.update({
            "cgroup_path": str(current_cgroup),
            "read_throttle_file": str(
                current_cgroup / "blkio.throttle.read_bps_device"
            ),
            "write_throttle_file": str(
                current_cgroup / "blkio.throttle.write_bps_device"
            ),
        })
        row["blkio_authority"] = authority
        if exited:
            stopped.add(worker_id)

    original_classify = settle.alg.classify_xp_mysql_liveness

    def classify_recovery_seed(worker: Mapping[str, Any]) -> dict[str, Any]:
        worker_id = str(worker["worker_id"])
        container = str(worker["container"])
        expected_id = str(worker.get("container_id") or "")
        if worker_id in absent:
            if reset8._container_id(container) or (cgroup_root / expected_id).exists():
                raise RuntimeError(f"{worker_id} absent recovery changed")
            semantics = "zero_by_exact_absent_interrupted_discard_and_absent_cgroup"
            observed = None
        elif worker_id in stopped:
            inspected = reset8._docker_json("inspect", container)
            state = dict(inspected.get("State") or {})
            labels = dict((inspected.get("Config") or {}).get("Labels") or {})
            if (
                inspected.get("Id") != expected_id
                or state.get("Running") is not False
                or state.get("Status") != "exited"
                or int(state.get("Pid") or 0) != 0
                or state.get("Restarting") is not False
                or state.get("Dead") is not False
                or labels.get("ra.owner") != "mysql_four_scene_v5"
                or labels.get("ra.worker_id") != worker_id
                or (cgroup_root / expected_id).exists()
            ):
                raise RuntimeError(f"{worker_id} stopped recovery changed")
            semantics = "zero_by_exact_stopped_identity_and_absent_cgroup"
            observed = container
        else:
            return original_classify(worker)
        return {
            "worker_id": worker_id,
            "expected_container": container,
            "observed_container": observed,
            "expected_container_id": expected_id,
            "first_container_id": expected_id if observed else None,
            "docker_running": False,
            "container_identity_stable": True,
            "mysqld_rows": [],
            "live_mysqld_rows": [],
            "status": "PROVEN_NO_LIVE_MYSQLD",
            "safe_to_discard_unresponsive_predecessor": True,
            "session_count_semantics": semantics,
            "technical_authority_recovery_only": True,
        }

    settle.alg.classify_xp_mysql_liveness = classify_recovery_seed
    original_prepare = reset8._prepare_fresh_xp

    def prepare_recovery_seed(
        freeze_value: dict[str, Any], worker: dict[str, Any],
        prior: dict[str, Any], quarantine_root: Path, output_root: Path,
        resume_stalled_copy: bool, reset_context: str = "invalid_r0",
        **kwargs: Any,
    ) -> dict[str, Any]:
        worker_id = str(worker["worker_id"])
        if worker_id not in absent:
            return original_prepare(
                freeze_value, worker, prior, quarantine_root, output_root,
                resume_stalled_copy, reset_context=reset_context, **kwargs,
            )
        return original_prepare(
            freeze_value, worker, prior, quarantine_root, output_root, True,
            reset_context=reset_context,
            allow_unresponsive_old_container=True,
            allow_absent_predecessor_after_interrupted_discard=True,
            **{
                key: value for key, value in kwargs.items()
                if key not in {
                    "allow_unresponsive_old_container",
                    "allow_absent_predecessor_after_interrupted_discard",
                }
            },
        )

    reset8._prepare_fresh_xp = prepare_recovery_seed
    settle.reset_xp(
        freeze, workers, output, args.token, worker_ids=set(workers),
    )
    binding = output / "xp_resets" / args.token / "reset_binding.json"
    document = load(binding)
    reset_rows = list(document.get("workers") or [])
    if (
        document.get("status") != "PASS"
        or {str(row.get("worker_id")) for row in reset_rows} != WORKER_IDS
        or set(document.get("reset_worker_ids") or []) != WORKER_IDS
        or int(document.get("reset_concurrency") or 0) != 8
    ):
        raise RuntimeError("fresh eight-worker reset binding differs")
    atomic(state_path, {
        "schema_version": "mysql_algorithm_a_xp_io_round6_data_plane_recovery_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_io",
        "worker_ids": sorted(workers),
        "physical_worker_parallelism": 8,
        "docker_control_concurrency": 2,
        "accepted_or_rejected_round_credit": 0,
        "candidate_measurements_reused": 0,
        "reaped_exact_empty_stopped_cgroups": reaped_empty_cgroups,
        "reset_binding_path": str(binding),
        "reset_binding_sha256": sha(binding),
        "source_paths": {str(path): digest for path, digest in EXPECTED.items()},
    })
    print(binding)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
