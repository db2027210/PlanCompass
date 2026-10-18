#!/usr/bin/env python3
"""Restore the original XP/IO A cohort after terminal C, without replaying A.

The C terminal state is a sequence gate only.  The exact Algorithm-A resource
freeze, worker identities, canonical baseline, accepted rounds 1--3, and the
Round-4 checkpoint remain the A predecessor's own evidence.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_mixed_ssd_post_reset_settle_v1 as settle
from scripts import refresh_mysql_xp8_live_authority_from_reset_v1 as authority
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8
from scripts import run_mysql_algorithm_a_xp_io_round4_checkpoint_resume_v1 as resume
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1"
    / "xp_effective_resource_successor.json"
)
SOURCE_AUTHORITY = (
    HISTORY / "v857_to_a_io_v856_recovery_authority_20260828"
    / "prepared_binding.json"
)
SCENE = "hybench_xp_io"
EXPECTED = {f"xp_io_w{i}" for i in range(1, 9)}
BLKIO_LIMITED_ROOT = Path("/sys/fs/cgroup/blkio/docker-limited")


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def fresh_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.exists() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"fresh directory required: {path}")
    if HISTORY.resolve() not in path.parents:
        raise argparse.ArgumentTypeError("output beneath formal history required")
    return path


def load(path: Path) -> dict[str, Any]:
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def current_discard_blkio_authority(
    worker_id: str,
    row: Mapping[str, Any],
    inspected: Mapping[str, Any],
    impl: Mapping[str, Any],
) -> dict[str, Any]:
    """Refresh stale A authority to the current post-C container identity.

    Algorithm-C point resets replace the container while keeping its logical
    worker name and mounts.  Algorithm A's preserved checkpoint consequently
    contains the correct storage device but an old container-id cgroup path.
    The transition reset needs the *current* throttle files only so it can
    safely lift those limits before discarding the post-C data plane.
    """
    container_id = str(inspected.get("Id") or "")
    pid = int((inspected.get("State") or {}).get("Pid") or 0)
    old = dict(row.get("blkio_authority") or {})
    device = str(old.get("device_major_minor") or "")
    cgroup = BLKIO_LIMITED_ROOT / container_id
    tasks = cgroup / "tasks"
    files = {
        "read": cgroup / "blkio.throttle.read_bps_device",
        "write": cgroup / "blkio.throttle.write_bps_device",
    }
    if (
        len(container_id) != 64
        or pid <= 1
        or not device
        or cgroup.is_symlink()
        or not cgroup.is_dir()
        or tasks.is_symlink()
        or not tasks.is_file()
        or any(path.is_symlink() or not path.is_file() for path in files.values())
    ):
        raise RuntimeError(f"{worker_id} current discard blkio identity differs")
    task_ids = {
        int(value) for value in tasks.read_text(encoding="ascii").split()
        if value.isdigit()
    }
    prep = impl["prep"]
    if (
        pid not in task_ids
        or prep._controller_path(pid, container_id, "blkio", "blkio") != cgroup
    ):
        raise RuntimeError(f"{worker_id} current discard blkio task differs")
    rates = {
        direction: prep._target_device_rate(path, device)
        for direction, path in files.items()
    }
    old_rates = {
        "read": int(old.get("read_bps") or 0),
        "write": int(old.get("write_bps") or 0),
    }
    limited = all(value is not None and int(value) > 0 for value in rates.values())
    already_lifted = all(value is None for value in rates.values())
    if not limited and not (
        already_lifted and all(value > 0 for value in old_rates.values())
    ):
        raise RuntimeError(f"{worker_id} current discard blkio rate differs")
    # C's terminal cleanup may already have removed both throttle entries.
    # The reset's discard helper still requires positive prior authority before
    # writing zero idempotently, so retain the sealed prior rates in that case.
    # Mixed read/write state remains fail-closed.
    discard_rates = rates if limited else old_rates
    return {
        "status": (
            "CURRENT_POST_C_DISCARD_AUTHORITY"
            if limited else "CURRENT_POST_C_ALREADY_LIFTED_DISCARD_AUTHORITY"
        ),
        "container_id": container_id,
        "container_pid": pid,
        "cgroup_path": str(cgroup),
        "device_major_minor": device,
        "read_throttle_file": str(files["read"]),
        "write_throttle_file": str(files["write"]),
        "read_bps": int(discard_rates["read"]),
        "write_bps": int(discard_rates["write"]),
        "readback_before_discard": rates,
        "already_lifted_before_transition": already_lifted,
        "authorizes_algorithm_a_workload": False,
        "used_only_to_unthrottle_before_discard": True,
    }


def stopped_discard_blkio_authority(
    worker_id: str,
    row: Mapping[str, Any],
    inspected: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind an already stopped post-C wrapper to its now-absent cgroup."""
    container_id = str(inspected.get("Id") or "")
    state = dict(inspected.get("State") or {})
    old = dict(row.get("blkio_authority") or {})
    device = str(old.get("device_major_minor") or "")
    cgroup = BLKIO_LIMITED_ROOT / container_id
    if (
        len(container_id) != 64
        or state.get("Running") is not False
        or state.get("Status") != "exited"
        or int(state.get("Pid") or 0) != 0
        or not isinstance(state.get("ExitCode"), int)
        or not device
        or cgroup.exists()
    ):
        raise RuntimeError(f"{worker_id} stopped discard identity differs")
    return {
        "status": "STOPPED_POST_C_DISCARD_AUTHORITY",
        "container_id": container_id,
        "container_pid": 0,
        "cgroup_path": str(cgroup),
        "device_major_minor": device,
        "read_throttle_file": str(cgroup / "blkio.throttle.read_bps_device"),
        "write_throttle_file": str(cgroup / "blkio.throttle.write_bps_device"),
        "read_bps": int(old.get("read_bps") or 1),
        "write_bps": int(old.get("write_bps") or 1),
        "authorizes_algorithm_a_workload": False,
        "used_only_for_interrupted_discard_recovery": True,
    }


