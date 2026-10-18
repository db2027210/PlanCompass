#!/usr/bin/env python3
"""Materialize one short-lived, zero-credit JOB post-R0 debug launch."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any, Mapping

TARGET_ID = "659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef"
TARGET_MANIFEST_SHA256 = "f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406"
SUPPORT_ID = "ec061f7b186af215e14daaaac64333fa3e88c8f934f78fe055d1f3ef091ace0b"
SUPPORT_MANIFEST_SHA256 = "1117b36cc27c5dbb4d69b26420240d3fbaf8b0d20b8e97ef6ba5490a87bc0438"
DISPATCH_SHA256 = "a0b0200c0dbca23350840a8c11af376f9cab30f4ca09fd7944ea4a5ef0cb3a01"
SUPPORT_PREDECESSOR_ID = "6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74"
SUPPORT_PREDECESSOR_MANIFEST_SHA256 = "5c5e59098304942800ef44dc17c777ffa6df0a81261a275b526a9737474c77ba"
PARAMETERS = [
    "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
    "memory_temptable_row_cost",
]


class MaterializerError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MaterializerError(f"invalid input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    sidecar = Path(str(path) + ".sha256")
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.read_text().strip() != hashlib.sha256(canonical(value)).hexdigest():
        raise MaterializerError(f"sidecar differs: {path}")
    return value


def verify_bundle(path: Path, bundle_id: str, manifest_sha: str) -> Path:
    resolved = path.resolve()
    manifest = resolved / "bundle_manifest.json"
    if path.is_symlink() or resolved.name != bundle_id or manifest.is_symlink() or not manifest.is_file():
        raise MaterializerError("bundle path/identity differs")
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != manifest_sha:
        raise MaterializerError("bundle manifest differs")
    return resolved


def build_materials(
    *, successor: Path, target: Path, support: Path, fresh_r0: Mapping[str, Any],
    handoff: Mapping[str, Any], handoff_path: Path, gate: Mapping[str, Any], baseline_path: Path,
    output_directory: Path, ttl_seconds: int, now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    attempt = fresh_r0.get("attempt_token")
    if (
        fresh_r0.get("schema_version") != "mysql_job_distributed_v2_fresh_r0_evidence_v1"
        or fresh_r0.get("status") != "PASS_FRESH_R0"
        or fresh_r0.get("bundle_id") != TARGET_ID
        or fresh_r0.get("accepted_rounds_credit") != 0
        or fresh_r0.get("accepted_round_authorized") is not False
        or fresh_r0.get("candidate_credit") != 0
        or fresh_r0.get("top5_replay_credit") != 0
        or handoff.get("schema_version") != "mysql_job_distributed_v2_same_live_planner_handoff_v1"
        or handoff.get("status") != "READY_DEBUG_SAME_LIVE_PLANNER"
        or handoff.get("stage") != "debug"
        or handoff.get("attempt_token") != attempt
        or handoff.get("planner_identity") != fresh_r0.get("planner_identity")
        or handoff.get("accepted_round_authorized") is not False
        or handoff.get("formal10_authorized") is not False
        or gate.get("schema_version") != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or gate.get("status") != "PASS"
        or gate.get("attempt_token") != attempt
        or gate.get("formal_scope") != "mysql_only"
        or gate.get("cross_engine_comparison_prohibited") is not True
        or gate.get("accepted_round_authorized") is not False
    ):
        raise MaterializerError("post-R0 inputs differ")
    template = json.loads((target / "predecessor/config.json").read_text(encoding="utf-8"))
    if template.get("cost_parameters", {}).get("active") != PARAMETERS:
        raise MaterializerError("frozen four-parameter order differs")
    config = copy.deepcopy(template)
    config.update(
        {
            "schema_version": "mysql_job_post_r0_debug_current_config_v1",
            "attempt_token": attempt,
            "target_bundle_id": TARGET_ID,
            "target_bundle_path": str(target),
            "target_manifest_sha256": TARGET_MANIFEST_SHA256,
            "support_bundle_id": SUPPORT_ID,
            "support_bundle_path": str(support),
            "support_manifest_sha256": SUPPORT_MANIFEST_SHA256,
            "shared_dispatch_sha256": DISPATCH_SHA256,
            "qualification_support_predecessor_bundle_id": SUPPORT_PREDECESSOR_ID,
            "qualification_support_predecessor_manifest_sha256": SUPPORT_PREDECESSOR_MANIFEST_SHA256,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "accepted_round_authorized": False,
            "outer_execution_authorized": True,
            "debug_only": True,
            "formal_launch_authorized": False,
            "commit_best": False,
            "exact_service_module": str(successor / "campaign/mysql_job_post_r0_exact_services_v1.py"),
            "fresh_r0_sha256": hashlib.sha256(canonical(fresh_r0)).hexdigest(),
            "debug_handoff_sha256": hashlib.sha256(canonical(handoff)).hexdigest(),
            "dynamic_gate_sha256": hashlib.sha256(canonical(gate)).hexdigest(),
        }
    )
    handoff_file_sha = (
        hashlib.sha256(handoff_path.read_bytes()).hexdigest()
        if handoff_path.is_file()
        else hashlib.sha256(canonical(handoff)).hexdigest()
    )
    runtime = json.loads((target / "distributed_runtime_contract.json").read_text(encoding="utf-8"))
    remote_bundle = str(runtime["bundle_remote_path_rule"]).replace("<bundle_id>", TARGET_ID)
    config["same_live_planner_authority"] = {
        "path": str(handoff_path.resolve()), "sha256": handoff_file_sha,
        "attempt_token": attempt, "bundle_id": TARGET_ID,
    }
    config["distributed_v2"] = {
        "local_bundle_path": str(target), "remote_bundle_path": remote_bundle,
        "bundle_manifest_sha256": TARGET_MANIFEST_SHA256,
        "remote_python": runtime["toolchain"]["python_runtime"],
    }
    config["output"] = {"directory": str(output_directory), "verbose": True}
    config["production"] = {
        "service_factory": "mysql_job_post_r0_exact_services_v1:build",
        "launch_authorized": True,
        "debug_only": True,
    }
    config["formal_outer_protocol"].update(
        {
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 5,
            "max_outer_attempts": 1,
            "accepted_shrink_count": 0,
            "rejected_count": 0,
            "target_accepted_shrinks": 0,
            "formal_checkpoint_path": str(output_directory / "formal_outer_checkpoint.json"),
            "formal_real_r0_checkpoint_path": str(output_directory / "formal_real_r0.json"),
            "lane_lease_path": str(output_directory / "job_debug_lane.lock"),
        }
    )
    token = secrets.token_hex(32)
    authority = {
        "schema_version": "mysql_job_post_r0_debug_authority_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "attempt_token": attempt,
        "planner_identity": fresh_r0["planner_identity"],
        "current_config_sha256": hashlib.sha256(canonical(config)).hexdigest(),
        "main_agent_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "not_before_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "debug_round_authorized": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "support_bundle_id": SUPPORT_ID,
        "support_manifest_sha256": SUPPORT_MANIFEST_SHA256,
        "shared_dispatch_sha256": DISPATCH_SHA256,
    }
    launch = {
        "schema_version": "mysql_job_post_r0_debug_launch_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "argv": [
            sys_executable(), str(successor / "campaign/mysql_job_post_r0_debug_campaign_v1.py"),
            "--config", str(output_directory / "current_config.json"),
            "--baseline-validation", str(baseline_path.resolve()),
            "--main-agent-authority", str(output_directory / "main_agent_authority.json"),
            "--main-agent-token-file", str(output_directory / "main_agent_token"),
            "--output", str(output_directory / "optimizer_result.json"),
        ],
        "token_inline": False,
    }
    return config, authority, token, launch


def sys_executable() -> str:
    import sys
    return str(Path(sys.executable).resolve())


def write_json(path: Path, value: Mapping[str, Any], mode: int = 0o444) -> None:
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise MaterializerError(f"refusing to overwrite {path}")
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    path.write_text(payload, encoding="utf-8")
    sidecar.write_text(hashlib.sha256(canonical(value)).hexdigest() + "\n", encoding="utf-8")
    os.chmod(path, mode); os.chmod(sidecar, 0o444)


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("successor-bundle", "target-bundle", "support-bundle", "fresh-r0", "debug-handoff", "dynamic-gate", "baseline-validation", "output-directory"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=1800)
    args = parser.parse_args()
    successor = args.successor_bundle.resolve()
    target = verify_bundle(args.target_bundle, TARGET_ID, TARGET_MANIFEST_SHA256)
    support = verify_bundle(args.support_bundle, SUPPORT_ID, SUPPORT_MANIFEST_SHA256)
    if successor.is_symlink() or not (successor / "bundle_manifest.json").is_file():
        raise MaterializerError("successor bundle differs")
    fresh, handoff, gate = load(args.fresh_r0), load(args.debug_handoff), load(args.dynamic_gate)
    load(args.baseline_validation)
    output = args.output_directory
    if output.exists() or output.is_symlink():
        raise MaterializerError("output directory already exists")
    output.mkdir(parents=True, mode=0o700)
    config, authority, token, launch = build_materials(
        successor=successor, target=target, support=support, fresh_r0=fresh,
        handoff=handoff, handoff_path=args.debug_handoff, gate=gate, baseline_path=args.baseline_validation,
        output_directory=output.resolve(), ttl_seconds=args.ttl_seconds,
    )
    write_json(output / "current_config.json", config)
    write_json(output / "main_agent_authority.json", authority)
    token_path = output / "main_agent_token"
    token_path.write_text(token + "\n", encoding="utf-8"); os.chmod(token_path, 0o600)
    write_json(output / "launch.json", launch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
