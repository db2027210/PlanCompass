#!/usr/bin/env python3
"""Build the local-only ff313/shared-inventory qualification support seal.

This builder performs no remote action and grants no formal credit.  It
derives from the exact current shared collector, keeps its two-daemon Docker
inventory, and hardens each process sample with a bounded consistent-snapshot
retry plus fail-closed raw diagnostic sealing.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "44db536b86ff7319787a5e471d656e3810cb7cb5c03f8efe282b860029c83ba9"
PARENT_MANIFEST_SHA256 = "9a814d5056b9de17253282dceb738a2611bfcaba588144571ef7daa7eab0276f"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_current_v1" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_inventory_retry_support_v1"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
BUILDER = "authority/build_mysql_tpch_inventory_retry_support_v1.py"
TEST = "tests/test_mysql_tpch_inventory_retry_support_v1.py"
REMEDIATION = "authority/mysql_tpch_ff313_inventory_retry_remediation_v1.json"
BLKIO_SUPPLEMENT = "qualification/mysql_tpch_legacy_blkio_supplement_v1.py"
TPCH_ID = "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
TPCH_MANIFEST_SHA256 = "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
TPCH_COLLECTOR_SHA256 = "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"
TPCH_LAUNCHER_SHA256 = "120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408"
TPCH_AUTHORITY_RENDERER_SHA256 = "f8f543902d906d1570cd1dfa7c752ce1262a33543914dbddeb9bed9ebcbed7e2"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(label + " is not an object")
    return value


def files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path, "parent manifest")
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or files(PARENT) != manifest.get("files")
        or (manifest.get("target_bundle_ids") or {}).get("tpch_memory") != TPCH_ID
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity/scope/target/authority differs")
    target_manifest = load(
        ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1"
        / TPCH_ID / "bundle_manifest.json",
        "ff313 target manifest",
    )
    hashes = target_manifest.get("component_hashes") or {}
    if (
        sha_file(
            ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1"
            / TPCH_ID / "bundle_manifest.json"
        ) != TPCH_MANIFEST_SHA256
        or hashes.get("collector") != TPCH_COLLECTOR_SHA256
        or hashes.get("bootstrap_authority") != TPCH_LAUNCHER_SHA256
        or hashes.get("qualification_authority_renderer")
        != TPCH_AUTHORITY_RENDERER_SHA256
    ):
        raise BuildError("ff313 manifest/collector/launcher/authority seal differs")
    return manifest


def patch_entry(payload: bytes) -> bytes:
    text = payload.decode("utf-8")
    marker = '_active_profile = None\n'
    addition = '''_active_profile = None
PROCESS_INVENTORY_MAXIMUM_ATTEMPTS = 16
PROCESS_INVENTORY_RETRY_BACKOFF_SECONDS = (0.01, 0.02, 0.03, 0.04)
_latest_process_inventory_result = None
_daemon_process_baselines = {}
TPCH_LEGACY_BLKIO_SUPPLEMENT = "blkio_throttle_authority.supplement.json"
'''
    if text.count(marker) != 1:
        raise BuildError("entry global marker differs")
    text = text.replace(marker, addition)

    old_merge = '''            merged.update(containers)
            evidence_by_unit[profile["unit"]] = evidence
'''
    new_merge = '''            for container_id, row in containers.items():
                row["docker_socket_uri"] = profile["socket_uri"]
                row["docker_unit"] = profile["unit"]
                merged[container_id] = row
            evidence_by_unit[profile["unit"]] = evidence
'''
    if text.count(old_merge) != 1:
        raise BuildError("multi-socket merge marker differs")
    text = text.replace(old_merge, new_merge)

    old_combined = '''        "sockets": evidence_by_unit,
        "container_ids": sorted(merged),
'''
    new_combined = '''        "sockets": evidence_by_unit,
        "container_ids": sorted(merged),
        "containers": [
            {
                "container_id": row["container_id"],
                "name": row["name"],
                "init_pid": row["init_pid"],
                "mounts": row["mounts"],
                "mounts_sha256": row["mounts_sha256"],
                "docker_socket_uri": row["docker_socket_uri"],
                "docker_unit": row["docker_unit"],
            }
            for row in sorted(merged.values(), key=lambda value: value["container_id"])
        ],
'''
    if text.count(old_combined) != 1:
        raise BuildError("multi-socket closure marker differs")
    text = text.replace(old_combined, new_combined)

    validation_marker = '''def validate_effective_blkio_authority(container_row, runtime, defaults_file, bundle_id):
'''
    validation_helper = r'''def _validate_tpch_legacy_blkio_supplement(
    supplement, authority_path, authority_value, container_row, pid,
    container_id, cgroup_path, storage, envelope, bundle_id,
):
    supplement_path = authority_path.with_name(TPCH_LEGACY_BLKIO_SUPPLEMENT)
    if (
        supplement.get("schema_version")
        != "mysql_tpch_legacy_blkio_live_supplement_v1"
        or supplement.get("status")
        != "PASS_LEGACY_AUTHORITY_PLUS_LIVE_READ_ONLY_SUPPLEMENT"
        or supplement.get("formal_scope") != "mysql_only"
        or supplement.get("cross_engine_comparison_prohibited") is not True
        or supplement.get("postgresql_mutation_prohibited") is not True
        or supplement.get("source_authority_path") != str(authority_path)
        or supplement.get("source_authority_file_sha256")
        != core.sha256(authority_path)
        or supplement.get("source_authority_self_sha256")
        != authority_value.get("authority_sha256")
        or supplement.get("bundle_id") != bundle_id
        or supplement.get("attempt_token") != authority_path.parent.name
        or supplement.get("database_container_id") != container_id
        or int(supplement.get("database_pid") or 0) != pid
        or int(supplement.get("database_pid_starttime_ticks") or 0)
        != core.process_start_ticks(pid)
        or supplement.get("container_running") is not True
        or supplement.get("container_restarting") is not False
        or type(supplement.get("container_restart_count")) is not int
        or supplement.get("container_restart_count") != 0
        or supplement.get("database_restarted_by_remediation") is not False
        or supplement.get("cgroup_version") != "v1"
        or supplement.get("blkio_cgroup_path") != cgroup_path
        or supplement.get("device_major_minor")
        != storage["expected_live_major_minor"]
        or int(supplement.get("read_bps") or 0)
        != int(envelope["io_read_bps"])
        or int(supplement.get("write_bps") or 0)
        != int(envelope["io_write_bps"])
        or supplement.get("live_effective_cgroup_v1_throttle_verified")
        is not True
        or type(supplement.get("memory_swappiness")) is not int
        or not 0 <= supplement.get("memory_swappiness") <= 100
        or supplement.get("memory_swappiness_enforcement")
        != "recorded_only_zero_swap_capacity_is_authoritative"
        or type(supplement.get("host_config_memory_bytes")) is not int
        or supplement.get("host_config_memory_bytes")
        != int(envelope["memory_bytes"])
        or type(supplement.get("host_config_memory_swap_bytes")) is not int
        or supplement.get("host_config_memory_swap_bytes")
        != int(envelope["memory_swap_bytes"])
        or supplement.get("host_config_memory_swap_bytes")
        != supplement.get("host_config_memory_bytes")
        or type(supplement.get("host_config_swap_capacity_bytes")) is not int
        or supplement.get("host_config_swap_capacity_bytes") != 0
        or type(supplement.get("cgroup_total_swap_bytes")) is not int
        or supplement.get("cgroup_total_swap_bytes") != 0
        or type(supplement.get("process_tree_total_swap_bytes")) is not int
        or supplement.get("process_tree_total_swap_bytes") != 0
        or supplement.get("process_snapshot_consistent") is not True
        or supplement.get("authority_file_mutated") is not False
        or supplement.get("database_or_client_mutation_performed") is not False
        or supplement.get("postgresql_mutation_performed") is not False
        or supplement.get("storage_mutation_performed") is not False
        or supplement.get("network_mutation_performed") is not False
        or supplement.get("formal_launch_authorized") is not False
        or supplement.get("formal_r0_authorized") is not False
        or supplement.get("accepted_round_authorized") is not False
        or supplement.get("accepted_rounds_credit") != 0
        or not core.valid_sidecar(supplement_path)
    ):
        raise core.QualificationError(
            "TPC-H legacy blkio live supplement differs"
        )
    state = container_row.get("State") or {}
    if (
        state.get("Running") is not True
        or state.get("Restarting") is not False
        or int(container_row.get("RestartCount") or 0) != 0
    ):
        raise core.QualificationError(
            "TPC-H live container restart identity differs"
        )
    return {
        "path": str(supplement_path),
        "sha256": core.sha256(supplement_path),
        "database_pid_starttime_ticks":
            supplement["database_pid_starttime_ticks"],
        "memory_swappiness": supplement["memory_swappiness"],
        "memory_swappiness_enforcement":
            supplement["memory_swappiness_enforcement"],
        "host_config_memory_bytes": supplement["host_config_memory_bytes"],
        "host_config_memory_swap_bytes":
            supplement["host_config_memory_swap_bytes"],
        "host_config_swap_capacity_bytes":
            supplement["host_config_swap_capacity_bytes"],
        "cgroup_total_swap_bytes": supplement["cgroup_total_swap_bytes"],
        "process_tree_total_swap_bytes":
            supplement["process_tree_total_swap_bytes"],
        "container_restart_count": supplement["container_restart_count"],
        "database_restarted_by_remediation": False,
        "postgresql_mutation_performed": False,
        "storage_mutation_performed": False,
    }


def validate_effective_blkio_authority(container_row, runtime, defaults_file, bundle_id):
'''
    if text.count(validation_marker) != 1:
        raise BuildError("blkio validator marker differs")
    text = text.replace(validation_marker, validation_helper)

    old_legacy = '''    if scenario == "tpch_memory":
        unsigned = dict(value)
        recorded = unsigned.pop("authority_sha256", None)
        if (
            recorded != core.sha_bytes(core.canonical(unsigned))
            or int(value.get("database_pid_starttime_ticks") or 0)
            != core.process_start_ticks(pid)
            or value.get("memory_swappiness") != 0
            or value.get("total_swap_bytes") != 0
            or value.get("database_restarted_by_remediation") is not False
            or value.get("postgresql_mutation_performed") is not False
            or value.get("storage_mutation_performed") is not False
        ):
            raise core.QualificationError(
                "TPC-H restart blkio authority digest/identity differs"
            )
    else:
        recorded = core.sha256(authority_path)
'''
    new_legacy = '''    legacy_supplement = None
    if scenario == "tpch_memory":
        unsigned = dict(value)
        recorded = unsigned.pop("authority_sha256", None)
        if recorded != core.sha_bytes(core.canonical(unsigned)):
            raise core.QualificationError(
                "TPC-H legacy blkio authority self-digest differs"
            )
        supplement_path = authority_path.with_name(
            TPCH_LEGACY_BLKIO_SUPPLEMENT
        )
        supplement = core.load_object(
            supplement_path, "TPC-H legacy blkio live supplement"
        )
        legacy_supplement = _validate_tpch_legacy_blkio_supplement(
            supplement, authority_path, value, container_row, pid,
            container_id, cgroup_path, storage, envelope, bundle_id,
        )
    else:
        recorded = core.sha256(authority_path)
'''
    if text.count(old_legacy) != 1:
        raise BuildError("legacy blkio validation block differs")
    text = text.replace(old_legacy, new_legacy)

    old_return = '''        "docker_hostconfig_is_non_authoritative": True,
    }
'''
    new_return = '''        "docker_hostconfig_is_non_authoritative": True,
        "legacy_live_supplement": legacy_supplement,
    }
'''
    if text.count(old_return) != 1:
        raise BuildError("shared blkio return marker differs")
    text = text.replace(old_return, new_return)

    start = text.index("def shared_rolling_process_inventory(*args, **kwargs):")
    end = text.index("\ndef _root_docker_mount_inventory():", start)
    replacement = r'''def _numeric_proc_pid_set():
    try:
        return {
            int(path.name) for path in core.PROC_ROOT.iterdir()
            if path.name.isdigit()
        }
    except (OSError, PermissionError, ValueError) as exc:
        raise core.QualificationError(
            "full process snapshot boundary unreadable: %s" % exc
        )


def _mark_pid_set_consistency(result, before, after):
    result["pid_set_boundary"] = {
        "before_count": len(before),
        "after_count": len(after),
        "before_sha256": core.sha_bytes(core.canonical(sorted(before))),
        "after_sha256": core.sha_bytes(core.canonical(sorted(after))),
        "appeared_pids": sorted(after - before),
        "disappeared_pids": sorted(before - after),
        "consistent": before == after,
    }
    if before != after:
        result["inventory_complete"] = False
        result["race_count"] = int(result.get("race_count") or 0) + 1
        result["conflicts"] = sorted(
            set(result.get("conflicts") or []) | {"process_inventory_race"}
        )
    return result


def _expected_daemon_argv(profile):
    return [
        "/usr/bin/dockerd",
        "--host=" + profile["socket_uri"],
        "--data-root=" + profile["data_root"],
        "--exec-root=" + profile["exec_root"],
        "--pidfile=" + profile["pidfile"],
        "--storage-driver=overlay2", "--bridge=none", "--iptables=false",
        "--ip-masq=false", "--ip-forward=false", "--userland-proxy=false",
        "--log-driver=none",
    ]


def _daemon_process_snapshot(profile, result, target):
    try:
        pid = int(Path(profile["pidfile"]).read_text(encoding="ascii").strip())
        proc = core.PROC_ROOT / str(pid)
        raw_cmdline = (proc / "cmdline").read_bytes()
        argv = [
            value.decode("utf-8", errors="strict")
            for value in raw_cmdline.split(b"\0") if value
        ]
        status = core.process_status(pid)
        startticks = core.process_start_ticks(pid)
        socket_state = os.stat(profile["socket"], follow_symlinks=False)
        listener = core._unix_listener_identity(profile["socket"], pid)
        data_state = os.stat(profile["data_root"], follow_symlinks=True)
    except (OSError, PermissionError, UnicodeError, ValueError) as exc:
        raise core.QualificationError(
            "dedicated daemon rolling identity unreadable: %s" % exc
        )
    rows = [
        row for row in result.get("processes") or []
        if int(row.get("pid") or 0) == pid
    ]
    actual_mems = core.expand_cpuset(status.get("Mems_allowed_list", ""))
    if (
        len(rows) != 1
        or rows[0].get("role") != "co_resident"
        or rows[0].get("comm") != "dockerd"
        or int(rows[0].get("ppid") or 0) != 1
        or rows[0].get("cmdline_sha256") != core.sha_bytes(raw_cmdline)
        or argv != _expected_daemon_argv(profile)
        or int(rows[0].get("starttime_ticks") or 0) != startticks
        or core.expand_cpuset(status.get("Cpus_allowed_list", ""))
        != core.expand_cpuset(profile["daemon_cpuset"])
        or not actual_mems
        or not stat.S_ISSOCK(socket_state.st_mode)
        or "%d:%d" % (os.major(data_state.st_dev), os.minor(data_state.st_dev))
        != profile["device_major_minor"]
    ):
        raise core.QualificationError("dedicated daemon rolling identity differs")
    row = rows[0]
    immutable_baseline = {
        "pid": pid,
        "starttime_ticks": startticks,
        "cmdline_sha256": core.sha_bytes(raw_cmdline),
        "socket_inode": socket_state.st_ino,
        "data_root_major_minor": profile["device_major_minor"],
        "cpus_allowed_list": status.get("Cpus_allowed_list"),
        "mems_allowed_list": status.get("Mems_allowed_list"),
    }
    prior = _daemon_process_baselines.get(profile["unit"])
    if prior is None:
        _daemon_process_baselines[profile["unit"]] = immutable_baseline
    elif prior != immutable_baseline:
        raise core.QualificationError(
            "dedicated daemon PID/startticks/CPU/NUMA identity changed"
        )
    row["infrastructure_subrole"] = (
        "target_dedicated_docker_daemon" if target
        else "co_resident_dedicated_docker_daemon"
    )
    if target:
        row["role"] = "target_infrastructure"
    return {
        "target": bool(target),
        "unit": profile["unit"],
        "pid": pid,
        "starttime_ticks": startticks,
        "socket": profile["socket"],
        "socket_uri": profile["socket_uri"],
        "socket_inode": socket_state.st_ino,
        "socket_listener_identity": listener,
        "data_root": profile["data_root"],
        "data_root_major_minor": profile["device_major_minor"],
        "cpus_allowed_list": status.get("Cpus_allowed_list"),
        "mems_allowed_list": status.get("Mems_allowed_list"),
        "same_live_immutable_baseline": immutable_baseline,
        "numa_binding_source": "first_exact_same_live_snapshot_then_stable",
        "cmdline_sha256": core.sha_bytes(raw_cmdline),
        "declared_device_writable_fds": int(
            row.get("declared_device_writable_fds") or 0
        ),
        "process_io": row.get("process_io"),
    }


def _classify_two_verified_daemons(result, mysql_cpuset, workload_cpuset,
                                   docker_containers):
    selected = _active_profile
    if selected is None:
        raise core.QualificationError("shared Docker profile is not bound")
    daemon_evidence = []
    for profile in PROFILES.values():
        daemon_evidence.append(
            _daemon_process_snapshot(profile, result, profile is selected)
        )
    mysql_cpus = core.expand_cpuset(mysql_cpuset)
    workload_cpus = core.expand_cpuset(workload_cpuset)
    target_daemon = next(row for row in daemon_evidence if row["target"])
    peer_daemon = next(row for row in daemon_evidence if not row["target"])
    if (
        not core.expand_cpuset(target_daemon["cpus_allowed_list"]).isdisjoint(
            mysql_cpus | workload_cpus
        )
        or not core.expand_cpuset(peer_daemon["cpus_allowed_list"]).isdisjoint(
            mysql_cpus | workload_cpus
        )
        or peer_daemon["declared_device_writable_fds"] != 0
    ):
        raise core.QualificationError(
            "dedicated daemon CPU or target-device isolation differs"
        )
    dangerous = [
        row for row in result.get("processes") or []
        if row.get("role") == "co_resident" and (
            not core.expand_cpuset(row.get("cpus_allowed_list", "")).isdisjoint(
                mysql_cpus | workload_cpus
            )
            or int(row.get("declared_device_writable_fds") or 0) > 0
        )
    ]
    conflicts = list(result.get("conflicts") or [])
    if not dangerous:
        conflicts = [
            value for value in conflicts
            if value != "co_resident_cpu_or_device"
        ]
    result["foreign_writer_count"] = sum(
        1 for row in result.get("processes") or []
        if row.get("role") == "co_resident"
        and int(row.get("declared_device_writable_fds") or 0) > 0
    )
    result["conflicts"] = sorted(set(conflicts))
    result["dedicated_daemon_processes"] = daemon_evidence
    result["two_daemon_exact_rolling_identity_verified"] = True
    result["target_infrastructure_write_is_not_foreign"] = True
    result["postgresql_static_process_identity_trusted"] = False
    result["postgresql_inventory_policy"] = "dynamic_read_only_interference_gate"
    result["multi_socket_container_inventory"] = [
        {
            "container_id": row["container_id"],
            "name": row["name"],
            "init_pid": row["init_pid"],
            "mounts": row["mounts"],
            "mounts_sha256": row["mounts_sha256"],
            "docker_socket_uri": row["docker_socket_uri"],
            "docker_unit": row["docker_unit"],
        }
        for row in sorted(
            docker_containers.values(), key=lambda value: value["container_id"]
        )
    ]
    return result


def _compact_retry_diagnostic(result, attempt):
    boundary = result.get("pid_set_boundary") or {}
    return {
        "attempt": attempt,
        "inventory_complete": result.get("inventory_complete"),
        "permission_failure_count": result.get("permission_failure_count"),
        "race_count": result.get("race_count"),
        "foreign_writer_count": result.get("foreign_writer_count"),
        "conflicts": result.get("conflicts"),
        "pid_set_boundary": boundary,
    }


def _seal_process_inventory_failure(result, attempts, retry_exhausted):
    try:
        output = Path(_exact_option(sys.argv, "--output"))
    except core.QualificationError:
        return None
    diagnostic = output.with_name(output.name + ".process_inventory_failure.json")
    if diagnostic.exists() or diagnostic.is_symlink():
        return diagnostic
    bundle_value = None
    if "--bundle" in sys.argv:
        bundle_value = str(Path(_exact_option(sys.argv, "--bundle")).resolve())
    attempt_id = None
    if "--command-spec" in sys.argv:
        command = core.load_object(
            Path(_exact_option(sys.argv, "--command-spec")),
            "diagnostic command spec",
        )
        attempt_id = command.get("qualification_attempt_id")
    value = {
        "schema_version": "mysql_tpch_process_inventory_failure_raw_v1",
        "status": "NOT_READY_PROCESS_INVENTORY",
        "captured_at_epoch": time.time(),
        "collector_bundle_id": HERE.parent.name,
        "collector_manifest_sha256": core.sha256(HERE.parent / "bundle_manifest.json"),
        "target_bundle": bundle_value,
        "qualification_attempt_id": attempt_id,
        "mode": (
            "qualification_paired" if "--qualification-paired" in sys.argv
            else "qualification_solo" if "--qualification-solo" in sys.argv
            else "collect_only"
        ),
        "maximum_snapshot_attempts": PROCESS_INVENTORY_MAXIMUM_ATTEMPTS,
        "snapshot_attempts": attempts,
        "retry_exhausted": bool(retry_exhausted),
        "raw_last_snapshot": result,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }
    core.atomic_json(diagnostic, value)
    return diagnostic


def shared_rolling_process_inventory(*args, **kwargs):
    """Accept only a bounded, exact, zero-race two-daemon snapshot."""
    global _latest_process_inventory_result
    docker_containers = kwargs.get("docker_containers")
    if docker_containers is None and len(args) > 7:
        docker_containers = args[7]
    if not isinstance(docker_containers, dict):
        raise core.QualificationError("shared Docker inventory is absent")
    diagnostics = []
    result = None
    for attempt in range(1, PROCESS_INVENTORY_MAXIMUM_ATTEMPTS + 1):
        before = _numeric_proc_pid_set()
        result = _original_rolling_process_inventory(*args, **kwargs)
        after = _numeric_proc_pid_set()
        result = _mark_pid_set_consistency(result, before, after)
        result = _classify_two_verified_daemons(
            result, args[4], args[5], docker_containers
        )
        diagnostics.append(_compact_retry_diagnostic(result, attempt))
        conflicts = set(result.get("conflicts") or [])
        race_only = (
            int(result.get("race_count") or 0) > 0
            and conflicts <= {"process_inventory_race"}
            and int(result.get("permission_failure_count") or 0) == 0
            and int(result.get("foreign_writer_count") or 0) == 0
        )
        if not race_only:
            break
        if attempt < PROCESS_INVENTORY_MAXIMUM_ATTEMPTS:
            time.sleep(PROCESS_INVENTORY_RETRY_BACKOFF_SECONDS[min(attempt - 1, 3)])
    assert result is not None
    result["inventory_snapshot_attempts"] = len(diagnostics)
    result["inventory_snapshot_retry_diagnostics"] = diagnostics
    result["inventory_snapshot_retry_policy"] = {
        "schema_version": "mysql_tpch_proc_consistent_snapshot_retry_policy_v1",
        "maximum_complete_snapshot_attempts": PROCESS_INVENTORY_MAXIMUM_ATTEMPTS,
        "retry_backoff_milliseconds": [10, 20, 30, 40],
        "retryable_condition": "zero-permission-zero-foreign-writer-race-only",
        "race_rows_ignored": False,
        "accepted_snapshot_requires_zero_races": True,
        "permission_or_affinity_or_device_conflict_relaxed": False,
    }
    retry_exhausted = (
        len(diagnostics) == PROCESS_INVENTORY_MAXIMUM_ATTEMPTS
        and int(result.get("race_count") or 0) > 0
    )
    result["inventory_snapshot_retry_exhausted"] = retry_exhausted
    accepted = (
        result.get("inventory_complete") is True
        and result.get("conflicts") == []
        and int(result.get("permission_failure_count") or 0) == 0
        and int(result.get("race_count") or 0) == 0
        and int(result.get("foreign_writer_count") or 0) == 0
        and (result.get("pid_set_boundary") or {}).get("consistent") is True
    )
    result["consistent_snapshot_accepted"] = accepted
    _latest_process_inventory_result = result
    if not accepted:
        diagnostic = _seal_process_inventory_failure(
            result, len(diagnostics), retry_exhausted
        )
        result["failure_diagnostic_path"] = (
            str(diagnostic) if diagnostic is not None else None
        )
    return result
'''
    text = text[:start] + replacement + text[end:]

    old_exit = '''    except core.QualificationError as exc:
        sys.stderr.write("NOT_READY: %s\\n" % exc)
        raise SystemExit(2)
'''
    new_exit = '''    except core.QualificationError as exc:
        if _latest_process_inventory_result is not None:
            _seal_process_inventory_failure(
                _latest_process_inventory_result,
                int(_latest_process_inventory_result.get(
                    "inventory_snapshot_attempts"
                ) or 1),
                bool(_latest_process_inventory_result.get(
                    "inventory_snapshot_retry_exhausted"
                )),
            )
        sys.stderr.write("NOT_READY: %s\\n" % exc)
        raise SystemExit(2)
'''
    if text.count(old_exit) != 1:
        raise BuildError("entry fail-closed exit marker differs")
    text = text.replace(old_exit, new_exit)

    return text.encode("utf-8")


def blkio_supplement_payload() -> bytes:
    return b'''#!/usr/bin/env python3
"""Seal read-only live facts missing from the exact legacy ff313 blkio authority."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