def install_cross_assignment_transition_reset(
    rows: Mapping[str, Mapping[str, Any]],
) -> None:
    """Normalize reused physical wrappers before the ordinary A reset.

    Earlier CPU/IO capacity expansion reused these exact physical container
    names with different logical worker labels and bind mounts.  The sealed A
    reset correctly refuses to reuse such a wrapper.  Remove only the wrapper
    after exact live/stopped identity validation, leave its source datadir in
    place, and let the ordinary reset recreate the target A worker from the
    sealed template at the target A runtime root.
    """
    impl = reset8.predecessor.predecessor.predecessor.base._impl
    original = impl["_prepare_fresh_xp"]

    def normalized(
        freeze: dict[str, Any], worker: dict[str, Any], prior: dict[str, Any],
        quarantine_root: Path, output_root: Path, resume_stalled_copy: bool,
        reset_context: str = "invalid_r0", **kwargs: Any,
    ) -> dict[str, Any]:
        worker_id = str(worker["worker_id"])
        container = str(worker["container"])
        if worker_id not in rows or container != str(prior.get("container") or ""):
            raise RuntimeError(f"{worker_id} transition wrapper scope differs")
        existing = impl["_docker_run"](
            ["docker", "ps", "-aq", "--filter", f"name=^{container}$"],
            timeout=60,
        ).stdout.strip()
        if not existing:
            raise RuntimeError(f"{worker_id} transition wrapper is absent")
        inspected = impl["_docker_json"]("inspect", container)
        state = dict(inspected.get("State") or {})
        labels = dict((inspected.get("Config") or {}).get("Labels") or {})
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        if (
            inspected.get("Id") != prior.get("container_id")
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or mounts.get("/var/lib/mysql") != str(prior.get("datadir") or "")
            or mounts.get("/run/ra-credentials")
            != str(Path(str(prior.get("credential_defaults") or "")).parent)
        ):
            raise RuntimeError(f"{worker_id} transition current identity differs")
        if state.get("Running") is True:
            impl["_stop_mysql_container"](
                container, worker=prior,
                defaults=Path(str(prior["credential_defaults"])),
                allow_failed_workload_nonzero_exit=True,
            )
        else:
            stopped_discard_blkio_authority(worker_id, prior, inspected)
        surviving = impl["_docker_run"](
            ["docker", "ps", "-aq", "--filter", f"name=^{container}$"],
            timeout=60,
        ).stdout.strip()
        if surviving:
            current = impl["_docker_json"]("inspect", container)
            if (
                current.get("Id") != prior.get("container_id")
                or (current.get("State") or {}).get("Running") is not False
            ):
                raise RuntimeError(f"{worker_id} stopped wrapper identity differs")
            impl["_docker_run"](["docker", "rm", container], timeout=120)
        deadline = time.monotonic() + 30.0
        cgroup = Path(str((prior.get("blkio_authority") or {}).get("cgroup_path") or ""))
        while (
            (impl["_container_id"](container) or cgroup.exists())
            and time.monotonic() < deadline
        ):
            time.sleep(0.25)
        if impl["_container_id"](container) or cgroup.exists():
            raise RuntimeError(f"{worker_id} transition wrapper removal differs")
        return original(
            freeze, worker, prior, quarantine_root, output_root, True,
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

    impl["_prepare_fresh_xp"] = normalized
    reset8._prepare_fresh_xp = normalized


def install_cross_assignment_liveness(
    rows: Mapping[str, Mapping[str, Any]],
) -> None:
    """Treat an exact stopped cross-assigned wrapper as session count zero."""
    original = alg.classify_xp_mysql_liveness
    impl = reset8.predecessor.predecessor.predecessor.base._impl

    def normalized(worker: Mapping[str, Any]) -> dict[str, Any]:
        worker_id = str(worker.get("worker_id") or "")
        observation = dict(
            worker.get("pre_transition_docker_resource_observation") or {}
        )
        if worker_id not in rows or observation.get("current_running") is not False:
            return original(worker)
        container = str(worker.get("container") or "")
        inspected = impl["_docker_json"]("inspect", container)
        state = dict(inspected.get("State") or {})
        labels = dict((inspected.get("Config") or {}).get("Labels") or {})
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        container_id = str(inspected.get("Id") or "")
        cgroup = BLKIO_LIMITED_ROOT / container_id
        proven = (
            container_id == str(worker.get("container_id") or "")
            and state.get("Running") is False
            and state.get("Status") == "exited"
            and int(state.get("Pid") or 0) == 0
            and state.get("Restarting") is False
            and state.get("Dead") is False
            and labels.get("ra.owner") == "mysql_four_scene_v5"
            and mounts.get("/var/lib/mysql") == str(worker.get("datadir") or "")
            and mounts.get("/run/ra-credentials")
            == str(Path(str(worker.get("credential_defaults") or "")).parent)
            and not cgroup.exists()
        )
        if not proven:
            return original(worker)
        return {
            "status": "PROVEN_NO_LIVE_MYSQLD",
            "worker_id": worker_id,
            "container": container,
            "expected_container_id": container_id,
            "first_container_id": container_id,
            "docker_running": False,
            "container_identity_stable": True,
            "mysqld_rows": [],
            "live_mysqld_rows": [],
            "safe_to_discard_unresponsive_predecessor": True,
            "session_count_semantics": (
                "zero_by_exact_stopped_cross_assignment_and_absent_cgroup"
            ),
            "stopped_cross_assignment_recovery": True,
        }

    alg.classify_xp_mysql_liveness = normalized


def validate_c_sequence_gate(path: Path) -> dict[str, Any]:
    state = load(path)
    promotion = state.get("effective_terminal_state_projection") is True
    legacy_promotion = isinstance(state.get("source_terminal_promotion"), Mapping)
    if (
        state.get("algorithm") != "C"
        or state.get("async_scene") != SCENE
        or state.get("active_scenes") != [SCENE]
        or state.get("status") != "COMPLETE"
        or state.get("stage") != "FINISHED"
        or state.get("error") not in (None, "")
        or (state.get("converged") or {}).get(SCENE) is not True
        or state.get("completion_reason")
        != "all_active_log_domain_widths_reached_1pct"
        or not (promotion or legacy_promotion)
    ):
        raise RuntimeError("XP/IO Algorithm-C terminal sequence gate differs")
    return state


def current_predecessor_rows() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    freeze = load(FREEZE)
    source = load(SOURCE_AUTHORITY)
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or [] if row.get("worker_id") in EXPECTED
    }
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source.get("workers") or [] if row.get("worker_id") in EXPECTED
    }
    if (
        freeze.get("schema_version") != "mysql_xp_eight_worker_resource_successor_v1"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or source.get("status") != "PREPARED"
        or set(frozen) != EXPECTED
        or set(rows) != EXPECTED
        or any(frozen[name].get("scene") != SCENE for name in EXPECTED)
    ):
        raise RuntimeError("XP/IO Algorithm-A original cohort authority differs")

    reset8.install_for_scene(rows)
    reset8.configure(freeze=FREEZE, prepared=SOURCE_AUTHORITY)
    impl = reset8.predecessor.predecessor.predecessor.base._impl
    for worker_id in sorted(EXPECTED):
        row = rows[worker_id]
        inspected = impl["_docker_json"]("inspect", str(row["container"]))
        state = inspected.get("State") or {}
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        datadir = Path(mounts.get("/var/lib/mysql") or "")
        credentials = Path(mounts.get("/run/ra-credentials") or "")
        running = state.get("Running") is True and int(state.get("Pid") or 0) > 1
        stopped = (
            state.get("Running") is False
            and state.get("Status") == "exited"
            and int(state.get("Pid") or 0) == 0
            and isinstance(state.get("ExitCode"), int)
        )
        if (
            not (running or stopped)
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or not datadir.is_dir()
            or not credentials.is_dir()
            or not (credentials / "selected.cnf").is_file()
        ):
            raise RuntimeError(f"{worker_id} current predecessor identity differs")
        row.update({
            "container_id": str(inspected["Id"]),
            "container_pid": int(state["Pid"]),
            "runtime_root": str(datadir.parent),
            "datadir": str(datadir),
            "credential_defaults": str(credentials / "selected.cnf"),
            "benchmark_defaults": str(credentials / "benchmark.cnf"),
            "ready": running,
        })
        row["blkio_authority"] = (
            current_discard_blkio_authority(worker_id, row, inspected, impl)
            if running
            else stopped_discard_blkio_authority(worker_id, row, inspected)
        )
        # These containers have just finished Algorithm C.  Their current CPU
        # quota and memory limit are therefore allowed (and expected) to
        # differ from Algorithm A's frozen envelope.  Validating A's target
        # envelope here made the C-to-A transition impossible: the check ran
        # before the reset whose purpose is to restore that envelope.
        #
        # Keep a non-authorizing observation for provenance.  The eight-way
        # reset below writes the A envelope and its reset binding validates the
        # effective kernel cgroup and blkio readback before any A workload may
        # start.  In particular, do not call ``_enforce_blkio`` here because a
        # pre-transition inventory must not mutate the completed C cohort.
        host = dict(inspected.get("HostConfig") or {})
        row["pre_transition_docker_resource_observation"] = {
            "source_algorithm": "C",
            "authorizes_algorithm_a_workload": False,
            "restored_and_validated_by_transition_reset": True,
            "cpu_period": int(host.get("CpuPeriod") or 0),
            "cpu_quota": int(host.get("CpuQuota") or 0),
            "memory": int(host.get("Memory") or 0),
            "memory_swap": int(host.get("MemorySwap") or 0),
            "pids_limit": int(host.get("PidsLimit") or 0),
            "cpuset_cpus": str(host.get("CpusetCpus") or ""),
            "cpuset_mems": str(host.get("CpusetMems") or ""),
            "current_running": running,
            "current_logical_worker_label": str(labels.get("ra.worker_id") or ""),
            "current_datadir_mount": str(datadir),
            "discard_blkio_authority_refreshed_to_current_container": True,
        }
    return freeze, rows


