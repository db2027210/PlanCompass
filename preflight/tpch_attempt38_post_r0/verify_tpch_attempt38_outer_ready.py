#!/usr/bin/env python3
"""Read-only fail-closed readiness check for TPC-H attempt38 outer search."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict


EXPECTED = {
    "parameter_optimizer.py": "81c01808e08277c1d2f4e047323b1bf469141de9a40423895d5965e860f1a8ba",
    "formal_pg_engine.py": "b3cd68489fdfefc00e11ce569ccfa4e327919f20f0354b74746c44d4f6012430",
    "formal_pg_point_runner.py": "9b655402d82ede45a7d509cd81fdf9eb7c79c828c066b1b117bdc8fef2fd19ae",
}
EXPECTED_TABLE_ARTIFACT_SHA256 = (
    "3aa085b7f920c27d518954d9316a096074778720a7cb7cf1351b10a59f08d0e9"
)
EXPECTED_TABLE_CANONICAL_SHA256 = (
    "a2fca76dd0ad04758ceebebb29a675ec43f1e52c5e820efca4b25a5fbaeaf07b"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def finite_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    args = parser.parse_args()

    stage = args.stage_root.resolve()
    workspace = args.workspace.resolve()
    authorization_path = args.authorization.resolve()
    protocol_path = stage / "tpch_attempt38_post_r0_outer_protocol.json"
    checkpoint_path = stage / "tpch_attempt38_outer_checkpoint.json"
    table_path = stage / "experience_table_T_tpch_memory_v2.json"

    protocol = read_json(protocol_path)
    checkpoint = read_json(checkpoint_path)
    authorization = read_json(authorization_path)
    table_artifact = read_json(table_path)

    require(protocol.get("r0_attempt") == 38, "protocol is not bound to attempt38")
    require(
        protocol.get("immutable_target_resource") == "memory",
        "target resource changed away from memory",
    )
    require(
        checkpoint.get("authorized_to_start") is True
        and checkpoint.get("phase") == "armed_after_r0_formal_pass",
        "checkpoint is not armed after formal R0 pass",
    )
    require(authorization.get("authorized") is True, "R0 authorization is false")
    require(authorization.get("r0_attempt") == 38, "R0 authorization attempt mismatch")
    require(
        authorization.get("target_resource") == "memory",
        "R0 authorization target mismatch",
    )
    for key in (
        "resource_gate_passed",
        "stability_gate_passed",
        "workload_contract_passed",
        "physical_boundary_passed",
    ):
        require(authorization.get(key) is True, f"R0 authorization gate failed: {key}")

    r0_binding = protocol.get("r0_binding") or {}
    require(r0_binding.get("outer_allowed") is True, "protocol R0 binding is not armed")
    require(
        r0_binding.get("gate_decision_sha256") == sha256(authorization_path),
        "R0 authorization SHA mismatch",
    )
    for path_key, sha_key in (
        ("resource_samples_path", "resource_samples_sha256"),
        ("run_manifest_path", "run_manifest_sha256"),
    ):
        bound_path = Path(str(r0_binding.get(path_key) or ""))
        require(bound_path.is_file(), f"missing bound R0 artifact: {path_key}")
        require(
            sha256(bound_path) == r0_binding.get(sha_key),
            f"bound R0 artifact SHA mismatch: {path_key}",
        )

    for name, expected_sha in EXPECTED.items():
        path = workspace / name
        require(path.is_file(), f"authoritative source missing: {path}")
        require(sha256(path) == expected_sha, f"authoritative source SHA mismatch: {name}")

    require(
        sha256(table_path) == EXPECTED_TABLE_ARTIFACT_SHA256,
        "experience-table artifact SHA mismatch",
    )
    canonical = hashlib.sha256(
        json.dumps(
            table_artifact["table"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    require(canonical == EXPECTED_TABLE_CANONICAL_SHA256, "experience-table canonical SHA mismatch")
    active = table_artifact.get("active_params") or []
    require(len(active) == 7 and len(set(active)) == 7, "active parameter set is not exactly seven")
    require((1 << len(active)) - 1 == 127, "all-subset count is not 127")

    qps = protocol.get("authoritative_db_qps") or {}
    require(
        qps.get("source") == "pg_stat_statements_sum_calls_delta_per_second",
        "DB QPS source is not authoritative",
    )
    require(
        qps.get("query_completion_qps_substitution_allowed") is False,
        "query-completion QPS substitution is enabled",
    )
    outer = checkpoint.get("outer_progress") or {}
    require(outer.get("candidate_real_replay_count") == 0, "candidate replay counter is not zero")
    require(outer.get("candidate_real_replay_maximum") == 10, "candidate replay maximum is not ten")
    require(outer.get("accepted_shrink_count") == 0, "accepted-shrink counter is not zero")
    require(outer.get("accepted_shrink_target") == 10, "accepted-shrink target is not ten")
    startup = checkpoint.get("startup_baseline") or {}
    require(startup.get("count") == 0, "formal startup baseline already consumed")
    require(startup.get("candidate_budget_consumed") == 0, "startup baseline consumed candidate budget")

    physical = protocol.get("physical_docker_boundary") or {}
    require(physical.get("scope") == "owned_fresh_point_container_only", "physical scope is not Docker-only")
    require(physical.get("fresh_template_before_every_formal_point") is True, "fresh-point restore is disabled")
    require(physical.get("resource_measurement_scope") == "same Docker cgroup as PostgreSQL for that point", "resource scope is not point-container cgroup")
    for field in ("cpu_quota_cores", "memory_bytes", "io_read_bps", "io_write_bps"):
        require(finite_positive(physical.get(field)), f"invalid physical envelope field: {field}")

    print(
        json.dumps(
            {
                "status": "ready",
                "r0_attempt": 38,
                "target_resource": "memory",
                "subset_count": 127,
                "initial_real_replay_top_k": 5,
                "maximum_real_replay_top_k": 10,
                "accepted_shrink_target": 10,
                "db_qps_source": qps["source"],
                "physical_scope": physical["scope"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
