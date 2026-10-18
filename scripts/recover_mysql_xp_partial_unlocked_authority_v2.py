#!/usr/bin/env python3
"""Complete only missing lanes from a partially successful unlocked XP8 reset.

The completed sibling reset rows are reused only after live Docker, MySQL,
kernel-cgroup, and blkio read-back.  Missing lanes are reset from their exact
current container identities.  A running identity that was created by an
earlier interrupted reset has its blkio authority re-read before discard;
this prevents a stale predecessor cgroup path from being treated as current
authority.  No workload measurement or optimizer-round credit is reused.
"""

from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import refresh_mysql_xp8_live_authority_from_reset_v1 as refresh
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v3 as reset_v3
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v8 as runtime_v8


FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1"
    / "xp_effective_resource_successor.json"
)
SCENES = {
    "hybench_xp_cpu": {
        "prefix": "xp_cpu",
        "token": "algorithm_a_round_002_wave_002_unlocked_authority_v2",
    },
    "hybench_xp_io": {
        "prefix": "xp_io",
        "token": "algorithm_b_outer_012_rank_1_unlocked_authority_v2",
    },
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--partial-stage", required=True)
    parser.add_argument(
        "--additional-completed-stage", action="append", default=[],
        help="Earlier partial stage(s) whose successful reset rows are merged.",
    )
    parser.add_argument(
        "--resume-interrupted-stage",
        help=(
            "An interrupted recovery stage whose exact run-scoped quarantine "
            "and staging directories must be resumed instead of discarded."
        ),
    )
    parser.add_argument(
        "--finalize-completed-stage", action="store_true",
        help=(
            "Finalize a stage whose eight reset_worker rows are already "
            "complete but whose merged account/binding/authority step was "
            "interrupted. No data-plane reset is performed."
        ),
    )
    parser.add_argument("--reset-output", required=True)
    parser.add_argument("--authority-output", required=True)
    args = parser.parse_args()

    scene = args.scene
    prefix = str(SCENES[scene]["prefix"])
    token = str(SCENES[scene]["token"])
    prepared_path = Path(args.prepared).resolve()
    partial_stage = Path(args.partial_stage).resolve()
    additional_completed_stages = [
        Path(value).resolve() for value in args.additional_completed_stage
    ]
    resume_interrupted_stage = (
        Path(args.resume_interrupted_stage).resolve()
        if args.resume_interrupted_stage else None
    )
    reset_output = Path(args.reset_output).resolve()
    authority_output = Path(args.authority_output).resolve()
    if reset_output.exists() or reset_output.is_symlink():
        raise RuntimeError(f"fresh reset output required: {reset_output}")
    if authority_output.exists() or authority_output.is_symlink():
        raise RuntimeError(f"fresh authority output required: {authority_output}")

    freeze = load(FREEZE)
    prepared = load(prepared_path)
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    all_frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
    }
    frozen = {
        worker_id: copy.deepcopy(all_frozen[worker_id])
        for worker_id in sorted(expected)
    }
    prior = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or []
        if row.get("worker_id") in expected
    }
    completed: dict[str, dict[str, Any]] = {}
    completed_stage_sources = [partial_stage, *additional_completed_stages]
    for completed_stage_index, completed_stage in enumerate(completed_stage_sources):
        if (
            not completed_stage.is_dir()
            or (
                completed_stage_index == 0
                and completed_stage.name != token
            )
            or completed_stage.parent.name != "xp_resets"
            or (
                (completed_stage / "reset_binding.json").exists()
                and not (
                    args.finalize_completed_stage
                    and completed_stage_index == 0
                )
            )
        ):
            raise RuntimeError("partial completed-stage authority differs")
        for path in completed_stage.glob("*/reset_worker.json"):
            worker_id = path.parent.name
            row = load(path)
            prior_row = completed.get(worker_id)
            if prior_row is not None and prior_row != row:
                raise RuntimeError(f"{worker_id} duplicate completed row differs")
            completed[worker_id] = row
    missing = expected - set(completed)
    partial_boundary_ok = (
        bool(completed)
        and set(completed) < expected
        and bool(missing)
        and not args.finalize_completed_stage
    )
    completed_finalize_boundary_ok = (
        args.finalize_completed_stage
        and set(completed) == expected
        and not missing
        and (partial_stage / "reset_binding.json").is_file()
    )
    if (
        prepared.get("status") != "PREPARED"
        or set(prior) != expected
        or not (partial_boundary_ok or completed_finalize_boundary_ok)
    ):
        raise RuntimeError("partial XP8 recovery boundary differs")

    reset_v3.install_for_workers(all_frozen)
    reset_v3.configure(freeze=FREEZE, prepared=prepared_path)
    if (
        reset_v3.predecessor.base._impl["_docker_run"]
        is not reset_v3.predecessor.base._base_docker_run
        or int(reset_v3.predecessor.base._impl["DOCKER_CONTROL_CONCURRENCY"]) != 8
    ):
        raise RuntimeError("unlocked eight-way Docker control is not active")
    impl = reset_v3.predecessor.base._impl
    original_reset_subprocess = impl["subprocess"]
    physical_storage_timeout_seconds = 7200

    class PhysicalStorageSubprocessProxy:
        def __getattr__(self, name: str) -> Any:
            return getattr(original_reset_subprocess, name)

        def run(self, argv: Any, *run_args: Any, **run_kwargs: Any):
            tokens = [str(value) for value in argv] if isinstance(
                argv, (list, tuple)
            ) else []
            long_physical_operation = (
                ("cp" in tokens and "--reflink=never" in tokens)
                or ("chown" in tokens and "-R" in tokens)
                or ("find" in tokens and "-exec" in tokens)
                or ("rm" in tokens and "-rf" in tokens)
            )
            if long_physical_operation:
                run_kwargs["timeout"] = max(
                    float(run_kwargs.get("timeout") or 0),
                    float(physical_storage_timeout_seconds),
                )
            elif "test" in tokens and any(
                str(value).startswith(("/disk1/", "/disk2/"))
                for value in tokens
            ):
                run_kwargs["timeout"] = max(
                    float(run_kwargs.get("timeout") or 0), 300.0,
                )
            return original_reset_subprocess.run(argv, *run_args, **run_kwargs)

    impl["subprocess"] = PhysicalStorageSubprocessProxy()
    original_docker_run = impl["_docker_run"]
    docker_control_timeout_seconds = 900

    def recovery_docker_run(argv: list[str], *, timeout: int = 240):
        # Full-parallel XP launches intentionally allow many scene-local
        # create/start calls to coexist. Docker may complete the operation in
        # the daemon while its client response is queued for more than the
        # historical 120/240-second bound. Preserve fail-closed identity
        # checks, but do not misclassify that control-plane latency.
        return original_docker_run(
            argv, timeout=max(int(timeout), docker_control_timeout_seconds)
        )

    impl["_docker_run"] = recovery_docker_run

    workers: dict[str, dict[str, Any]] = {}
    for worker_id, row in sorted(completed.items()):
        inspect = reset_v3._docker_json("inspect", str(row["container"]))
        state = inspect.get("State") or {}
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspect.get("Mounts") or []
        }
        tables = impl["_host_mysql"](
            row, Path(str(row["credential_defaults"])),
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x';",
            timeout=60,
        ).stdout.strip()
        if (
            row.get("worker_id") != worker_id
            or inspect.get("Id") != row.get("container_id")
            or state.get("Running") is not True
            or labels.get("ra.worker_id") != worker_id
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or mounts.get("/var/lib/mysql") != str(row["datadir"])
            or tables != "8"
        ):
            raise RuntimeError(f"{worker_id} completed sibling reset differs")
        impl["prep"]._kernel_resource_readback(inspect, row)
        current_blkio = impl["prep"]._enforce_blkio(inspect, freeze, row)
        if current_blkio != row.get("blkio_authority"):
            raise RuntimeError(f"{worker_id} completed sibling blkio differs")
        workers[worker_id] = copy.deepcopy(row)

    refreshed_running: list[str] = []
    stopped_exact: list[str] = []
    absent_after_interrupted_discard: list[str] = []
    for worker_id in sorted(missing):
        row = copy.deepcopy(prior[worker_id])
        container = str(frozen[worker_id]["container"])
        current_container_id = reset_v3._container_id(container)
        if not current_container_id:
            prior_container_id = str(row.get("container_id") or "")
            prior_cgroup = Path(str(
                (row.get("blkio_authority") or {}).get("cgroup_path") or ""
            ))
            expected_cgroup = (
                Path("/sys/fs/cgroup/blkio/docker-limited")
                / prior_container_id
            )
            datadir = Path(str(row.get("datadir") or ""))
            credential_defaults = Path(str(row.get("credential_defaults") or ""))
            if (
                len(prior_container_id) != 64
                or prior_cgroup != expected_cgroup
                or prior_cgroup.exists()
                or not datadir.is_dir()
                or not credential_defaults.is_file()
                or datadir.parent != credential_defaults.parent.parent
            ):
                raise RuntimeError(
                    f"{worker_id} absent interrupted-discard authority differs"
                )
            row["technical_recovery_absent_after_interrupted_discard"] = True
            absent_after_interrupted_discard.append(worker_id)
            workers[worker_id] = row
            continue
        inspect = reset_v3._docker_json("inspect", container)
        state = inspect.get("State") or {}
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspect.get("Mounts") or []
        }
        current_id = str(inspect.get("Id") or "")
        expected_datadir = str(row["datadir"])
        if (
            len(current_id) != 64
            or labels.get("ra.worker_id") != worker_id
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or mounts.get("/var/lib/mysql") != expected_datadir
        ):
            raise RuntimeError(f"{worker_id} current Docker recovery seed differs")
        row.update({
            "container_id": current_id,
            "container_pid": int(state.get("Pid") or 0),
            "datadir": expected_datadir,
        })
        cgroup = Path("/sys/fs/cgroup/blkio/docker-limited") / current_id
        if state.get("Running") is True and int(state.get("Pid") or 0) > 1:
            row["kernel_cgroup_resources"] = impl["prep"]._kernel_resource_readback(
                inspect, frozen[worker_id]
            )
            row["blkio_authority"] = impl["prep"]._enforce_blkio(
                inspect, freeze, frozen[worker_id]
            )
            row["technical_recovery_current_blkio_refreshed"] = True
            refreshed_running.append(worker_id)
        elif (
            state.get("Running") is False
            and int(state.get("Pid") or 0) == 0
            and state.get("Restarting") is False
            and state.get("Dead") is False
            and not cgroup.exists()
        ):
            # A previous interrupted reset can have cleanly stopped the exact
            # current container after it had already replaced the authority
            # seed.  Rebind the absent discard-cgroup authority to that exact
            # inspected identity; no throttle is inferred for a live task.
            authority = copy.deepcopy(dict(row.get("blkio_authority") or {}))
            if (
                not authority.get("device_major_minor")
                or int(authority.get("read_bps") or 0) <= 0
                or int(authority.get("write_bps") or 0) <= 0
            ):
                raise RuntimeError(f"{worker_id} stopped blkio seed differs")
            cgroup_text = str(cgroup)
            authority.update({
                "cgroup_path": cgroup_text,
                "read_throttle_file": (
                    cgroup_text + "/blkio.throttle.read_bps_device"
                ),
                "write_throttle_file": (
                    cgroup_text + "/blkio.throttle.write_bps_device"
                ),
            })
            row["blkio_authority"] = authority
            row["technical_recovery_stopped_current_identity_rebound"] = (
                current_id != prior[worker_id].get("container_id")
            )
            stopped_exact.append(worker_id)
        else:
            raise RuntimeError(f"{worker_id} current liveness is not recoverable")
        workers[worker_id] = row

    alg = runtime_v8.settle_v32.runtime.alg
    alg.FREEZE = FREEZE
    alg.reset = reset_v3
    alg.WORKER_IDS = tuple(sorted(all_frozen))
    alg.XP_IDS = tuple(sorted(all_frozen))
    alg.XP_RESET_MAX_WORKERS = 8
    original_classify = alg.classify_xp_mysql_liveness

    def classify_recovery_seed(worker: Mapping[str, Any]) -> dict[str, Any]:
        worker_id = str(worker["worker_id"])
        if worker_id in absent_after_interrupted_discard:
            container = str(worker["container"])
            expected_id = str(worker["container_id"])
            cgroup = Path("/sys/fs/cgroup/blkio/docker-limited") / expected_id
            if reset_v3._container_id(container) or cgroup.exists():
                raise RuntimeError(f"{worker_id} absent recovery proof differs")
            return {
                "worker_id": worker_id,
                "expected_container": container,
                "observed_container": None,
                "expected_container_id": expected_id,
                "first_container_id": None,
                "docker_running": False,
                "container_identity_stable": True,
                "mysqld_rows": [],
                "live_mysqld_rows": [],
                "status": "PROVEN_NO_LIVE_MYSQLD",
                "safe_to_discard_unresponsive_predecessor": True,
                "session_count_semantics": (
                    "zero_by_exact_absent_interrupted_discard_identity_and_absent_cgroup"
                ),
                "technical_authority_recovery_only": True,
            }
        if worker_id not in stopped_exact:
            return original_classify(worker)
        container = str(worker["container"])
        expected_id = str(worker["container_id"])
        inspect = reset_v3._docker_json("inspect", container)
        state = inspect.get("State") or {}
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        cgroup = Path("/sys/fs/cgroup/blkio/docker-limited") / expected_id
        if (
            inspect.get("Id") != expected_id
            or state.get("Running") is not False
            or int(state.get("Pid") or 0) != 0
            or state.get("Restarting") is not False
            or state.get("Dead") is not False
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or cgroup.exists()
        ):
            raise RuntimeError(f"{worker_id} stopped recovery proof differs")
        return {
            "worker_id": worker_id,
            "expected_container": container,
            "observed_container": container,
            "expected_container_id": expected_id,
            "first_container_id": expected_id,
            "docker_running": False,
            "container_identity_stable": True,
            "mysqld_rows": [],
            "live_mysqld_rows": [],
            "status": "PROVEN_NO_LIVE_MYSQLD",
            "safe_to_discard_unresponsive_predecessor": True,
            "session_count_semantics": (
                "zero_by_exact_stopped_reset_identity_and_absent_cgroup"
            ),
            "technical_authority_recovery_only": True,
        }

    alg.classify_xp_mysql_liveness = classify_recovery_seed
    reset_output.mkdir(parents=True, mode=0o700)
    started = time.monotonic()
    original_prepare = reset_v3._prepare_fresh_xp
    if resume_interrupted_stage is not None:
        if (
            resume_interrupted_stage != partial_stage
            or resume_interrupted_stage.name != token
            or resume_interrupted_stage.parent.name != "xp_resets"
            or not resume_interrupted_stage.is_dir()
        ):
            raise RuntimeError("interrupted recovery stage authority differs")
        resume_run_name = resume_interrupted_stage.parent.parent.name
        # mixed_four_worker_v2 derives the exact per-device quarantine from
        # only these final two components.  The placeholder prefix is never
        # used as a storage authority.
        resume_quarantine = Path("/resume-authority") / resume_run_name / token

        def prepare_interrupted_staging(
            freeze_value: dict[str, Any], worker_value: dict[str, Any],
            prior_value: dict[str, Any], _quarantine_root: Path,
            output_root: Path, _resume_stalled_copy: bool,
            reset_context: str = "formal_point", **kwargs: Any,
        ) -> dict[str, Any]:
            worker_id = str(worker_value["worker_id"])
            if worker_id in absent_after_interrupted_discard:
                # The exact predecessor was already discarded by the prior
                # interrupted attempt.  Its absent cgroup and runtime-root
                # identity were proven above; carry that explicit authority
                # into the physical reset instead of falling back to the
                # default old-container-present check.
                kwargs["allow_unresponsive_old_container"] = True
                kwargs[
                    "allow_absent_predecessor_after_interrupted_discard"
                ] = True
            return original_prepare(
                freeze_value, worker_value, prior_value, resume_quarantine,
                output_root, True, reset_context,
                allow_bootstrap_touched_staging=True,
                force_fresh_copy_on_resume=False,
                **kwargs,
            )

        reset_v3._prepare_fresh_xp = prepare_interrupted_staging
    if missing:
        try:
            runtime_v8.scene_scoped_reset(
                freeze, workers, reset_output, token, worker_ids=set(missing),
            )
        finally:
            reset_v3._prepare_fresh_xp = original_prepare
        stage = reset_output / "xp_resets" / token
    else:
        reset_v3._prepare_fresh_xp = original_prepare
        stage = reset_output / "xp_resets" / token
        stage.mkdir(parents=True, mode=0o700)

    # The base reset provisions only the lanes it reset.  Provision the
    # completed siblings now so the merged authority is complete.
    account_root = stage / "accounts"
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="xp8-recovery-users") as pool:
        futures = {
            pool.submit(users._run_worker, workers[worker_id], account_root, True, False): worker_id
            for worker_id in sorted(completed)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            workers[worker_id] = {
                **frozen[worker_id], **workers[worker_id], **future.result(),
            }

    sync_rows: list[dict[str, Any]] = []
    for worker_id in sorted(expected):
        datadir = Path(str(workers[worker_id]["datadir"])).resolve()
        anchor = datadir.parent
        completed_sync = subprocess.run(
            ["sync", "-f", str(anchor)], capture_output=True, text=True,
            check=False, timeout=300,
        )
        if completed_sync.returncode != 0:
            raise RuntimeError(f"{worker_id} aggregate recovery syncfs failed")
        sync_rows.append({
            "worker_id": worker_id,
            "datadir": str(datadir),
            "syncfs_anchor": str(anchor),
            "syncfs_returncode": completed_sync.returncode,
        })

    binding_path = stage / "reset_binding.json"
    binding = load(
        binding_path if binding_path.is_file()
        else partial_stage / "reset_binding.json"
    )
    binding.update({
        "schema_version": "mysql_xp8_partial_unlocked_reset_recovery_v2",
        "workers": [workers[worker_id] for worker_id in sorted(expected)],
        "reset_worker_ids": sorted(missing),
        "reset_concurrency": len(missing),
        "elapsed_seconds": time.monotonic() - started,
        "technical_recovery": {
            "partial_stage": str(partial_stage),
            "completed_stage_sources": [
                str(path) for path in completed_stage_sources
            ],
            "completed_sibling_resets_reused": sorted(completed),
            "missing_workers_reset": sorted(missing),
            "running_seed_blkio_authority_refreshed": sorted(refreshed_running),
            "stopped_exact_identity_recovered": sorted(stopped_exact),
            "absent_after_interrupted_discard_recovered": sorted(
                absent_after_interrupted_discard
            ),
            "interrupted_staging_resume": (
                str(resume_interrupted_stage)
                if resume_interrupted_stage is not None else None
            ),
            "completed_stage_finalization_only": bool(
                args.finalize_completed_stage and not missing
            ),
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
            "docker_create_start_lock": False,
            "physical_storage_operation_timeout_seconds": (
                physical_storage_timeout_seconds
            ),
            "docker_control_operation_timeout_seconds": (
                docker_control_timeout_seconds
            ),
        },
    })
    alg.atomic(binding_path, binding)
    alg.atomic(stage / "aggregate_post_reset_storage_settle.json", {
        "schema_version": "mysql_xp8_partial_recovery_storage_settle_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": scene,
        "workers": sync_rows,
        "scene_scoped": True,
        "device_wide_quiet_wait": False,
        "sibling_scene_completion_dependency": False,
    })
    result = refresh.prepare(scene, binding_path, authority_output)
    print(json.dumps({
        "status": "PASS",
        "scene": scene,
        "reset_binding": str(binding_path),
        "authority": result["authority"],
        "completed_reused": sorted(completed),
        "missing_reset": sorted(missing),
        "worker_count": len(result["workers"]),
        "candidate_measurements_reused": 0,
        "accepted_or_rejected_credit": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_RECOVERY_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise
