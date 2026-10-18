from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def module(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(value)
    return value


PAIRED = module("controller/mysql_job_v17_qualification_paired_authority_v1.py", "paired_v17")
GATES = module("qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py", "gates_v17")
TREES = module("qualification/mysql_job_v17_portable_tree_inventory_v1.py", "trees_v17")


def write_sealed(path: Path, value: dict) -> None:
    payload = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                          allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.with_name(path.name + ".sha256").write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )


def test_portable_tree_inventory_checks_hardlinks_symlinks_and_mutation(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    for root in (source, destination):
        (root / "database").mkdir(parents=True)
        (root / "database/a.ibd").write_bytes(b"exact-content")
        os.link(root / "database/a.ibd", root / "database/a-hardlink.ibd")
        os.symlink("a.ibd", root / "database/a-link.ibd")
        fixed = 1_700_000_000_000_000_000
        os.utime(root / "database/a.ibd", ns=(fixed, fixed))
        os.utime(root / "database/a-link.ibd", ns=(fixed, fixed), follow_symlinks=False)
        os.utime(root / "database", ns=(fixed, fixed))
        os.utime(root, ns=(fixed, fixed))
    source_value = TREES.inventory(source, "source")
    destination_value = TREES.inventory(destination, "destination")
    assert source_value["content_identity_sha256"] == destination_value["content_identity_sha256"]
    source_json = tmp_path / "source.json"
    destination_json = tmp_path / "destination.json"
    TREES.write(source_json, source_value)
    TREES.write(destination_json, destination_value)
    comparison = TREES.compare(source_json, destination_json)
    assert comparison["status"] == "PASS_SOURCE_DESTINATION_EXACT_TREE_IDENTITY"
    (destination / "database/a.ibd").write_bytes(b"different")
    changed_json = tmp_path / "changed.json"
    TREES.write(changed_json, TREES.inventory(destination, "changed"))
    with pytest.raises(TREES.InventoryError):
        TREES.compare(source_json, changed_json)


def contract_value() -> dict:
    expected = {
        "job": ("18-20,54-56", "25-27,61-63", "16-17,52-53"),
        "tpch": ("32-33,68-69", "0-7,36-43", "32-33,68-69"),
        "tpcc": ("21-24,57-60", "8-15,44-51", "34-35,70-71"),
    }
    return {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenarios": {
            key: {
                "resource_envelope": {"cpuset_cpus": values[0]},
                "client_resource_envelope": {"cpuset_cpus": values[1]},
                "target_infrastructure": {"cpuset_cpus": values[2]},
            }
            for key, values in expected.items()
        },
        "same_host_parallel_policy": {
            "fallback_decision": "BLOCK_CONCURRENT_LAUNCH_NO_SERIAL_SUBSTITUTION",
            "historical_same_host_mutex": "PROHIBITED_AS_V16_PARALLEL_AUTHORITY",
        },
    }


def gate_value(left: str, right: str) -> dict:
    now = datetime.now(timezone.utc)
    directions = ["A_solo_vs_A_while_B_runs", "B_solo_vs_B_while_A_runs"]
    return {
        "schema_version": "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "policy_version": "mysql_algorithm_c_same_host_bidirectional_interference_gate_v2",
        "status": "PASS", "host_id": "node04", "hostname": "node04",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False, "formal10_authorized": False,
        "debug_only": True, "capture_authorized": False,
        "formal_r0_authorized": False, "formal_authorized": False,
        "duration_seconds": 180, "sample_period_seconds": 1,
        "minimum_samples_per_stream": 181,
        "directions_passed": directions,
        "directions": {key: {"passed": True} for key in directions},
        "database_counter_missing_samples": 0,
        "database_counter_timeout_count": 0,
        "all_tolerances_passed": True,
        "both_directions_same_version_verified": True,
        "authorization_reuse_verified_absent": True,
        "ongoing_rolling_window_required": True,
        "ongoing_monitor_required": True,
        "concurrent_capture_r0_or_debug_eligible_for_main_agent_decision": True,
        "serial_fallback_required": False,
        "raw_evidence_closure_sha256": {key: "a" * 64 for key in ("A_solo", "A_paired", "B_solo", "B_paired")},
        "raw_artifacts": {"pair.raw_evidence.json": "b" * 64},
        "captured_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=10)).isoformat(),
        "scenarios": [left, right], "collector_sha256": "c" * 64,
    }