TARGET_BUNDLE_ID = "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
TARGET_MANIFEST_SHA256 = "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
TARGET_ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
TARGET_CONTAINER = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db"
DOCKER_SOCKET_URI = "unix:///run/ra-docker-mysql-tpch-v1.sock"
DEVICE = "259:1"
READ_BPS = 272629760
WRITE_BPS = 272629760
MAX_SNAPSHOT_ATTEMPTS = 16
MODERN_FIELDS = {
    "database_pid_starttime_ticks", "memory_swappiness",
    "total_swap_bytes", "database_restarted_by_remediation",
}


class SupplementError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\\n").encode("utf-8")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SupplementError(label + " must be a regular file")
    sidecar = path.with_name(path.name + ".sha256")
    if (sidecar.is_symlink() or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii")
            != "%s  %s\\n" % (sha256(path), path.name)):
        raise SupplementError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SupplementError(label + " must be an object")
    return value


def atomic_json(path, value):
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise SupplementError("refusing to overwrite live supplement")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    sidecar_temporary = sidecar.with_name(".%s.%d.tmp" % (sidecar.name, os.getpid()))
    try:
        with temporary.open("xb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        with sidecar_temporary.open("xb") as stream:
            stream.write(("%s  %s\\n" % (sha_bytes(payload), path.name)).encode("ascii"))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        os.replace(str(sidecar_temporary), str(sidecar))
        os.chmod(str(path), 0o600); os.chmod(str(sidecar), 0o600)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def run(argv, label):
    completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=30, check=False)
    if completed.returncode != 0:
        raise SupplementError("%s failed: %s" % (label, completed.stderr[-500:]))
    return completed.stdout


