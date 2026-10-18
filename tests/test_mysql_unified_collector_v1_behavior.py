from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PAIRED = load("controller/mysql_unified_qualification_paired_authority_v2.py", "paired_v2")
AGGREGATE = load("qualification/mysql_unified_three_pair_gate_aggregate_v2.py", "aggregate_v2")


def sealed(path, value):
    payload = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                          allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.with_name(path.name + ".sha256").write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )


def readback(scenario, bundle_id, major_minor, port, container):
    cpus = {
        "job_memory": ("18-20,54-56", "25-27,61-63"),
        "tpch_memory": ("32-33,68-69", "0-7,36-43"),
    }[scenario]
    return {
        "schema_version": "mysql_algorithm_c_distributed_live_readback_v1",
        "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
        "scenario": scenario, "hostname": "node04", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "capture_authorized": False,
        "formal_r0_authorized": False, "formal_launch_authorized": False,
        "accepted_round_authorized": False, "bundle_id": bundle_id,
        "resource_envelope": {"cpuset_cpus": cpus[0]},
        "client_resource_envelope": {"cpuset_cpus": cpus[1]},
        "storage": {"major_minor": major_minor}, "mysql_port": port,
        "container": {"container_name": container},
    }


def test_paired_v2_verifies_separate_target_and_unified_collector_closures(tmp_path):
    target_id = "d" * 64
    target = tmp_path / target_id
    target.mkdir()
    target_file = target / "target.txt"
    target_file.write_text("sealed target\n")
    (target / "bundle_manifest.json").write_text(json.dumps({
        "bundle_id": target_id,
        "files": {"target.txt": hashlib.sha256(target_file.read_bytes()).hexdigest()},
    }))
    own = tmp_path / "own.json"; peer = tmp_path / "peer.json"
    sealed(own, readback("job_memory", target_id, "259:3", 65102, "/job-db"))
    sealed(peer, readback("tpch_memory", "e" * 64, "259:1", 65103, "/tpch-db"))
    host = tmp_path / "host"; container_root = tmp_path / "container"; peer_root = tmp_path / "peer"
    host.mkdir(); container_root.mkdir(); peer_root.mkdir()
    solo_attempt = "solo-job-" + "1" * 32
    solo = tmp_path / "solo.json"
    sealed(solo, {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "mode": "qualification_solo", "bundle_id": target_id,
        "qualification_attempt_id": solo_attempt, "debug_only": True,
        "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
        "workload_process_isolation": {"cpuset_cpus": "25-27,61-63"},
        "measurement_output": {"host_tmpfs_directory": str(host),
                               "container_tmpfs_destination": str(container_root)},
        "argv": ["/python", "/adapter", "--attempt-id=" + solo_attempt,
                 "--metric-stream", "/measurement/solo.jsonl"],
        "environment": {"QUALIFICATION_ROLE": "solo"},
        "client_container": {"name": "job-client"},
    })
    authority = tmp_path / "solo-authority.json"
    sealed(authority, {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "mode": "qualification_solo", "bundle_id": target_id,
        "qualification_attempt_id": solo_attempt,
        "command_spec_sha256": hashlib.sha256(solo.read_bytes()).hexdigest(),
        "expires_epoch": datetime.now(timezone.utc).timestamp() + 600,
        "debug_only": True, "capture_authorized": False,
        "formal_r0_authorized": False, "formal_authorized": False,
        "accepted_round_authorized": False,
    })
    output = tmp_path / "paired"
    result = PAIRED.produce(argparse.Namespace(
        scenario_bundle=target, collector_bundle=ROOT,
        collector=ROOT / "qualification/mysql_unified_job_tpch_tpcc_collector_entry_v1.py",
        own_readback=own, peer_readback=peer, solo_command=solo,
        solo_authority=authority,
        qualification_attempt_id="job-paired-" + "2" * 32,
        peer_qualification_attempt_id="tpch-paired-" + "3" * 32,
        peer_workload_metric_stream=peer_root / "peer.jsonl",
        output_directory=output, ttl_seconds=600,
    ))
    command = json.loads((output / "command.json").read_text())
    paired_authority = json.loads((output / "main_agent_authority.json").read_text())
    manifest_sha = hashlib.sha256((ROOT / "bundle_manifest.json").read_bytes()).hexdigest()
    assert result["status"] == "SEALED_PAIRED_AUTHORITY_NOT_EXECUTED"
    assert command["collector_bundle_id"] == ROOT.name
    assert paired_authority["collector_bundle_id"] == ROOT.name
    assert paired_authority["collector_manifest_sha256"] == manifest_sha