def test_three_pair_gate_aggregate_requires_all_three_exact_fresh_passes(tmp_path):
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(contract_value()), encoding="utf-8")
    pairs = [("job_memory", "tpch_memory"), ("job_memory", "tpcc_cpu"),
             ("tpch_memory", "tpcc_cpu")]
    gates = []
    for index, pair in enumerate(pairs):
        path = tmp_path / ("gate%d.json" % index)
        write_sealed(path, gate_value(*pair))
        gates.append(path)
    result = GATES.aggregate(contract, list(zip(gates, ("JOB/TPCH", "JOB/TPCC", "TPCH/TPCC"))), tmp_path / "aggregate.json")
    assert result["pair_count"] == 3
    assert result["qualification_credit"] == result["r0_credit"] == 0
    assert result["formal_launch_authorized"] is False
    duplicate = tmp_path / "duplicate.json"
    with pytest.raises(GATES.AggregateError):
        GATES.aggregate(contract, [(gates[0], "one"), (gates[0], "two"), (gates[2], "three")], duplicate)


def readback(scenario: str, bundle_id: str, major_minor: str, port: int, container: str) -> dict:
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


def test_paired_authority_is_derived_from_fresh_sealed_solo_and_binds_peer(tmp_path):
    bundle_id = "d" * 64
    bundle = tmp_path / bundle_id
    collector = bundle / "qualification/collector.py"
    collector.parent.mkdir(parents=True)
    collector.write_text("# sealed\n", encoding="utf-8")
    collector_sha = hashlib.sha256(collector.read_bytes()).hexdigest()
    (bundle / "bundle_manifest.json").write_text(json.dumps({
        "bundle_id": bundle_id, "files": {"qualification/collector.py": collector_sha}
    }), encoding="utf-8")
    own = tmp_path / "own.json"; peer = tmp_path / "peer.json"
    write_sealed(own, readback("job_memory", bundle_id, "259:3", 65102, "/job-db"))
    write_sealed(peer, readback("tpch_memory", "e" * 64, "259:0", 65103, "/tpch-db"))
    host_metrics = tmp_path / "host-metrics"; container_metrics = tmp_path / "container-metrics"
    peer_metrics = tmp_path / "peer-metrics/paired.jsonl"
    host_metrics.mkdir(); container_metrics.mkdir(); peer_metrics.parent.mkdir()
    solo_attempt = "solo-attempt-" + "1" * 32
    solo = tmp_path / "solo.json"
    write_sealed(solo, {
        "schema_version": "mysql_algorithm_c_debug_qualification_command_v1",
        "mode": "qualification_solo", "bundle_id": bundle_id,
        "qualification_attempt_id": solo_attempt,
        "debug_only": True, "capture_authorized": False,
        "formal_r0_authorized": False, "formal_authorized": False,
        "accepted_round_authorized": False,
        "workload_process_isolation": {"cpuset_cpus": "25-27,61-63"},
        "measurement_output": {"host_tmpfs_directory": str(host_metrics),
                               "container_tmpfs_destination": str(container_metrics)},
        "argv": ["/python", "/adapter", "--attempt-id=" + solo_attempt,
                 "--metric-stream", "/old/native.jsonl"],
        "environment": {"QUALIFICATION_ROLE": "solo"},
        "client_container": {"name": "job-client"},
    })
    authority = tmp_path / "solo-authority.json"
    write_sealed(authority, {
        "schema_version": "mysql_algorithm_c_main_agent_qualification_authority_v1",
        "mode": "qualification_solo", "bundle_id": bundle_id,
        "qualification_attempt_id": solo_attempt,
        "command_spec_sha256": hashlib.sha256(solo.read_bytes()).hexdigest(),
        "expires_epoch": datetime.now(timezone.utc).timestamp() + 600,
        "debug_only": True, "capture_authorized": False,
        "formal_r0_authorized": False, "formal_authorized": False,
        "accepted_round_authorized": False,
    })
    paired_attempt = "paired-job-" + "2" * 32
    peer_attempt = "paired-tpch-" + "3" * 32
    output = tmp_path / "paired-authority"
    args = argparse.Namespace(
        scenario_bundle=bundle, collector=collector, own_readback=own,
        peer_readback=peer, solo_command=solo, solo_authority=authority,
        qualification_attempt_id=paired_attempt,
        peer_qualification_attempt_id=peer_attempt,
        peer_workload_metric_stream=peer_metrics, output_directory=output,
        ttl_seconds=600,
    )
    result = PAIRED.produce(args)
    command = json.loads((output / "command.json").read_text())
    assert result["status"] == "SEALED_PAIRED_AUTHORITY_NOT_EXECUTED"
    assert command["mode"] == "qualification_paired"
    assert command["paired_peer_workload"]["qualification_attempt_id"] == peer_attempt
    assert command["paired_peer_workload"]["workload_metric_stream_path"] == str(peer_metrics)
    assert json.loads((output / "launch.json").read_text())["qualification_credit"] == 0