def process_startticks(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    boundary = raw.rfind(") ")
    if boundary < 0:
        raise SupplementError("database process stat differs")
    fields = raw[boundary + 2:].split()
    value = int(fields[19])
    if value <= 0:
        raise SupplementError("database process startticks differs")
    return value


def controller_root(pid, controller):
    rows = (Path("/proc") / str(pid) / "cgroup").read_text(encoding="ascii").splitlines()
    matches = []
    for row in rows:
        pieces = row.split(":", 2)
        if len(pieces) == 3 and controller in pieces[1].split(","):
            matches.append(pieces[2])
    if len(matches) != 1 or not matches[0].startswith("/"):
        raise SupplementError("cgroup-v1 %s identity differs" % controller)
    return Path("/sys/fs/cgroup") / controller / matches[0].lstrip("/")


def throttle_rows(path):
    rows = {}
    for line in Path(path).read_text(encoding="ascii").splitlines():
        pieces = line.split()
        if len(pieces) != 2 or re.fullmatch(r"[0-9]+:[0-9]+", pieces[0]) is None:
            raise SupplementError("blkio throttle row differs")
        if pieces[0] in rows:
            raise SupplementError("duplicate blkio throttle device")
        rows[pieces[0]] = int(pieces[1])
    return rows


def vm_swap_bytes(pid):
    rows = (Path("/proc") / str(pid) / "status").read_text(
        encoding="utf-8", errors="strict"
    ).splitlines()
    values = [row.split() for row in rows if row.startswith("VmSwap:")]
    if len(values) != 1 or len(values[0]) != 3 or values[0][2] != "kB":
        raise SupplementError("process VmSwap readback differs")
    return int(values[0][1]) * 1024


def consistent_process_swap(memory_root, database_pid):
    diagnostics = []
    for attempt in range(1, MAX_SNAPSHOT_ATTEMPTS + 1):
        try:
            before = sorted(int(value) for value in (memory_root / "cgroup.procs").read_text(
                encoding="ascii"
            ).split() if value)
            if database_pid not in before:
                raise SupplementError("database PID absent from memory cgroup")
            rows = [{"pid": pid, "vm_swap_bytes": vm_swap_bytes(pid)} for pid in before]
            after = sorted(int(value) for value in (memory_root / "cgroup.procs").read_text(
                encoding="ascii"
            ).split() if value)
        except FileNotFoundError:
            diagnostics.append({"attempt": attempt, "race": True})
            if attempt < MAX_SNAPSHOT_ATTEMPTS:
                time.sleep(0.01 * min(attempt, 4)); continue
            raise SupplementError("process swap snapshot race exhausted")
        if before == after:
            return {
                "attempts": attempt,
                "consistent": True,
                "pids": before,
                "processes": rows,
                "total_swap_bytes": sum(row["vm_swap_bytes"] for row in rows),
                "retry_diagnostics": diagnostics,
            }
        diagnostics.append({"attempt": attempt, "before": before, "after": after})
        if attempt < MAX_SNAPSHOT_ATTEMPTS:
            time.sleep(0.01 * min(attempt, 4))
    raise SupplementError("process swap snapshot consistency exhausted")


def build(bundle, authority_path, container_name):
    bundle = Path(bundle).resolve(strict=True)
    if (bundle.name != TARGET_BUNDLE_ID
            or sha256(bundle / "bundle_manifest.json") != TARGET_MANIFEST_SHA256):
        raise SupplementError("exact ff313 target seal differs")
    authority_path = Path(authority_path).resolve(strict=True)
    authority = load(authority_path, "legacy blkio authority")
    unsigned = dict(authority)
    recorded = unsigned.pop("authority_sha256", None)
    if (authority_path.name != "blkio_throttle_authority.json"
            or authority_path.parent.name != TARGET_ATTEMPT
            or MODERN_FIELDS.intersection(authority)
            or recorded != sha_bytes(canonical(unsigned))
            or authority.get("schema_version") != "mysql_tpch_blkio_v1_throttle_authority_v1"
            or authority.get("status") != "PASS_EFFECTIVE_CGROUP_V1_THROTTLE"
            or authority.get("formal_scope") != "mysql_only"
            or authority.get("cross_engine_comparison_prohibited") is not True
            or authority.get("bundle_id") != TARGET_BUNDLE_ID
            or authority.get("attempt_token") != TARGET_ATTEMPT
            or authority.get("device_major_minor") != DEVICE
            or int(authority.get("read_bps") or 0) != READ_BPS
            or int(authority.get("write_bps") or 0) != WRITE_BPS
            or authority.get("formal_launch_authorized") is not False
            or authority.get("accepted_round_authorized") is not False):
        raise SupplementError("legacy ff313 authority identity differs")
    if container_name != TARGET_CONTAINER:
        raise SupplementError("exact current TPC-H container name differs")
    raw = run(["docker", "--host", DOCKER_SOCKET_URI, "inspect", container_name],
              "TPC-H container inspect")
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != 1:
        raise SupplementError("TPC-H container inspect cardinality differs")
    container = rows[0]
    state = container.get("State") or {}
    host_config = container.get("HostConfig") or {}
    container_id = container.get("Id")
    pid = int(state.get("Pid") or 0)
    host_memory = int(host_config.get("Memory") or 0)
    host_memory_swap = int(host_config.get("MemorySwap") or 0)
    if (container_id != authority.get("database_container_id")
            or pid != int(authority.get("database_pid") or 0)
            or state.get("Running") is not True
            or state.get("Restarting") is not False
            or int(container.get("RestartCount") or 0) != 0
            or host_memory != 6174015488
            or host_memory_swap != host_memory):
        raise SupplementError("same-live no-restart container identity differs")
    blkio_root = controller_root(pid, "blkio")
    memory_root = controller_root(pid, "memory")
    if str(blkio_root) != authority.get("cgroup_path"):
        raise SupplementError("legacy/live blkio cgroup path differs")
    read_rows = throttle_rows(blkio_root / "blkio.throttle.read_bps_device")
    write_rows = throttle_rows(blkio_root / "blkio.throttle.write_bps_device")
    if read_rows.get(DEVICE) != READ_BPS or write_rows.get(DEVICE) != WRITE_BPS:
        raise SupplementError("live effective throttle differs")
    swappiness = int((memory_root / "memory.swappiness").read_text(encoding="ascii").strip())
    memory = int((memory_root / "memory.usage_in_bytes").read_text(encoding="ascii").strip())
    memsw = int((memory_root / "memory.memsw.usage_in_bytes").read_text(encoding="ascii").strip())
    memory_stat = {}
    for line in (memory_root / "memory.stat").read_text(encoding="ascii").splitlines():
        pieces = line.split()
        if len(pieces) != 2 or pieces[0] in memory_stat:
            raise SupplementError("memory.stat row differs")
        memory_stat[pieces[0]] = int(pieces[1])
    if "total_swap" not in memory_stat:
        raise SupplementError("cgroup-v1 total_swap readback is absent")
    cgroup_swap = memory_stat["total_swap"]
    process_swap = consistent_process_swap(memory_root, pid)
    if cgroup_swap != 0 or process_swap["total_swap_bytes"] != 0:
        raise SupplementError("live no-swap authority differs")
    return {
        "schema_version": "mysql_tpch_legacy_blkio_live_supplement_v1",
        "status": "PASS_LEGACY_AUTHORITY_PLUS_LIVE_READ_ONLY_SUPPLEMENT",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "renderer_sha256": sha256(Path(__file__).resolve()),
        "source_authority_path": str(authority_path),
        "source_authority_file_sha256": sha256(authority_path),
        "source_authority_self_sha256": recorded,
        "bundle_id": TARGET_BUNDLE_ID,
        "bundle_manifest_sha256": TARGET_MANIFEST_SHA256,
        "attempt_token": TARGET_ATTEMPT,
        "database_container_name": container_name,
        "database_container_id": container_id,
        "database_pid": pid,
        "database_pid_starttime_ticks": process_startticks(pid),
        "container_running": True,
        "container_restarting": False,
        "container_restart_count": 0,
        "database_restarted_by_remediation": False,
        "cgroup_version": "v1",
        "blkio_cgroup_path": str(blkio_root),
        "memory_cgroup_path": str(memory_root),
        "device_major_minor": DEVICE,
        "read_bps": READ_BPS,
        "write_bps": WRITE_BPS,
        "read_throttle_rows": read_rows,
        "write_throttle_rows": write_rows,
        "live_effective_cgroup_v1_throttle_verified": True,
        "memory_swappiness": swappiness,
        "memory_swappiness_enforcement":
            "recorded_only_zero_swap_capacity_is_authoritative",
        "host_config_memory_bytes": host_memory,
        "host_config_memory_swap_bytes": host_memory_swap,
        "host_config_swap_capacity_bytes": host_memory_swap - host_memory,
        "host_config_memory_swappiness": host_config.get("MemorySwappiness"),
        "memory_current_bytes": memory,
        "memory_memsw_current_bytes": memsw,
        "cgroup_total_swap_source": "memory.stat.total_swap",
        "cgroup_total_swap_bytes": cgroup_swap,
        "process_tree_total_swap_bytes": process_swap["total_swap_bytes"],
        "process_snapshot_consistent": process_swap["consistent"],
        "process_snapshot": process_swap,
        "authority_file_mutated": False,
        "database_or_client_mutation_performed": False,
        "postgresql_mutation_performed": False,
        "storage_mutation_performed": False,
        "network_mutation_performed": False,
        "formal_launch_authorized": False,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_by_renderer": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--legacy-authority", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = args.legacy_authority.parent / "blkio_throttle_authority.supplement.json"
    if args.output != expected:
        raise SupplementError("supplement output path differs")
    value = build(args.bundle, args.legacy_authority, args.container)
    atomic_json(args.output, value)
    print(json.dumps({"status": value["status"], "output": str(args.output),
                      "accepted_rounds_credit": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError,
            subprocess.TimeoutExpired, SupplementError) as exc:
        sys.stderr.write("NOT_READY: %s\\n" % exc)
        raise SystemExit(2)
'''


def test_payload() -> bytes:
    return b'''from __future__ import annotations
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import pytest

ENTRY = Path(__file__).resolve().parents[1] / "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
SPEC = importlib.util.spec_from_file_location("inventory_retry_support", ENTRY)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
SUPPLEMENT_PATH = ENTRY.parent / "mysql_tpch_legacy_blkio_supplement_v1.py"
SUPPLEMENT_SPEC = importlib.util.spec_from_file_location(
    "legacy_blkio_supplement", SUPPLEMENT_PATH
)
SUPPLEMENT = importlib.util.module_from_spec(SUPPLEMENT_SPEC)
assert SUPPLEMENT_SPEC.loader is not None
SUPPLEMENT_SPEC.loader.exec_module(SUPPLEMENT)


def sample(race=0, conflicts=None, permission=0, foreign=0):
    return {
        "inventory_complete": race == 0 and permission == 0,
        "race_count": race,
        "permission_failure_count": permission,
        "foreign_writer_count": foreign,
        "conflicts": list(conflicts or ([] if race == 0 else ["process_inventory_race"])),
        "processes": [],
    }


def install(monkeypatch, rows, before=None, after=None):
    iterator = iter(rows)
    monkeypatch.setattr(MODULE, "_original_rolling_process_inventory", lambda *a, **k: next(iterator))
    pid_sets = iter([before or {1}, after or {1}] * len(rows))
    monkeypatch.setattr(MODULE, "_numeric_proc_pid_set", lambda: next(pid_sets))
    monkeypatch.setattr(MODULE, "_classify_two_verified_daemons", lambda result, *a: result)
    monkeypatch.setattr(MODULE.time, "sleep", lambda value: None)
    monkeypatch.setattr(MODULE, "_seal_process_inventory_failure", lambda *a: Path("sealed.json"))


ARGS = (1, 2, 3, "259:1", "0", "1", "0")
KWARGS = {"docker_containers": {"a": {}}}


def test_two_transient_races_then_exact_snapshot(monkeypatch):
    install(monkeypatch, [sample(1), sample(1), sample()])
    value = MODULE.shared_rolling_process_inventory(*ARGS, **KWARGS)
    assert value["consistent_snapshot_accepted"] is True
    assert value["inventory_snapshot_attempts"] == 3
    assert value["race_count"] == 0


@pytest.mark.parametrize("row", [
    sample(0, ["process_fd_or_io_permission_failure"], 1, 0),
    sample(0, ["co_resident_cpu_or_device"], 0, 1),
])
def test_permission_or_real_conflict_never_retried(monkeypatch, row):
    install(monkeypatch, [row])
    value = MODULE.shared_rolling_process_inventory(*ARGS, **KWARGS)
    assert value["consistent_snapshot_accepted"] is False
    assert value["inventory_snapshot_attempts"] == 1
    assert value["failure_diagnostic_path"] == "sealed.json"


def test_retry_exhaustion_is_fail_closed(monkeypatch):
    install(monkeypatch, [sample(1) for _ in range(16)])
    value = MODULE.shared_rolling_process_inventory(*ARGS, **KWARGS)
    assert value["consistent_snapshot_accepted"] is False
    assert value["inventory_snapshot_attempts"] == 16
    assert value["inventory_snapshot_retry_exhausted"] is True


def test_pid_set_boundary_change_is_a_race(monkeypatch):
    install(monkeypatch, [sample(), sample()])
    pid_sets = iter([{1}, {1, 2}, {1}, {1}])
    monkeypatch.setattr(MODULE, "_numeric_proc_pid_set", lambda: next(pid_sets))
    value = MODULE.shared_rolling_process_inventory(*ARGS, **KWARGS)
    assert value["consistent_snapshot_accepted"] is True
    assert value["inventory_snapshot_attempts"] == 2
    assert value["race_count"] == 0


def sealed(path, value):
    payload = MODULE.core.canonical(value)
    path.write_bytes(payload)
    path.with_name(path.name + ".sha256").write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\\n",
        encoding="ascii",
    )


def supplement_fixture(tmp_path, monkeypatch):
    attempt = tmp_path / MODULE.CURRENT_TPCH_ATTEMPT
    attempt.mkdir()
    authority_path = attempt / "blkio_throttle_authority.json"
    authority = {"authority_sha256": "a" * 64}
    sealed(authority_path, authority)
    supplement_path = attempt / MODULE.TPCH_LEGACY_BLKIO_SUPPLEMENT
    supplement = {
        "schema_version": "mysql_tpch_legacy_blkio_live_supplement_v1",
        "status": "PASS_LEGACY_AUTHORITY_PLUS_LIVE_READ_ONLY_SUPPLEMENT",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "source_authority_path": str(authority_path),
        "source_authority_file_sha256": MODULE.core.sha256(authority_path),
        "source_authority_self_sha256": "a" * 64,
        "bundle_id": "b" * 64,
        "attempt_token": MODULE.CURRENT_TPCH_ATTEMPT,
        "database_container_id": "c" * 64,
        "database_pid": 123,
        "database_pid_starttime_ticks": 456,
        "container_running": True,
        "container_restarting": False,
        "container_restart_count": 0,
        "database_restarted_by_remediation": False,
        "cgroup_version": "v1",
        "blkio_cgroup_path": "/sys/fs/cgroup/blkio/docker/" + "c" * 64,
        "device_major_minor": "259:1",
        "read_bps": 272629760,
        "write_bps": 272629760,
        "live_effective_cgroup_v1_throttle_verified": True,
        "memory_swappiness": 60,
        "memory_swappiness_enforcement":
            "recorded_only_zero_swap_capacity_is_authoritative",
        "host_config_memory_bytes": 6174015488,
        "host_config_memory_swap_bytes": 6174015488,
        "host_config_swap_capacity_bytes": 0,
        "cgroup_total_swap_bytes": 0,
        "process_tree_total_swap_bytes": 0,
        "process_snapshot_consistent": True,
        "authority_file_mutated": False,
        "database_or_client_mutation_performed": False,
        "postgresql_mutation_performed": False,
        "storage_mutation_performed": False,
        "network_mutation_performed": False,
        "formal_launch_authorized": False,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    sealed(supplement_path, supplement)
    monkeypatch.setattr(MODULE.core, "process_start_ticks", lambda pid: 456)
    args = (
        supplement, authority_path, authority,
        {"State": {"Running": True, "Restarting": False}, "RestartCount": 0},
        123, "c" * 64,
        "/sys/fs/cgroup/blkio/docker/" + "c" * 64,
        {"expected_live_major_minor": "259:1"},
        {
            "io_read_bps": 272629760,
            "io_write_bps": 272629760,
            "memory_bytes": 6174015488,
            "memory_swap_bytes": 6174015488,
        },
        "b" * 64,
    )
    return args


def test_legacy_blkio_supplement_is_exact_and_zero_credit(tmp_path, monkeypatch):
    args = supplement_fixture(tmp_path, monkeypatch)
    value = MODULE._validate_tpch_legacy_blkio_supplement(*args)
    assert value["memory_swappiness"] == 60
    assert value["host_config_swap_capacity_bytes"] == 0
    assert value["cgroup_total_swap_bytes"] == 0
    assert value["database_restarted_by_remediation"] is False


def test_legacy_blkio_supplement_accepts_inherited_swappiness_with_zero_swap_capacity(tmp_path, monkeypatch):
    args = list(supplement_fixture(tmp_path, monkeypatch))
    args[0] = dict(args[0], memory_swappiness=0)
    MODULE._validate_tpch_legacy_blkio_supplement(*args)
    args[0] = dict(args[0], memory_swappiness=100)
    MODULE._validate_tpch_legacy_blkio_supplement(*args)


def test_legacy_blkio_supplement_rejects_nonzero_swap_capacity(tmp_path, monkeypatch):
    args = list(supplement_fixture(tmp_path, monkeypatch))
    args[0] = dict(args[0], host_config_memory_swap_bytes=6174015489,
                   host_config_swap_capacity_bytes=1)
    with pytest.raises(MODULE.core.QualificationError, match="supplement differs"):
        MODULE._validate_tpch_legacy_blkio_supplement(*args)


def test_legacy_renderer_parses_exact_throttle_and_rejects_duplicate(tmp_path):
    path = tmp_path / "throttle"
    path.write_text("259:1 272629760\\n", encoding="ascii")
    assert SUPPLEMENT.throttle_rows(path) == {"259:1": 272629760}
    path.write_text("259:1 272629760\\n259:1 272629760\\n", encoding="ascii")
    with pytest.raises(SUPPLEMENT.SupplementError, match="duplicate"):
        SUPPLEMENT.throttle_rows(path)


def test_legacy_renderer_requires_absent_modern_fields_before_supplement():
    assert SUPPLEMENT.MODERN_FIELDS == {
        "database_pid_starttime_ticks",
        "memory_swappiness",
        "total_swap_bytes",
        "database_restarted_by_remediation",
    }
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = verify_parent()
    content = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    content[ENTRY] = patch_entry(content[ENTRY])
    content[BLKIO_SUPPLEMENT] = blkio_supplement_payload()
    content[BUILDER] = HERE.read_bytes()
    content[TEST] = test_payload()
    content[REMEDIATION] = canonical({
        "schema_version": "mysql_tpch_ff313_inventory_retry_remediation_v1",
        "status": "SEALED_LOCAL_SUPPORT_FRESH_QUALIFICATION_REQUIRED",
        "target_bundle_id": TPCH_ID,
        "target_manifest_sha256": TPCH_MANIFEST_SHA256,
        "target_predecessor_collector_sha256": TPCH_COLLECTOR_SHA256,
        "target_launcher_sha256": TPCH_LAUNCHER_SHA256,
        "target_qualification_authority_renderer_sha256": TPCH_AUTHORITY_RENDERER_SHA256,
        "shared_collector_predecessor_bundle_id": PARENT_ID,
        "maximum_process_snapshot_attempts": 16,
        "accepted_snapshot_requires_zero_races": True,
        "permission_conflict_relaxed": False,
        "cpu_affinity_conflict_relaxed": False,
        "device_conflict_relaxed": False,
        "foreign_writer_conflict_relaxed": False,
        "two_docker_daemons_inventory_required": True,
        "target_dedicated_dockerd_role": "target_infrastructure_exact_identity_only",
        "peer_dedicated_dockerd_role": "co_resident",
        "raw_failure_diagnostic_required": True,
        "legacy_blkio_authority_mutation_prohibited": True,
        "zero_swap_authority": {
            "host_config_memory_swap_equals_memory_positive_required": True,
            "cgroup_total_swap_zero_required": True,
            "process_tree_vm_swap_zero_required": True,
            "inherited_memory_swappiness_recorded_not_gated": True,
        },
        "legacy_blkio_live_supplement_renderer_sha256":
            sha_bytes(content[BLKIO_SUPPLEMENT]),
        "legacy_blkio_live_supplement_required_fields": [
            "database_pid_starttime_ticks",
            "memory_swappiness",
            "memory_swappiness_enforcement",
            "host_config_memory_bytes",
            "host_config_memory_swap_bytes",
            "host_config_swap_capacity_bytes",
            "cgroup_total_swap_bytes",
            "process_tree_total_swap_bytes",
            "database_restarted_by_remediation",
            "postgresql_mutation_performed",
            "storage_mutation_performed",
        ],
        "legacy_blkio_live_supplement_exact_argv": [
            "[SEALED_PYTHON]",
            "[THIS_SUPPORT_BUNDLE]/qualification/mysql_tpch_legacy_blkio_supplement_v1.py",
            "--bundle",
            "[EXACT_FF313_BUNDLE]",
            "--legacy-authority",
            "[CURRENT_ATTEMPT_ROOT]/blkio_throttle_authority.json",
            "--container",
            "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db",
            "--output",
            "[CURRENT_ATTEMPT_ROOT]/blkio_throttle_authority.supplement.json",
        ],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    })
    sealed_files = {
        relative: sha_bytes(payload) for relative, payload in sorted(content.items())
    }
    identity = {
        key: value for key, value in parent.items()
        if key not in ("bundle_id", "files", "schema_version", "status")
    }
    identity.update({
        "schema_version": "mysql_tpch_inventory_retry_support_bundle_v1",
        "status": "SEALED_LOCAL_SUPPORT_NOT_EXECUTED_NOT_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(HERE),
        "support_target_bundle_id": TPCH_ID,
        "support_target_manifest_sha256": TPCH_MANIFEST_SHA256,
        "support_target_collector_sha256": TPCH_COLLECTOR_SHA256,
        "support_target_launcher_sha256": TPCH_LAUNCHER_SHA256,
        "support_target_qualification_authority_renderer_sha256": TPCH_AUTHORITY_RENDERER_SHA256,
        "process_inventory_maximum_attempts": 16,
        "consistent_zero_race_snapshot_required": True,
        "two_daemon_exact_inventory_required": True,
        "failure_raw_sample_sealed": True,
        "legacy_blkio_authority_mutation_prohibited": True,
        "legacy_blkio_live_supplement_renderer_sha256":
            sealed_files[BLKIO_SUPPLEMENT],
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": sealed_files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".tpch-inventory-retry-v1.", dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    actual = files(destination)
    if actual != sealed_files:
        raise BuildError("published file closure differs")
    manifest_actual = load(destination / "bundle_manifest.json", "published manifest")
    unsigned = dict(manifest_actual)
    actual_id = unsigned.pop("bundle_id", None)
    if (
        actual_id != destination.name
        or sha_bytes(canonical(unsigned)) != destination.name
        or sha_file(destination / "bundle_manifest.json")
        != (destination / "bundle_manifest.json.sha256").read_text(encoding="ascii").split()[0]
    ):
        raise BuildError("published content address differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published support bundle is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_TPCH_INVENTORY_RETRY_SUPPORT_V1",
        "bundle_id": destination.name,
        "bundle": str(destination),
        "manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "target_bundle_id": TPCH_ID,
        "maximum_snapshot_attempts": 16,
        "remote_actions": 0,
        "formal_launch_authorized": False,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, UnicodeError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
