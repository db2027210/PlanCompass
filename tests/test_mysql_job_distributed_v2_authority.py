from __future__ import annotations

from datetime import datetime, timedelta, timezone
import copy
import hashlib
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/mysql_job_distributed_v2_authority.py"


def load_module():
    spec = importlib.util.spec_from_file_location("job_v2_authority", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUTH = load_module()


def frozen_spec() -> dict:
    attempt = "job-v2-" + ("a" * 32)
    bundle_id = "b" * 64
    attempt_root = f"{AUTH.RUNTIME_ROOT}/attempts/{attempt}"
    now = datetime.now(timezone.utc)
    return {
        "schema_version": "mysql_job_distributed_v2_bootstrap_spec_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": AUTH.SCENARIO,
        "host_id": AUTH.HOSTNAME,
        "attempt_token": attempt,
        "bundle": {
            "path": f"{AUTH.RUNTIME_ROOT}/bundles/{bundle_id}",
            "bundle_id": bundle_id,
            "manifest_sha256": "c" * 64,
        },
        "contract_sha256": AUTH.CONTRACT_SHA256,
        "reconciliation_sha256": AUTH.RECONCILIATION_SHA256,
        "mysql_image": {
            "reference": "mysql@sha256:" + ("d" * 64),
            "image_id": "sha256:" + ("e" * 64),
        },
        "client_image": {
            "reference": "mysql@sha256:" + ("f" * 64),
            "image_id": "sha256:" + ("1" * 64),
        },
        "paths": {
            "template": AUTH.TEMPLATE,
            "runtime_root": AUTH.RUNTIME_ROOT,
            "attempt_root": attempt_root,
            "datadir": attempt_root + "/datadir",
            "credentials_dir": attempt_root + "/credentials",
            "measurement_tmpfs": attempt_root + "/measurement",
            "python_runtime_root": (
                AUTH.RUNTIME_ROOT + "/runtime/python311-" + ("2" * 64)
            ),
        },
        "containers": {
            "database": "ra_mysql_job_v2_job_v2_" + ("a" * 32) + "_db",
            "client": "ra_mysql_job_v2_job_v2_" + ("a" * 32) + "_client",
        },
        "database_counter_bridge": {
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "client_container":
                "ra_mysql_job_v2_job_v2_" + ("a" * 32) + "_client",
            "mysql_binary_in_client": AUTH.MYSQL_BINARY_IN_CLIENT,
            "stdout_only": True,
            "docker_socket_mount_prohibited": True,
            "target_device_write_prohibited": True,
        },
        "python_runtime": {
            "manifest_relative": AUTH.PYTHON_MANIFEST_RELATIVE,
            "manifest_sha256": AUTH.PYTHON_MANIFEST_SHA256,
            "tree_content_sha256": AUTH.PYTHON_TREE_CONTENT_SHA256,
            "executable_relative": AUTH.PYTHON_EXECUTABLE_RELATIVE,
            "executable_path": (
                AUTH.RUNTIME_ROOT + "/runtime/python311-" + ("2" * 64)
                + "/" + AUTH.PYTHON_EXECUTABLE_RELATIVE
            ),
            "executable_sha256": AUTH.PYTHON_EXECUTABLE_SHA256,
        },
        "mysql_defaults_file": attempt_root + "/credentials/mysql.cnf",
        "collector": (
            f"{AUTH.RUNTIME_ROOT}/bundles/{bundle_id}/qualification/"
            "mysql_distributed_live_readback_qualification_v1.py"
        ),
        "created_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=30)).isoformat(),
        "main_agent_token_sha256": hashlib.sha256(b"private-token").hexdigest(),
        "qualification_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def test_bootstrap_account_sql_is_percent_safe_and_secret_stdin_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "CHANGE_ME" * 12
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(AUTH, "_run", fake_run)
    AUTH._apply_bootstrap_account_sql("bootstrap-db", password)
    argv, kwargs = calls[0]
    assert argv == [
        "docker", "exec", "-i", "bootstrap-db",
        "mysql", "-uroot", "-NB",
    ]
    assert password not in " ".join(argv)
    assert kwargs["stdin_text"].count(password) == 3
    assert "'root'@'%'" in kwargs["stdin_text"]
    assert kwargs["sensitive_values"] == (password,)


def test_attempt_defaults_pin_root_and_reject_placeholder(tmp_path: Path) -> None:
    password = "CHANGE_ME" * 12
    defaults = tmp_path / "mysql.cnf"
    AUTH._write_client_defaults(defaults, password)
    assert AUTH._verify_client_defaults_binding(defaults) == {
        "user": "root", "placeholder_free": True,
    }
    text = defaults.read_text(encoding="utf-8")
    assert "user=root" in text
    assert "UNKNOWN_USER" not in text
    assert password not in repr(AUTH._verify_client_defaults_binding(defaults))
    defaults.write_text("[client]\nuser=UNKNOWN_USER\npassword=x\n", encoding="utf-8")
    with pytest.raises(AUTH.AuthorityError, match="user binding differs"):
        AUTH._verify_client_defaults_binding(defaults)


def test_sensitive_stdin_failure_redacts_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "CHANGE_ME" * 16

    def fake_subprocess_run(*args, **kwargs):
        assert kwargs["input"] == "SQL " + password
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="server rejected " + password,
        )

    monkeypatch.setattr(AUTH.subprocess, "run", fake_subprocess_run)
    with pytest.raises(AUTH.AuthorityError) as exc:
        AUTH._run(
            ["docker", "exec", "db", "mysql"],
            stdin_text="SQL " + password,
            sensitive_values=(password,),
        )
    assert password not in str(exc.value)
    assert "<redacted>" in str(exc.value)


def test_f058_bundle_and_failed_attempt_are_denied() -> None:
    bundle_spec = frozen_spec()
    bundle_spec["bundle"]["bundle_id"] = AUTH.INVALIDATED_F058_BUNDLE_ID
    bundle_spec["bundle"]["path"] = (
        AUTH.RUNTIME_ROOT + "/bundles/" + AUTH.INVALIDATED_F058_BUNDLE_ID
    )
    with pytest.raises(AUTH.AuthorityError, match="f058"):
        AUTH.validate_spec(bundle_spec)

    attempt_spec = frozen_spec()
    attempt = AUTH.INVALIDATED_F058_ATTEMPT_TOKEN
    root = AUTH.RUNTIME_ROOT + "/attempts/" + attempt
    attempt_spec["attempt_token"] = attempt
    attempt_spec["paths"].update(
        {
            "attempt_root": root,
            "datadir": root + "/datadir",
            "credentials_dir": root + "/credentials",
            "measurement_tmpfs": root + "/measurement",
        }
    )
    suffix = re.sub(r"[^a-z0-9]", "_", attempt.lower())[:48]
    attempt_spec["containers"] = {
        "database": "ra_mysql_job_v2_" + suffix + "_db",
        "client": "ra_mysql_job_v2_" + suffix + "_client",
    }
    attempt_spec["database_counter_bridge"]["client_container"] = (
        attempt_spec["containers"]["client"]
    )
    attempt_spec["mysql_defaults_file"] = root + "/credentials/mysql.cnf"
    with pytest.raises(AUTH.AuthorityError, match="f058"):
        AUTH.validate_spec(attempt_spec)


def test_4ee_bundle_and_failed_attempt_are_denied() -> None:
    bundle_spec = frozen_spec()
    bundle_spec["bundle"]["bundle_id"] = AUTH.INVALIDATED_FOUREE_BUNDLE_ID
    bundle_spec["bundle"]["path"] = (
        AUTH.RUNTIME_ROOT + "/bundles/" + AUTH.INVALIDATED_FOUREE_BUNDLE_ID
    )
    with pytest.raises(AUTH.AuthorityError, match="4ee"):
        AUTH.validate_spec(bundle_spec)
    attempt_spec = frozen_spec()
    attempt = AUTH.INVALIDATED_FOUREE_ATTEMPT_TOKEN
    attempt_spec["attempt_token"] = attempt
    root = AUTH.RUNTIME_ROOT + "/attempts/" + attempt
    attempt_spec["paths"]["attempt_root"] = root
    attempt_spec["paths"]["datadir"] = root + "/datadir"
    attempt_spec["paths"]["credentials_dir"] = root + "/credentials"
    attempt_spec["paths"]["measurement_tmpfs"] = root + "/measurement"
    suffix = attempt.replace("-", "_")[:48]
    attempt_spec["containers"] = {
        "database": "ra_mysql_job_v2_%s_db" % suffix,
        "client": "ra_mysql_job_v2_%s_client" % suffix,
    }
    attempt_spec["database_counter_bridge"]["client_container"] = (
        attempt_spec["containers"]["client"]
    )
    attempt_spec["mysql_defaults_file"] = root + "/credentials/mysql.cnf"
    with pytest.raises(AUTH.AuthorityError, match="4ee"):
        AUTH.validate_spec(attempt_spec)


def test_f1c705_bundle_is_denied() -> None:
    spec = frozen_spec()
    spec["bundle"]["bundle_id"] = AUTH.INVALIDATED_F1C705_BUNDLE_ID
    spec["bundle"]["path"] = (
        AUTH.RUNTIME_ROOT + "/bundles/" + AUTH.INVALIDATED_F1C705_BUNDLE_ID
    )
    with pytest.raises(AUTH.AuthorityError, match="f1c705"):
        AUTH.validate_spec(spec)


def test_7e08bd_bundle_is_denied() -> None:
    spec = frozen_spec()
    spec["bundle"]["bundle_id"] = AUTH.INVALIDATED_SEVENEO8_BUNDLE_ID
    spec["bundle"]["path"] = (
        AUTH.RUNTIME_ROOT + "/bundles/" + AUTH.INVALIDATED_SEVENEO8_BUNDLE_ID
    )
    with pytest.raises(AUTH.AuthorityError, match="7e08bd"):
        AUTH.validate_spec(spec)


def test_2fe2ae_bundle_is_denied() -> None:
    spec = frozen_spec()
    spec["bundle"]["bundle_id"] = AUTH.INVALIDATED_TWOFEE_BUNDLE_ID
    spec["bundle"]["path"] = (
        AUTH.RUNTIME_ROOT + "/bundles/" + AUTH.INVALIDATED_TWOFEE_BUNDLE_ID
    )
    with pytest.raises(AUTH.AuthorityError, match="2fe2ae"):
        AUTH.validate_spec(spec)


def db_inspect(spec: dict) -> dict:
    return {
        "Id": "3" * 64,
        "Image": spec["mysql_image"]["image_id"],
        "Name": "/" + spec["containers"]["database"],
        "Created": "2026-07-31T00:00:00Z",
        "RestartCount": 0,
        "Config": {"Labels": {"ra.attempt": spec["attempt_token"]}},
        "State": {"Running": True, "Pid": 31001},
        "HostConfig": {
            "CpuPeriod": AUTH.DB_ENVELOPE["cpu_period_us"],
            "CpuQuota": AUTH.DB_ENVELOPE["cpu_quota_us"],
            "CpusetCpus": AUTH.DB_ENVELOPE["cpuset_cpus"],
            "CpusetMems": AUTH.DB_ENVELOPE["cpuset_mems"],
            "Memory": AUTH.DB_ENVELOPE["memory_bytes"],
            "MemorySwap": AUTH.DB_ENVELOPE["memory_swap_bytes"],
            "PidsLimit": AUTH.DB_ENVELOPE["pids_max"],
            "BlkioDeviceReadBps": [
                {
                    "Path": AUTH.DEVICE,
                    "Rate": AUTH.DB_ENVELOPE["io_read_bps"],
                }
            ],
            "BlkioDeviceWriteBps": [
                {
                    "Path": AUTH.DEVICE,
                    "Rate": AUTH.DB_ENVELOPE["io_write_bps"],
                }
            ],
            "PortBindings": {
                "3306/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": str(AUTH.MYSQL_PORT)}
                ]
            },
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": spec["paths"]["datadir"],
                "Destination": "/var/lib/mysql",
                "RW": True,
            },
            {
                "Type": "bind",
                "Source": spec["paths"]["credentials_dir"],
                "Destination": "/run/ra-credentials",
                "RW": False,
            },
        ],
    }


