#!/usr/bin/env python3
"""Render and verify a fresh, short-lived TPC-C v13 qualification handoff.

This program is strictly offline.  It binds reviewed qualification and typed
capture materialization argv to the immutable v13 bundle, but it never runs
those argv, contacts a host/database, starts a container, or grants formal
launch authority.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shell"))
import mysql_tpcc_v6_bootstrap_spec as spec  # noqa: E402


BUNDLE_ID = "4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
BUNDLE_MANIFEST_SHA256 = (
    "2172eae52c4736ba1afbf4a1342f8e02d20db6fa771828839db14cc9e5a17e90"
)
INVALIDATED_D377 = (
    "d3772996017a15fca17ce94e71a348c3861c0f3a1ffee4f0ab60a033f3809adc"
)
BUNDLE = (
    ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v13" / BUNDLE_ID
)
OUT = ROOT / "artifacts/mysql_tpcc_v13_qualification_capture_materials_v1"
# The inherited v4 authority validates at most 30 minutes.  v13 records the
# effective fail-closed validity instead of inheriting v12's nominal 50-minute
# timestamp with an earlier hidden validation cutoff.
TTL = timedelta(minutes=30)

EXECUTION_SEQUENCE = (
    "verify_materials_unexpired_and_hash_closed_locally",
    "verify_remote_bundle_exact_closure_and_manifest_sha256",
    "prepare_physical_copy",
    "enforce_private_measurement_tmpfs_after_copy",
    "provision_random_attempt_password_before_config",
    "materialize_attempt_config_before_service_plan",
    "emit_service_plan_only",
    "main_agent_authorization_barrier_before_start",
    "start_containers_after_main_agent_authorization",
    "enforce_sealed_cgroup_v1_blkio_after_start_before_adopt",
    "adopt_same_live_services_after_blkio_readback",
    "prefault_zero_io_gate",
    "materialize_qualification_only",
    "execute_materialized_qualification_command",
    "atomically_request_qualification_stop_after_180s",
    "verify_qualification_gate_pass_before_typed_capture",
    "materialize_typed_capture_after_qualification_gate_only",
    "execute_materialized_typed_capture_command",
    "enable_and_readback_history_long_consumers_before_benchbase_start",
    "verify_real_owner_trace_lock_header_and_sensitive_sql_join_authority",
    "collect_exact_34_typed_bindings_and_l2_l3_without_qps",
    "capture_remains_zero_r0_and_zero_accepted_round_credit",
)

TYPED_AUTHORITY = {
    "performance_schema_prelaunch": {
        "consumers": [
            "events_statements_history_long",
            "events_waits_history_long",
        ],
        "enable_before_benchbase_process_start": True,
        "exact_yes_readback_required": True,
        "positive_history_long_sizes_required": True,
        "sealed_defaults_file_only": True,
        "defaults_file_symlink_prohibited": True,
    },
    "sensitive_stock_level_real_join": {
        "binding_id":
            "34:SensitiveStockLevel:"
            "SensitiveStockLevel/item-in-orderline-w1",
        "normalized_transaction_type": "StockLevel",
        "owner_trace_header_and_lock_required": True,
        "capture_token_and_manifest_hash_bound": True,
        "consumer_authority_hash_required": True,
        "sensitive_sql_sha256":
            "cd8fed7df71c5a681b001083397181804b185816225e68ae8449f8cc384d507e",
        "java_joiner_authority_path_and_sha256_required": True,
        "synthetic_or_fabricated_join_prohibited": True,
    },
    "qps_resource_input_prohibited": True,
    "capture_result_is_r0": False,
    "capture_result_is_accepted_round": False,
}


class MaterialsError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MaterialsError("regular JSON file required: " + str(path))
    value = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(value, dict):
        raise MaterialsError("JSON object required: " + str(path))
    return value


def write_new(path: Path, payload: bytes, mode: int) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def sidecar(path: Path) -> None:
    write_new(
        Path(str(path) + ".sha256"),
        (digest(path) + "  " + path.name + "\n").encode("ascii"),
        0o444,
    )


def bind_v13_spec() -> None:
    """Rebind the vetted v12 argv constructor without changing v12 files."""
    remote = spec.prior.base.RUNTIME_ROOT + "/bundles/" + BUNDLE_ID
    spec.BUNDLE_ID = BUNDLE_ID
    spec.prior.BUNDLE_ID = BUNDLE_ID
    spec.prior.base.BUNDLE_ID = BUNDLE_ID
    spec.prior.base.BUNDLE_REMOTE = remote
    spec.prior.base.SERVICE_REMOTE = (
        remote + "/authority/mysql_tpcc_node04_adopted_service_v2.py"
    )
    spec.prior.base.MATERIALIZER_REMOTE = (
        remote + "/authority/mysql_tpcc_materialize_phase_command_v2.py"
    )
    spec.prior.base.CONFIG_MATERIALIZER_REMOTE = (
        remote + "/authority/mysql_tpcc_config_materialize_v2.py"
    )
    spec.prior.MEASUREMENT_BRIDGE_HELPER_REMOTE = (
        remote + "/authority/mysql_tpcc_measurement_bridge_v1.py"
    )


@contextmanager
def v13_spec_binding():
    """Apply the inherited module-global binding only for one local action."""
    fields = (
        (spec, "BUNDLE_ID"),
        (spec.prior, "BUNDLE_ID"),
        (spec.prior.base, "BUNDLE_ID"),
        (spec.prior.base, "BUNDLE_REMOTE"),
        (spec.prior.base, "SERVICE_REMOTE"),
        (spec.prior.base, "MATERIALIZER_REMOTE"),
        (spec.prior.base, "CONFIG_MATERIALIZER_REMOTE"),
        (spec.prior, "MEASUREMENT_BRIDGE_HELPER_REMOTE"),
    )
    saved = [(owner, name, getattr(owner, name)) for owner, name in fields]
    bind_v13_spec()
    try:
        yield
    finally:
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)


def verify_bundle() -> dict[str, Any]:
    if BUNDLE.is_symlink() or not BUNDLE.is_dir():
        raise MaterialsError("v13 immutable bundle is absent")
    manifest_path = BUNDLE / "bundle_manifest.json"
    if digest(manifest_path) != BUNDLE_MANIFEST_SHA256:
        raise MaterialsError("v13 manifest SHA256 differs")
    recorded = (BUNDLE / "bundle_manifest.json.sha256").read_text(
        encoding="ascii"
    ).split()[0]
    manifest = load(manifest_path)
    if (
        recorded != BUNDLE_MANIFEST_SHA256
        or manifest.get("bundle_id") != BUNDLE_ID
        or manifest.get("schema_version")
        != "mysql_tpcc_typed_distributed_successor_bundle_v13"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get(
            "performance_schema_consumers_enabled_before_workload"
        ) is not True
        or manifest.get("real_sensitive_join_authority_hash_bound") is not True
        or manifest.get("synthetic_or_fabricated_join_prohibited") is not True
        or manifest.get("qps_resource_input_prohibited") is not True
    ):
        raise MaterialsError("v13 manifest authority differs")
    template = load(
        BUNDLE / "qualification/typed_capture_command_template_v2.json"
    )
    join = template.get("sensitive_stock_level_real_join_authority") or {}
    prelaunch = template.get("performance_schema_prelaunch_authority") or {}
    environment = template.get("environment") or {}
    if (
        template.get("qps_resource_input_prohibited") is not True
        or prelaunch.get("enable_before_benchbase_process_start") is not True
        or prelaunch.get("exact_consumer_readback_required") is not True
        or join.get("owner_trace_header_and_lock_required") is not True
        or join.get("capture_token_and_manifest_hash_bound") is not True
        or join.get("synthetic_or_fabricated_join_prohibited") is not True
        or environment.get("MYSQL_TPCC_TYPED_DEFAULTS_FILE")
        != "${MYSQL_DEFAULTS_FILE}"
    ):
        raise MaterialsError("v13 typed capture authority template differs")
    orchestration = (
        BUNDLE
        / "predecessor/orchestration/formal_tpcc_composite_workload.py"
    ).read_text(encoding="utf-8")
    java = (
        BUNDLE / "predecessor/source/TpccTypedTrace.java"
    ).read_text(encoding="utf-8")
    if any(
        marker not in orchestration
        for marker in (
            "enable_typed_consumers_before_workload",
            "PASS_ENABLED_BEFORE_WORKLOAD_START",
            "wait_for_real_typed_join_authority",
            "PASS_REAL_OWNER_TRACE_JOIN",
            "args.defaults_extra_file.is_symlink()",
        )
    ) or any(
        marker not in java
        for marker in (
            "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_PATH",
            "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_SHA256",
        )
    ):
        raise MaterialsError("v13 typed capture implementation differs")
    return manifest


def attempt_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "tpcc-v4-v13-typed-authority-" + stamp + "-" + secrets.token_hex(12)


def validate_plan(plan: dict[str, Any]) -> None:
    spec.validate_plan_bindings(plan)
    payload = canonical(plan).decode("ascii")
    remote_prefix = spec.prior.base.RUNTIME_ROOT + "/bundles/"
    observed = {part[:64] for part in payload.split(remote_prefix)[1:]}
    if (
        plan.get("schema_version")
        != "mysql_tpcc_v13_fresh_qualification_capture_plan_v1"
        or tuple(plan.get("execution_sequence") or ()) != EXECUTION_SEQUENCE
        or plan.get("typed_capture_authority") != TYPED_AUTHORITY
        or plan.get("formal_launch_authorized") is not False
        or plan.get("accepted_round_authorized") is not False
        or observed != {BUNDLE_ID}
        or INVALIDATED_D377 in payload
    ):
        raise MaterialsError("v13 qualification/capture plan binding differs")


def verify_materials(
    directory: Path, *, now: datetime | None = None
) -> dict[str, Any]:
    with v13_spec_binding():
        return _verify_materials_bound(directory, now=now)


def _verify_materials_bound(
    directory: Path, *, now: datetime | None = None
) -> dict[str, Any]:
    verify_bundle()
    if directory.is_symlink() or not directory.is_dir():
        raise MaterialsError("materials directory is not regular")
    required = {
        "main-agent-handoff-token",
        "qualification_spec.json",
        "qualification_spec.json.sha256",
        "qualification_plan.json",
        "qualification_plan.json.sha256",
        "materials_manifest.json",
        "materials_manifest.json.sha256",
    }
    if {path.name for path in directory.iterdir()} != required:
        raise MaterialsError("materials file set differs")
    if any(path.is_symlink() for path in directory.iterdir()):
        raise MaterialsError("materials symlink is prohibited")
    if stat.S_IMODE(directory.stat().st_mode) != 0o500:
        raise MaterialsError("materials directory mode differs")
    token_path = directory / "main-agent-handoff-token"
    if stat.S_IMODE(token_path.stat().st_mode) != 0o600:
        raise MaterialsError("handoff token mode differs")
    for name in required - {"main-agent-handoff-token"}:
        if stat.S_IMODE((directory / name).stat().st_mode) != 0o444:
            raise MaterialsError("sealed material mode differs: " + name)
    for name in (
        "qualification_spec.json",
        "qualification_plan.json",
        "materials_manifest.json",
    ):
        recorded = (directory / (name + ".sha256")).read_text(
            encoding="ascii"
        ).split()
        if recorded != [digest(directory / name), name]:
            raise MaterialsError("material SHA256 sidecar differs: " + name)
    rendered = load(directory / "qualification_spec.json")
    plan = load(directory / "qualification_plan.json")
    material = load(directory / "materials_manifest.json")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    spec.prior.base.validate(rendered, now=current)
    created = datetime.fromisoformat(rendered["created_at_utc"]).astimezone(
        timezone.utc
    )
    expires = datetime.fromisoformat(rendered["expires_at_utc"]).astimezone(
        timezone.utc
    )
    token = token_path.read_text(encoding="ascii").strip()
    if (
        expires - created != TTL
        or current >= expires
        or rendered["bundle"]
        != {
            "id": BUNDLE_ID,
            "remote_path": spec.prior.base.BUNDLE_REMOTE,
            "manifest_sha256": BUNDLE_MANIFEST_SHA256,
        }
        or rendered["attempt"]["id"] != directory.name
        or token != directory.name
        or hashlib.sha256(token.encode("ascii")).hexdigest()
        != rendered["attempt"]["handoff_token_sha256"]
    ):
        raise MaterialsError("fresh v13 spec binding differs")
    validate_plan(plan)
    expected_files = {
        "qualification_spec.json": digest(
            directory / "qualification_spec.json"
        ),
        "qualification_plan.json": digest(
            directory / "qualification_plan.json"
        ),
    }
    if (
        material.get("schema_version")
        != "mysql_tpcc_v13_qualification_capture_materials_v1"
        or material.get("bundle_id") != BUNDLE_ID
        or material.get("bundle_manifest_sha256")
        != BUNDLE_MANIFEST_SHA256
        or material.get("invalidated_predecessor_bundle_id")
        != INVALIDATED_D377
        or material.get("attempt_id") != directory.name
        or material.get("created_at_utc") != rendered["created_at_utc"]
        or material.get("expires_at_utc") != rendered["expires_at_utc"]
        or material.get("ttl_seconds") != int(TTL.total_seconds())
        or material.get("offline_render_only") is not True
        or material.get("remote_actions") != 0
        or material.get("capture_launch_authorized") is not False
        or material.get("formal_r0_authorized") is not False
        or material.get("formal_launch_authorized") is not False
        or material.get("accepted_round_authorized") is not False
        or material.get("typed_capture_authority") != TYPED_AUTHORITY
        or material.get("execution_sequence") != list(EXECUTION_SEQUENCE)
        or material.get("files") != expected_files
    ):
        raise MaterialsError("materials manifest differs")
    return material


def build(
    output_root: Path = OUT,
    selected_attempt: str | None = None,
    *,
    now: datetime | None = None,
) -> Path:
    with v13_spec_binding():
        verify_bundle()
        created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        identifier = selected_attempt or attempt_id()
        if spec.prior.base.TOKEN.fullmatch(identifier) is None:
            raise MaterialsError(
                "attempt identifier is not safe for reviewed argv"
            )
        output_root.mkdir(parents=True, exist_ok=True)
        final = output_root / identifier
        if final.exists():
            raise MaterialsError("fresh materials already exist: " + str(final))
        stage = Path(
            tempfile.mkdtemp(
                prefix=".v13-qualification-capture.", dir=output_root
            )
        )
        try:
            token_path = stage / "main-agent-handoff-token"
            write_new(token_path, (identifier + "\n").encode("ascii"), 0o600)
            rendered = spec.make_spec(
                BUNDLE / "bundle_manifest.json",
                identifier,
                identifier,
                now=created,
            )
            plan = spec.command_plan(rendered)
            rendered["expires_at_utc"] = (created + TTL).isoformat()
            spec.prior.base.validate(rendered, now=created)
            plan["schema_version"] = (
                "mysql_tpcc_v13_fresh_qualification_capture_plan_v1"
            )
            plan["execution_sequence"] = list(EXECUTION_SEQUENCE)
            plan["typed_capture_authority"] = TYPED_AUTHORITY
            plan["formal_launch_authorized"] = False
            plan["accepted_round_authorized"] = False
            validate_plan(plan)
            write_new(
                stage / "qualification_spec.json", canonical(rendered), 0o444
            )
            sidecar(stage / "qualification_spec.json")
            write_new(
                stage / "qualification_plan.json", canonical(plan), 0o444
            )
            sidecar(stage / "qualification_plan.json")
            material = {
                "schema_version":
                    "mysql_tpcc_v13_qualification_capture_materials_v1",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "postgresql_mutation_prohibited": True,
                "offline_render_only": True,
                "remote_actions": 0,
                "bundle_id": BUNDLE_ID,
                "bundle_manifest_sha256": BUNDLE_MANIFEST_SHA256,
                "invalidated_predecessor_bundle_id": INVALIDATED_D377,
                "attempt_id": identifier,
                "created_at_utc": rendered["created_at_utc"],
                "expires_at_utc": rendered["expires_at_utc"],
                "ttl_seconds": int(TTL.total_seconds()),
                "handoff_token_sha256": hashlib.sha256(
                    identifier.encode("ascii")
                ).hexdigest(),
                "database_credentials_present": False,
                "qualification_materialization_only": True,
                "typed_capture_materialization_after_qualification_gate_only":
                    True,
                "capture_launch_authorized": False,
                "formal_r0_authorized": False,
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
                "typed_capture_authority": TYPED_AUTHORITY,
                "execution_sequence": list(EXECUTION_SEQUENCE),
                "files": {
                    "qualification_spec.json": digest(
                        stage / "qualification_spec.json"
                    ),
                    "qualification_plan.json": digest(
                        stage / "qualification_plan.json"
                    ),
                },
            }
            write_new(
                stage / "materials_manifest.json", canonical(material), 0o444
            )
            sidecar(stage / "materials_manifest.json")
            os.chmod(stage, 0o500)
            os.replace(stage, final)
            _verify_materials_bound(final, now=created)
            return final
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--attempt-id")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        material = verify_materials(args.verify.resolve(strict=True))
        print(
            json.dumps(
                {
                    "status": "PASS_VERIFIED_UNEXPIRED",
                    "bundle_id": BUNDLE_ID,
                    "attempt_id": material["attempt_id"],
                    "expires_at_utc": material["expires_at_utc"],
                },
                sort_keys=True,
            )
        )
        return 0
    result = build(args.output_root.resolve(), args.attempt_id)
    material = load(result / "materials_manifest.json")
    print(
        json.dumps(
            {
                "status": "PASS_RENDERED_VERIFIED_OFFLINE_ONLY",
                "bundle_id": BUNDLE_ID,
                "attempt_id": material["attempt_id"],
                "created_at_utc": material["created_at_utc"],
                "expires_at_utc": material["expires_at_utc"],
                "ttl_seconds": material["ttl_seconds"],
                "directory": str(result),
                "remote_actions": 0,
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, MaterialsError, spec.SpecError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
