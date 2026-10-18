#!/usr/bin/env python3
"""Seal three fresh PASS bidirectional gates into one zero-credit aggregate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


EXPECTED_PAIRS = {
    frozenset(("job_memory", "tpch_memory")): "job_tpch",
    frozenset(("job_memory", "tpcc_cpu")): "job_tpcc",
    frozenset(("tpch_memory", "tpcc_cpu")): "tpch_tpcc",
}
EXPECTED_CPU = {
    "job_memory": ("18-20,54-56", "25-27,61-63", "16-17,52-53"),
    "tpch_memory": ("32-33,68-69", "0-7,36-43", "32-33,68-69"),
    "tpcc_cpu": ("21-24,57-60", "8-15,44-51", "34-35,70-71"),
}
REQUIRED_DIRECTIONS = ["A_solo_vs_A_while_B_runs", "B_solo_vs_B_while_A_runs"]
HEX64 = re.compile(r"[0-9a-f]{64}$")


class AggregateError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise AggregateError(label + " is absent or symlinked")
    sidecar = path.with_name(path.name + ".sha256")
    required = sha256(path) + "  " + path.name + "\n"
    if (sidecar.is_symlink() or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii") != required):
        raise AggregateError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AggregateError(label + " must be an object")
    return value


def cpus(value):
    result = set()
    for part in value.split(","):
        ends = [int(item) for item in part.split("-")]
        result.update(range(ends[0], ends[-1] + 1))
    return result


def verify_static_contract(contract):
    if (contract.get("formal_scope") != "mysql_only"
            or contract.get("cross_engine_comparison_prohibited") is not True
            or contract.get("postgresql_mutation_prohibited") is not True):
        raise AggregateError("formal scope authority differs")
    scenes = contract.get("scenarios") or {}
    unions = {}
    for scenario, name in (("job", "job_memory"), ("tpch", "tpch_memory"), ("tpcc", "tpcc_cpu")):
        row = scenes.get(scenario) or {}; expected = EXPECTED_CPU[name]
        observed = ((row.get("resource_envelope") or {}).get("cpuset_cpus"),
                    (row.get("client_resource_envelope") or {}).get("cpuset_cpus"),
                    (row.get("target_infrastructure") or {}).get("cpuset_cpus"))
        if observed != expected:
            raise AggregateError("four-scene static CPU authority differs")
        unions[name] = cpus(expected[0]) | cpus(expected[1]) | cpus(expected[2])
    for pair in EXPECTED_PAIRS:
        left, right = tuple(pair)
        if not unions[left].isdisjoint(unions[right]):
            raise AggregateError("four-scene static pair overlaps")
    policy = contract.get("same_host_parallel_policy") or {}
    if (policy.get("fallback_decision") != "BLOCK_CONCURRENT_LAUNCH_NO_SERIAL_SUBSTITUTION"
            or policy.get("historical_same_host_mutex") != "PROHIBITED_AS_V16_PARALLEL_AUTHORITY"):
        raise AggregateError("no-serial contract differs")


def validate_gate(path, label, now):
    gate = load(path, label)
    pair = frozenset(gate.get("scenarios") or [])
    try:
        captured = datetime.fromisoformat(str(gate.get("captured_at_utc")).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(gate.get("expires_at_utc")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AggregateError(label + " time identity differs: " + str(exc))
    closure = gate.get("raw_evidence_closure_sha256") or {}
    raw_artifacts = gate.get("raw_artifacts") or {}
    if (pair not in EXPECTED_PAIRS or len(pair) != 2
            or gate.get("schema_version") != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
            or gate.get("policy_version") != "mysql_algorithm_c_same_host_bidirectional_interference_gate_v2"
            or gate.get("status") != "PASS" or gate.get("host_id") != "node04"
            or gate.get("hostname") != "node04" or gate.get("formal_scope") != "mysql_only"
            or gate.get("cross_engine_comparison_prohibited") is not True
            or gate.get("accepted_round_authorized") is not False
            or gate.get("formal10_authorized") is not False
            or gate.get("debug_only") is not True
            or gate.get("capture_authorized") is not False
            or gate.get("formal_r0_authorized") is not False
            or gate.get("formal_authorized") is not False
            or gate.get("duration_seconds") != 180
            or gate.get("sample_period_seconds") != 1
            or gate.get("minimum_samples_per_stream") != 181
            or gate.get("directions_passed") != REQUIRED_DIRECTIONS
            or set((gate.get("directions") or {})) != set(REQUIRED_DIRECTIONS)
            or not all((gate["directions"][key] or {}).get("passed") is True for key in REQUIRED_DIRECTIONS)
            or gate.get("database_counter_missing_samples") != 0
            or gate.get("database_counter_timeout_count") != 0
            or gate.get("all_tolerances_passed") is not True
            or gate.get("both_directions_same_version_verified") is not True
            or gate.get("authorization_reuse_verified_absent") is not True
            or gate.get("ongoing_rolling_window_required") is not True
            or gate.get("ongoing_monitor_required") is not True
            or gate.get("concurrent_capture_r0_or_debug_eligible_for_main_agent_decision") is not True
            or gate.get("serial_fallback_required") is not False
            or set(closure) != {"A_solo", "A_paired", "B_solo", "B_paired"}
            or not all(isinstance(value, str) and HEX64.fullmatch(value) for value in closure.values())
            or not isinstance(raw_artifacts, dict) or len(raw_artifacts) != 1
            or not all(isinstance(key, str) and key.endswith(".raw_evidence.json")
                       and isinstance(value, str) and HEX64.fullmatch(value)
                       for key, value in raw_artifacts.items())
            or captured.tzinfo is None or expires.tzinfo is None
            or expires <= now or expires <= captured
            or (expires - captured).total_seconds() > 900):
        raise AggregateError(label + " gate is stale, failed, incomplete, or relaxed")
    return gate, EXPECTED_PAIRS[pair], expires


def aggregate(contract_path, gate_paths, output):
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    verify_static_contract(contract)
    now = datetime.now(timezone.utc)
    gates = {}; expiries = []
    for path, label in gate_paths:
        gate, key, expires = validate_gate(path, label, now)
        if key in gates:
            raise AggregateError("duplicate pair gate")
        gates[key] = {"path": str(Path(path).resolve()), "sha256": sha256(path), "evidence": gate}
        expiries.append(expires)
    if set(gates) != set(EXPECTED_PAIRS.values()):
        raise AggregateError("all three pair gates are required")
    collector_hashes = sorted({row["evidence"].get("collector_sha256") for row in gates.values()},
                              key=lambda value: str(value))
    if not all(isinstance(value, str) and HEX64.fullmatch(value)
               for value in collector_hashes):
        raise AggregateError("collector identity differs")
    result = {
        "schema_version": "mysql_job_v17_three_pair_dynamic_gate_aggregate_v1",
        "status": "PASS_ALL_THREE_PAIRWISE_DYNAMIC_GATES_NOT_LAUNCH_AUTHORITY",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "host": "node04",
        "pairs": gates, "pair_count": 3,
        "contract_path": str(Path(contract_path).resolve()),
        "contract_sha256": sha256(contract_path),
        "captured_at_utc": now.isoformat(),
        "expires_at_utc": min(expiries).astimezone(timezone.utc).isoformat(),
        "fresh_actual_readback_exact_match_required": True,
        "ongoing_rolling_monitor_required": True,
        "serial_fallback_authorized": False,
        "concurrent_debug_eligible_for_main_agent_decision": True,
        "formal_launch_authorized": False, "formal10_authorized": False,
        "accepted_round_authorized": False, "qualification_credit": 0,
        "r0_credit": 0, "candidate_credit": 0, "top5_replay_credit": 0,
        "accepted_rounds_credit": 0, "remote_actions": 0,
        "collector_sha256_set": collector_hashes,
    }
    output = Path(output)
    if output.exists() or output.is_symlink() or output.with_name(output.name + ".sha256").exists():
        raise AggregateError("aggregate output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(result); output.write_bytes(payload); output.chmod(0o400)
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.write_text(sha_bytes(payload) + "  " + output.name + "\n", encoding="ascii"); sidecar.chmod(0o400)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--job-tpch-gate", type=Path, required=True)
    parser.add_argument("--job-tpcc-gate", type=Path, required=True)
    parser.add_argument("--tpch-tpcc-gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate(args.contract, [(args.job_tpch_gate, "JOB/TPCH"), (args.job_tpcc_gate, "JOB/TPCC"), (args.tpch_tpcc_gate, "TPCH/TPCC")], args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (AggregateError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
