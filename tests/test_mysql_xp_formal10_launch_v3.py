from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(relative: str, name: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


builder = load_module(
    "shell/build_mysql_xp_formal10_launch_v3.py", "xp_launch_v3_builder"
)
service = load_module(
    "shell/mysql_xp_node02_adopted_service_v1.py", "xp_node02_service"
)
materializer = load_module(
    "shell/mysql_xp_node02_formal10_materializer_v1.py",
    "xp_formal10_materializer",
)
spec_builder = load_module(
    "shell/mysql_xp_v3_bootstrap_spec.py", "xp_v3_bootstrap_spec"
)
qualification_authority = load_module(
    "shell/mysql_xp_qualification_authority_v1.py",
    "xp_qualification_authority",
)
qualification_collector = load_module(
    "shell/mysql_distributed_live_readback_qualification_v1.py",
    "xp_qualification_collector",
)
formal_evidence_producer = load_module(
    "shell/mysql_xp_formal_evidence_producer_v1.py",
    "xp_formal_evidence_producer",
)
raw_evidence_executor = load_module(
    "shell/mysql_xp_raw_evidence_executor_v1.py", "xp_raw_evidence_executor"
)
capture_transition = load_module(
    "shell/mysql_xp_capture_transition_v1.py", "xp_capture_transition"
)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    output = tmp_path_factory.mktemp("xp-launch-v3")
    first = builder.publish(builder.BASE, output)
    second = builder.publish(builder.BASE, output)
    assert first == second
    return first


def test_bundle_is_atomic_content_addressed_frozen_and_idempotent(bundle):
    manifest = builder.verify_bundle(bundle)
    assert manifest["schema_version"] == "mysql_xp_distributed_formal10_launch_v3"
    assert manifest["supersedes_bundle_id"] == builder.SUPERSEDED_ID
    assert manifest["collector_sha256"] == hashlib.sha256(
        (
            bundle
            / "qualification/mysql_distributed_live_readback_qualification_v1.py"
        ).read_bytes()
    ).hexdigest()
    assert manifest["collector_base_sha256"] == builder.BASE_COLLECTOR_SHA256
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    for path in [bundle, *bundle.rglob("*")]:
        assert path.is_symlink() is False
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


def test_exact_algorithm_and_resource_contract(bundle):
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    algorithm = contract["algorithm"]
    assert algorithm["active_parameter_count"] == 7
    assert algorithm["required_nonempty_subsets"] == 127
    assert algorithm["fixed_real_replay_top_k"] == 5
    assert algorithm["target_accepted_rounds"] == 10
    assert algorithm["original_gradient_only"] is True
    assert algorithm["gradient_formula"] == (
        "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
    )
    assert algorithm["parameter_update_source"] == "g_j_and_eta_only"
    db = contract["database_resource_envelope"]
    assert db["cpu_quota_cores"] == 5.3
    assert db["io_read_bps"] == 29360128
    assert db["io_write_bps"] == 524288
    client = contract["client_resource_envelope"]
    assert client["cpuset_cpus"] == "3-11,39-47"
    assert client["cpuset_mems"] == "0"
    assert client["tmpfs_bytes"] == 2147483648
    capture = contract["capture_only_resource_envelope"]
    assert capture["io_read_bps"] == 1205862400
    assert capture["io_write_bps"] == 1205862400
    assert capture["capture_is_r0"] is False
    assert capture["accepted_rounds_credit"] == 0
    capture_authority = contract["capture_authority"]
    assert capture_authority["runtime_authority_schema"] == (
        "xp_runtime_authority_bundle_v1"
    )
    assert capture_authority["capture_authorization_schema"] == (
        "mysql_xp_capture_launch_authorization_v1"
    )
    assert capture_authority["capture_r0_formal_accepted_authority"] is False
    assert (bundle / capture_authority["materializer"]).is_file()
    gate = contract["bidirectional_qualification_gate"]
    assert gate["collector_mode"] == "--compare-bidirectional"
    assert gate["peer_selected_at_launch"] is True
    assert gate["reciprocal_peer_binding_required"] is True
    assert gate["no_preselected_postgresql_peer"] is True
    assert gate["materializer_stage"] == "gate"
    executor = contract["raw_evidence_executor"]
    assert executor["actual_sealed_java_workload_required"] is True
    assert executor["capture_ledger_requires_adopted_client_cgroup_identity"] is True
    assert executor["fresh_r0_requires_180_real_pid_starttime_counter_samples"] is True
    assert executor["counter_missing_or_timeout_result"] == "FAIL"
    assert executor["sealed_trace_jar"] == raw_evidence_executor.JAR
    assert (bundle / executor["path"]).is_file()
    transition = contract["capture_transition"]
    assert transition["database_blkio_parent_major_minor"] == "259:1"
    assert transition["capture_io_bps_per_direction"] == 1205862400
    assert transition["formal_io_read_bps"] == 29360128
    assert transition["formal_io_write_bps"] == 524288
    assert transition["transactional_readback_and_restore_required"] is True
    assert transition["capture_workload_must_run_in_adopted_client_cgroup"] is True
    assert (bundle / transition["path"]).is_file()
    assert "os.fsync(descriptor)" not in (
        bundle / transition["path"]
    ).read_text(encoding="utf-8")
    invalidated = json.loads(
        (bundle / "invalidated_missing_raw_executor_bundle.json").read_text()
    )
    assert invalidated["bundle_id"] == builder.INVALIDATED_RAW_EXECUTOR_MISSING_BUNDLE_ID
    assert invalidated["accepted_rounds_credit"] == 0
    invalidated_transition = json.loads(
        (bundle / "invalidated_unbound_capture_transition_bundle.json").read_text()
    )
    assert invalidated_transition["bundle_id"] == (
        builder.INVALIDATED_UNBOUND_CAPTURE_TRANSITION_BUNDLE_ID
    )
    assert invalidated_transition["accepted_rounds_credit"] == 0


def test_b67_is_invalidated_and_qualification_config_is_manifest_bound(bundle):
    manifest = builder.verify_bundle(bundle)
    assert builder.INVALIDATED_QUALIFICATION_CONFIG_BUNDLE_ID in (
        manifest["invalid_development_bundle_ids"]
    )
    invalidation = json.loads(
        (bundle / "invalidated_qualification_config_bundle.json").read_text()
    )
    assert invalidation["bundle_id"] == (
        builder.INVALIDATED_QUALIFICATION_CONFIG_BUNDLE_ID
    )
    assert invalidation["observed_same_live_service"] == {
        "database_pid": 64503,
        "client_pid": 64645,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
    }
    config = bundle / "qualification/config"
    assert {path.name for path in config.iterdir()} == {
        "hybench_xp_qualification.properties",
        "mysql-client.cnf",
        "stmt_mysql_formal_case_normalized.toml",
    }
    properties = (config / "hybench_xp_qualification.properties").read_text()
    for exact in (
        "url=jdbc:mysql://127.0.0.1:65104/hybench_sf10x?",
        "xapclient=8\n",
        "xtpclient=8\n",
        "formal_warmup_seconds=60\n",
        "formal_maximum_measurement_seconds=600\n",
        "formal_graceful_quiescence_seconds=120\n",
        "formal_stop_file=/measurement/hybench-stop-request\n",
    ):
        assert exact in properties
    runtime = json.loads(
        (bundle / "distributed_runtime_contract.json").read_text()
    )
    assert runtime["resource_envelope"]["cpu_quota_cores"] == 5.3
    assert runtime["resource_envelope"]["io_read_bps"] == 29360128
    assert runtime["resource_envelope"]["io_write_bps"] == 524288
    assert runtime["database_counter_transport"]["schema_version"] == (
        "mysql_algorithm_c_database_counter_transport_v2"
    )


def test_same_live_qualification_command_can_be_materialized_without_recreate(
    bundle, tmp_path, monkeypatch
):
    runtime_root = tmp_path / "jdk"
    java = runtime_root / "bin/java"
    java.parent.mkdir(parents=True)
    java.write_bytes(b"sealed-java")
    java.chmod(0o555)
    host_tmpfs = tmp_path / "host-tmpfs"
    host_tmpfs.mkdir()
    adoption_path = tmp_path / "adopted_service.json"
    adoption_path.write_text("{}\n", encoding="utf-8")
    adoption = {
        "client": {
            "name": "xp-client",
            "container_id": "a" * 64,
            "image_id": "sha256:" + ("b" * 64),
            "created": "2026-07-31T00:00:00Z",
            "inspect_config_sha256": "c" * 64,
        },
        "host_output_tmpfs": {"path": str(host_tmpfs)},
        "runtime_publication": {
            "live_tree_readback": {"root": str(runtime_root)},
            "live_java_binary_sha256":
                qualification_authority.sha256(java),
        },
    }
    monkeypatch.setattr(
        qualification_authority,
        "verify_adoption",
        lambda unused_path, unused_bundle_id: adoption,
    )
    token = tmp_path / "token"
    token.write_text("main-agent-" + ("x" * 40), encoding="utf-8")
    token.chmod(0o600)
    result = qualification_authority.materialize(
        bundle,
        adoption_path,
        "qualification_solo",
        "xp-solo-" + ("1" * 40),
        token,
        host_tmpfs / "solo",
    )
    command_path = Path(result["command_spec"])
    authority_path = Path(result["main_agent_authority"])
    runtime = json.loads(
        (bundle / "distributed_runtime_contract.json").read_text()
    )
    command = qualification_collector.validate_command_spec(
        command_path,
        bundle.name,
        "qualification_solo",
        runtime["client_resource_envelope"],
    )
    assert command["argv"][5:] == [
        "/opt/mysql-xp-config/hybench_xp_qualification.properties",
        "/opt/mysql-xp-config/stmt_mysql_formal_case_normalized.toml",
    ]
    assert command["client_container"]["container_id"] == "a" * 64
    assert qualification_collector.validate_main_agent_authority(
        authority_path,
        token.read_text(encoding="utf-8").strip(),
        bundle.name,
        "qualification_solo",
        command_path,
    )["accepted_round_authorized"] is False


def test_paired_authority_allows_not_yet_created_peer_attempt_leaf(tmp_path):
    peer_attempt = "job-paired-" + ("2" * 40)
    peer_tmpfs = tmp_path / "peer-host-tmpfs"
    peer_tmpfs.mkdir()
    target = peer_tmpfs / peer_attempt / "workload_metrics.jsonl"
    assert not target.parent.exists()
    assert qualification_authority.validate_peer_metric_target(
        target, peer_attempt
    ) == target
    with pytest.raises(
        qualification_authority.AuthorityError,
        match="paired peer metric path differs",
    ):
        qualification_authority.validate_peer_metric_target(
            peer_tmpfs / peer_attempt / "wrong.jsonl", peer_attempt
        )


def test_a8f5_paired_authority_deadlock_bundle_is_invalidated(bundle):
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    invalidated = (
        "a8f508d566b934e226c1a896ab85b917e9c0058162939dd6fb68b942f80b42b5"
    )
    assert invalidated in manifest["invalid_development_bundle_ids"]
    evidence = json.loads(
        (bundle / "invalidated_paired_authority_bundle.json").read_text()
    )
    assert evidence["bundle_id"] == invalidated
    assert evidence["formal_launch_authorized"] is False
    assert evidence["accepted_rounds_credit"] == 0


def test_xp_gate_binding_normalizes_generic_bidirectional_gate():
    raw = {
        "schema_version":
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "status": "PASS",
        "scenario": "hybench_xp_io",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "duration_seconds": 180,
        "database_counter_timeout_count": 0,
        "both_directions_same_version_verified": True,
        "authorization_reuse_verified_absent": True,
        "ongoing_rolling_window_required": True,
        "serial_fallback_required": False,
        "collector_sha256": "c" * 64,
        "streams_complete": ["database_counters", "device_io"],
        "required_streams": ["database_counters", "device_io"],
        "directions": {
            "A_solo_vs_A_while_B_runs": {
                "passed": True,
                "foreign_device_io_attribution_bytes": 0,
            },
            "B_solo_vs_B_while_A_runs": {
                "passed": True,
                "foreign_device_io_attribution_bytes": 0,
            },
        },
    }
    identity = {"database_container_id": "d" * 64}
    result = materializer.normalize_dynamic_gate(
        raw, "b" * 64, identity, "a" * 64, "c" * 64, "e" * 64
    )
    assert result["bundle_id"] == "b" * 64
    assert result["same_live_identity"] == identity
    assert result["bidirectional"] is True
    assert result["duration_seconds_each_direction"] == 180
    assert result["same_collector_version"] is True
    assert result["all_required_streams_complete"] is True
    assert result["foreign_device_io_attribution_bytes"] == 0
    assert result["rolling_window_gate_passed"] is True
    assert result["accepted_rounds_credit"] == 0


def test_c063_unbound_gate_bundle_is_invalidated(bundle):
    invalidated = (
        "c063606102e1087d0f12c69edb5c7b90055642ee0950a83fa7eb4e9d55ca123f"
    )
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    assert invalidated in manifest["invalid_development_bundle_ids"]
    evidence = json.loads(
        (bundle / "invalidated_gate_binding_bundle.json").read_text()
    )
    assert evidence["bundle_id"] == invalidated
    assert evidence["formal_launch_authorized"] is False
    assert evidence["accepted_rounds_credit"] == 0


def _producer_live_readback(bundle_id, identity):
    return {
        "schema_version": "mysql_algorithm_c_distributed_live_readback_v1",
        "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
        "bundle_id": bundle_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_envelope": dict(formal_evidence_producer.FORMAL_ENVELOPE),
        "container": {
            "container_id": identity["database_container_id"],
            "pid": identity["mysql_pid"],
        },
        "database_counter_transport": {
            "client_container_id": identity["client_container_id"],
            "client_pid": identity["client_init_pid"],
            "host_mysql_cli_required": False,
        },
        "database_counters": {"Questions": 1},
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "_evidence_sha256": "e" * 64,
    }


def _producer_identity():
    return {
        "database_container_id": "d" * 64,
        "mysql_pid": 101,
        "mysql_starttime_ticks": 1001,
        "physical_datadir": "/data/xp",
        "client_container_id": "c" * 64,
        "client_init_pid": 102,
        "client_init_starttime_ticks": 1002,
        "attempt_id": "xp-v3-" + ("a" * 40),
        "handoff_token_sha256": "f" * 64,
    }


def test_formal_producer_rechecks_live_adopted_pid_starttimes(tmp_path):
    identity = _producer_identity()
    adoption = {
        "database": {
            "container_id": identity["database_container_id"],
            "pid": identity["mysql_pid"],
            "pid_starttime_ticks": identity["mysql_starttime_ticks"],
        },
        "client": {
            "container_id": identity["client_container_id"],
            "pid": identity["client_init_pid"],
            "pid_starttime_ticks": identity["client_init_starttime_ticks"],
        },
        "physical_datadir": identity["physical_datadir"],
        "attempt_id": identity["attempt_id"],
        "handoff_token_sha256": identity["handoff_token_sha256"],
    }
    for pid, ticks in (
        (identity["mysql_pid"], identity["mysql_starttime_ticks"]),
        (
            identity["client_init_pid"],
            identity["client_init_starttime_ticks"],
        ),
    ):
        directory = tmp_path / str(pid)
        directory.mkdir()
        fields = ["S"] + (["0"] * 18) + [str(ticks)]
        (directory / "stat").write_text(
            "%d (process with spaces) %s\n" % (pid, " ".join(fields)),
            encoding="ascii",
        )
    assert formal_evidence_producer.verify_live_adopted_identity(
        adoption, tmp_path
    ) == identity
    (tmp_path / str(identity["mysql_pid"]) / "stat").write_text(
        "%d (mysql) %s\n"
        % (
            identity["mysql_pid"],
            " ".join(["S"] + (["0"] * 18) + ["9999"]),
        ),
        encoding="ascii",
    )
    with pytest.raises(
        formal_evidence_producer.ProducerError,
        match="database adopted PID/starttime identity changed",
    ):
        formal_evidence_producer.verify_live_adopted_identity(
            adoption, tmp_path
        )


def test_formal_capture_producer_requires_at_workers_and_restored_envelope(
    tmp_path,
):
    identity = _producer_identity()
    manifest = {"bundle_id": "b" * 64, "collector_sha256": "c" * 64}
    trace = tmp_path / "trace.jsonl"
    trace.write_text("sealed trace\n", encoding="utf-8")
    live = _producer_live_readback(manifest["bundle_id"], identity)
    events = []
    for name in sorted(formal_evidence_producer.REQUIRED_AT):
        events.extend([
            {
                "event_type": "statement",
                "outcome": "success",
                "transaction_type": name,
            },
            {
                "event_type": "commit",
                "outcome": "success",
                "transaction_type": name,
            },
        ])
    validation = {
        "capture_authorized": True,
        "execution_authorized": False,
        "reasons": [],
        "events": events,
    }
    workers = [
        {
            "worker": name,
            "get_called": True,
            "status": "SUCCESS",
            "exception": None,
            "cancelled": False,
        }
        for name in sorted(formal_evidence_producer.REQUIRED_WORKERS)
    ]
    raw = {
        "schema_version": "mysql_xp_same_live_capture_raw_v1",
        "status": "PASS_CAPTURE_COMPLETE",
        "bundle_id": manifest["bundle_id"],
        "adopted_service_sha256": "a" * 64,
        "same_live_identity": identity,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "capture_only": True,
        "capture_is_r0": False,
        "accepted_rounds_credit": 0,
        "qps_resource_input_present": False,
        "real_production_trace": True,
        "trace_path": str(trace),
        "trace_sha256": formal_evidence_producer.sha256(trace),
        "capture_resource_envelope":
            dict(formal_evidence_producer.CAPTURE_IO),
        "formal_envelope_restored": True,
        "formal_envelope_readback_sha256": "e" * 64,
        "xapclient_independent_check_passed": True,
        "worker_future_count": 16,
        "worker_future_get_count": 16,
        "worker_failures": 0,
        "worker_futures": workers,
    }
    result = formal_evidence_producer.validate_capture_raw(
        raw, manifest, {}, identity, trace, validation, live, "a" * 64
    )
    assert result["schema_version"] == "mysql_xp_production_at_capture_v3"
    assert result["coverage"] == {
        "AT1": True, "AT2": True, "AT3": True,
        "AT4": True, "AT5": True, "AT6": True,
    }
    assert result["capture_is_r0"] is False
    assert result["accepted_rounds_credit"] == 0
    raw["worker_futures"][0]["exception"] = "boom"
    with pytest.raises(
        formal_evidence_producer.ProducerError,
        match="production capture evidence differs",
    ):
        formal_evidence_producer.validate_capture_raw(
            raw, manifest, {}, identity, trace, validation, live, "a" * 64
        )


def test_executor_ledger_is_the_only_capture_raw_future_source(tmp_path):
    identity = _producer_identity()
    manifest = {"bundle_id": "b" * 64}
    trace = tmp_path / "trace.jsonl"
    trace.write_text("trace\n", encoding="utf-8")
    workers = [{"worker": name, "get_called": True, "status": "SUCCESS",
                "exception": None, "cancelled": False}
               for name in sorted(formal_evidence_producer.REQUIRED_WORKERS)]
    ledger = {
        "schema_version": "mysql_xp_raw_executor_run_v1", "status": "PASS_RAW_RUN_ONLY",
        "mode": "capture", "bundle_id": manifest["bundle_id"], "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "trace_path": str(trace),
        "trace_sha256": formal_evidence_producer.sha256(trace), "trace_manifest_sha256": "m" * 64,
        "formal_readback_sha256": "f" * 64, "capture_readback_sha256": "c" * 64,
        "capture_client_cgroup_execution": True,
        "capture_client_container": identity["client_container_id"],
        "capture_evidence": {"future_exception_fail_closed": True,
            "future_get_source_sha256": "s" * 64,
            "at_commit_coverage": {"AT%d" % i: True for i in range(1, 7)},
            "worker_futures": workers},
    }
    raw = formal_evidence_producer.capture_raw_from_executor(
        ledger, manifest, "a" * 64, identity, trace, "f" * 64
    )
    assert raw["worker_future_get_count"] == 16
    ledger["capture_client_cgroup_execution"] = False
    with pytest.raises(formal_evidence_producer.ProducerError, match="executor capture ledger differs"):
        formal_evidence_producer.capture_raw_from_executor(
            ledger, manifest, "a" * 64, identity, trace, "f" * 64
        )
    ledger["capture_client_cgroup_execution"] = True
    ledger["capture_evidence"]["worker_futures"][0]["exception"] = "boom"
    raw = formal_evidence_producer.capture_raw_from_executor(
        ledger, manifest, "a" * 64, identity, trace, "f" * 64
    )
    with pytest.raises(formal_evidence_producer.ProducerError):
        formal_evidence_producer.validate_capture_raw(
            raw, manifest, {}, identity, trace,
            {"capture_authorized": True, "execution_authorized": False,
             "reasons": [], "events": []},
            _producer_live_readback(manifest["bundle_id"], identity), "a" * 64,
        )


def test_fresh_r0_producer_recomputes_180_samples_and_fails_on_missing_counter():
    identity = _producer_identity()
    manifest = {"bundle_id": "b" * 64, "collector_sha256": "c" * 64}
    live = _producer_live_readback(manifest["bundle_id"], identity)
    trace = {
        "schema_version": "mysql_xp_production_trace_registry_v3",
        "status": "SEALED_REAL_TRACE",
        "bundle_id": manifest["bundle_id"],
        "same_live_identity": identity,
        "formal_r0_authorized": True,
        "_evidence_sha256": "1" * 64,
    }
    gate = {
        "schema_version":
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "status": "PASS",
        "bundle_id": manifest["bundle_id"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "same_live_identity": identity,
        "bidirectional": True,
        "duration_seconds_each_direction": 180,
        "same_collector_version": True,
        "all_required_streams_complete": True,
        "database_counter_timeout_count": 0,
        "foreign_device_io_attribution_bytes": 0,
        "rolling_window_gate_passed": True,
        "_evidence_sha256": "2" * 64,
    }
    fingerprint = {
        "parameter_signature": "baseline",
        "l1": {"operator_types": ["scan"]},
        "l2": {"workers": 1},
        "l3": {
            name: 0 for name in materializer.L3_FIELDS
        },
    }
    samples = [
        {
            "sequence": index,
            "elapsed_seconds": float(index),
            "stable": True,
            "workload_pid": 201,
            "workload_pid_starttime_ticks": 2001,
            "database_counter_timeout": False,
            "database_counter_missing": False,
            "database_counters": {"Questions": index + 1},
            "utilization": {"cpu": 0.4, "memory": 0.5, "io": 0.9},
        }
        for index in range(180)
    ]
    raw = {
        "schema_version": "mysql_xp_fresh_r0_raw_samples_v1",
        "status": "PASS_MEASUREMENT_COMPLETE",
        "bundle_id": manifest["bundle_id"],
        "adopted_service_sha256": "a" * 64,
        "same_live_identity": identity,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_envelope_readback_sha256": "e" * 64,
        "trace_registry_sha256": "1" * 64,
        "dynamic_gate_sha256": "2" * 64,
        "collector_sha256": manifest["collector_sha256"],
        "collector_command_kind":
            "exact_client_container_docker_exec_stdout_v1",
        "host_mysql_binary_used": False,
        "capture_result": False,
        "failed_replay": False,
        "accepted_rounds_credit": 0,
        "measurement_seconds": 180,
        "graceful_quiescence_seconds": 120,
        "correctness_passed": True,
        "stability_passed": True,
        "cleanup_complete": True,
        "workload_process_absent_after_quiescence": True,
        "workload_pid": 201,
        "workload_pid_starttime_ticks": 2001,
        "samples": samples,
        "fingerprint": fingerprint,
    }
    result = formal_evidence_producer.validate_r0_raw(
        raw, manifest, {}, identity, trace, gate, live, "a" * 64
    )
    assert result["schema_version"] == "mysql_xp_fresh_r0_qualification_v3"
    assert result["stable_sample_count"] == 180
    assert result["database_counters"]["timeout_count"] == 0
    assert result["resource_result"] == {
        "cpu": pytest.approx(0.4),
        "memory": pytest.approx(0.5),
        "io": pytest.approx(0.9),
    }
    raw["samples"][10]["database_counter_missing"] = True
    with pytest.raises(
        formal_evidence_producer.ProducerError,
        match="sample/cadence/counter",
    ):
        formal_evidence_producer.validate_r0_raw(
            raw, manifest, {}, identity, trace, gate, live, "a" * 64
        )


def test_243f_missing_producer_bundle_is_invalidated(bundle):
    invalidated = (
        "243f4c6232773952ee3bcf18a9547193b04a20c1775281f0b8c0c664954c968c"
    )
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    assert invalidated in manifest["invalid_development_bundle_ids"]
    evidence = json.loads(
        (bundle / "invalidated_missing_formal_producer_bundle.json").read_text()
    )
    assert evidence["bundle_id"] == invalidated
    assert evidence["formal_launch_authorized"] is False
    assert evidence["accepted_rounds_credit"] == 0


def test_node02_copy_is_coreutils_822_and_fiemap_fail_closed(bundle):
    source = (
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    assert '"cp", "-a", "--"' in source
    assert "--reflink=never" not in source
    assert "FIEMAP_EXTENT_SHARED" in source
    assert "FIEMAP_UNTRUSTED" in source
    assert "source/target physical extent overlap" in source
    assert "tree_identity_sha256" in source
    assert "regular_file_count" in source
    assert "total_regular_file_bytes" in source
    assert "attempt_owned_datadir_only" in source
    assert "source_metadata_unchanged" in source
    assert "require_pristine_target=True" in source


def test_client_private_tmpfs_and_strict_stdout_sql_bridge(bundle):
    source = (
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    assert '"--read-only"' in source
    assert '"--tmpfs"' in source
    assert "rw,nosuid,nodev,noexec,size=%d" in source
    assert '"docker", "exec", client_name, "mysql"' in source
    assert "/usr/bin/mysql" not in source
    assert "client_private_tmpfs_not_host_bound" in source
    assert "host output tmpfs must not be client-mounted" in source


def test_exact_local_image_id_not_fake_registry_digest(bundle):
    exact = (
        "sha256:"
        "b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
    )
    assert service._safe_image(exact, "database") == exact
    with pytest.raises(service.ServiceError, match="exact local image ID"):
        service._safe_image(
            "local-image@sha256:"
            "b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172",
            "database",
        )
    source = (
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    assert 'row.get("Image") != expected_image' in source
    invalidated = json.loads(
        (bundle / "invalidated_image_reference_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "7d1fa34305637124dcbb4d1f97a91d77ee6f9810a5aa4ae229a8433de2b4df0f"
    )


def test_database_bind_mount_is_legacy_docker_compatible_and_rw_read_back(
    bundle, monkeypatch
):
    attempt_id = "xp-v3-legacy-docker-mount-test"
    attempt_root = Path(service.RUNTIME_ROOT) / attempt_id
    monkeypatch.setattr(
        service, "verify_bundle",
        lambda unused: {"bundle_id": bundle.name},
    )
    monkeypatch.setattr(
        service, "verify_runtime_publication",
        lambda unused_bundle, unused_publication: {},
    )
    monkeypatch.setattr(
        service, "verify_attempt",
        lambda unused_attempt, unused_token: "a" * 64,
    )
    monkeypatch.setattr(
        service, "verify_short_lived_authority",
        lambda unused_created, unused_expires: {
            "expires_at_utc": unused_expires,
        },
    )
    image = "sha256:" + "b" * 64
    config = bundle / "qualification/config"
    plan = service.build_plan(
        bundle, image, image, config, attempt_root / "datadir",
        attempt_root / "host_measurement_tmpfs", "/runtime/publication.json",
        attempt_id, attempt_id, "c" * 64,
        "2026-07-31T00:00:00+00:00", "2026-07-31T00:20:00+00:00",
    )
    create = plan["database"]["create_argv"]
    mount_spec = create[create.index("--mount") + 1]
    assert mount_spec == (
        "type=bind,src=%s,dst=/var/lib/mysql"
        % (attempt_root / "datadir")
    )
    assert "rw" not in mount_spec.split(",")
    assert create[create.index("--device-read-bps") + 1] == (
        "%s:%d" % (service.THROTTLE_DEVICE, service.READ_BPS)
    )
    assert create[create.index("--device-write-bps") + 1] == (
        "%s:%d" % (service.THROTTLE_DEVICE, service.WRITE_BPS)
    )
    assert plan["database"]["data_partition"] == service.DATA_PARTITION
    assert plan["database"]["throttle_device"] == service.THROTTLE_DEVICE

    labels = {
        "ra.formal_scope": "mysql_only",
        "ra.cross_engine_comparison_prohibited": "true",
        "ra.scenario": service.SCENARIO,
        "ra.bundle_id": plan["bundle_id"],
        "ra.attempt_id": plan["attempt_id"],
        "ra.handoff_token_sha256": plan["handoff_token_sha256"],
        "ra.main_agent_token_sha256": plan["main_agent_token_sha256"],
        "ra.authorization_expires_at_utc":
            plan["short_lived_authority"]["expires_at_utc"],
        "ra.role": "database",
    }
    row = {
        "Id": "container-id",
        "Image": image,
        "Created": "2026-07-31T00:00:00Z",
        "State": {"Running": True, "Pid": 222},
        "Config": {
            "Image": image,
            "Labels": labels,
            "Cmd": [
                "--datadir=/var/lib/mysql",
                "--innodb-buffer-pool-size=%d" % service.BUFFER_POOL,
            ],
        },
        "HostConfig": {
            "CpusetCpus": service.DB_CPUS,
            "CpusetMems": service.DB_MEMS,
            "CpuPeriod": service.DB_CPU_PERIOD,
            "CpuQuota": service.DB_CPU_QUOTA,
            "Memory": service.MEMORY,
            "MemorySwap": service.MEMORY,
            "PidsLimit": service.DB_PIDS,
            "RestartPolicy": {"Name": "no"},
            "BlkioDeviceReadBps": [{
                "Path": service.THROTTLE_DEVICE, "Rate": service.READ_BPS,
            }],
            "BlkioDeviceWriteBps": [{
                "Path": service.THROTTLE_DEVICE, "Rate": service.WRITE_BPS,
            }],
            "PortBindings": {"3306/tcp": [{
                "HostIp": "127.0.0.1", "HostPort": str(service.PORT),
            }]},
        },
        "Mounts": [{
            "Destination": "/var/lib/mysql",
            "Source": plan["database"]["datadir"],
            "RW": True,
        }],
        "NetworkSettings": {},
    }
    monkeypatch.setattr(
        service, "_device_major_minor",
        lambda unused: service.DATA_PARTITION_MAJOR_MINOR,
    )
    monkeypatch.setattr(service, "_proc_start_ticks", lambda unused: 1234)
    assert service._validate_container(row, plan, "database")["pid"] == 222
    row["Mounts"][0]["RW"] = False
    with pytest.raises(
        service.ServiceError, match="database storage/IO/port/config differs"
    ):
        service._validate_container(row, plan, "database")


def test_short_lived_spec_passes_exact_image_id_to_docker_plan():
    exact = spec_builder.MYSQL_IMAGE_ID
    spec = spec_builder.make_spec(
        "xp-v3-image-id-audit-20260731",
        "a" * 64,
        "mysql:8.0",
        exact,
        "mysql:8.0",
        exact,
    )
    plan = spec_builder.command_plan(spec)
    bootstrap = plan["commands_to_run_on_node02"]["bootstrap"]
    assert bootstrap[bootstrap.index("--database-image") + 1] == exact
    assert bootstrap[bootstrap.index("--client-image") + 1] == exact
    assert "mysql:8.0" not in bootstrap
    assert plan["expected_readbacks"]["database_image_id"] == exact
    assert spec["paths"]["config_dir"] == (
        spec_builder.BUNDLE + "/qualification/config"
    )
    assert plan["main_agent_token_required_for_bootstrap"] is True
    assert plan["execution_order"][:2] == ["physical_copy", "jdk_publish"]
    assert plan["host_control_cpu_affinity"] == "21-33,57-69"
    for command in plan["commands_to_run_on_node02"].values():
        assert command[:3] == ["taskset", "-c", "21-33,57-69"]
        assert command[3:7] == [
            "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B",
        ]
    assert plan["adopt_readback_is_alternative_to_bootstrap_only"] is True
    assert plan["main_agent_mount_private_host_tmpfs"] == {
        "path": spec["paths"]["host_output_tmpfs"],
        "minimum_bytes": 2147483648,
        "filesystem": "tmpfs",
        "required_options": ["rw", "nosuid", "nodev", "noexec"],
        "must_exist_before_bootstrap": True,
    }
    assert bootstrap[bootstrap.index("--main-agent-token") + 1] == (
        "MAIN_AGENT_TOKEN_REQUIRED"
    )
    assert bootstrap[bootstrap.index("--main-agent-token-sha256") + 1] == (
        "a" * 64
    )
    assert bootstrap[
        bootstrap.index("--authorization-created-at-utc") + 1
    ] == spec["created_at_utc"]
    assert bootstrap[
        bootstrap.index("--authorization-expires-at-utc") + 1
    ] == spec["expires_at_utc"]


def test_bootstrap_requires_main_agent_secret(bundle):
    source = (
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    assert "main-agent bootstrap token differs" in source
    assert '"ra.main_agent_token_sha256"' in source
    invalidated = json.loads(
        (bundle / "invalidated_tokenless_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "fd5fd07dcf42daf590fb08ea6f88394531eac982ead2052ec348e27f7f0ec2df"
    )


def test_remote_service_enforces_short_lived_authority():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc)
    assert service.verify_short_lived_authority(
        (now - timedelta(minutes=1)).isoformat(),
        (now + timedelta(minutes=24)).isoformat(),
        now=now,
    )["expires_at_utc"] == (now + timedelta(minutes=24)).isoformat()
    with pytest.raises(service.ServiceError, match="currently valid"):
        service.verify_short_lived_authority(
            (now - timedelta(minutes=26)).isoformat(),
            (now - timedelta(minutes=1)).isoformat(),
            now=now,
        )
    with pytest.raises(service.ServiceError, match="30 minutes"):
        service.verify_short_lived_authority(
            now.isoformat(),
            (now + timedelta(minutes=31)).isoformat(),
            now=now,
        )


def test_previous_local_only_expiry_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (bundle / "invalidated_unexpiring_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "fe1bcc7a8624e3fb1f12092c50e37216d730b2f2dfe55360c4cccfb631db0fb1"
    )
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    assert contract["adopted_service"][
        "short_lived_authority_checked_on_remote_before_create"
    ] is True


def test_arbitrary_config_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (bundle / "invalidated_unbound_config_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "ad6c994c2ee4101d67f893558ef5be87712c5fbd40c417670631d84a95b6b08c"
    )
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    assert contract["adopted_service"][
        "manifest_covered_sealed_config_required"
    ] is True


def test_precreate_gate_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (bundle / "invalidated_precreate_gate_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "7f41936b910a6b87e126ec11507c8e4e47c7e95b2259345fe95b3807400829c6"
    )
    source = (
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    tmpfs_gate = source.index(
        '_validate_host_tmpfs(plan["sql_stdout_bridge"]["host_output_tmpfs"])'
    )
    recheck = source.index(
        'authority = plan["short_lived_authority"]', tmpfs_gate
    )
    first_create = source.index(
        '_run(plan[role]["create_argv"])', recheck
    )
    assert tmpfs_gate < recheck < first_create


def test_port_gate_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (bundle / "invalidated_port_gate_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "4130d5dcf5b08732f352833dc7ea7bb4b599f4e008477b82f44a76ff64d1b14f"
    )
    source = (
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    assert '_validate_port_available()' in source
    assert 'probe.bind(("127.0.0.1", PORT))' in source


def test_runtime_socket_is_only_symlink_exception_and_not_copied(
    tmp_path, monkeypatch
):
    source = tmp_path / "datadir"
    source.mkdir()
    (source / "ibdata1").write_bytes(b"database")
    (source / "mysql").mkdir()
    socket_path = source / "mysql.sock"
    socket_path.symlink_to(service.RUNTIME_SOCKET_TARGET)
    identity = service._tree_records(
        source, logical_prefix="datadir", allow_runtime_socket=True
    )
    assert identity[4] == [{
        "path": "datadir/mysql.sock",
        "type": "runtime_socket_symlink",
        "target": service.RUNTIME_SOCKET_TARGET,
        "copied": False,
        "followed": False,
    }]
    monkeypatch.setattr(service, "TEMPLATE_DATADIR", str(source))
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(service._copy_argv(target), check=True)
    assert (target / "ibdata1").read_bytes() == b"database"
    assert not (target / "mysql.sock").exists()
    assert not (target / "mysql.sock").is_symlink()
    target_identity = service._tree_records(
        target, logical_prefix="datadir", allow_runtime_socket=False
    )
    assert target_identity[0:3] == identity[0:3]
    assert target_identity[4] == []


def test_any_other_datadir_symlink_is_rejected(tmp_path):
    source = tmp_path / "datadir"
    source.mkdir()
    (source / "unexpected").symlink_to("/tmp")
    with pytest.raises(service.ServiceError, match="symlink prohibited"):
        service._tree_records(
            source, logical_prefix="datadir", allow_runtime_socket=True
        )


def test_failed_637_runtime_socket_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (bundle / "invalidated_runtime_socket_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "637cf3a8d9e7fe85e979b846304a4cdb823f7566bcf6b17760d8c4b4c80bf503"
    )


def test_tmpfs_readback_uses_mountinfo_and_statvfs_not_findmnt(
    tmp_path
):
    mount = tmp_path / "collector"
    mount.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "81 72 0:55 / %s rw,nosuid,nodev,noexec - "
        "tmpfs tmpfs rw,size=2097152k\n" % mount
    )
    filesystem = SimpleNamespace(
        f_frsize=4096,
        f_blocks=service.TMPFS_BYTES // 4096,
    )
    result = service._validate_host_tmpfs(
        mount,
        mountinfo_path=mountinfo,
        statvfs=lambda unused: filesystem,
    )
    assert result["size_bytes"] == service.TMPFS_BYTES
    assert result["legacy_findmnt_dependency"] is False
    assert result["required_mount_options"] == [
        "nodev", "noexec", "nosuid", "rw",
    ]
    source = Path(service.__file__).read_text()
    function = source.split(
        "def _validate_host_tmpfs(", 1
    )[1].split("\ndef _validate_port_available", 1)[0]
    assert '["findmnt"' not in function
    assert '"-b"' not in function


@pytest.mark.parametrize(
    "filesystem,options,blocks,error",
    [
        ("ext4", "rw,nosuid,nodev,noexec", 524288, "not tmpfs"),
        ("tmpfs", "rw,nosuid,nodev", 524288, "options differ"),
        ("tmpfs", "rw,nosuid,nodev,noexec", 1, "too small"),
    ],
)
def test_tmpfs_readback_fails_closed(
    tmp_path, filesystem, options, blocks, error
):
    mount = tmp_path / "collector"
    mount.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "81 72 0:55 / %s %s - %s tmpfs rw\n"
        % (mount, options, filesystem)
    )
    stat = SimpleNamespace(f_frsize=4096, f_blocks=blocks)
    with pytest.raises(service.ServiceError, match=error):
        service._validate_host_tmpfs(
            mount,
            mountinfo_path=mountinfo,
            statvfs=lambda unused: stat,
        )


def test_failed_96d9_legacy_findmnt_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (bundle / "invalidated_legacy_findmnt_bundle.json").read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "96d938614f88e37445d56a3c0d4eeef960e6d0990f7df56c85aca3c11480fceb"
    )


def test_failed_3599_legacy_docker_mount_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (
            bundle / "invalidated_legacy_docker_mount_bundle.json"
        ).read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "3599cd89ad2950039bb2c685d9f3edf540e23ceae715fb4ccac42f41bb772111"
    )
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    adopted = contract["adopted_service"]
    assert adopted["database_bind_mount_create_mode"] == (
        "default_writable_without_bare_rw_field"
    )
    assert adopted["database_bind_mount_inspect_rw_required"] is True


def _storage_topology_fixture(tmp_path):
    devices = tmp_path / "devices"
    parent = devices / "nvme0n1"
    partition = parent / "nvme0n1p1"
    partition.mkdir(parents=True)
    (parent / "holders").mkdir()
    (partition / "holders").mkdir()
    (parent / "dev").write_text("259:1\n")
    (partition / "dev").write_text("259:4\n")
    (partition / "partition").write_text("1\n")
    links = tmp_path / "sys_dev_block"
    links.mkdir()
    (links / "259:1").symlink_to(parent)
    (links / "259:4").symlink_to(partition)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "81 72 259:4 / %s rw,nosuid,nodev - "
        "ext4 %s rw\n" % (service.MOUNT, service.DATA_PARTITION)
    )
    swaps = tmp_path / "swaps"
    swaps.write_text("Filename Type Size Used Priority\n")

    def block_stat(path):
        identities = {
            service.DATA_PARTITION: (259, 4),
            service.THROTTLE_DEVICE: (259, 1),
        }
        major, minor = identities[path]
        return SimpleNamespace(
            st_mode=stat.S_IFBLK,
            st_rdev=os.makedev(major, minor),
        )

    return parent, partition, links, mountinfo, swaps, block_stat


def test_partition_mount_and_unique_parent_throttle_topology(tmp_path):
    unused_parent, unused_partition, links, mountinfo, swaps, block_stat = (
        _storage_topology_fixture(tmp_path)
    )
    result = service._validate_storage_topology(
        mountinfo_path=mountinfo,
        sys_dev_block=links,
        swaps_path=swaps,
        block_stat=block_stat,
    )
    assert result["data_partition"] == "/dev/nvme0n1p1"
    assert result["data_partition_major_minor"] == "259:4"
    assert result["throttle_parent"] == "/dev/nvme0n1"
    assert result["throttle_parent_major_minor"] == "259:1"
    assert result["unique_partition_inventory"] == ["nvme0n1p1"]
    assert result["related_mount_count"] == 1


def test_parent_throttle_topology_fails_on_sibling_partition(tmp_path):
    parent, unused_partition, links, mountinfo, swaps, block_stat = (
        _storage_topology_fixture(tmp_path)
    )
    sibling = parent / "nvme0n1p2"
    sibling.mkdir()
    (sibling / "partition").write_text("2\n")
    with pytest.raises(service.ServiceError, match="one unique partition"):
        service._validate_storage_topology(
            mountinfo_path=mountinfo,
            sys_dev_block=links,
            swaps_path=swaps,
            block_stat=block_stat,
        )


@pytest.mark.parametrize("other_use", ["mount", "holder", "swap"])
def test_parent_throttle_topology_fails_on_other_use(tmp_path, other_use):
    parent, unused_partition, links, mountinfo, swaps, block_stat = (
        _storage_topology_fixture(tmp_path)
    )
    if other_use == "mount":
        with mountinfo.open("a") as stream:
            stream.write(
                "82 72 259:1 / /other rw - ext4 %s rw\n"
                % service.THROTTLE_DEVICE
            )
        error = "another or incorrect mount"
    elif other_use == "holder":
        (parent / "holders/dm-0").write_text("")
        error = "block holders"
    else:
        swaps.write_text(
            "Filename Type Size Used Priority\n%s partition 1 0 -2\n"
            % service.DATA_PARTITION
        )
        error = "in use as swap"
    with pytest.raises(service.ServiceError, match=error):
        service._validate_storage_topology(
            mountinfo_path=mountinfo,
            sys_dev_block=links,
            swaps_path=swaps,
            block_stat=block_stat,
        )


def test_failed_7981_partition_throttle_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (
            bundle / "invalidated_partition_throttle_bundle.json"
        ).read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "79815c2fe8c1fc8f31177e7be6954f73bbd2a6ac4a95488ad6d36aa6bb281f1a"
    )
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    assert contract["storage"]["data_partition_major_minor"] == "259:4"
    assert contract["storage"]["io_throttle_parent_major_minor"] == "259:1"
    assert contract["adopted_service"][
        "unique_partition_and_parent_exclusivity_gate_required"
    ] is True


def test_9bf_parent_throttle_bundle_is_zero_credit_and_exactly_rebound(bundle):
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    invalidation = json.loads(
        (bundle / "invalidated_parent_throttle_bundle.json").read_text()
    )
    binding = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )["collector_throttle_binding"]
    assert invalidation["bundle_id"] == (
        "9bfdd22abba1330fbb3938609f21102bccad68293b1d987b1dd9f7514a652568"
    )
    assert invalidation["accepted_rounds_credit"] == 0
    assert invalidation["r0_credit"] == 0
    assert invalidation["formal_launch_authorized"] is False
    assert invalidation["accepted_round_authorized"] is False
    assert invalidation["bundle_id"] in manifest["invalid_development_bundle_ids"]
    assert binding == {
        "data_partition": "/dev/nvme0n1p1",
        "data_partition_major_minor": "259:4",
        "docker_bps_declaration_device": "/dev/nvme0n1",
        "effective_cgroup_v1_throttle_major_minor": "259:1",
        "io_read_bps": 29360128,
        "io_write_bps": 524288,
        "exact_single_docker_binding_required": True,
        "exact_effective_v1_binding_required": True,
        "data_partition_and_throttle_parent_must_not_be_conflated": True,
    }
    collector = (
        bundle / "qualification/mysql_distributed_live_readback_qualification_v1.py"
    ).read_text()
    assert "def _exact_rate_binding" in collector
    assert "len(rows) == 1" in collector
    assert "def _v1_effective_throttle" in collector
    assert "XP throttle-parent requires cgroup v1" in collector
    assert "data_device == throttle_device" in collector


def test_attempt_owned_datadir_permission_normalization_and_source_unchanged(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "runtime"
    attempt = "xp-v3-permission-normalization-test"
    source = tmp_path / "source"
    source.mkdir()
    (source / "mysql").mkdir()
    (source / "mysql/table.ibd").write_bytes(b"table")
    (source / "ibdata1").write_bytes(b"system")
    (source / "mysql").chmod(0o755)
    (source / "mysql/table.ibd").chmod(0o644)
    (source / "ibdata1").chmod(0o640)
    target = runtime / attempt / "datadir"
    target.parent.mkdir(parents=True)
    shutil.copytree(source, target)
    source_before = service._metadata_summary(
        service._tree_metadata_records(source)
    )
    source_content = service._tree_content_identity(source)
    monkeypatch.setattr(service, "RUNTIME_ROOT", str(runtime))
    monkeypatch.setattr(service, "MYSQL_UID", os.getuid())
    monkeypatch.setattr(service, "MYSQL_GID", os.getgid())
    result = service._normalize_target_datadir(target, attempt)
    assert result["scope"] == "attempt_owned_datadir_only"
    assert result["directory_mode"] == 0o700
    assert result["regular_mode"] == 0o600
    assert result["owner_mode_inventory"] == [
        {
            "type": "directory", "uid": os.getuid(), "gid": os.getgid(),
            "mode": 0o700,
        },
        {
            "type": "regular", "uid": os.getuid(), "gid": os.getgid(),
            "mode": 0o600,
        },
    ]
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert stat.S_IMODE((target / "mysql").stat().st_mode) == 0o700
    assert stat.S_IMODE((target / "ibdata1").stat().st_mode) == 0o600
    assert service._metadata_summary(
        service._tree_metadata_records(source)
    ) == source_before
    assert service._tree_content_identity(target) == source_content
    (target / "ibdata1").chmod(0o400)
    with pytest.raises(
        service.ServiceError,
        match="attempt datadir owner/mode normalization differs",
    ):
        service._target_metadata_readback(target)


def test_permission_normalization_rejects_non_attempt_target(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ibdata1").write_bytes(b"unchanged")
    before = (outside / "ibdata1").stat().st_mode
    monkeypatch.setattr(service, "RUNTIME_ROOT", str(runtime))
    with pytest.raises(
        service.ServiceError, match="target is not attempt-owned"
    ):
        service._normalize_target_datadir(
            outside, "xp-v3-permission-normalization-test"
        )
    assert (outside / "ibdata1").stat().st_mode == before


def test_failed_d55f_unwritable_datadir_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (
            bundle / "invalidated_unwritable_datadir_bundle.json"
        ).read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "d55f477550f0616f80d7701eeb7c55e9c7e30590e3e1cdea34f05f2924d393ad"
    )
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    adopted = contract["adopted_service"]
    assert adopted["attempt_datadir_owner"] == {"uid": 999, "gid": 999}
    assert adopted["attempt_datadir_directory_mode"] == 0o700
    assert adopted["attempt_datadir_regular_mode"] == 0o600
    assert adopted[
        "source_metadata_before_after_identity_required"
    ] is True
    assert adopted[
        "pristine_target_metadata_and_content_rechecked_before_create"
    ] is True


def test_failed_0a97_bytecode_polluted_bundle_is_invalidated(bundle):
    invalidated = json.loads(
        (
            bundle / "invalidated_bytecode_polluted_bundle.json"
        ).read_text()
    )
    assert invalidated["status"] == "INVALIDATED_DO_NOT_LAUNCH"
    assert invalidated["bundle_id"] == (
        "0a97c902540917339fac5cb365a9d3c390d6f40a0ab3fe0eda2a18e72aa110b9"
    )
    assert invalidated["observed"]["database_container_created"] is False
    assert invalidated["observed"]["client_container_created"] is False
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    adopted = contract["adopted_service"]
    assert adopted["all_sealed_entrypoints_disable_bytecode_at_start"] is True
    assert adopted["host_python_required_argv"] == [
        "env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B",
    ]
    assert adopted["prepare_copy_bundle_closure_before_after_required"] is True
    assert adopted["sealed_bundle_pycache_prohibited"] is True


def test_sealed_entrypoints_disable_bytecode_before_other_imports(bundle):
    entries = (
        "launch/mysql_xp_node02_adopted_service_v1.py",
        "launch/mysql_xp_capture_authority_v1.py",
        "launch/mysql_xp_capture_transition_v1.py",
        "launch/mysql_xp_node02_formal10_materializer_v1.py",
        "launch/mysql_xp_formal_evidence_producer_v1.py",
        "launch/mysql_xp_raw_evidence_executor_v1.py",
        "runtime/mysql_xp_node02_jdk_publish_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_distributed_workload_metric_adapter_v1.py",
        "qualification/mysql_xp_qualification_authority_v1.py",
    )
    for relative in entries:
        source = (bundle / relative).read_text()
        assert "sys.dont_write_bytecode = True" in source
        assert source.index("sys.dont_write_bytecode = True") < source.index(
            "import argparse" if "import argparse" in source else "import json"
        )


def test_capture_entrypoints_are_python36_compatible_without_python_closure(bundle):
    """node02's host python is pre-3.7; capture cannot borrow JOB's runtime."""
    entries = (
        "launch/mysql_xp_capture_authority_v1.py",
        "launch/mysql_xp_capture_transition_v1.py",
        "launch/mysql_xp_raw_evidence_executor_v1.py",
        "launch/mysql_xp_node02_formal10_materializer_v1.py",
    )
    forbidden = (
        "from __future__ import annotations",
        "list[", "dict[", "tuple[", "set[", "Path |",
    )
    for relative in entries:
        source = (bundle / relative).read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), relative
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    assert contract["runtime_publication"]["unbound_python_runtime_prohibited"] is True


def test_plain_python_entrypoints_leave_writable_bundle_without_pycache(
    bundle, tmp_path
):
    detached = tmp_path / bundle.name
    shutil.copytree(bundle, detached)
    for path in [detached, *detached.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    entries = (
        "launch/mysql_xp_node02_adopted_service_v1.py",
        "launch/mysql_xp_capture_authority_v1.py",
        "launch/mysql_xp_capture_transition_v1.py",
        "launch/mysql_xp_node02_formal10_materializer_v1.py",
        "launch/mysql_xp_formal_evidence_producer_v1.py",
        "launch/mysql_xp_raw_evidence_executor_v1.py",
        "launch/mysql_xp_formal_evidence_producer_v1.py",
        "runtime/mysql_xp_node02_jdk_publish_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_distributed_workload_metric_adapter_v1.py",
    )
    for relative in entries:
        completed = subprocess.run(
            [sys.executable, str(detached / relative), "--help"],
            cwd=tmp_path,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode == 0, (relative, completed.stderr)
    assert not any(
        path.name == "__pycache__" or path.suffix in (".pyc", ".pyo")
        for path in detached.rglob("*")
    )


def test_sealed_bundle_readback_rejects_unmapped_pycache(bundle, tmp_path):
    result = service._sealed_bundle_readback(bundle)
    assert result["bytecode_artifacts"] == []
    detached = tmp_path / bundle.name
    shutil.copytree(bundle, detached)
    runtime = detached / "runtime"
    runtime.chmod(0o755)
    cache = runtime / "__pycache__"
    cache.mkdir()
    bytecode = cache / "polluted.pyc"
    bytecode.write_bytes(b"polluted")
    bytecode.chmod(0o444)
    cache.chmod(0o555)
    runtime.chmod(0o555)
    with pytest.raises(
        service.ServiceError,
        match="full file closure differs|bytecode artifact",
    ):
        service._sealed_bundle_readback(detached)
    source = (
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    assert source.rindex("sealed_bundle_after = _sealed_bundle_readback()") < (
        source.index("atomic_json(output, authority)")
    )


def test_runtime_publisher_and_manifest_are_sealed(bundle):
    contract = json.loads(
        (bundle / "xp_executable_launch_contract_v3.json").read_text()
    )
    publication = contract["runtime_publication"]
    assert publication["content_addressed"] is True
    assert publication["atomic_nonoverwriting_publish"] is True
    assert publication["exact_tree_readback_gate_required"] is True
    assert publication["tree_modes_preserved_as_manifested"] is True
    assert publication["client_mount_read_only_required"] is True
    assert publication["unbound_python_runtime_prohibited"] is True
    assert publication["tree_content_sha256"] == (
        "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
    )
    publisher = (
        bundle / "runtime/mysql_xp_node02_jdk_publish_v1.py"
    ).read_text()
    assert "verify_tree_manifest" in publisher
    assert "os.rename" in publisher
    assert "refusing to overwrite publication evidence" in publisher
    assert contract["runtime_entrypoints"] == [
        "launch/mysql_xp_node02_adopted_service_v1.py",
        "launch/mysql_xp_capture_authority_v1.py",
        "launch/mysql_xp_capture_transition_v1.py",
        "launch/mysql_xp_node02_formal10_materializer_v1.py",
        "launch/mysql_xp_formal_evidence_producer_v1.py",
        "launch/mysql_xp_raw_evidence_executor_v1.py",
        "runtime/mysql_xp_node02_jdk_publish_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_distributed_workload_metric_adapter_v1.py",
        "qualification/mysql_xp_qualification_authority_v1.py",
    ]


def test_runtime_entrypoints_import_from_detached_bundle(bundle, tmp_path):
    """The content-addressed closure, not this checkout, must supply imports."""
    detached = tmp_path / bundle.name
    shutil.copytree(bundle, detached)
    entries = (
        "launch/mysql_xp_node02_adopted_service_v1.py",
        "launch/mysql_xp_capture_authority_v1.py",
        "launch/mysql_xp_capture_transition_v1.py",
        "launch/mysql_xp_node02_formal10_materializer_v1.py",
        "runtime/mysql_xp_node02_jdk_publish_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_distributed_workload_metric_adapter_v1.py",
    )
    for relative in entries:
        completed = subprocess.run(
            [sys.executable, str(detached / relative), "--help"],
            cwd=tmp_path,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": "",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode == 0, (relative, completed.stderr)
    assert (detached / "qualification/runtime_tree_content_identity_v3.py").is_file()
    # The build file documents closure provenance; it is deliberately not a
    # remote/data-plane entrypoint and therefore must never enter the list.
    assert "build_mysql_xp_formal10_launch_v3.py" not in json.loads(
        (detached / "xp_executable_launch_contract_v3.json").read_text()
    )["runtime_entrypoints"]


def test_materializer_binds_full_promotion_chain_and_latest_collector(bundle):
    source = (
        bundle / "launch/mysql_xp_node02_formal10_materializer_v1.py"
    ).read_text()
    for token in (
        "mysql_xp_production_trace_registry_v3",
        "mysql_xp_fresh_r0_result_v3",
        "mysql_xp_algorithm_c_debug_round_sealed_v3",
        "mysql_xp_node02_same_live_formal10_handoff_v1",
        "manifest[\"collector_sha256\"]",
        "manifest-bound collector identity differs",
    ):
        assert token in source
    assert (
        "f3b367e839e365edf3169e77070c3fd0072d3ccdcdcd8c9798077573a1fcf781"
        not in source
    )
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    assert manifest["collector_sha256"] == hashlib.sha256(
        (
            bundle
            / "qualification/mysql_distributed_live_readback_qualification_v1.py"
        ).read_bytes()
    ).hexdigest()
    invalidation = json.loads(
        (
            bundle / "invalidated_materializer_collector_bundle.json"
        ).read_text()
    )
    assert invalidation["bundle_id"] == (
        "e550e412b6e8bd226c2299252ae7dc61c33d8fcd43506dd5741e8e72b251d4a6"
    )
    assert invalidation["formal_launch_authorized"] is False
    assert invalidation["accepted_rounds_credit"] == 0
    assert "range(1, 8)" in source
    assert "len(ranking) != 127" in source
    assert "len(journals) != 5" in source
    assert "len(fingerprints) != 132" in source
    assert "ongoing_rolling_gate_required" in source
    assert "capture_r0_debug_or_failed_replay_countable_as_accepted" in source


def test_gradient_formula_and_update_are_recomputed_not_trusted():
    parameter = materializer.PARAMETERS[0]
    gradient = (2.0 * 3.0 * 0.5) + (-1.0 * 4.0 * 0.25)
    assert gradient == 2.0
    eta = 0.1
    before = 1.5
    proposed = before * math.exp(eta * gradient)
    event = {
        "feature_ratio": {"cpu": 1.0},
        "predicted_resource": {"cpu": 0.4},
        "utilization": {"cpu": 0.4},
        "dScore/dU": {parameter: {"cpu": 2.0, "io": -1.0}},
        "T": {parameter: {"cpu": 3.0, "io": 4.0}},
        "confidence": {parameter: {"cpu": 0.5, "io": 0.25}},
        "g_j": {parameter: gradient},
        "eta": eta,
        "delta_param": {
            parameter: {
                "before": before,
                "proposed_before_clip": proposed,
                "after_clip": proposed,
                "absolute": proposed - before,
                "relative": (proposed - before) / before,
                "clipped": False,
            }
        },
        "candidate_score": 0.8,
        "gradient_source": "original_dScore_dU_times_T_times_confidence",
    }
    materializer.verify_gradient(event, "valid")
    event["g_j"][parameter] = gradient + 1
    with pytest.raises(materializer.MaterializerError, match="sum"):
        materializer.verify_gradient(event, "invalid")


def test_materializer_never_launches_data_plane(bundle):
    source = (
        bundle / "launch/mysql_xp_node02_formal10_materializer_v1.py"
    ).read_text()
    assert "subprocess" not in source
    assert '"docker", "run"' not in source
    assert '"docker", "create"' not in source
    assert "ssh" not in source.lower()
