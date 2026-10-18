#!/usr/bin/env python3
"""Execute and seal one same-live JOB v13 fresh R0.

The runner accepts only a PASS four-flow gate and the 15-minute
``READY_FRESH_R0_SAME_LIVE_PLANNER`` handoff emitted by the sealed v13
authority.  It reuses the bootstrap database/client and the shared JOB/XP
collector.  R0 is calibration only and always carries zero accepted credit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping


TARGET_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
HEX64 = re.compile(r"[0-9a-f]{64}$")


class R0Error(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path: Path, label: str) -> dict[str, Any]:
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise R0Error("%s must be a regular file" % label)
    expected = "%s  %s\n" % (sha256(path), path.name)
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.read_text(encoding="ascii") != expected:
        raise R0Error("%s sidecar differs" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise R0Error("%s must be an object" % label)
    return value


def read_token(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise R0Error("main-agent token must be a regular 0600 file")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise R0Error("main-agent token differs")
    return token


def inspect_identity(bootstrap: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    database = bootstrap.get("database") or {}
    client = bootstrap.get("client") or {}
    if (
        bootstrap.get("schema_version") != "mysql_job_dedicated_daemon_bootstrap_evidence_v1"
        or bootstrap.get("status") != "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY"
        or bootstrap.get("bundle_id") != TARGET_ID
        or bootstrap.get("formal_scope") != "mysql_only"
        or bootstrap.get("cross_engine_comparison_prohibited") is not True
        or bootstrap.get("postgresql_mutation_prohibited") is not True
        or bootstrap.get("qualification_only") is not True
        or bootstrap.get("formal_r0_authorized") is not False
        or bootstrap.get("accepted_round_authorized") is not False
        or HEX64.fullmatch(str(database.get("container_id") or "")) is None
        or int(database.get("pid") or 0) <= 1
        or HEX64.fullmatch(str(client.get("container_id") or "")) is None
        or int(client.get("pid") or 0) <= 1
    ):
        raise R0Error("bootstrap same-live identity differs")
    return dict(database), dict(client)


def inspect_config_identity(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical({
        "Config": row.get("Config"),
        "HostConfig": row.get("HostConfig"),
        "Mounts": row.get("Mounts"),
        "Image": row.get("Image"),
        "Created": row.get("Created"),
    })).hexdigest()


def validate_inputs(
    bootstrap: Mapping[str, Any],
    gate: Mapping[str, Any],
    handoff: Mapping[str, Any],
    command: Mapping[str, Any],
    authority: Mapping[str, Any],
    token: str,
    command_sha256: str | None = None,
) -> None:
    database, client = inspect_identity(bootstrap)
    attempt = bootstrap["attempt_token"]
    now = datetime.now(timezone.utc)
    try:
        expiry = datetime.fromisoformat(str(handoff["expires_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise R0Error("handoff expiry differs") from exc
    planner = handoff.get("planner_identity") or {}
    client_identity = handoff.get("client_identity") or {}
    if (
        gate.get("schema_version") != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or gate.get("status") != "PASS"
        or gate.get("scenario") != "job_memory"
        or gate.get("host_id") != "node02"
        or gate.get("attempt_token") != attempt
        or gate.get("formal_scope") != "mysql_only"
        or gate.get("cross_engine_comparison_prohibited") is not True
        or gate.get("all_tolerances_passed") is not True
        or gate.get("ongoing_monitor_required") is not True
        or gate.get("database_counter_missing_samples") != 0
        or gate.get("database_counter_timeout_count") != 0
        or gate.get("accepted_round_authorized") is not False
        or handoff.get("schema_version") != "mysql_job_distributed_v2_same_live_planner_handoff_v1"
        or handoff.get("status") != "READY_FRESH_R0_SAME_LIVE_PLANNER"
        or handoff.get("stage") != "fresh_r0"
        or handoff.get("attempt_token") != attempt
        or handoff.get("bundle_id") != TARGET_ID
        or handoff.get("formal_scope") != "mysql_only"
        or handoff.get("cross_engine_comparison_prohibited") is not True
        or handoff.get("postgresql_mutation_prohibited") is not True
        or handoff.get("scenario") != "job_memory"
        or handoff.get("host_id") != "node02"
        or handoff.get("dynamic_gate_sha256") != hashlib.sha256(canonical(gate)).hexdigest()
        or handoff.get("fresh_r0_authorized") is not True
        or handoff.get("formal10_authorized") is not False
        or handoff.get("target_accepted_rounds") != 0
        or handoff.get("outer_acceptance_authorized") is not False
        or handoff.get("new_campaign_or_planner_container_prohibited") is not True
        or handoff.get("qualification_container_adoption_required") is not True
        or handoff.get("ongoing_rolling_gate_required") is not True
        or handoff.get("capture_or_r0_or_debug_countable_as_accepted") is not False
        or handoff.get("accepted_round_authorized") is not False
        or expiry.tzinfo is None
        or now >= expiry.astimezone(timezone.utc)
        or planner.get("container_id") != database["container_id"]
        or int(planner.get("pid") or 0) != int(database["pid"])
        or planner.get("database_inspect_config_sha256")
        != inspect_config_identity(database.get("inspect") or {})
        or client_identity.get("container_id") != client["container_id"]
        or int(client_identity.get("pid") or 0) != int(client["pid"])
        or client_identity.get("inspect_config_sha256")
        != inspect_config_identity(client.get("inspect") or {})
        or command.get("schema_version") != "mysql_algorithm_c_debug_qualification_command_v1"
        or command.get("bundle_id") != TARGET_ID
        or command.get("mode") != "qualification_solo"
        or command.get("debug_only") is not True
        or command.get("formal_r0_authorized") is not False
        or command.get("accepted_round_authorized") is not False
        or (command.get("client_container") or {}).get("container_id") != client["container_id"]
        or authority.get("schema_version") != "mysql_algorithm_c_main_agent_qualification_authority_v1"
        or authority.get("bundle_id") != TARGET_ID
        or authority.get("mode") != "qualification_solo"
        or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
        or authority.get("main_agent_token_sha256") != hashlib.sha256(token.encode("utf-8")).hexdigest()
        or (command_sha256 is not None and authority.get("command_spec_sha256") != command_sha256)
        or float(authority.get("not_before_epoch") or 0) > now.timestamp()
        or float(authority.get("expires_epoch") or 0) <= now.timestamp()
        or authority.get("accepted_round_authorized") is not False
    ):
        raise R0Error("fresh-R0 gate/handoff/command authority differs")


def build_evidence(
    bootstrap: Mapping[str, Any],
    gate: Mapping[str, Any],
    raw: Mapping[str, Any],
    raw_sha256: str,
) -> dict[str, Any]:
    database, _ = inspect_identity(bootstrap)
    summary = raw.get("summary") or {}
    resources = summary.get("resource_utilization") or {}
    samples = raw.get("samples")
    initial = raw.get("initial_live_readback") or {}
    initial_container = initial.get("container") or {}
    if (
        raw.get("schema_version") != "mysql_algorithm_c_debug_qualification_evidence_v2"
        or raw.get("status") != "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY"
        or raw.get("mode") != "qualification_solo"
        or raw.get("bundle_id") != TARGET_ID
        or raw.get("scenario") != "job_memory"
        or raw.get("hostname") != "node02"
        or raw.get("debug_only") is not True
        or raw.get("formal_r0_authorized") is not False
        or raw.get("formal_authorized") is not False
        or raw.get("duration_seconds") != 180
        or raw.get("minimum_samples") != 181
        or not isinstance(samples, list)
        or len(samples) < 181
        or int(summary.get("sample_count") or 0) < 181
        or float(summary.get("elapsed_seconds") or 0) < 180.0
        or initial_container.get("container_id") != database["container_id"]
        or int(initial_container.get("pid") or 0) != int(database["pid"])
        or raw.get("accepted_round_authorized") is not False
    ):
        raise R0Error("fresh-R0 raw evidence differs")
    utilization = {
        "cpu": float(resources.get("cpu_usage_ratio")),
        "memory": float(resources.get("memory_utilization_ratio")),
        "io": float(resources.get("device_utilization_ratio")),
    }
    profile_pass = (
        0.30 <= utilization["cpu"] <= 0.50
        and 0.85 <= utilization["memory"] <= 0.95
        and 0.30 <= utilization["io"] <= 0.50
    )
    if not profile_pass:
        raise R0Error("fresh-R0 resource profile differs")
    planner = {
        "container_name": (bootstrap.get("database") or {}).get("inspect", {}).get("Name"),
        "container_id": database["container_id"],
        "pid": database["pid"],
        "datadir": (bootstrap.get("copy") or {}).get("destination"),
        "database_inspect_config_sha256": inspect_config_identity(
            database.get("inspect") or {}
        ),
    }
    return {
        "schema_version": "mysql_job_distributed_v2_fresh_r0_evidence_v1",
        "status": "PASS_FRESH_R0",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_token": bootstrap["attempt_token"],
        "bundle_id": TARGET_ID,
        "planner_identity": planner,
        "dynamic_gate_sha256": hashlib.sha256(canonical(gate)).hexdigest(),
        "raw_qualification_sha256": raw_sha256,
        "r0_profile_gate_passed": True,
        "database_counter_gate_passed": True,
        "database_counter_missing_samples": 0,
        "database_counter_timeout_count": 0,
        "stable_seconds": float(summary["elapsed_seconds"]),
        "sample_count": int(summary["sample_count"]),
        "utilization": utilization,
        "original_gradient_inputs_created": False,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "accepted_round_authorized": False,
    }


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise R0Error("refusing to overwrite fresh-R0 evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name), encoding="ascii")
    sidecar.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-dispatch", type=Path, required=True)
    parser.add_argument("--shared-dispatch-sha256", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--dynamic-gate", type=Path, required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--command-spec", type=Path, required=True)
    parser.add_argument("--main-agent-authority", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--database-container", required=True)
    parser.add_argument("--client-container", required=True)
    parser.add_argument("--mysql-defaults-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    dispatch = args.shared_dispatch.resolve(strict=True)
    if HEX64.fullmatch(args.shared_dispatch_sha256) is None or sha256(dispatch) != args.shared_dispatch_sha256:
        raise R0Error("shared dispatch identity differs")
    if args.bundle.resolve(strict=True).name != TARGET_ID:
        raise R0Error("JOB target bundle differs")
    bootstrap = load_sealed(args.bootstrap, "bootstrap")
    gate = load_sealed(args.dynamic_gate, "dynamic gate")
    handoff = load_sealed(args.handoff, "fresh-R0 handoff")
    command = load_sealed(args.command_spec, "command spec")
    authority = load_sealed(args.main_agent_authority, "main-agent authority")
    token = read_token(args.main_agent_token_file)
    validate_inputs(bootstrap, gate, handoff, command, authority, token, sha256(args.command_spec))
    raw_path = args.output.with_name(args.output.name + ".raw_qualification.json")
    command_line = [
        sys.executable, str(dispatch), "--scenario-backend", "job",
        "--backend-path", str(args.bundle / "qualification/mysql_job_distributed_live_readback_qualification_v2.py"),
        "--qualification-solo", "--bundle", str(args.bundle),
        "--container", args.database_container,
        "--mysql-client-container", args.client_container,
        "--mysql-binary-in-client", "/usr/bin/mysql",
        "--mysql-defaults-file", str(args.mysql_defaults_file),
        "--output", str(raw_path),
        "--main-agent-token-file", str(args.main_agent_token_file),
        "--main-agent-authority", str(args.main_agent_authority),
        "--command-spec", str(args.command_spec),
    ]
    completed = subprocess.run(command_line, check=False, timeout=600)
    if completed.returncode != 0:
        raise R0Error("shared fresh-R0 collector failed rc=%d" % completed.returncode)
    raw = load_sealed(raw_path, "raw fresh-R0 qualification")
    result = build_evidence(bootstrap, gate, raw, sha256(raw_path))
    atomic_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired, R0Error) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