def client_inspect(spec: dict) -> dict:
    mounts = [
        (spec["bundle"]["path"], spec["bundle"]["path"]),
        (
            spec["paths"]["python_runtime_root"],
            spec["paths"]["python_runtime_root"],
        ),
        (
            spec["paths"]["credentials_dir"],
            spec["paths"]["credentials_dir"],
        ),
        (
            spec["bundle"]["path"]
            + "/"
            + AUTH.CLIENT_MYSQL_DATADIR_MASK_RELATIVE,
            "/var/lib/mysql",
        ),
    ]
    return {
        "Id": "4" * 64,
        "Image": spec["client_image"]["image_id"],
        "Name": "/" + spec["containers"]["client"],
        "Created": "2026-07-31T00:00:01Z",
        "RestartCount": 0,
        "Config": {
            "Labels": {"ra.attempt": spec["attempt_token"]},
            "User": AUTH.CLIENT_RUNTIME_USER,
        },
        "State": {"Running": True, "Pid": 31002},
        "HostConfig": {
            "CpuPeriod": AUTH.CLIENT_ENVELOPE["cpu_period_us"],
            "CpuQuota": AUTH.CLIENT_ENVELOPE["cpu_quota_us"],
            "CpusetCpus": AUTH.CLIENT_ENVELOPE["cpuset_cpus"],
            "CpusetMems": AUTH.CLIENT_ENVELOPE["cpuset_mems"],
            "Memory": AUTH.CLIENT_ENVELOPE["memory_bytes"],
            "MemorySwap": AUTH.CLIENT_ENVELOPE["memory_swap_bytes"],
            "PidsLimit": AUTH.CLIENT_ENVELOPE["pids_max"],
            "NetworkMode": "host",
            "ReadonlyRootfs": True,
            "LogConfig": {"Type": "none", "Config": {}},
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {
                "/measurement": (
                    "nodev,noexec,rw,size=1073741824,nosuid,uid=1008,gid=1008,mode=0700"
                )
            },
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": source,
                "Destination": destination,
                "RW": False,
            }
            for source, destination in mounts
        ],
    }


