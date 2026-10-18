#!/usr/bin/env python3
"""Seal the TPC-C successor that binds typed JDBC to a sealed defaults file.

Offline content-addressed builder only.  It never contacts Docker, MySQL, or
any remote host.  The resulting bundle remains qualification/capture-only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
BASE_ID = "544f06d3121968dd23e558bfb7ab3f2163d8baabc7f6cc3d500ac3729c8bf599"
BASE = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v11" / BASE_ID
RUNTIME_ID = "ecc83ef6d296a80a2ff154c33bca5e279a0fe3f6c040d685b051e4cd120db6be"
RUNTIME = ROOT / "artifacts/mysql_tpcc_typed_runtime_v1" / RUNTIME_ID
OUT = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v12"
MATERIALIZER_V1 = ROOT / "shell/mysql_tpcc_materialize_phase_command_v1.py"
MATERIALIZER_V2 = ROOT / "shell/mysql_tpcc_materialize_phase_command_v2.py"
READINESS_V2 = ROOT / "shell/verify_mysql_tpcc_v2_formal10_readiness.py"
READINESS_V3 = ROOT / "shell/verify_mysql_tpcc_v3_formal10_readiness.py"
INVALIDATION = ROOT / "preflight/mysql_tpcc_v11_544f_defaults_literal_credentials_invalidation_20260731.json"
OLD_RUNTIME_ID = "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        # Match the immutable predecessor/runtime canonicalization exactly.
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BuildError("regular JSON file required: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def helper() -> Any:
    source = ROOT / "shell/build_mysql_tpcc_distributed_successor_v4.py"
    spec = importlib.util.spec_from_file_location("tpcc_v4_copy_helper", source)
    if spec is None or spec.loader is None:
        raise BuildError("v4 copy helper is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            # A bundle's closure is intentionally self-excluding.  Nested
            # predecessor manifests remain part of this successor's closure.
            if relative in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
                continue
            result[relative] = sha256(path)
    return result


def verify_bundle(bundle: Path, schema: str) -> dict[str, Any]:
    manifest = load(bundle / "bundle_manifest.json")
    bundle_id = manifest.get("bundle_id")
    if schema == "mysql_tpcc_typed_runtime_identity_v1":
        # Runtime v1 deliberately adds publication/runtime/authorization
        # read-back after deriving its content identity.
        unsigned = {
            key: manifest[key]
            for key in (
                "schema_version",
                "supersedes_bundle_ids",
                "formal_scope",
                "cross_engine_comparison_prohibited",
                "capture_only",
                "outer_execution_authorized",
                "files",
                "contract",
            )
        }
    else:
        unsigned = dict(manifest)
        unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or sha_bytes(canonical(unsigned)) != bundle.name
        or manifest.get("schema_version") != schema
        or manifest.get("files") != files(bundle)
    ):
        raise BuildError("immutable bundle closure differs: " + str(bundle))
    return manifest


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited in staged successor")
        if path.is_dir():
            os.chmod(path, 0o555)
        elif path.is_file():
            executable = bool(stat.S_IMODE(path.stat().st_mode) & 0o111)
            os.chmod(path, 0o555 if executable else 0o444)
    os.chmod(root, 0o555)


def replace_runtime_id(payload: bytes) -> bytes:
    return payload.replace(OLD_RUNTIME_ID.encode("ascii"), RUNTIME_ID.encode("ascii"))


def typed_template(stage: Path) -> None:
    path = stage / "qualification/typed_capture_command_template_v2.json"
    value = load(path)
    environment = value.get("environment")
    if not isinstance(environment, dict):
        raise BuildError("typed capture environment is absent")
    for forbidden in (
        "MYSQL_TPCC_TYPED_METRICS_USER",
        "MYSQL_TPCC_TYPED_METRICS_PASSWORD",
        "MYSQL_TPCC_TYPED_TARGET_USER",
        "MYSQL_TPCC_TYPED_TARGET_PASSWORD",
    ):
        if forbidden not in environment:
            raise BuildError("544f literal credential placeholder is absent")
        environment.pop(forbidden)
    environment["MYSQL_TPCC_TYPED_DEFAULTS_FILE"] = "${MYSQL_DEFAULTS_FILE}"
    if environment.get("MYSQL_TPCC_TYPED_TARGET_JDBC_URL") != "${MYSQL_JDBC_URL}":
        raise BuildError("typed sensitive JDBC URL differs")
    value["environment"] = environment
    value["typed_jdbc_credentials"] = {
        "defaults_file": "/opt/mysql-tpcc-config/mysql.cnf",
        "materialized_from": "${MYSQL_DEFAULTS_FILE}",
        "metrics_and_sensitive_jdbc": "sealed_defaults_file_only",
        "user_password_environment_prohibited": True,
        "credentials_in_logs_trace_or_manifest_prohibited": True,
    }
    helper().write(path, canonical(value))


def patch_stage(stage: Path) -> None:
    h = helper()
    shutil.rmtree(stage / "predecessor")
    h.copy_mutable(RUNTIME, stage / "predecessor")
    # copy_mutable deliberately omits the copied root's manifest.  Here the
    # runtime is an immutable predecessor, so retain its verified identity as
    # part of the successor closure.
    for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
        h.write(stage / "predecessor" / name, (RUNTIME / name).read_bytes())
    h.write(
        stage / "authority/mysql_tpcc_materialize_phase_command_v1.py",
        MATERIALIZER_V1.read_bytes(),
        0o755,
    )
    h.write(
        stage / "authority/mysql_tpcc_materialize_phase_command_v2.py",
        MATERIALIZER_V2.read_bytes(),
        0o755,
    )
    for name, source in (
        ("tpcc_v2_formal10_readiness_verifier.py", READINESS_V2),
        ("tpcc_v3_formal10_readiness_verifier.py", READINESS_V3),
    ):
        h.write(stage / "authority" / name, replace_runtime_id(source.read_bytes()), 0o755)
    for name in (
        "tpcc_v2_formal10_readiness_contract.json",
        "tpcc_v3_formal10_readiness_contract.json",
    ):
        h.write(stage / "authority" / name, replace_runtime_id((stage / "authority" / name).read_bytes()))
    h.write(
        stage / "authority/mysql_tpcc_v11_544f_defaults_literal_credentials_invalidation_20260731.json",
        INVALIDATION.read_bytes(),
    )
    h.write(stage / "authority/build_mysql_tpcc_distributed_successor_v12.py", BUILDER.read_bytes(), 0o755)
    typed_template(stage)
    runtime_path = stage / "distributed_runtime_contract.json"
    runtime = load(runtime_path)
    runtime["schema_version"] = "mysql_algorithm_c_tpcc_distributed_runtime_contract_v12"
    runtime["supersedes_bundle_id"] = BASE_ID
    runtime["typed_runtime_invariants"] = {
        **dict(runtime.get("typed_runtime_invariants") or {}),
        "typed_runtime_bundle_id": RUNTIME_ID,
        "jdbc_credentials_from_sealed_defaults_only": True,
        "sealed_defaults_file": "/opt/mysql-tpcc-config/mysql.cnf",
        "typed_user_password_environment_prohibited": True,
        "typed_credentials_in_logs_trace_manifest_prohibited": True,
    }
    h.write(runtime_path, canonical(runtime))


def verify_successor(bundle: Path) -> dict[str, Any]:
    manifest = verify_bundle(bundle, "mysql_tpcc_typed_distributed_successor_bundle_v12")
    if (
        manifest.get("supersedes_bundle_id") != BASE_ID
        or BASE_ID not in manifest.get("invalidated_bundle_ids", [])
        or manifest.get("typed_runtime_bundle_id") != RUNTIME_ID
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v12 identity/authorization differs")
    invalidation = load(bundle / "authority/mysql_tpcc_v11_544f_defaults_literal_credentials_invalidation_20260731.json")
    if (
        invalidation.get("bundle_id") != BASE_ID
        or invalidation.get("accepted_rounds_credit") != 0
        or invalidation.get("capture_credit") != 0
        or invalidation.get("fresh_r0_credit") != 0
        or invalidation.get("typed_capture_authorized") is not False
    ):
        raise BuildError("544f zero-credit invalidation differs")
    template = load(bundle / "qualification/typed_capture_command_template_v2.json")
    environment = template.get("environment") or {}
    forbidden = {
        "MYSQL_TPCC_TYPED_METRICS_USER",
        "MYSQL_TPCC_TYPED_METRICS_PASSWORD",
        "MYSQL_TPCC_TYPED_TARGET_USER",
        "MYSQL_TPCC_TYPED_TARGET_PASSWORD",
    }
    if (
        environment.get("MYSQL_TPCC_TYPED_DEFAULTS_FILE") != "${MYSQL_DEFAULTS_FILE}"
        or forbidden & set(environment)
        or (template.get("typed_jdbc_credentials") or {}).get("user_password_environment_prohibited")
        is not True
    ):
        raise BuildError("typed defaults materialization differs")
    predecessor = load(bundle / "predecessor/bundle_manifest.json")
    if predecessor.get("bundle_id") != RUNTIME_ID:
        raise BuildError("v12 typed runtime predecessor differs")
    for source in (
        bundle / "predecessor/source/TpccTypedTrace.java",
        bundle / "predecessor/source/TpccSensitiveTypedMain.java",
        bundle / "predecessor/orchestration/formal_tpcc_composite_workload.py",
        bundle / "authority/mysql_tpcc_materialize_phase_command_v2.py",
    ):
        text = source.read_text(encoding="utf-8")
        if "MYSQL_TPCC_TYPED_METRICS_PASSWORD" in text or "MYSQL_TPCC_TYPED_TARGET_PASSWORD" in text:
            raise BuildError("typed credential environment remains: " + str(source))
    for path in (bundle, *sorted(bundle.rglob("*"))):
        if path.is_symlink():
            raise BuildError("v12 symlink differs")
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir() and mode != 0o555:
            raise BuildError("v12 directory mode differs")
        if path.is_file() and mode not in (0o444, 0o555):
            raise BuildError("v12 file mode differs")
    return manifest


def build(output_root: Path = OUT) -> Path:
    base = verify_bundle(BASE, "mysql_tpcc_typed_distributed_successor_bundle_v11")
    runtime = verify_bundle(RUNTIME, "mysql_tpcc_typed_runtime_identity_v1")
    jdbc = (runtime.get("contract") or {}).get("jdbc_credentials") or {}
    if (
        jdbc.get("defaults_file") != "/opt/mysql-tpcc-config/mysql.cnf"
        or jdbc.get("environment_user_password_prohibited") is not True
        or not all(path.is_file() and not path.is_symlink() for path in (MATERIALIZER_V1, MATERIALIZER_V2, READINESS_V2, READINESS_V3, INVALIDATION, BUILDER))
    ):
        raise BuildError("v12 sealed input authority differs")
    invalidation = load(INVALIDATION)
    if invalidation.get("bundle_id") != BASE_ID or invalidation.get("accepted_rounds_credit") != 0:
        raise BuildError("544f invalidation input differs")
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpcc-v12.", dir=str(output_root)))
    h = helper()
    try:
        h.copy_mutable(BASE, stage)
        patch_stage(stage)
        closure = files(stage)
        identity = {
            "schema_version": "mysql_tpcc_typed_distributed_successor_bundle_v12",
            "scenario": "tpcc",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "immutable": True,
            "offline_build_only": True,
            "remote_actions": 0,
            "supersedes_bundle_id": BASE_ID,
            "source_base_bundle_id": BASE_ID,
            "typed_runtime_bundle_id": RUNTIME_ID,
            "base_manifest_sha256": sha256(BASE / "bundle_manifest.json"),
            "typed_runtime_manifest_sha256": sha256(RUNTIME / "bundle_manifest.json"),
            "invalidated_bundle_ids": [*base.get("invalidated_bundle_ids", []), BASE_ID],
            "invalidation_544f_sha256": sha256(INVALIDATION),
            "typed_jdbc_credentials": {
                "defaults_file": "/opt/mysql-tpcc-config/mysql.cnf",
                "metrics_and_sensitive_jdbc": "sealed_defaults_file_only",
                "user_password_environment_prohibited": True,
                "credentials_in_logs_trace_or_manifest_prohibited": True,
            },
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "fresh_r0_required": True,
            "real_production_trace_required": True,
            "files": closure,
        }
        bundle_id = sha_bytes(canonical(identity))
        final = output_root / bundle_id
        if final.exists():
            shutil.rmtree(stage)
            verify_successor(final)
            return final
        h.write(stage / "bundle_manifest.json", canonical({**identity, "bundle_id": bundle_id}))
        h.write(stage / "bundle_manifest.json.sha256", (sha256(stage / "bundle_manifest.json") + "  bundle_manifest.json\n").encode("ascii"))
        freeze(stage)
        os.replace(stage, final)
        verify_successor(final)
        return final
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUT)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        manifest = verify_successor(args.verify.resolve(strict=True))
        print(json.dumps({"status": "PASS_VERIFIED", "bundle_id": manifest["bundle_id"]}, sort_keys=True))
        return 0
    bundle = build(args.output_root.resolve())
    print(json.dumps({"status": "PUBLISHED_VERIFIED", "bundle_id": bundle.name, "directory": str(bundle), "formal_launch_authorized": False, "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