def gate(left, right, collector_sha):
    now = datetime.now(timezone.utc)
    directions = ["A_solo_vs_A_while_B_runs", "B_solo_vs_B_while_A_runs"]
    return {
        "schema_version": "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "policy_version": "mysql_algorithm_c_same_host_bidirectional_interference_gate_v2",
        "status": "PASS", "host_id": "node04", "hostname": "node04",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False, "formal10_authorized": False,
        "debug_only": True, "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "duration_seconds": 180, "sample_period_seconds": 1,
        "minimum_samples_per_stream": 181, "directions_passed": directions,
        "directions": {key: {"passed": True} for key in directions},
        "database_counter_missing_samples": 0, "database_counter_timeout_count": 0,
        "all_tolerances_passed": True, "both_directions_same_version_verified": True,
        "authorization_reuse_verified_absent": True,
        "ongoing_rolling_window_required": True, "ongoing_monitor_required": True,
        "concurrent_capture_r0_or_debug_eligible_for_main_agent_decision": True,
        "serial_fallback_required": False,
        "raw_evidence_closure_sha256": {key: "a" * 64 for key in ("A_solo", "A_paired", "B_solo", "B_paired")},
        "raw_artifacts": {"pair.raw_evidence.json": "b" * 64},
        "captured_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=10)).isoformat(),
        "scenarios": [left, right], "collector_sha256": collector_sha,
    }


def test_aggregate_requires_this_one_unified_manifest_sha(tmp_path):
    expected = {
        "job": ("18-20,54-56", "25-27,61-63", "16-17,52-53"),
        "tpch": ("32-33,68-69", "0-7,36-43", "32-33,68-69"),
        "tpcc": ("21-24,57-60", "8-15,44-51", "34-35,70-71"),
    }
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenarios": {key: {"resource_envelope": {"cpuset_cpus": value[0]},
                            "client_resource_envelope": {"cpuset_cpus": value[1]},
                            "target_infrastructure": {"cpuset_cpus": value[2]}}
                      for key, value in expected.items()},
        "same_host_parallel_policy": {
            "fallback_decision": "BLOCK_CONCURRENT_LAUNCH_NO_SERIAL_SUBSTITUTION",
            "historical_same_host_mutex": "PROHIBITED_AS_V16_PARALLEL_AUTHORITY"},
    }))
    collector_sha = hashlib.sha256((ROOT / "bundle_manifest.json").read_bytes()).hexdigest()
    pairs = [("job_memory", "tpch_memory"), ("job_memory", "tpcc_cpu"),
             ("tpch_memory", "tpcc_cpu")]
    paths = []
    for index, pair in enumerate(pairs):
        path = tmp_path / ("gate%d.json" % index)
        sealed(path, gate(*pair, collector_sha)); paths.append(path)
    result = AGGREGATE.aggregate(contract, list(zip(paths, ("one", "two", "three"))),
                                 tmp_path / "aggregate.json")
    assert result["collector_sha256_set"] == [collector_sha]
    stale = tmp_path / "stale.json"
    sealed(stale, gate(*pairs[0], "f" * 64))
    try:
        AGGREGATE.aggregate(contract, [(stale, "one"), (paths[1], "two"),
                                       (paths[2], "three")], tmp_path / "blocked.json")
    except AGGREGATE.AggregateError:
        pass
    else:
        raise AssertionError("mixed collector identities were accepted")
