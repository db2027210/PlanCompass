#!/usr/bin/env python3
"""Bind read-only TPC-C same-live evidence to its current materials file.

This program never opens a network connection, invokes Docker, reads /proc, or
starts a workload.  The main agent supplies files captured remotely with
read-only commands.  The resulting authority is an immutable *continuation
attestation*, not permission for qualification, capture, R0, replay, or an
accepted round.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any


class MaterializationError(RuntimeError):
    pass


IDENTITY_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_RESOURCE_KEYS = frozenset(
    {"qps", "throughput", "transactions_per_second"}
)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False)
        + "\n"
    ).encode("utf-8")


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def regular(path: Path, label: str) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError as exc:
        raise MaterializationError(f"{label} is absent") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise MaterializationError(f"{label} must be a regular file")
    return resolved


def object_file(path: Path, label: str, *, docker_inspect: bool = False) -> tuple[Path, dict[str, Any]]:
    source = regular(path, label)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"{label} is not valid JSON") from exc
    if docker_inspect and isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        raise MaterializationError(f"{label} must contain one JSON object")
    return source, value


def verify_sidecar(path: Path, label: str) -> str:
    sidecar = regular(Path(str(path) + ".sha256"), f"{label} sidecar")
    actual = digest(path)
    expected = f"{actual}  {path.name}\n"
    if sidecar.read_text(encoding="ascii") != expected:
        raise MaterializationError(f"{label} sidecar differs")
    return actual


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterializationError(f"{label} must be a nonempty string")
    return value


def require_identifier(value: Any, label: str) -> str:
    identifier = require_string(value, label)
    if not IDENTITY_RE.fullmatch(identifier):
        raise MaterializationError(f"{label} must be a full lowercase container ID")
    return identifier


def reject_qps_resource_input(value: Any, location: str) -> None:
    """Reject QPS recursively; it is not valid evidence for this authority."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_RESOURCE_KEYS:
                raise MaterializationError(
                    f"QPS/throughput is not allowed in {location}"
                )
            reject_qps_resource_input(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_qps_resource_input(item, f"{location}[{index}]")


def validate_materials(materials: dict[str, Any]) -> tuple[str, str, list[str], str]:
    if materials.get("schema_version") != "mysql_tpcc_v13_dedicated_materials_v2":
        raise MaterializationError("materials schema differs")
    if materials.get("status") != "ISSUED_NOT_EXECUTED":
        raise MaterializationError("materials status is not an unexecuted authority")
    if materials.get("formal_scope") != "mysql_only":
        raise MaterializationError("materials scope differs")
    for key in (
        "qualification_only", "formal_launch_authorized", "formal_r0_authorized",
        "accepted_round_authorized", "cross_engine_comparison_prohibited",
        "postgresql_mutation_prohibited",
    ):
        expected = key in {"qualification_only", "cross_engine_comparison_prohibited", "postgresql_mutation_prohibited"}
        if materials.get(key) is not expected:
            raise MaterializationError(f"materials {key} differs")
    attempt_id = require_string(materials.get("attempt_id"), "materials attempt_id")
    bundle_id = require_identifier(materials.get("bundle_id"), "materials bundle_id")
    stage_order = materials.get("stage_order")
    stages = materials.get("stages")
    if not isinstance(stage_order, list) or not stage_order or not all(isinstance(x, str) for x in stage_order):
        raise MaterializationError("materials stage_order differs")
    if len(set(stage_order)) != len(stage_order) or not isinstance(stages, dict) or set(stages) != set(stage_order):
        raise MaterializationError("materials stages do not close stage_order")
    image_id = require_string(materials.get("mysql_image_id"), "materials mysql_image_id")
    return attempt_id, bundle_id, stage_order, image_id


def inspect_identity(row: dict[str, Any], label: str, expected_image: str | None) -> dict[str, Any]:
    container_id = require_identifier(row.get("Id"), f"{label} inspect Id")
    state = row.get("State")
    if not isinstance(state, dict) or state.get("Running") is not True:
        raise MaterializationError(f"{label} inspect is not running")
    pid = state.get("Pid")
    if not isinstance(pid, int) or pid <= 1:
        raise MaterializationError(f"{label} inspect PID differs")
    if not require_string(state.get("StartedAt"), f"{label} inspect StartedAt"):
        raise AssertionError("unreachable")
    if expected_image is not None and row.get("Image") != expected_image:
        raise MaterializationError(f"{label} inspect image differs from materials")
    if not isinstance(row.get("Config"), dict) or not isinstance(row.get("HostConfig"), dict):
        raise MaterializationError(f"{label} inspect config closure differs")
    if not isinstance(row.get("Mounts"), list) or not row["Mounts"]:
        raise MaterializationError(f"{label} inspect mounts are absent")
    return {"container_id": container_id, "pid": pid}


def validate_runtime(runtime: dict[str, Any], attempt_id: str, identities: dict[str, dict[str, Any]]) -> None:
    if runtime.get("schema_version") != "mysql_tpcc_same_live_runtime_readback_v1":
        raise MaterializationError("runtime readback schema differs")
    if runtime.get("capture_mode") != "remote_read_only" or runtime.get("attempt_id") != attempt_id:
        raise MaterializationError("runtime readback provenance or attempt differs")
    if runtime.get("old_workload_pids") != []:
        raise MaterializationError("runtime readback reports an old workload")
    for label, identity in identities.items():
        observed = runtime.get(label)
        if not isinstance(observed, dict):
            raise MaterializationError(f"runtime {label} identity is absent")
        if observed.get("container_id") != identity["container_id"] or observed.get("pid") != identity["pid"]:
            raise MaterializationError(f"runtime {label} container/PID differs")
        ticks = observed.get("process_start_ticks")
        if not isinstance(ticks, int) or ticks <= 0:
            raise MaterializationError(f"runtime {label} start ticks differ")
        cgroup_version = observed.get("cgroup_version")
        if cgroup_version not in (1, 2):
            raise MaterializationError(
                f"runtime {label} cgroup_version must be 1 or 2"
            )
        if not require_string(
            observed.get("cgroup_path"), f"runtime {label} cgroup_path"
        ):
            raise AssertionError("unreachable")


def validate_ledger(ledger: dict[str, Any], attempt_id: str, bundle_id: str,
                    materials_sha256: str) -> None:
    """Bind an as-yet-unsealed remote ledger without inventing its schema.

    No repository sample defines a current remote phase-ledger schema.  It is
    unsafe to reinterpret the materials' local stage list as one.  This
    successor therefore records the supplied ledger by content hash only,
    while rejecting any explicit contradicting identity or authorization.
    A later sealed ledger-schema authority is required before its phase
    semantics can be used for qualification or capture.
    """
    reject_qps_resource_input(ledger, "phase ledger")
    for key, expected in (
        ("attempt_id", attempt_id),
        ("bundle_id", bundle_id),
        ("materials_sha256", materials_sha256),
    ):
        observed = ledger.get(key)
        if observed is not None and observed != expected:
            raise MaterializationError(f"phase ledger {key} differs")
    for key in (
        "qualification_started", "capture_started", "formal_launch_authorized",
        "accepted_round_authorized",
    ):
        if ledger.get(key) is True:
            raise MaterializationError(f"phase ledger {key} is unexpectedly true")
    if ledger.get("old_workload_pids") not in (None, []):
        raise MaterializationError("phase ledger reports an old workload")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--db-inspect", type=Path, required=True)
    parser.add_argument("--client-inspect", type=Path, required=True)
    parser.add_argument("--runtime-readback", type=Path, required=True)
    parser.add_argument("--phase-ledger", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)

    materials_path, materials = object_file(args.materials, "materials")
    materials_sha256 = verify_sidecar(materials_path, "materials")
    attempt_id, bundle_id, stage_order, mysql_image = validate_materials(materials)
    db_path, db_row = object_file(args.db_inspect, "database inspect", docker_inspect=True)
    client_path, client_row = object_file(args.client_inspect, "client inspect", docker_inspect=True)
    runtime_path, runtime = object_file(args.runtime_readback, "runtime readback")
    ledger_path, ledger = object_file(args.phase_ledger, "phase ledger")
    for label, value in (
        ("materials", materials), ("database inspect", db_row),
        ("client inspect", client_row), ("runtime readback", runtime),
    ):
        reject_qps_resource_input(value, label)
    identities = {
        "database": inspect_identity(db_row, "database", mysql_image),
        "client": inspect_identity(client_row, "client", None),
    }
    validate_runtime(runtime, attempt_id, identities)
    validate_ledger(ledger, attempt_id, bundle_id, materials_sha256)

    inputs = {
        "materials": materials_sha256,
        "db_inspect": digest(db_path),
        "client_inspect": digest(client_path),
        "runtime_readback": digest(runtime_path),
        "phase_ledger": digest(ledger_path),
    }
    authority = {
        "schema_version": "mysql_tpcc_v13_current_same_live_continuation_authority_v2",
        "status": "PASS_READ_ONLY_SAME_LIVE_CONTINUATION_NOT_QUALIFIED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_id": attempt_id,
        "bundle_id": bundle_id,
        "materials_sha256": materials_sha256,
        "same_live_identity": identities,
        "materials_stage_order": stage_order,
        "phase_ledger_bound_as_opaque_remote_readonly_evidence": True,
        "phase_ledger_semantics_verified": False,
        "typed_capture_evidence_verified": False,
        "typed_capture_evidence_required_before_capture_authority": True,
        "qps_resource_input_prohibited": True,
        "inputs": inputs,
        "qualification_authorized": False,
        "capture_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    authority_id = digest_bytes(canonical(authority))
    authority["authority_id"] = authority_id
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / authority_id
    if target.exists() or target.is_symlink():
        existing_path, existing = object_file(target / "same_live_continuation_authority.json", "existing authority")
        if existing != authority:
            raise MaterializationError("existing content-addressed authority differs")
        print(json.dumps({"status": "PASS_IDEMPOTENT_READ_ONLY_SAME_LIVE_CONTINUATION", "authority": str(existing_path), "authority_id": authority_id}, sort_keys=True))
        return 0
    stage = output_root / f".stage-{authority_id}-{secrets.token_hex(6)}"
    stage.mkdir(mode=0o700)
    try:
        payload = canonical(authority)
        (stage / "same_live_continuation_authority.json").write_bytes(payload)
        (stage / "same_live_continuation_authority.json.sha256").write_text(
            f"{digest_bytes(payload)}  same_live_continuation_authority.json\n",
            encoding="ascii",
        )
        os.rename(stage, target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({"status": authority["status"], "authority": str(target), "authority_id": authority_id, "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MaterializationError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"NOT_READY: {exc}\n")
        raise SystemExit(2)
