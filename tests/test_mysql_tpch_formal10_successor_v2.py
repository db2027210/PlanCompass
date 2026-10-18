from __future__ import annotations

from datetime import datetime, timedelta, timezone
import copy
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import pwd
import grp
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from shell import build_mysql_tpch_formal10_successor_v2 as builder
from shell import mysql_tpch_distributed_v2_authority as authority
from shell import mysql_tpch_blkio_v1 as blkio_helper
from shell import mysql_tpch_v2_qualification_authority_v1 as qualification_renderer
from shell import mysql_tpch_v2_fresh_r0_runner_v1 as fresh_r0_runner
from shell import mysql_tpch_formal10_readiness_v2 as readiness
from shell import mysql_distributed_live_readback_qualification_v1 as collector
from shell import render_mysql_tpch_distributed_v2_bootstrap_spec_v1 as spec_renderer


ROOT = Path(__file__).resolve().parents[1]
NODE04_ROOT_SCANNER_FIXTURE = (
    ROOT / "tests/fixtures/mysql_tpch_node04_root_scanner_pass.json"
)


def frozen_spec() -> dict:
    attempt = "tpch-v2-" + ("a" * 32)
    bundle_id = "b" * 64
    attempt_root = authority.RUNTIME_ROOT + "/attempts/" + attempt
    now = datetime.now(timezone.utc)
    return {
        "schema_version": "mysql_tpch_distributed_v2_bootstrap_spec_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": authority.SCENARIO,
        "host_id": authority.HOSTNAME,
        "attempt_token": attempt,
        "bundle": {
            "path": authority.RUNTIME_ROOT + "/bundles/" + bundle_id,
            "bundle_id": bundle_id,
            "manifest_sha256": "c" * 64,
        },
        "runtime": {
            "source_root": authority.PYTHON_RUNTIME_SOURCE,
            "target_root": authority.PYTHON_RUNTIME_TARGET,
            "portable_manifest_path":
                authority.RUNTIME_ROOT
                + "/bundles/"
                + bundle_id
                + "/runtime/python_runtime_tree_manifest_v3.json",
            "portable_manifest_sha256":
                authority.PYTHON_RUNTIME_MANIFEST_SHA256,
            "tree_content_sha256":
                authority.PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "publication_mode":
                "cross_device_tar_stream_exact_manifest_atomic_rename",
        },
        "contract_sha256": authority.CONTRACT_SHA256,
        "v40_invalidation_sha256": "d" * 64,
        "mysql_image": {
            "reference": "mysql@sha256:" + ("e" * 64),
            "image_id": "sha256:" + ("f" * 64),
        },
        "client_image": {
            "reference": "client@sha256:" + ("1" * 64),
            "image_id": "sha256:" + ("2" * 64),
        },
        "paths": {
            "template": authority.TEMPLATE,
            "runtime_root": authority.RUNTIME_ROOT,
            "attempt_root": attempt_root,
            "datadir": attempt_root + "/datadir",
            "credentials_dir": attempt_root + "/credentials",
            "host_measurement_tmpfs":
                "/dev/shm/ra_mysql_tpch_v2/" + attempt,
            "python_runtime_root":
                authority.PYTHON_RUNTIME_TARGET,
        },
        "containers": {
            "database":
                "ra_mysql_tpch_v2_tpch_v2_" + ("a" * 32) + "_db",
            "client":
                "ra_mysql_tpch_v2_tpch_v2_" + ("a" * 32) + "_client",
        },
        "mysql_binary": "/usr/bin/mysql",
        "mysql_defaults_file": attempt_root + "/credentials/mysql.cnf",
        "collector": (
            authority.RUNTIME_ROOT
            + "/bundles/"
            + bundle_id
            + "/qualification/"
            + "mysql_distributed_live_readback_qualification_v1.py"
        ),
        "created_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=30)).isoformat(),
        "main_agent_token_sha256": hashlib.sha256(b"token").hexdigest(),
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def db_inspect(spec: dict) -> dict:
    return {
        "Id": "4" * 64,
        "Image": spec["mysql_image"]["image_id"],
        "Created": "2026-07-31T00:00:00Z",
        "RestartCount": 0,
        "Config": {"Labels": {"ra.attempt": spec["attempt_token"]}},
        "State": {"Running": True, "Pid": 31001},
        "HostConfig": {
            "CpuPeriod": authority.DB_ENVELOPE["cpu_period_us"],
            "CpuQuota": authority.DB_ENVELOPE["cpu_quota_us"],
            "CpusetCpus": authority.DB_ENVELOPE["cpuset_cpus"],
            "CpusetMems": authority.DB_ENVELOPE["cpuset_mems"],
            "Memory": authority.DB_ENVELOPE["memory_bytes"],
            "MemorySwap": authority.DB_ENVELOPE["memory_swap_bytes"],
            "PidsLimit": authority.DB_ENVELOPE["pids_max"],
            "BlkioDeviceReadBps": [
                {
                    "Path": authority.DEVICE,
                    "Rate": authority.DB_ENVELOPE["io_read_bps"],
                }
            ],
            "BlkioDeviceWriteBps": [
                {
                    "Path": authority.DEVICE,
                    "Rate": authority.DB_ENVELOPE["io_write_bps"],
                }
            ],
            "PortBindings": {
                "3306/tcp": [
                    {
                        "HostIp": "127.0.0.1",
                        "HostPort": str(authority.MYSQL_PORT),
                    }
                ]
            },
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": spec["paths"]["datadir"],
                "Destination": "/var/lib/mysql",
                "RW": True,
            }
        ],
    }


def client_inspect(spec: dict) -> dict:
    return {
        "Id": "5" * 64,
        "Image": spec["client_image"]["image_id"],
        "Created": "2026-07-31T00:00:01Z",
        "RestartCount": 0,
        "Config": {
            "Labels": {"ra.attempt": spec["attempt_token"]},
            "User": authority.CLIENT_ENVELOPE["user"],
        },
        "State": {"Running": True, "Pid": 31002},
        "HostConfig": {
            "CpuPeriod": authority.CLIENT_ENVELOPE["cpu_period_us"],
            "CpuQuota": authority.CLIENT_ENVELOPE["cpu_quota_us"],
            "CpusetCpus": authority.CLIENT_ENVELOPE["cpuset_cpus"],
            "CpusetMems": authority.CLIENT_ENVELOPE["cpuset_mems"],
            "Memory": authority.CLIENT_ENVELOPE["memory_bytes"],
            "MemorySwap": authority.CLIENT_ENVELOPE["memory_swap_bytes"],
            "PidsLimit": authority.CLIENT_ENVELOPE["pids_max"],
            "NetworkMode": "host",
            "ReadonlyRootfs": True,
            "LogConfig": {"Type": "none", "Config": {}},
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": source,
                "Destination": source,
                "RW": False,
            }
            for source in (
                spec["bundle"]["path"],
                spec["paths"]["python_runtime_root"],
                spec["paths"]["credentials_dir"],
            )
        ] + [
            {
                "Type": "bind",
                "Source": (
                    spec["paths"]["host_measurement_tmpfs"]
                    + "/"
                    + authority.CLIENT_MEASUREMENT_SUBDIRECTORY
                ),
                "Destination": "/measurement",
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": (
                    spec["bundle"]["path"]
                    + "/"
                    + authority.CLIENT_MYSQL_DATADIR_MASK_RELATIVE
                ),
                "Destination": "/var/lib/mysql",
                "RW": False,
            }
        ],
    }


