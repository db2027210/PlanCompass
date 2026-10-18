#!/usr/bin/env python3
"""Build a strict-closure TPC-C v13 dedicated-daemon successor."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT
    / "artifacts/mysql_tpcc_typed_distributed_successor_v13"
    / "4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
)
OUTPUT = ROOT / "artifacts/mysql_tpcc_v13_dedicated_daemon_successor_v2"
SOCKET = "unix:///run/ra-docker-mysql-tpcc-v1.sock"
PREDECESSOR_MANIFEST_SHA256 = (
    "2172eae52c4736ba1afbf4a1342f8e02d20db6fa771828839db14cc9e5a17e90"
)
INVALIDATED_DIRTY_BUNDLE_ID = (
    "8d389a57eaeafb1bbcfe6735abddd4dcde6969c8aa6570251754acefebcb1601"
)
SCHEMA = "mysql_tpcc_v13_dedicated_daemon_successor_bundle_v2"


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def patch_python(source: str) -> str:
    source = source.replace(
        '"docker",', '"docker", "--host", "' + SOCKET + '",'
    ).replace(
        "['docker',", "['docker', '--host', '" + SOCKET + "',"
    )
    source = re.sub(
        r"(?<![A-Za-z0-9_-])docker "
        r"(?=(?:run|exec|inspect|ps|stop|rm|image|info|create|start)\b)",
        "docker --host " + SOCKET + " ",
        source,
    )
    source = source.replace(
        '--publish", f"127.0.0.1:{PORT}:3306"', '--network", "host"'
    ).replace(
        '"--publish", f"127.0.0.1:{PORT}:3306"', '"--network", "host"'
    ).replace(
        '"--publish", f"127.0.0.1:{args.port}:3306"',
        '"--network", "host"',
    )
    return source.replace(
        '"--main-agent-token"', '"--main-agent-token-file"'
    )


def verifier_module():
    path = ROOT / "shell/mysql_tpcc_v13_strict_bundle_verifier_v1.py"
    spec = importlib.util.spec_from_file_location("tpcc_strict_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("strict verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build() -> dict[str, object]:
    predecessor_manifest_path = PREDECESSOR / "bundle_manifest.json"
    predecessor_manifest = json.loads(
        predecessor_manifest_path.read_text(encoding="utf-8")
    )
    if (
        PREDECESSOR.name != predecessor_manifest.get("bundle_id")
        or digest(predecessor_manifest_path) != PREDECESSOR_MANIFEST_SHA256
    ):
        raise RuntimeError("TPC-C v13 predecessor identity differs")

    payload: dict[str, bytes] = {}
    for relative in predecessor_manifest["files"]:
        if relative == "closure.json":
            continue
        source = PREDECESSOR / relative
        value = source.read_bytes()
        if relative.endswith(".py"):
            value = patch_python(value.decode("utf-8")).encode("utf-8")
        payload[relative] = value

    validator = (
        ROOT / "shell/mysql_tpcc_v13_dedicated_daemon_validator_v1.py"
    ).read_bytes()
    issuer = (
        ROOT / "shell/mysql_tpcc_v13_dedicated_issuer_v1.py"
    ).read_text(encoding="utf-8").replace(
        "mysql_tpcc_v13_dedicated_daemon_successor_bundle_v1", SCHEMA
    ).encode("utf-8")
    issuer_test = (
        ROOT / "shell/test_mysql_tpcc_v13_dedicated_issuer_v1.py"
    ).read_bytes()
    strict_verifier = (
        ROOT / "shell/mysql_tpcc_v13_strict_bundle_verifier_v1.py"
    ).read_bytes()
    payload[
        "authority/mysql_tpcc_v13_dedicated_daemon_validator_v1.py"
    ] = validator
    payload["materials/mysql_tpcc_v13_dedicated_issuer_v1.py"] = issuer
    payload["tests/test_mysql_tpcc_v13_dedicated_issuer_v1.py"] = issuer_test
    payload[
        "authority/mysql_tpcc_v13_strict_bundle_verifier_v1.py"
    ] = strict_verifier

    contract = {
        "socket": "/run/ra-docker-mysql-tpcc-v1.sock",
        "data_root":
            "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/"
            "docker_ra_tpcc_v1",
        "device_major_minor": "259:2",
        "unit": "ra-docker-mysql-tpcc-v1.service",
        "db_client_network": "host",
        "mysql_server_port": 65106,
        "all_docker_argv_explicit_host_required": True,
        "direct_host_daemon_required": True,
        "daemon_identity": [
            "unit", "MainPID", "startticks", "ppid=1", "cgroup",
            "namespaces", "no_docker_ancestry",
        ],
        "graphdriver_fields": [
            "UpperDir", "WorkDir", "MergedDir", "LowerDir",
        ],
        "failed_quiescence_required": True,
        "token_file_only": True,
        "typed_trace_contract": [
            "34_bindings", "Delivery", "NewOrder", "OrderStatus",
            "Payment", "StockLevel", "L2_L3", "QPS_resource_input_prohibited",
        ],
        "strict_manifest_file_closure_required": True,
        "generated_python_bytecode_prohibited": True,
    }
    payload[
        "authority/mysql_tpcc_v13_dedicated_daemon_contract_v2.json"
    ] = canonical(contract)
    payload[
        "authority/mysql_tpcc_v13_default_daemon_invalidation_20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "reason":
                "default socket/daemon cannot prove direct-host identity "
                "or non-PG GraphDriver",
            "accepted_round_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )
    payload[
        "authority/mysql_tpcc_v13_8d389_bytecode_pollution_invalidation_"
        "20260731.json"
    ] = canonical(
        {
            "status": "INVALIDATED_DO_NOT_USE",
            "invalidated_bundle_id": INVALIDATED_DIRTY_BUNDLE_ID,
            "reason":
                "local sealed directory acquired manifest-external "
                "__pycache__/.pyc files after testing",
            "declared_file_hashes_changed": False,
            "strict_file_closure_passed": False,
            "accepted_round_credit": 0,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    )

    files = {
        relative: digest_bytes(value)
        for relative, value in sorted(payload.items())
    }
    closure = {
        "schema_version": "mysql_tpcc_v13_dedicated_daemon_closure_v2",
        "files": files,
        "dedicated_daemon_contract": contract,
        "strict_manifest_file_closure_required": True,
    }
    payload["closure.json"] = canonical(closure)
    files = {
        relative: digest_bytes(value)
        for relative, value in sorted(payload.items())
    }
    manifest = {
        key: value
        for key, value in predecessor_manifest.items()
        if key not in ("bundle_id", "files")
    }
    manifest.update(
        {
            "schema_version": SCHEMA,
            "predecessor_bundle_id": PREDECESSOR.name,
            "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
            "invalidates_dirty_bundle_id": INVALIDATED_DIRTY_BUNDLE_ID,
            "dedicated_daemon_contract": contract,
            "strict_manifest_file_closure_required": True,
            "generated_python_bytecode_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": files,
        }
    )
    bundle_id = digest_bytes(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    target = OUTPUT / bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        stage = OUTPUT / (".stage-" + secrets.token_hex(8))
        stage.mkdir(mode=0o700)
        try:
            for relative, value in payload.items():
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(value)
                os.chmod(destination, 0o444)
            raw_manifest = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw_manifest)
            (stage / "bundle_manifest.json.sha256").write_text(
                digest_bytes(raw_manifest) + "  bundle_manifest.json\n",
                encoding="ascii",
            )
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    verification = verifier_module().verify(target)
    return {
        "status": "PASS_SEALED_STRICT_TPCC_DEDICATED_DAEMON_SUCCESSOR",
        "bundle": str(target),
        "bundle_id": bundle_id,
        "manifest_sha256": digest(target / "bundle_manifest.json"),
        "strict_verification": verification,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def main() -> None:
    print(json.dumps(build(), sort_keys=True))


if __name__ == "__main__":
    main()