def prepare(c_terminal: Path, reset_root: Path, authority_root: Path) -> Path:
    c_state = validate_c_sequence_gate(c_terminal)
    predecessor, _candidates = resume.validate_predecessor()
    reuse = load(resume.REUSE_MANIFEST)
    if (
        int(predecessor.get("accepted") or 0) != 3
        or int(predecessor.get("round_index") or 0) != 4
        or ((reuse.get("algorithm_a") or {}).get("feedback_to_b_or_c")) is not False
        or (
            ((reuse.get("algorithm_a") or {}).get("scenes") or {})
            .get(SCENE, {}).get("disposition")
        ) != "RESUME_FROM_VALID_ROUND4_AFTER_C_TERMINAL"
    ):
        raise RuntimeError("XP/IO Algorithm-A Round-4 reuse boundary differs")
    freeze, rows = current_predecessor_rows()
    install_cross_assignment_liveness(rows)
    install_cross_assignment_transition_reset(rows)
    reset_root.mkdir(parents=True, mode=0o700)
    token = reset_root.name + "_a_io_round4_transition"

    # Configure the shared measurement reset exactly as Algorithm A v12 does.
    alg.FREEZE = FREEZE
    alg.r0mod.FREEZE = FREEZE
    alg.r0mod.FREEZE_SCHEMA = str(freeze["schema_version"])
    alg.XP_IDS = tuple(sorted(EXPECTED))
    alg.WORKER_IDS = tuple(sorted(EXPECTED))
    alg.reset = reset8
    settle.alg.reset = reset8
    settle.reset_xp(freeze, rows, reset_root, token, worker_ids=set(EXPECTED))

    binding = reset_root / "xp_resets" / token / "reset_binding.json"
    reset_document = load(binding)
    if (
        reset_document.get("status") != "PASS"
        or set(reset_document.get("reset_worker_ids") or []) != EXPECTED
        or reset_document.get("freeze_sha256") != alg.sha(FREEZE)
        or int(reset_document.get("reset_concurrency") or 0) != 8
    ):
        raise RuntimeError("XP/IO Algorithm-A transition reset differs")
    result = authority.prepare(SCENE, binding, authority_root)
    live = Path(str(result.get("authority") or "")).resolve()
    live_document = load(live)
    if (
        result.get("status") != "PASS"
        or live_document.get("status") != "PASS"
        or live_document.get("scene") != SCENE
        or int(live_document.get("physical_replay_parallelism") or 0) != 8
    ):
        raise RuntimeError("XP/IO Algorithm-A post-C live authority differs")
    alg.atomic(reset_root / "a_round4_transition_manifest.json", {
        "schema_version": "mysql_algorithm_a_xp_io_round4_after_c_transition_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE,
        "algorithm_c_sequence_gate_only": True,
        "algorithm_c_state_path": str(c_terminal),
        "algorithm_c_state_sha256": alg.sha(c_terminal),
        "algorithm_c_candidate_qps_ranges_or_direction_used_by_a": False,
        "pre_transition_c_resource_envelope_authorized_for_a": False,
        "algorithm_a_resource_envelope_restored_before_workload": True,
        "algorithm_a_rounds_1_to_3_replayed": False,
        "algorithm_a_round4_new_physical_work_starts_at_rank": 1,
        "algorithm_a_predecessor_state_path": str(resume.SOURCE_STATE),
        "algorithm_a_predecessor_state_sha256": alg.sha(resume.SOURCE_STATE),
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": alg.sha(FREEZE),
        "reset_binding_path": str(binding),
        "reset_binding_sha256": alg.sha(binding),
        "live_authority_path": str(live),
        "live_authority_sha256": alg.sha(live),
        "accepted_or_rejected_round_credit_increment": 0,
        "c_outer_attempt": int(c_state.get("outer_attempt") or 0),
    })
    return live


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c-terminal-state", type=regular_file, required=True)
    parser.add_argument("--reset-root", type=fresh_directory, required=True)
    parser.add_argument("--authority-root", type=fresh_directory, required=True)
    args = parser.parse_args()
    live = prepare(args.c_terminal_state, args.reset_root, args.authority_root)
    print(live)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