def test_client_immutable_projection_is_execids_stable_and_full_chain_equal() -> None:
    spec = frozen_spec()
    before = client_inspect(spec)
    before["State"]["ExecIDs"] = ["sql-bridge-before"]
    during = copy.deepcopy(before)
    during["State"]["ExecIDs"] = [
        "sql-bridge-before",
        "sql-bridge-transient-current",
    ]

    bootstrap_same_live = authority._client_container_config_identity(
        before
    )
    command_materializer = authority._client_container_config_identity(
        during
    )
    database_counter_transport = (
        collector._client_container_config_identity(before)
    )
    qualification_collector = (
        collector._client_container_config_identity(during)
    )
    assert len(
        {
            bootstrap_same_live,
            command_materializer,
            database_counter_transport,
            qualification_collector,
        }
    ) == 1

    immutable_change = copy.deepcopy(during)
    immutable_change["HostConfig"]["CpuQuota"] += 1
    assert authority._client_container_config_identity(
        immutable_change
    ) != bootstrap_same_live
    assert collector._client_container_config_identity(
        immutable_change
    ) != bootstrap_same_live


def test_helper_import_never_creates_unmanifested_bytecode(
    tmp_path: Path,
) -> None:
    for script in (
        Path(authority.__file__),
        Path(readiness.__file__),
    ):
        root = tmp_path / script.stem
        root.mkdir()
        copied_script = root / script.name
        copied_helper = (
            root / "mysql_tpch_client_immutable_identity_v1.py"
        )
        shutil.copy2(script, copied_script)
        shutil.copy2(
            ROOT / "shell/mysql_tpch_client_immutable_identity_v1.py",
            copied_helper,
        )
        subprocess.run(
            [sys.executable, str(copied_script), "--help"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert not (root / "__pycache__").exists()


def bootstrap_evidence(spec: dict) -> dict:
    database = db_inspect(spec)
    client = client_inspect(spec)
    expected_cgroup = (
        "/sys/fs/cgroup/blkio/docker/" + database["Id"]
    )
    effective_blkio = {
        "schema_version": authority.BLKIO_AUTHORITY_SCHEMA,
        "status": "PASS_EFFECTIVE_CGROUP_V1_THROTTLE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": spec["bundle"]["bundle_id"],
        "attempt_token": spec["attempt_token"],
        "database_container": spec["containers"]["database"],
        "database_container_id": database["Id"],
        "database_pid": database["State"]["Pid"],
        "host_effective_uid_gid": [0, 0],
        "cgroup_version": "v1",
        "cgroup_path": expected_cgroup,
        "device_major_minor": authority.MAJOR_MINOR,
        "read_bps": authority.DB_ENVELOPE["io_read_bps"],
        "write_bps": authority.DB_ENVELOPE["io_write_bps"],
        "read_throttle_file":
            expected_cgroup + "/blkio.throttle.read_bps_device",
        "write_throttle_file":
            expected_cgroup + "/blkio.throttle.write_bps_device",
        "docker_hostconfig_is_declaration_not_effective_authority": True,
        "database_or_client_started": True,
        "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    effective_blkio["authority_sha256"] = authority.sha_bytes(
        authority.canonical(effective_blkio)
    )
    live = {
        "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
        "bundle_id": spec["bundle"]["bundle_id"],
        "scenario": authority.SCENARIO,
        "container": {
            "container_id": database["Id"],
            "pid": database["State"]["Pid"],
        },
        "accepted_round_authorized": False,
    }
    runtime_readback = {
        "status": "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK",
        "root": authority.PYTHON_RUNTIME_TARGET,
        "tree_content_sha256":
            authority.PYTHON_RUNTIME_TREE_CONTENT_SHA256,
        "exact_entry_set_verified": True,
        "directory_path_and_mode_verified": True,
        "file_sha256_size_mode_verified": True,
        "symlink_target_verified": True,
        "special_entries_rejected": True,
    }
    result = {
        "schema_version": "mysql_tpch_distributed_v2_bootstrap_evidence_v1",
        "status": "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": authority.SCENARIO,
        "host_id": authority.HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "bundle_manifest_sha256": spec["bundle"]["manifest_sha256"],
        "contract_sha256": authority.CONTRACT_SHA256,
        "v40_invalidation_sha256": spec["v40_invalidation_sha256"],
        "spec_sha256": authority.sha_bytes(authority.canonical(spec)),
        "copy": {
            "command": "tar regular-file byte stream",
            "overlay": False,
            "reflink": False,
            "clone_api_used": False,
            "full_physical_copy": True,
            "atomic_rename_complete": True,
            "source": authority.TEMPLATE,
            "destination": spec["paths"]["datadir"],
            "device_major_minor": authority.MAJOR_MINOR,
            "source_tree_sha256": "6" * 64,
            "destination_tree_sha256": "6" * 64,
            "file_count": 173,
            "byte_count": 27_110_986_878,
            "tree_identity_normalization": authority.TREE_IDENTITY_NORMALIZATION,
            "source_root_inode": 100,
            "destination_root_inode": 200,
            "target_permission_normalization": {
                "schema_version":
                    "mysql_tpch_target_datadir_permission_normalization_v1",
                "target_only": True,
                "frozen_image_uid": 999,
                "frozen_image_gid": 999,
                "directory_mode": "0700",
                "regular_file_mode": "0600",
                "directory_count": 17,
                "regular_file_count": 173,
                "socket_excluded_from_target": True,
                "source_metadata_sha256_before": "8" * 64,
                "source_metadata_sha256_after": "8" * 64,
            },
            "extent_audit": {
                "method":
                    "linux_fiemap_physical_extent_interval_intersection_v1",
                "source_file_count": 173,
                "destination_file_count": 173,
                "source_extent_count": 190,
                "destination_extent_count": 190,
                "source_and_destination_same_device": True,
                "shared_physical_extent_bytes": 0,
                "shared_physical_extents": False,
                "audit_complete": True,
            },
        },
        "runtime_publication": {
            "publication_mode":
                "tar_regular_file_byte_stream_no_clone_api",
            "source_root": authority.PYTHON_RUNTIME_SOURCE,
            "target_root": authority.PYTHON_RUNTIME_TARGET,
            "source_device_major_minor": "8:3",
            "target_device_major_minor": authority.MAJOR_MINOR,
            "source_and_target_different_device": True,
            "portable_manifest_sha256":
                authority.PYTHON_RUNTIME_MANIFEST_SHA256,
            "tree_content_sha256":
                authority.PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "source_readback_sha256": "7" * 64,
            "stage_readback_sha256": "8" * 64,
            "target_readback_sha256": authority.sha_bytes(
                authority.canonical(runtime_readback)
            ),
            "target_readback": runtime_readback,
            "atomic_rename_complete": True,
            "preexisting_content_addressed_target_reused": False,
            "reflink_clone_api_used": False,
            "accepted_round_authorized": False,
        },
        "template_owner_inventory": {
            "method": "stdlib_proc_fd_cwd_root_inventory",
            "local": {
                "schema_version":
                    "mysql_tpch_proc_open_owner_inventory_v1",
                "proc_root": "/proc",
                "template": authority.TEMPLATE,
                "scanned_pid_count": 42,
                "open_owners": [],
                "unreadable_pids": [],
                "complete": True,
                "pass": True,
            },
            "docker_fallback_used": False,
            "complete": True,
            "pass": True,
        },
        "database": {
            "container_id": database["Id"],
            "pid": 31001,
            "starttime_ticks": 1001,
            "inspect": database,
        },
        "client": {
            "container_id": client["Id"],
            "pid": 31002,
            "starttime_ticks": 1002,
            "inspect": client,
        },
        "effective_blkio_authority": effective_blkio,
        "measurement_bind": {
            "host_tmpfs_root": spec["paths"]["host_measurement_tmpfs"],
            "host_source": (
                spec["paths"]["host_measurement_tmpfs"]
                + "/"
                + authority.CLIENT_MEASUREMENT_SUBDIRECTORY
            ),
            "destination": "/measurement",
            "filesystem": "tmpfs",
            "mount_source": "tmpfs",
            "mount_target_exact": True,
            "mount_options_required": sorted(
                authority.HOST_MEASUREMENT_TMPFS_OPTIONS
            ),
            "size_bytes": authority.HOST_MEASUREMENT_TMPFS_BYTES,
            "statvfs_capacity_bytes":
                authority.HOST_MEASUREMENT_TMPFS_BYTES,
            "uid": authority.CLIENT_ENVELOPE["measurement_directory_uid"],
            "gid": authority.CLIENT_ENVELOPE["measurement_directory_gid"],
            "mode": authority.CLIENT_ENVELOPE["measurement_directory_mode"],
            "container_user": authority.CLIENT_ENVELOPE["user"],
        },
        "collect_only": {
            "sha256": authority.sha_bytes(authority.canonical(live)),
            "document": live,
        },
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    result["same_live_identity"] = authority.validate_bootstrap_evidence(
        result, spec
    )
    return result


def gradient_event(
    subset_id: str, active_subset: tuple[str, ...], score: float
) -> dict:
    slopes = {
        parameter: {"cpu": 1.0, "memory": -2.0, "io": 0.5}
        for parameter in active_subset
    }
    matrix = {
        parameter: {"cpu": 0.1, "memory": 0.2, "io": -0.1}
        for parameter in active_subset
    }
    confidence = {parameter: 0.8 for parameter in active_subset}
    gradients = {
        parameter: (
            1.0 * 0.1 * 0.8
            + -2.0 * 0.2 * 0.8
            + 0.5 * -0.1 * 0.8
        )
        for parameter in active_subset
    }
    eta = 0.01
    deltas = {}
    for parameter, gradient in gradients.items():
        before = 1.0
        proposed = before * __import__("math").exp(eta * gradient)
        deltas[parameter] = {
            "before": before,
            "proposed_before_clip": proposed,
            "after_clip": proposed,
            "absolute": proposed - before,
            "relative": proposed - before,
            "clipped": False,
        }
    return {
        "subset_id": subset_id,
        "active_subset": list(active_subset),
        "gradient_source": "original_dScore_dU_times_T_times_confidence",
        "feature_ratio": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        "predicted_resource": {"cpu": 0.4, "memory": 0.9, "io": 0.4},
        "utilization": {"cpu": 0.4, "memory": 0.9, "io": 0.4},
        "dScore/dU": slopes,
        "T": matrix,
        "confidence": confidence,
        "g_j": gradients,
        "eta": eta,
        "delta_param": deltas,
        "candidate_score": score,
    }


def debug_document(spec: dict, bootstrap: dict) -> dict:
    subsets = [
        combination
        for width in range(1, 5)
        for combination in itertools.combinations(readiness.PARAMETERS, width)
    ]
    events = [
        gradient_event(f"subset_{index:02d}", subset, float(16 - index))
        for index, subset in enumerate(subsets, 1)
    ]
    ranking = [
        {
            "rank": index,
            "subset_id": event["subset_id"],
            "candidate_score": event["candidate_score"],
        }
        for index, event in enumerate(events, 1)
    ]
    journals = [
        {
            "rank": index,
            "subset_id": ranking[index - 1]["subset_id"],
            "sealed_candidate_identity": str(index) * 64,
            "attempt_token": f"{spec['attempt_token']}-top5-{index}",
            "started": True,
            "measurement_complete": True,
            "completion_recorded": True,
            "real_replay": True,
            "failed_replay": False,
            "accepted_rounds_credit": 0,
        }
        for index in range(1, 6)
    ]
    fingerprints = []
    for row in ranking:
        fingerprints.append(
            {
                "stage": "predicted_candidate",
                "candidate_id": row["subset_id"],
                "source_subset_id": row["subset_id"],
                "parameter_signature": "p:" + row["subset_id"],
                "l1": {"operator_types": ["scan"]},
                "l2": {"workers": 1, "hash_batches": 0},
                "l3": {
                    "memory_peak": 1,
                    "dynamic_execution_memory": 1,
                    "spill": 0,
                },
            }
        )
    for row in ranking[:5]:
        fingerprints.append(
            {
                "stage": "real_top5_replay",
                "candidate_id": "replay:" + row["subset_id"],
                "source_subset_id": row["subset_id"],
                "parameter_signature": "p:" + row["subset_id"],
                "l1": {"operator_types": ["scan"]},
                "l2": {"workers": 1, "hash_batches": 0},
                "l3": {
                    "memory_peak": 1,
                    "dynamic_execution_memory": 1,
                    "spill": 0,
                },
            }
        )
    return {
        "schema_version": "mysql_tpch_distributed_v2_debug_round_evidence_v1",
        "status": "PASS_DEBUG_ROUND",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": spec["bundle"]["bundle_id"],
        "bundle_manifest_sha256": spec["bundle"]["manifest_sha256"],
        "attempt_token": spec["attempt_token"],
        "same_live_identity": bootstrap["same_live_identity"],
        "completed_nonempty_subsets": 15,
        "sealed_top5_ranks": [1, 2, 3, 4, 5],
        "replayed_top5_ranks": [1, 2, 3, 4, 5],
        "fixed_top5_only": True,
        "candidate_order": "global_descending_final_candidate_score",
        "algorithm_formula":
            "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
        "parameter_update_source": "g_j_and_eta_only",
        "qps_role": "outer_acceptance_after_real_replay_only",
        "fingerprint_duplicate_rule":
            "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3",
        "missing_fingerprint_level_policy": "independent_candidate",
        "accepted_rounds_credit": 0,
        "accepted_round_authorized": False,
        "gradient_events": events,
        "global_candidate_ranking": ranking,
        "top5_point_journals": journals,
        "candidate_fingerprints": fingerprints,
        "prediction_vs_real_analysis": {
            "predicted_top5_count": 5,
            "real_top5_count": 5,
            "real_order_complete": True,
            "classification": "no_issue",
        },
    }


def test_exact_plan_has_full_copy_and_both_exact_envelopes() -> None:
    plan = authority.command_plan(frozen_spec())
    assert "tar -C /template -cpf -" in plan["copy"]["argv"][-1]
    assert "chown 999:999" in plan["copy"]["argv"][-1]
    assert "chmod 0700" in plan["copy"]["argv"][-1]
    assert "chmod 0600" in plan["copy"]["argv"][-1]
    assert "-printf '%U:%G:%m\\n'" in plan["copy"]["argv"][-1]
    assert "-printf '%u:%g:%m\\n'" not in plan["copy"]["argv"][-1]
    assert "source_before=$(metadata_digest /template)" in (
        plan["copy"]["argv"][-1]
    )
    assert plan["copy"]["target_permission_normalization_required"] is True
    assert plan["copy"]["frozen_mysql_image_identity"] == {
        "uid": 999,
        "gid": 999,
        "directory_mode": "0700",
        "regular_file_mode": "0600",
    }
    assert "reflink" not in " ".join(plan["copy"]["argv"])
    assert plan["copy"]["fiemap_no_shared_extents_required"] is True
    assert "tar -C /source -cpf -" in (
        plan["runtime_publication"]["argv"][-1]
    )
    assert (
        plan["runtime_publication"]["source"]
        == authority.PYTHON_RUNTIME_SOURCE
    )
    assert (
        plan["runtime_publication"]["final_destination"]
        == authority.PYTHON_RUNTIME_TARGET
    )
    assert plan["collect_only"]["argv"][0].endswith("/bin/python3.11")
    assert "--mysql-client-container" in plan["collect_only"]["argv"]
    assert (
        plan["collect_only"]["argv"][
            plan["collect_only"]["argv"].index("--mysql-client-container") + 1
        ]
        == frozen_spec()["containers"]["client"]
    )
    assert "--mysql-binary-in-client" in plan["collect_only"]["argv"]
    assert authority.DEVICE in " ".join(plan["database"]["argv"])
    assert authority.DB_ENVELOPE["cpuset_cpus"] in plan["database"]["argv"]
    assert authority.CLIENT_ENVELOPE["cpuset_cpus"] in plan["client"]["argv"]
    assert "--read-only" in plan["client"]["argv"]
    assert "--cap-drop" in plan["client"]["argv"]
    assert frozen_spec()["paths"]["datadir"] not in " ".join(
        plan["client"]["argv"]
    )
    client_plan = " ".join(plan["client"]["argv"])
    assert (
        authority.CLIENT_MYSQL_DATADIR_MASK_RELATIVE in client_plan
    )
    assert "dst=/var/lib/mysql,readonly" in client_plan
    assert plan["sql_stdout_bridge"]["docker_log_driver_dependency"] is False
    assert plan["phase_order"] == [
        "start_database_and_client",
        "seal_start_authority",
        "enforce_and_read_back_effective_cgroup_v1_blkio_as_root",
        "adopt_same_live_database_and_client",
        "collect_only",
    ]
    assert plan["effective_blkio_enforce"]["host_root_required"] is True
    assert plan["effective_blkio_enforce"][
        "docker_hostconfig_is_declaration_not_effective_authority"
    ] is True
    assert plan["effective_blkio_enforce"]["argv"][1].endswith(
        "/qualification/mysql_tpch_blkio_v1.py"
    )


def test_effective_blkio_is_authority_and_hostconfig_is_declaration() -> None:
    spec = frozen_spec()
    evidence = bootstrap_evidence(spec)
    evidence["database"]["inspect"]["HostConfig"][
        "BlkioDeviceReadBps"
    ] = []
    evidence["database"]["inspect"]["HostConfig"][
        "BlkioDeviceWriteBps"
    ] = []
    evidence["same_live_identity"] = authority.validate_bootstrap_evidence(
        evidence, spec
    )
    assert evidence["same_live_identity"][
        "effective_blkio_authority_sha256"
    ] == evidence["effective_blkio_authority"]["authority_sha256"]
    evidence["database"]["inspect"]["HostConfig"][
        "BlkioDeviceReadBps"
    ] = [{"Path": authority.DEVICE, "Rate": 1}]
    with pytest.raises(authority.AuthorityError, match="exact envelope"):
        authority.validate_bootstrap_evidence(evidence, spec)


def test_client_mount_mask_rejects_anonymous_volume_and_mutable_mask() -> None:
    spec = frozen_spec()
    evidence = bootstrap_evidence(spec)
    anonymous = copy.deepcopy(evidence)
    anonymous["client"]["inspect"]["Mounts"].append(
        {
            "Type": "volume",
            "Source": "/var/lib/docker/volumes/cd6d/_data",
            "Destination": "/var/lib/mysql",
            "RW": True,
        }
    )
    with pytest.raises(authority.AuthorityError, match="client container"):
        authority.validate_bootstrap_evidence(anonymous, spec)
    mutable = copy.deepcopy(evidence)
    mask = next(
        row
        for row in mutable["client"]["inspect"]["Mounts"]
        if row["Destination"] == "/var/lib/mysql"
    )
    mask["RW"] = True
    with pytest.raises(authority.AuthorityError, match="client container"):
        authority.validate_bootstrap_evidence(mutable, spec)


def test_root_blkio_helper_writes_reads_and_seals_same_live_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    start_path = tmp_path / "start_authority.json"
    output = tmp_path / "blkio_throttle_authority.json"
    container_id = "4" * 64
    start = {
        "schema_version": "mysql_tpch_distributed_v2_start_authority_v1",
        "status": "PASS_CONTAINERS_STARTED_BLKIO_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": "b" * 64,
        "attempt_token": "tpch-v2-" + "a" * 32,
        "database": {
            "name": "db",
            "container_id": container_id,
            "pid": 31001,
        },
        "accepted_round_authorized": False,
    }
    start["authority_sha256"] = hashlib.sha256(
        blkio_helper.canonical(start)
    ).hexdigest()
    start_path.write_bytes(blkio_helper.canonical(start))
    Path(str(start_path) + ".sha256").write_text(
        "%s  %s\n"
        % (
            hashlib.sha256(start_path.read_bytes()).hexdigest(),
            start_path.name,
        ),
        encoding="ascii",
    )
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    for name in (
        "blkio.throttle.read_bps_device",
        "blkio.throttle.write_bps_device",
    ):
        (cgroup / name).write_text("", encoding="ascii")
    monkeypatch.setattr(blkio_helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(blkio_helper.os, "getegid", lambda: 0)
    monkeypatch.setattr(
        blkio_helper,
        "_cgroup_path",
        lambda pid, observed_id: cgroup,
    )
    inspect_row = {
        "Id": container_id,
        "State": {"Running": True, "Pid": 31001},
        "Config": {
            "Labels": {
                "ra.bundle_id": "b" * 64,
                "ra.attempt": "tpch-v2-" + "a" * 32,
                "ra.scenario": "tpch_memory",
            }
        },
    }
    monkeypatch.setattr(
        blkio_helper,
        "_run",
        lambda argv: SimpleNamespace(
            returncode=0, stdout=json.dumps([inspect_row]), stderr=""
        ),
    )
    result = blkio_helper.enforce(start_path, output)
    assert result["read_bps"] == 100_663_296
    assert result["write_bps"] == 100_663_296
    assert result["database_container_id"] == container_id
    assert output.is_file()
    assert Path(str(output) + ".sha256").is_file()


def test_qualification_authority_renderer_binds_short_token_and_sidecars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / ("b" * 64)
    bundle.mkdir()
    command_path = tmp_path / "command.json"
    token_path = tmp_path / "token"
    output = tmp_path / "authority.json"
    command = {
        "schema_version":
            "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": bundle.name,
        "bundle_manifest_sha256": "c" * 64,
        "mode": "qualification_solo",
        "qualification_attempt_id": "q-" + "d" * 40,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "cwd": "/measurement",
    }
    command_path.write_bytes(qualification_renderer.canonical(command))
    Path(str(command_path) + ".sha256").write_text(
        "%s  %s\n"
        % (qualification_renderer.sha256(command_path), command_path.name),
        encoding="ascii",
    )
    token_path.write_text("token-" + "e" * 48, encoding="ascii")
    token_path.chmod(0o600)
    manifest_path = bundle / "bundle_manifest.json"
    manifest_path.write_text("manifest", encoding="utf-8")
    monkeypatch.setattr(
        qualification_renderer,
        "verify_bundle",
        lambda _bundle: {
            "bundle_id": bundle.name,
            "schema_version":
                "mysql_tpch_distributed_formal10_successor_v2",
        },
    )
    monkeypatch.setattr(
        qualification_renderer,
        "sha256",
        lambda path: (
            "c" * 64
            if Path(path) == manifest_path
            else hashlib.sha256(Path(path).read_bytes()).hexdigest()
        ),
    )
    authority_document = qualification_renderer.render(
        bundle, command_path, token_path, output, 900, now=1000.0
    )
    assert authority_document["expires_epoch"] == 1900.0
    assert authority_document["not_before_epoch"] == 999.0
    assert authority_document["command_spec_sha256"] == hashlib.sha256(
        command_path.read_bytes()
    ).hexdigest()
    assert output.is_file()
    assert Path(str(output) + ".sha256").is_file()


def test_fresh_r0_builder_requires_real_profile_counters_and_l1_l2_l3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / ("b" * 64)
    bundle.mkdir()
    manifest_path = bundle / "bundle_manifest.json"
    manifest_path.write_text("manifest", encoding="utf-8")
    monkeypatch.setattr(
        fresh_r0_runner,
        "load_sealed",
        lambda path, label: {"bundle_id": bundle.name},
    )
    identity = {
        "database_container_id": "4" * 64,
        "database_pid": 31001,
        "client_container_id": "5" * 64,
        "client_pid": 31002,
        "client_starttime_ticks": 1002,
        "client_inspect_config_sha256": "6" * 64,
    }
    bootstrap = {
        "status": "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY",
        "bundle_id": bundle.name,
        "attempt_token": "tpch-v2-" + "a" * 32,
        "same_live_identity": identity,
    }
    gate = {
        "schema_version":
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "status": "PASS",
        "scenario": "tpch_memory",
        "host_id": "node04",
        "attempt_token": bootstrap["attempt_token"],
        "duration_seconds": 180,
        "minimum_samples_per_stream": 181,
        "database_counter_missing_samples": 0,
        "database_counter_timeout_count": 0,
        "all_tolerances_passed": True,
        "serial_fallback_required": False,
    }
    counters = {name: 1 for name in fresh_r0_runner.DB_COUNTER_NAMES}
    samples = [
        {
            "required_streams": {
                "database_counters": dict(counters),
                "memory_current_swap_and_pressure": {
                    "memory_current_bytes": 900_000 + index
                },
            }
        }
        for index in range(181)
    ]
    raw = {
        "schema_version":
            "mysql_algorithm_c_debug_qualification_evidence_v2",
        "status":
            "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY",
        "mode": "qualification_solo",
        "bundle_id": bundle.name,
        "scenario": "tpch_memory",
        "hostname": "node04",
        "duration_seconds": 180,
        "minimum_samples": 181,
        "samples": samples,
        "summary": {
            "elapsed_seconds": 180.0,
            "sample_count": 181,
            "resource_utilization": {
                "cpu_usage_ratio": 0.4,
                "memory_utilization_ratio": 0.9,
                "device_utilization_ratio": 0.4,
            },
        },
        "initial_live_readback": {
            "container": {
                "container_id": identity["database_container_id"],
                "pid": identity["database_pid"],
            },
            "database_counter_transport": {
                "client_container_id": identity["client_container_id"],
                "client_pid": identity["client_pid"],
                "client_process_start_ticks":
                    identity["client_starttime_ticks"],
                "client_inspect_config_sha256":
                    identity["client_inspect_config_sha256"],
            },
        },
        "accepted_round_authorized": False,
    }
    explains = [
        {
            "query": f"Q{index}.sql",
            "sql_sha256": str(index).zfill(64),
            "explain_sha256": str(index + 30).zfill(64),
            "document": {
                "query_block": {
                    "nested_loop": [
                        {
                            "table": {
                                "table_name": f"t{index}",
                                "access_type": "ALL",
                                "rows_examined_per_scan": 10,
                            }
                        }
                    ]
                }
            },
        }
        for index in range(1, 23)
    ]
    before = {
        name: 10
        for name in (
            fresh_r0_runner.DB_COUNTER_NAMES
            + fresh_r0_runner.SPILL_COUNTER_NAMES
        )
    }
    after = dict(before)
    after["Created_tmp_disk_tables"] = 12
    document = fresh_r0_runner.build_fresh_r0(
        bundle,
        bootstrap,
        gate,
        "7" * 64,
        raw,
        "8" * 64,
        before,
        after,
        explains,
    )
    assert document["status"] == "PASS_FRESH_R0"
    assert document["sample_count"] == 181
    assert document["database_counter_timeout_count"] == 0
    assert document["fingerprint"]["l1"]["scan_types"] == ["ALL"]
    assert document["fingerprint"]["l3"]["spill"][
        "Created_tmp_disk_tables"
    ] == 2
    assert document["accepted_rounds_credit"] == 0


def test_bundle_payload_filter_keeps_nested_manifest_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "predecessor").mkdir()
    (tmp_path / "bundle_manifest.json").write_text("root", encoding="utf-8")
    (tmp_path / "bundle_manifest.json.sha256").write_text(
        "root-sidecar", encoding="utf-8"
    )
    nested = tmp_path / "predecessor/bundle_manifest.json"
    nested.write_text("nested", encoding="utf-8")
    payload = authority.bundle_payload_files(tmp_path)
    assert payload == {
        "predecessor/bundle_manifest.json":
            hashlib.sha256(b"nested").hexdigest()
    }


def test_template_inventory_has_no_lsof_path_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/path-without-lsof")
    inventory = {
        "schema_version": "mysql_tpch_proc_open_owner_inventory_v1",
        "proc_root": "/proc",
        "template": authority.TEMPLATE,
        "scanned_pid_count": 10,
        "open_owners": [],
        "unreadable_pids": [],
        "complete": True,
        "pass": True,
    }
    monkeypatch.setattr(
        authority, "_scan_proc_open_owners", lambda *_: inventory
    )
    monkeypatch.setattr(
        authority,
        "_run",
        lambda *_args, **_kwargs: pytest.fail(
            "complete stdlib /proc scan must not invoke a host tool"
        ),
    )
    result = authority._template_owner_inventory(Path("/bundle"), frozen_spec())
    assert result["pass"] is True
    assert result["docker_fallback_used"] is False


def test_proc_fallback_uses_verified_node04_root_scanner_minimum_capabilities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The live node04 fixture fixes the only accepted fallback closure."""
    bundle = tmp_path / "sealed-bundle"
    bundle.mkdir(mode=0o700)
    script = bundle / "mysql_tpch_distributed_v2_authority.py"
    script.write_text("# sealed fallback entrypoint\n", encoding="utf-8")
    os.chmod(bundle, 0o700)
    local = {
        "schema_version": "mysql_tpch_proc_open_owner_inventory_v1",
        "proc_root": "/proc",
        "template": authority.TEMPLATE,
        "scanned_pid_count": 10,
        "open_owners": [],
        "unreadable_pids": [99],
        "complete": False,
        "pass": False,
    }
    monkeypatch.setattr(authority, "_scan_proc_open_owners", lambda *_: local)

    def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        assert "--user" in argv
        position = argv.index("--user")
        assert argv[position + 1] == "0:0"
        assert argv.count("--cap-add") == 2
        assert argv[argv.index("--cap-add") + 1] == "SYS_PTRACE"
        second_cap = argv.index("--cap-add", argv.index("--cap-add") + 1)
        assert argv[second_cap + 1] == "DAC_READ_SEARCH"
        assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
        assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
        assert "--read-only" in argv
        assert not any("docker.sock" in item for item in argv)
        assert str(bundle) in " ".join(argv)
        assert "--pid" in argv and argv[argv.index("--pid") + 1] == "host"
        assert "--proc-root" in argv and "/host-proc" in argv
        return SimpleNamespace(
            stdout=NODE04_ROOT_SCANNER_FIXTURE.read_text(encoding="utf-8")
        )

    monkeypatch.setattr(authority, "_run", fake_run)
    result = authority._template_owner_inventory(bundle, frozen_spec())
    assert result["docker_fallback_used"] is True
    assert result["docker_fallback_requested_user"] == "0:0"
    assert result["docker_fallback_capabilities"] == [
        "SYS_PTRACE", "DAC_READ_SEARCH"
    ]
    assert result["docker_fallback_docker_socket_mount_prohibited"] is True
    assert result["docker_fallback"]["scanner_identity"] == (
        result["docker_fallback_requested_identity"]
    )


def test_proc_fallback_failure_retains_stdout_for_bootstrap_error_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = {
        "schema_version": "mysql_tpch_proc_open_owner_inventory_v1",
        "proc_root": "/proc",
        "template": authority.TEMPLATE,
        "scanned_pid_count": 10,
        "open_owners": [],
        "unreadable_pids": [99],
        "complete": False,
        "pass": False,
    }
    monkeypatch.setattr(authority, "_scan_proc_open_owners", lambda *_: local)
    monkeypatch.setattr(
        authority,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="not-json-root-scanner"),
    )
    with pytest.raises(authority.FallbackInventoryError) as raised:
        authority._template_owner_inventory(Path("/bundle"), frozen_spec())
    assert raised.value.stdout == "not-json-root-scanner"
    assert "--user" in raised.value.argv


def test_stdlib_proc_scan_detects_template_fd(tmp_path: Path) -> None:
    process = tmp_path / "123"
    fd_root = process / "fd"
    fd_root.mkdir(parents=True)
    (fd_root / "7").symlink_to(authority.TEMPLATE + "/ibdata1")
    result = authority._scan_proc_open_owners(tmp_path, authority.TEMPLATE)
    assert result["pass"] is False
    assert result["open_owners"] == [
        {
            "pid": 123,
            "kind": "fd/7",
            "target": authority.TEMPLATE + "/ibdata1",
        }
    ]


def test_fiemap_audit_accepts_byte_copy_and_rejects_shared_extent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    copied = tmp_path / "copied"
    aliased = tmp_path / "aliased"
    source.mkdir()
    copied.mkdir()
    aliased.mkdir()
    (source / "payload").write_bytes(b"physical-copy-test" * 65536)
    shutil.copyfile(source / "payload", copied / "payload")
    os.link(source / "payload", aliased / "payload")
    os.sync()
    result = authority._physical_extent_audit(source, copied)
    assert result["shared_physical_extent_bytes"] == 0
    with pytest.raises(authority.AuthorityError, match="non-alias"):
        authority._physical_extent_audit(source, aliased)


def test_bootstrap_binds_pid_starttime_datadir_and_client() -> None:
    spec = frozen_spec()
    evidence = bootstrap_evidence(spec)
    identity = authority.validate_bootstrap_evidence(evidence, spec)
    assert identity["database_pid"] == 31001
    assert identity["database_starttime_ticks"] == 1001
    assert identity["database_datadir"] == spec["paths"]["datadir"]
    assert identity["client_pid"] == 31002
    assert identity["client_starttime_ticks"] == 1002
    assert identity["python_runtime_root"] == authority.PYTHON_RUNTIME_TARGET
    changed = copy.deepcopy(evidence)
    changed["client"]["starttime_ticks"] = 9999
    changed["same_live_identity"]["client_starttime_ticks"] = 9999
    # A changed bootstrap may be internally valid, but no later R0/debug
    # identity may differ from the originally sealed evidence.
    assert (
        authority.validate_bootstrap_evidence(changed, spec)[
            "client_starttime_ticks"
        ]
        == 9999
    )
    assert evidence["same_live_identity"] != changed["same_live_identity"]


@pytest.mark.parametrize("source_tree", [True, False])
def test_tree_digest_normalizes_only_the_exact_runtime_socket(
    monkeypatch: pytest.MonkeyPatch, source_tree: bool
) -> None:
    calls = []

    def fake_run(argv, timeout=300, check=True):
        calls.append(argv)
        return SimpleNamespace(
            stdout=(
                "a" * 64
                + f" {authority.EXPECTED_TEMPLATE_REGULAR_FILE_COUNT}"
                + f" {authority.EXPECTED_TEMPLATE_REGULAR_FILE_BYTES}\n"
            )
        )

    monkeypatch.setattr(authority, "_run", fake_run)
    digest = authority._tree_digest(
        "sha256:" + "1" * 64, "/tree", source_tree
    )
    script = calls[0][-1]
    argv = calls[0]
    assert argv[:8] == [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--user", "0:0",
    ]
    assert argv[8:12] == [
        "--cap-drop", "ALL", "--cap-add", "DAC_READ_SEARCH"
    ]
    assert argv[argv.index("--entrypoint") + 1] == "/bin/bash"
    assert any(
        value.startswith("type=bind,src=/tree,dst=/tree,readonly")
        for value in argv
    )
    assert "set -o pipefail" in script
    assert (
        f'test "$count" = {authority.EXPECTED_TEMPLATE_REGULAR_FILE_COUNT}'
        in script
    )
    assert (
        f'test "$bytes" = {authority.EXPECTED_TEMPLATE_REGULAR_FILE_BYTES}'
        in script
    )
    assert "find . -xdev -mindepth 1 ! -path ./mysql.sock -print0" in script
    assert "sort -z" in script
    assert "stat -c %s" in script
    assert "sha256sum -- \"$entry\"" in script
    assert "tar " not in script
    assert digest["tree_identity_normalization"] == (
        authority.TREE_IDENTITY_NORMALIZATION
    )
    assert digest["tree_identity_normalization"][
        "path_type_size_sha256_content_identity"
    ] is True
    assert digest["tree_identity_normalization"][
        "ownership_and_mode_excluded_from_content_identity"
    ] is True
    if source_tree:
        assert "readlink mysql.sock" in script
        assert "-type l ! -path ./mysql.sock" in script
    else:
        assert "test ! -e mysql.sock" in script
        assert "-type l -print -quit" in script


def test_tree_digest_rejects_incomplete_file_or_byte_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        authority,
        "_run",
        lambda argv, timeout=300, check=True: SimpleNamespace(
            stdout="a" * 64 + " 172 27111280639\n"
        ),
    )
    with pytest.raises(authority.AuthorityError, match="output differs"):
        authority._tree_digest(
            "sha256:" + "1" * 64, "/tree", source_tree=False
        )


def test_copy_failure_evidence_denies_database_client_and_credit() -> None:
    spec = frozen_spec()
    evidence = authority._copy_failure_evidence(
        spec, authority.AuthorityError("identity differs"), None, None
    )
    assert evidence["status"] == "FAIL_PHYSICAL_COPY_IDENTITY_BEFORE_DB_CLIENT"
    assert evidence["database_or_client_started"] is False
    assert evidence["accepted_round_authorized"] is False


def test_attempt_private_tmpfs_mount_is_exact_and_read_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempt = "tpch-v2-" + ("m" * 32)
    measurement_root = tmp_path / "ra_mysql_tpch_v2"
    host_tmpfs = measurement_root / attempt
    host_tmpfs.mkdir(parents=True, mode=0o700)
    calls: list[list[str]] = []
    uid = os.getuid()
    gid = os.getgid()
    monkeypatch.setattr(authority, "HOST_MEASUREMENT_ROOT", measurement_root)
    monkeypatch.setattr(authority.os, "geteuid", lambda: 0)
    monkeypatch.setattr(authority.os, "getegid", lambda: 0)
    monkeypatch.setitem(
        authority.CLIENT_ENVELOPE, "measurement_directory_uid", uid
    )
    monkeypatch.setitem(
        authority.CLIENT_ENVELOPE, "measurement_directory_gid", gid
    )
    monkeypatch.setattr(authority.os, "chown", lambda *_: None)
    monkeypatch.setattr(
        authority.os,
        "statvfs",
        lambda *_: SimpleNamespace(f_frsize=4096, f_blocks=524288),
    )

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[0] == "findmnt":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    f"{host_tmpfs} tmpfs tmpfs "
                    f"rw,nosuid,nodev,noexec,size=2G,uid={uid},gid={gid},mode=700\n"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(authority, "_run", fake_run)
    evidence = authority._prepare_secure_measurement_bind(host_tmpfs)
    assert calls[0][0:3] == ["mount", "-t", "tmpfs"]
    assert f"size={authority.HOST_MEASUREMENT_TMPFS_BYTES}" in calls[0][4]
    assert evidence["mount_target_exact"] is True
    assert evidence["size_bytes"] == 2 * 1024 ** 3
    assert evidence["statvfs_capacity_bytes"] == 2 * 1024 ** 3
    assert evidence["mount_options_required"] == [
        "nodev", "noexec", "nosuid", "rw"
    ]
    assert (host_tmpfs / "client_measurement").is_dir()


def test_attempt_private_tmpfs_verification_failure_unmounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempt = "tpch-v2-" + ("n" * 32)
    measurement_root = tmp_path / "ra_mysql_tpch_v2"
    host_tmpfs = measurement_root / attempt
    host_tmpfs.mkdir(parents=True, mode=0o700)
    calls: list[list[str]] = []
    monkeypatch.setattr(authority, "HOST_MEASUREMENT_ROOT", measurement_root)
    monkeypatch.setattr(authority.os, "geteuid", lambda: 0)
    monkeypatch.setattr(authority.os, "getegid", lambda: 0)
    monkeypatch.setattr(
        authority.os,
        "statvfs",
        lambda *_: SimpleNamespace(f_frsize=4096, f_blocks=262144),
    )

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[0] == "findmnt":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    f"{host_tmpfs} tmpfs tmpfs "
                    "rw,nosuid,nodev,noexec,size=1G,uid=1008,gid=1008,mode=700\n"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(authority, "_run", fake_run)
    with pytest.raises(
        authority.AuthorityError,
        match="measurement private tmpfs readback differs",
    ):
        authority._prepare_secure_measurement_bind(host_tmpfs)
    assert [call[0] for call in calls] == ["mount", "findmnt", "umount"]


def test_bootstrap_evidence_rejects_non_target_only_permission_normalization() -> None:
    spec = frozen_spec()
    evidence = bootstrap_evidence(spec)
    evidence["copy"]["target_permission_normalization"]["target_only"] = False
    with pytest.raises(authority.AuthorityError, match="physical no-reflink"):
        authority.validate_bootstrap_evidence(evidence, spec)


def test_readiness_permission_normalization_is_target_only_and_hash_bound() -> None:
    normalization = bootstrap_evidence(frozen_spec())["copy"][
        "target_permission_normalization"
    ]
    assert readiness._target_permission_normalization_valid(normalization, 173)
    normalization["source_metadata_sha256_after"] = "not-the-same"
    assert not readiness._target_permission_normalization_valid(normalization, 173)


def test_readiness_recomputes_same_live_identity_from_raw_inspect() -> None:
    spec = frozen_spec()
    evidence = bootstrap_evidence(spec)
    assert (
        readiness._bootstrap_raw_identity(evidence)
        == evidence["same_live_identity"]
    )
    tampered = copy.deepcopy(evidence)
    tampered["same_live_identity"]["client_starttime_ticks"] += 1
    assert (
        readiness._bootstrap_raw_identity(tampered)
        != tampered["same_live_identity"]
    )


def test_gradient_math_full_subspace_and_fingerprint_fail_closed() -> None:
    spec = frozen_spec()
    bootstrap = bootstrap_evidence(spec)
    document = debug_document(spec, bootstrap)
    readiness.verify_debug_round_document(
        document,
        bundle_id=spec["bundle"]["bundle_id"],
        attempt_token=spec["attempt_token"],
        identity=bootstrap["same_live_identity"],
    )
    bad = copy.deepcopy(document)
    first_parameter = bad["gradient_events"][0]["active_subset"][0]
    bad["gradient_events"][0]["g_j"][first_parameter] += 0.01
    with pytest.raises(readiness.ReadinessError, match="not sum"):
        readiness.verify_debug_round_document(
            bad,
            bundle_id=spec["bundle"]["bundle_id"],
            attempt_token=spec["attempt_token"],
            identity=bootstrap["same_live_identity"],
        )
    bad = copy.deepcopy(document)
    bad["candidate_fingerprints"][0]["l3"].pop("spill")
    with pytest.raises(readiness.ReadinessError, match="L1/L2/L3"):
        readiness.verify_debug_round_document(
            bad,
            bundle_id=spec["bundle"]["bundle_id"],
            attempt_token=spec["attempt_token"],
            identity=bootstrap["same_live_identity"],
        )


def test_bundle_is_current_collector_not_e29_and_v40_is_denied() -> None:
    bundle = builder.publish(builder.DEFAULT_OUTPUT_ROOT)
    verified = readiness.verify_bundle(bundle)
    assert spec_renderer._manifest(
        bundle / "bundle_manifest.json"
    )["bundle_id"] == bundle.name
    manifest = verified["manifest"]
    collector = (
        bundle
        / "qualification/mysql_distributed_live_readback_qualification_v1.py"
    )
    collector_source = collector.read_text(encoding="utf-8")
    assert manifest["component_hashes"]["collector"] == hashlib.sha256(
        collector.read_bytes()
    ).hexdigest()
    assert collector_source.count(
        '"mysql_tpch_distributed_formal10_successor_v2"'
    ) == 1
    assert "schema.startswith" not in collector_source
    assert "client anonymous or named volume is prohibited" in collector_source
    assert "qualification/client_volume_masks/mysql_datadir" in collector_source
    sealed_authority_source = (
        bundle / "mysql_tpch_distributed_v2_authority.py"
    ).read_text(encoding="utf-8")
    assert '"cwd": "/measurement"' in sealed_authority_source
    runtime = json.loads(
        (bundle / "distributed_runtime_contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert runtime["client_resource_envelope"] == (
        builder.verify_inputs()["tpch"]["client_resource_envelope"]
    )
    assert runtime["database_counter_transport"][
        "kind"
    ] == "exact_client_container_docker_exec_stdout_v1"
    assert runtime["effective_blkio_authority"]["read_bps"] == 100663296
    assert runtime["effective_blkio_authority"]["write_bps"] == 100663296
    assert runtime["effective_blkio_authority"][
        "docker_hostconfig_role"
    ] == "declaration_only"
    assert runtime["client_mysql_datadir_volume_mask"] == {
        "source_relative":
            builder.CLIENT_MYSQL_DATADIR_MASK_RELATIVE,
        "destination": "/var/lib/mysql",
        "mount_type": "bind",
        "read_only_required": True,
        "manifest_covered_marker_required": True,
        "anonymous_or_named_volume_prohibited": True,
        "mutable_application_mount_prohibited": True,
        "docker_managed_resolver_metadata_only_exception": True,
    }
    assert runtime["qualification_authority_renderer"][
        "json_and_sha256_sidecars_required"
    ] is True
    assert runtime["fresh_r0_runner"]["duration_seconds"] == 180
    assert runtime["fresh_r0_runner"]["sample_count"] == 181
    assert runtime["fresh_r0_runner"][
        "database_counter_missing_or_timeout_result"
    ] == "FAIL"
    assert runtime["fresh_r0_runner"]["l1_l2_l3_required"] is True
    assert runtime["toolchain"]["java_required"] is False
    collector_invalidation = json.loads(
        (bundle / "invalidated_collector_schema_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert collector_invalidation["bundle_id"] == (
        builder.INVALIDATED_COLLECTOR_SCHEMA_BUNDLE.name
    )
    assert collector_invalidation["accepted_rounds_credit"] == 0
    blkio_invalidation = json.loads(
        (bundle / "invalidated_effective_blkio_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert blkio_invalidation["bundle_id"] == (
        builder.INVALIDATED_EFFECTIVE_BLKIO_BUNDLE.name
    )
    assert blkio_invalidation["accepted_rounds_credit"] == 0
    assert blkio_invalidation["reusable"] is False
    volume_invalidation = json.loads(
        (bundle / "invalidated_client_volume_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert volume_invalidation["bundle_id"] == (
        builder.INVALIDATED_CLIENT_VOLUME_BUNDLE.name
    )
    assert volume_invalidation["database_pid"] == 87352
    assert volume_invalidation["client_pid"] == 87558
    assert volume_invalidation["accepted_rounds_credit"] == 0
    cwd_invalidation = json.loads(
        (bundle / "invalidated_qualification_cwd_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert cwd_invalidation["bundle_id"] == (
        builder.INVALIDATED_QUALIFICATION_CWD_BUNDLE.name
    )
    assert cwd_invalidation["workload_started"] is False
    assert cwd_invalidation["accepted_rounds_credit"] == 0
    identity_invalidation = json.loads(
        (
            bundle
            / "invalidated_client_identity_projection_bundle.json"
        ).read_text(encoding="utf-8")
    )
    assert identity_invalidation["bundle_id"] == (
        builder.INVALIDATED_CLIENT_IDENTITY_BUNDLE.name
    )
    assert identity_invalidation[
        "same_live_client_inspect_config_sha256"
    ] == "fbc8a5b5f9b025fe807793160d4645652a5ce9cb3f874bbce4e988b440a57fb5"
    assert identity_invalidation[
        "collect_only_client_inspect_config_sha256"
    ] == "440a1093e2584491583a8c0eda5d36055f056c75b0cba9ce3456a8eaeafb688b"
    assert identity_invalidation["accepted_rounds_credit"] == 0
    assert identity_invalidation["reusable"] is False
    bytecode_invalidation = json.loads(
        (bundle / "invalidated_import_bytecode_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert bytecode_invalidation["bundle_id"] == (
        builder.INVALIDATED_IMPORT_BYTECODE_BUNDLE.name
    )
    assert bytecode_invalidation[
        "published_exact_payload_file_count"
    ] == 1027
    assert bytecode_invalidation[
        "bootstrap_reached_container_or_workload_start"
    ] is False
    assert bytecode_invalidation["accepted_rounds_credit"] == 0
    assert bytecode_invalidation["reusable"] is False
    mask_marker = (
        bundle
        / builder.CLIENT_MYSQL_DATADIR_MASK_RELATIVE
        / "mask_identity.json"
    )
    assert mask_marker.is_file()
    assert manifest["component_hashes"][
        "client_mysql_datadir_readonly_mask"
    ] == hashlib.sha256(mask_marker.read_bytes()).hexdigest()
    assert manifest["component_hashes"]["blkio_v1_helper"] == hashlib.sha256(
        (bundle / "qualification/mysql_tpch_blkio_v1.py").read_bytes()
    ).hexdigest()
    for key, relative in (
        (
            "qualification_authority_renderer",
            "qualification/mysql_tpch_v2_qualification_authority_v1.py",
        ),
        (
            "fresh_r0_runner",
            "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py",
        ),
        (
            "client_identity_helper_root",
            "mysql_tpch_client_immutable_identity_v1.py",
        ),
        (
            "client_identity_helper_qualification",
            "qualification/mysql_tpch_client_immutable_identity_v1.py",
        ),
    ):
        assert manifest["component_hashes"][key] == hashlib.sha256(
            (bundle / relative).read_bytes()
        ).hexdigest()
    assert builder.INVALIDATED_COLLECTOR_SCHEMA_BUNDLE.name in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert builder.INVALIDATED_EFFECTIVE_BLKIO_BUNDLE.name in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert builder.INVALIDATED_CLIENT_VOLUME_BUNDLE.name in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert builder.INVALIDATED_QUALIFICATION_CWD_BUNDLE.name in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert builder.INVALIDATED_CLIENT_IDENTITY_BUNDLE.name in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert builder.INVALIDATED_IMPORT_BYTECODE_BUNDLE.name in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert manifest["e29_collector_inherited"] is False
    assert (
        "e29cafa825e8ed9134e1467a3eafffcaa8572fc5a6d9a9ba0dd676a091248d27"
        in manifest["invalidated_predecessor_bundle_ids"]
    )
    assert verified["invalidation"]["phase"] == "INVALID_R0_UNSTABLE"
    assert verified["invalidation"]["reusable"] is False
    assert verified["invalidation"]["accepted_rounds_credit"] == 0
    assert (
        verified["invalidated_bootstrap_bundle"]["status"]
        == "INVALIDATED_DO_NOT_LAUNCH"
    )
    assert (
        verified["invalidated_bootstrap_bundle"]["bundle_id"]
        == authority.INVALIDATED_BUNDLE_ID
    )
    assert (
        verified["invalidated_closure_bundle"]["status"]
        == "INVALIDATED_DO_NOT_LAUNCH"
    )
    assert (
        verified["invalidated_closure_bundle"]["bundle_id"]
        == authority.INVALIDATED_CLOSURE_BUNDLE_ID
    )
    assert (
        verified["invalidated_lsof_bundle"]["status"]
        == "INVALIDATED_DO_NOT_LAUNCH"
    )
    assert (
        verified["invalidated_lsof_bundle"]["bundle_id"]
        == authority.INVALIDATED_LSOF_BUNDLE_ID
    )
    assert (
        verified["invalidated_fallback_bundle"]["bundle_id"]
        == authority.INVALIDATED_FALLBACK_BUNDLE_ID
    )
    assert authority.INVALIDATED_FALLBACK_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert (
        verified["invalidated_nonroot_fallback_bundle"]["bundle_id"]
        == authority.INVALIDATED_NONROOT_FALLBACK_BUNDLE_ID
    )
    assert authority.INVALIDATED_NONROOT_FALLBACK_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert (
        verified["invalidated_socket_identity_bundle"]["bundle_id"]
        == authority.INVALIDATED_SOCKET_IDENTITY_BUNDLE_ID
    )
    assert authority.INVALIDATED_SOCKET_IDENTITY_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert (
        verified["invalidated_permission_bundle"]["bundle_id"]
        == authority.INVALIDATED_PERMISSION_BUNDLE_ID
    )
    assert authority.INVALIDATED_PERMISSION_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert (
        verified["invalidated_permission_readback_bundle"]["bundle_id"]
        == authority.INVALIDATED_PERMISSION_READBACK_BUNDLE_ID
    )
    assert authority.INVALIDATED_PERMISSION_READBACK_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert authority.EXPECTED_TEMPLATE_REGULAR_FILE_COUNT == 173
    assert authority.EXPECTED_TEMPLATE_REGULAR_FILE_BYTES == 27_110_986_878
    assert (
        verified["invalidated_tree_digest_bytes_bundle"]["bundle_id"]
        == authority.INVALIDATED_TREE_DIGEST_BYTES_BUNDLE_ID
    )
    assert (
        verified["invalidated_tree_digest_bytes_bundle"][
            "read_only_scanner_regular_file_bytes"
        ]
        == authority.EXPECTED_TEMPLATE_REGULAR_FILE_BYTES
    )
    assert authority.INVALIDATED_TREE_DIGEST_BYTES_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert (
        verified["invalidated_tree_digest_bundle"]["bundle_id"]
        == authority.INVALIDATED_TREE_DIGEST_BUNDLE_ID
    )
    assert (
        verified["invalidated_tree_digest_bundle"]["bundle_manifest_sha256"]
        == "b545b4e7ae47987a54082bf1a2e5b8ccbec6320f3bd6733f9bcd5d9edb11bb56"
    )
    assert authority.INVALIDATED_TREE_DIGEST_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )
    assert (
        verified["invalidated_metadata_sensitive_tree_identity_bundle"][
            "bundle_id"
        ]
        == authority.INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE_ID
    )
    assert verified[
        "invalidated_metadata_sensitive_tree_identity_bundle"
    ]["target_permission_readback_remains_separate_and_strict"] is True
    assert authority.INVALIDATED_METADATA_SENSITIVE_TREE_IDENTITY_BUNDLE_ID in (
        manifest["invalidated_predecessor_bundle_ids"]
    )


def test_sealed_collector_accepts_exact_tpch_schema_and_runtime_closure() -> None:
    bundle = builder.publish(builder.DEFAULT_OUTPUT_ROOT)
    collector_path = (
        bundle
        / "qualification/mysql_distributed_live_readback_qualification_v1.py"
    )
    module_spec = importlib.util.spec_from_file_location(
        "sealed_tpch_live_collector", collector_path
    )
    assert module_spec is not None and module_spec.loader is not None
    collector = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(collector)
    manifest, runtime = collector.verify_bundle(bundle)
    assert manifest["schema_version"] == (
        "mysql_tpch_distributed_formal10_successor_v2"
    )
    assert runtime["scenario"] == "tpch_memory"
    assert runtime["database_counter_transport"]["schema_version"] == (
        "mysql_algorithm_c_database_counter_transport_v1"
    )


def test_find_printf_numeric_uid_gid_are_not_username_group_name_fields(
    tmp_path: Path,
) -> None:
    """Exercise find's actual %u/%U and %g/%G semantics used by the plan."""
    name_fields = subprocess.check_output(
        ["find", str(tmp_path), "-maxdepth", "0", "-printf", "%u:%g:%m"],
        text=True,
    )
    numeric_fields = subprocess.check_output(
        ["find", str(tmp_path), "-maxdepth", "0", "-printf", "%U:%G:%m"],
        text=True,
    )
    assert name_fields == (
        f"{pwd.getpwuid(os.getuid()).pw_name}:"
        f"{grp.getgrgid(os.getgid()).gr_name}:{tmp_path.stat().st_mode & 0o777:o}"
    )
    assert numeric_fields == (
        f"{os.getuid()}:{os.getgid()}:{tmp_path.stat().st_mode & 0o777:o}"
    )


def test_builder_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    first = builder.publish(tmp_path / "bundles")
    second = builder.publish(tmp_path / "bundles")
    assert first == second
    assert first.name == second.name
    builder.verify_bundle(first)