def bootstrap_evidence(spec: dict) -> dict:
    database = db_inspect(spec)
    client = client_inspect(spec)
    live = {
        "schema_version": "mysql_algorithm_c_distributed_live_readback_v1",
        "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
        "bundle_id": spec["bundle"]["bundle_id"],
        "scenario": AUTH.SCENARIO,
        "container": {
            "container_id": database["Id"],
            "pid": database["State"]["Pid"],
        },
        "accepted_round_authorized": False,
    }
    return {
        "schema_version": "mysql_job_distributed_v2_bootstrap_evidence_v1",
        "status": "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": AUTH.SCENARIO,
        "host_id": AUTH.HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "contract_sha256": AUTH.CONTRACT_SHA256,
        "reconciliation_sha256": AUTH.RECONCILIATION_SHA256,
        "spec_sha256": AUTH.sha_bytes(AUTH.canonical(spec)),
        "copy": {
            "command": "userspace_read_write_plus_fiemap_nonsharing_v1",
            "overlay": False,
            "reflink": False,
            "full_physical_copy": True,
            "atomic_rename_complete": True,
            "source": AUTH.TEMPLATE,
            "destination": spec["paths"]["datadir"],
            "device_major_minor": AUTH.MAJOR_MINOR,
            "source_tree_sha256": "5" * 64,
            "destination_tree_sha256": "5" * 64,
            "file_count": 173,
            "byte_count": 10_509_725_696,
            "tree_identity_normalization": AUTH.TREE_IDENTITY_NORMALIZATION,
            "source_root_inode": 100,
            "destination_root_inode": 200,
            "physical_copy_evidence": {
                "schema_version": "mysql_job_physical_copy_evidence_v1",
                "status": "PASS_BYTE_COPY_AND_FIEMAP_NONSHARING",
                "implementation":
                    "userspace_read_write_no_clone_no_copy_file_range_v1",
                "copy_api": "os.read/os.write",
                "cp_used": False,
                "reflink_api_used": False,
                "copy_file_range_used": False,
                "sendfile_used": False,
                "tree_identity_normalization": AUTH.TREE_IDENTITY_NORMALIZATION,
                "target_permission_normalization": {
                    "schema_version": "mysql_job_target_datadir_permission_normalization_v1",
                    "target_only": True,
                    "frozen_image_uid": 999,
                    "frozen_image_gid": 999,
                    "directory_mode": "0700",
                    "regular_file_mode": "0600",
                    "directory_count": 12,
                    "regular_file_count": 173,
                    "socket_excluded_from_target": True,
                    "source_metadata_sha256_before": "6" * 64,
                    "source_metadata_sha256_after": "6" * 64,
                },
                "fiemap": {
                    "fiemap_extent_shared_absent": True,
                    "source_destination_physical_extent_overlap_absent": True,
                },
                "accepted_round_authorized": False,
            },
        },
        "database": {
            "container_id": database["Id"],
            "pid": database["State"]["Pid"],
            "inspect": database,
        },
        "client": {
            "container_id": client["Id"],
            "pid": client["State"]["Pid"],
            "inspect": client,
        },
        "credential_binding": {
            "user": "root",
            "placeholder_free": True,
            "password_source": "random_attempt_secret_not_logged_or_hashed",
        },
        "collect_only": {
            "sha256": AUTH.sha_bytes(AUTH.canonical(live)),
            "document": live,
        },
        "preflight": {
            "python_runtime": {
                "status": "PASS_MANIFEST_BOUND_EXACT_RUNTIME",
                "manifest_sha256": AUTH.PYTHON_MANIFEST_SHA256,
                "tree_content_sha256": AUTH.PYTHON_TREE_CONTENT_SHA256,
                "executable_path":
                    spec["python_runtime"]["executable_path"],
                "executable_sha256": AUTH.PYTHON_EXECUTABLE_SHA256,
                "exact_entry_set_verified": True,
            },
            "database_counter_bridge": {
                "status":
                    "PASS_EXACT_CLIENT_DOCKER_EXEC_SQL_STDOUT_BRIDGE",
                "kind": "exact_client_container_docker_exec_stdout_v1",
                "client_container": spec["containers"]["client"],
                "mysql_binary_in_client": AUTH.MYSQL_BINARY_IN_CLIENT,
                "host_mysql_cli_required": False,
                "docker_socket_mounted": False,
                "target_datadir_mounted": False,
                "stdout_only": True,
                "execution_uid_gid": [1008, 1008],
                "credentials_defaults_file": spec["mysql_defaults_file"],
                "credentials_readable": True,
                "measurement_writable": True,
                "accepted_round_authorized": False,
            },
            "all_required_components_present": True,
            "host_mysql_cli_required": False,
            "accepted_round_authorized": False,
        },
        "qualification_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def planner_identity(spec: dict, bootstrap: dict) -> dict:
    checked = AUTH.validate_bootstrap_evidence(bootstrap, spec)
    return {
        "container_name": checked["planner_container_name"],
        "container_id": checked["planner_container_id"],
        "pid": checked["planner_pid"],
        "datadir": checked["planner_datadir"],
        "database_inspect_config_sha256": checked[
            "database_inspect_config_sha256"
        ],
    }


def dynamic_gate(spec: dict) -> dict:
    return {
        "schema_version": (
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        ),
        "policy_version": "mysql_algorithm_c_same_host_bidirectional_interference_gate_v2",
        "cpu_contention_policy_version": "mysql_algorithm_c_cpu_contention_signal_v2",
        "cpu_contention_mode": "proc_stat_loadavg_fallback_v1",
        "memory_pressure_mode": "cgroup_memory_occupancy_fallback_v1",
        "status": "PASS",
        "scenario": AUTH.SCENARIO,
        "host_id": AUTH.HOSTNAME,
        "attempt_token": spec["attempt_token"],
        "all_tolerances_passed": True,
        "ongoing_monitor_required": True,
        "database_counter_missing_samples": 0,
        "database_counter_timeout_count": 0,
        "accepted_round_authorized": False,
    }


def fresh_r0(spec: dict, bootstrap: dict, gate: dict) -> dict:
    return {
        "schema_version": "mysql_job_distributed_v2_fresh_r0_evidence_v1",
        "status": "PASS_FRESH_R0",
        "attempt_token": spec["attempt_token"],
        "bundle_id": spec["bundle"]["bundle_id"],
        "dynamic_gate_sha256": AUTH.sha_bytes(AUTH.canonical(gate)),
        "planner_identity": planner_identity(spec, bootstrap),
        "r0_profile_gate_passed": True,
        "database_counter_gate_passed": True,
        "stable_seconds": 180,
        "sample_count": 181,
        "accepted_rounds_credit": 0,
        "accepted_round_authorized": False,
    }


def debug_round(spec: dict, bootstrap: dict) -> dict:
    fields = {
        "feature_ratio": {"cpu": 1.0},
        "predicted_resource": {"cpu": 0.4},
        "utilization": {"cpu": 0.4},
        "dScore/dU": {"cpu": 1.0},
        "T": {"cpu": 0.2},
        "confidence": 1.0,
        "g_j": 0.2,
        "eta": 0.1,
        "delta_param": 0.02,
    }
    events = [
        {
            **fields,
            "subset_id": "subset_%04d" % index,
            "candidate_score": float(16 - index),
        }
        for index in range(1, 16)
    ]
    ranking = [
        {
            "rank": index,
            "subset_id": "subset_%04d" % index,
            "candidate_score": float(16 - index),
        }
        for index in range(1, 16)
    ]
    return {
        "schema_version": (
            "mysql_job_distributed_v2_debug_round_evidence_v1"
        ),
        "status": "PASS_DEBUG_ROUND",
        "attempt_token": spec["attempt_token"],
        "planner_identity": planner_identity(spec, bootstrap),
        "completed_nonempty_subsets": 15,
        "sealed_top5_ranks": [1, 2, 3, 4, 5],
        "replayed_top5_ranks": [1, 2, 3, 4, 5],
        "fixed_top5_only": True,
        "gradient_event_schema_complete": True,
        "fingerprint_l1_l2_l3_complete": True,
        "accepted_rounds_credit": 0,
        "algorithm_formula": (
            "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
        ),
        "parameter_count": 4,
        "candidate_count": 15,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "gradient_events": events,
        "global_candidate_ranking": ranking,
    }


def test_plan_is_exact_and_never_uses_old_reflink_or_shared_lane() -> None:
    spec = frozen_spec()
    plan = AUTH.command_plan(spec)
    copy = plan["copy"]["argv"]
    assert any(
        value.endswith("/mysql_job_physical_copy_v1.py")
        for value in copy
    )
    assert "cp " not in " ".join(copy)
    assert "reflink" not in " ".join(copy)
    assert AUTH.DEVICE in " ".join(plan["database"]["argv"])
    assert AUTH.DB_ENVELOPE["cpuset_cpus"] in plan["database"]["argv"]
    assert AUTH.CLIENT_ENVELOPE["cpuset_cpus"] in plan["client"]["argv"]
    assert spec["paths"]["datadir"] not in " ".join(plan["client"]["argv"])
    assert (
        "type=bind,src=%s/qualification/client_mysql_datadir_mask,"
        "dst=/var/lib/mysql,readonly" % spec["bundle"]["path"]
    ) in plan["client"]["argv"]
    assert "--read-only" in plan["client"]["argv"]
    assert "--cap-drop" in plan["client"]["argv"]
    assert plan["collect_only"]["argv"][0].endswith("/bin/python3.11")
    assert "--mysql-client-container" in plan["collect_only"]["argv"]
    assert "--mysql-binary" not in plan["collect_only"]["argv"]
    assert "postgres" not in " ".join(
        str(value) for value in plan.values()
    ).lower()


def test_spec_rejects_path_and_client_envelope_override() -> None:
    spec = frozen_spec()
    spec["paths"]["datadir"] = AUTH.RUNTIME_ROOT + "/wrong"
    with pytest.raises(AUTH.AuthorityError, match="path identity"):
        AUTH.command_plan(spec)
    spec = frozen_spec()
    spec["containers"]["client"] = "ra_mysql_job_v2_wrong_client"
    with pytest.raises(AUTH.AuthorityError, match="attempt-derived"):
        AUTH.command_plan(spec)
    spec = frozen_spec()
    spec["python_runtime"]["executable_path"] = (
        spec["paths"]["python_runtime_root"] + "/bin/python"
    )
    with pytest.raises(AUTH.AuthorityError, match="tool or main-agent"):
        AUTH.command_plan(spec)


def test_bootstrap_evidence_binds_physical_copy_db_and_client() -> None:
    spec = frozen_spec()
    evidence = bootstrap_evidence(spec)
    result = AUTH.validate_bootstrap_evidence(evidence, spec)
    assert result["planner_container_id"] == "3" * 64
    assert result["client_container_id"] == "4" * 64
    tampered = copy.deepcopy(evidence)
    tampered["database"]["inspect"]["Mounts"][0]["Source"] = "/wrong"
    with pytest.raises(AUTH.AuthorityError, match="database container"):
        AUTH.validate_bootstrap_evidence(tampered, spec)
    tampered = copy.deepcopy(evidence)
    tampered["client"]["inspect"]["Mounts"].append(
        {
            "Type": "bind",
            "Source": spec["paths"]["datadir"],
            "Destination": "/forbidden",
            "RW": False,
        }
    )
    with pytest.raises(AUTH.AuthorityError, match="client container"):
        AUTH.validate_bootstrap_evidence(tampered, spec)
    tampered = copy.deepcopy(evidence)
    tampered["preflight"]["python_runtime"][
        "exact_entry_set_verified"
    ] = False
    with pytest.raises(AUTH.AuthorityError, match="preflight"):
        AUTH.validate_bootstrap_evidence(tampered, spec)
    tampered = copy.deepcopy(evidence)
    tampered["copy"]["physical_copy_evidence"]["fiemap"][
        "fiemap_extent_shared_absent"
    ] = False
    with pytest.raises(AUTH.AuthorityError, match="physical copy"):
        AUTH.validate_bootstrap_evidence(tampered, spec)
    tampered = copy.deepcopy(evidence)
    tampered["copy"]["physical_copy_evidence"][
        "target_permission_normalization"
    ]["frozen_image_uid"] = 1000
    with pytest.raises(AUTH.AuthorityError, match="physical copy"):
        AUTH.validate_bootstrap_evidence(tampered, spec)
    tampered = copy.deepcopy(evidence)
    tampered["copy"]["tree_identity_normalization"]["other_symlinks_rejected"] = False
    with pytest.raises(AUTH.AuthorityError, match="physical copy"):
        AUTH.validate_bootstrap_evidence(tampered, spec)


@pytest.mark.parametrize("source_tree", [True, False])
def test_tree_digest_binds_the_exact_normalized_socket_domain(
    monkeypatch: pytest.MonkeyPatch, source_tree: bool
) -> None:
    calls = []

    def fake_run(argv, timeout=300, check=True):
        calls.append(argv)
        return __import__("subprocess").CompletedProcess(
            argv, 0, "a" * 64 + " 17 29\n", ""
        )

    monkeypatch.setattr(AUTH, "_run", fake_run)
    digest = AUTH._tree_digest("sha256:" + "1" * 64, "/tree", source_tree)
    script = calls[0][-1]
    argv = calls[0]
    assert "--exclude=./mysql.sock" in script
    assert "--mode=0600" in script
    assert "other" not in script
    assert digest["tree_identity_normalization"] == AUTH.TREE_IDENTITY_NORMALIZATION
    assert digest["tree_identity_normalization"][
        "path_type_size_sha256_content_identity"
    ] is True
    assert argv[argv.index("--user") + 1] == "0:0"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--cap-add") + 1] == "DAC_READ_SEARCH"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    if source_tree:
        assert "readlink mysql.sock" in script
        assert "/var/run/mysqld/mysqld.sock" in script
        assert "-type l ! -path ./mysql.sock" in script
    else:
        assert "test ! -e mysql.sock" in script
        assert "-type l -print -quit" in script


def test_copy_failure_evidence_denies_all_launch_credit() -> None:
    spec = frozen_spec()
    evidence = AUTH._copy_failure_evidence(
        spec, AUTH.AuthorityError("identity differs"), None, None, "{}"
    )
    assert evidence["status"] == "FAIL_PHYSICAL_COPY_IDENTITY_BEFORE_DB_CLIENT"
    assert evidence["database_or_client_started"] is False
    assert evidence["accepted_round_authorized"] is False


def test_formal10_handoff_keeps_live_planner_and_denies_old_top5() -> None:
    spec = frozen_spec()
    bootstrap = bootstrap_evidence(spec)
    gate = dynamic_gate(spec)
    r0 = fresh_r0(spec, bootstrap, gate)
    debug = debug_round(spec, bootstrap)
    authority = AUTH.build_handoff_authority(
        spec, bootstrap, gate, r0, debug, formal10=True
    )
    assert authority["status"] == "READY_FORMAL10_SAME_LIVE_PLANNER"
    assert authority["new_campaign_or_planner_container_prohibited"] is True
    assert authority["old_top5_rerun_authorized"] is False
    assert len(authority["old_top5_attempt_tokens_denylist"]) == 5
    assert authority["historical_accepted_round_credit"] == 0
    assert authority["target_accepted_rounds"] == 10
    assert authority["next_accepted_round"] == 1
    assert authority["outer_acceptance_authorized"] is True
    assert authority["accepted_round_authorized"] is False


def test_fresh_r0_launch_adopts_qualified_planner_without_outer_credit() -> None:
    spec = frozen_spec()
    bootstrap = bootstrap_evidence(spec)
    authority = AUTH.build_fresh_r0_launch_authority(
        spec, bootstrap, dynamic_gate(spec)
    )
    assert authority["status"] == "READY_FRESH_R0_SAME_LIVE_PLANNER"
    assert authority["fresh_r0_authorized"] is True
    assert authority["formal10_authorized"] is False
    assert authority["new_campaign_or_planner_container_prohibited"] is True
    assert authority["old_top5_rerun_authorized"] is False
    assert authority["historical_accepted_round_credit"] == 0
    assert authority["accepted_round_authorized"] is False


def test_handoff_rejects_new_planner_and_incomplete_gradient_event() -> None:
    spec = frozen_spec()
    bootstrap = bootstrap_evidence(spec)
    gate = dynamic_gate(spec)
    r0 = fresh_r0(spec, bootstrap, gate)
    r0["planner_identity"]["container_id"] = "9" * 64
    with pytest.raises(AUTH.AuthorityError, match="changed the live planner"):
        AUTH.build_handoff_authority(spec, bootstrap, gate, r0)
    r0 = fresh_r0(spec, bootstrap, gate)
    debug = debug_round(spec, bootstrap)
    del debug["gradient_events"][0]["T"]
    with pytest.raises(AUTH.AuthorityError, match="debug round"):
        AUTH.build_handoff_authority(
            spec, bootstrap, gate, r0, debug, formal10=True
        )


def test_source_contains_no_old_top5_rerun_escape_hatch() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "old_top5_rerun_authorized\": False" in source
    assert "resume_old_outer_checkpoint\": False" in source
    assert "RECOVERY_AUTHORITY_RECONCILED_NO_RERUN" in source
