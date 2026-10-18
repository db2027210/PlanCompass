#!/usr/bin/env python3
"""Re-seal TPC-C v10 with a sealed attempt configuration materializer.

This is an offline content-addressed builder.  It deliberately begins from
the immutable v9 payload, preserves the actual ``/mnt/mysql_tpcc_nvme3n1``
SSD mount and invalidates v9 before bootstrap; it performs no remote action.
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
BASE_ID = "c86b6cfc4e80836c915afc235635e575e43134512ef0086b1ca83623ca33654d"
BASE = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v9" / BASE_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v10"
SERVICE = ROOT / "shell/mysql_tpcc_node04_adopted_service_v1.py"
READINESS = ROOT / "shell/verify_mysql_tpcc_v3_formal10_readiness.py"
READINESS_V2 = ROOT / "shell/verify_mysql_tpcc_v2_formal10_readiness.py"
INVALIDATION = ROOT / "preflight/mysql_tpcc_c86b6c_config_gap_invalidation_20260731.json"
CONFIG_MATERIALIZER = ROOT / "shell/mysql_tpcc_config_materialize_v1.py"
CONFIG_PROFILE = ROOT / "preflight/mysql_tpcc_v10_config_profile.json"
TPCC_TEMPLATE = ROOT / "preflight/mysql_tpcc_v10_tpcc.xml.template"
MYSQL_TEMPLATE = ROOT / "preflight/mysql_tpcc_v10_mysql.cnf.template"
OLD_MOUNT = "/mnt/nvme3n1"
MOUNT = "/mnt/mysql_tpcc_nvme3n1"


class BuildError(RuntimeError):
    pass


def _v4():
    spec = importlib.util.spec_from_file_location("sealed_tpcc_v4_builder", ROOT / "shell/build_mysql_tpcc_distributed_successor_v4.py")
    if spec is None or spec.loader is None:
        raise BuildError("v4 helper builder is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


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


def verify_base() -> dict[str, Any]:
    manifest = load(BASE / "bundle_manifest.json")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if bundle_id != BASE_ID or sha_bytes(canonical(unsigned)) != BASE_ID or manifest.get("schema_version") != "mysql_tpcc_typed_distributed_successor_bundle_v9":
        raise BuildError("v9 base identity differs")
    for relative, expected in (manifest.get("files") or {}).items():
        candidate = BASE / str(relative)
        if candidate.is_symlink() or not candidate.is_file() or sha256(candidate) != expected:
            raise BuildError("v9 base closure differs: " + str(relative))
    return manifest


def replace_layout(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(OLD_MOUNT, MOUNT)
    if isinstance(value, list):
        return [replace_layout(item) for item in value]
    if isinstance(value, dict):
        return {str(key): replace_layout(item) for key, item in value.items()}
    return value


def patch(stage: Path, helper: Any) -> None:
    helper.write(stage / "authority/mysql_tpcc_node04_adopted_service_v1.py", SERVICE.read_bytes(), 0o755)
    helper.write(stage / "authority/tpcc_v3_formal10_readiness_verifier.py", READINESS.read_bytes(), 0o755)
    helper.write(stage / "authority/tpcc_v2_formal10_readiness_verifier.py", READINESS_V2.read_bytes(), 0o755)
    helper.write(stage / "authority/mysql_tpcc_config_materialize_v1.py", CONFIG_MATERIALIZER.read_bytes(), 0o755)
    helper.write(stage / "authority/build_mysql_tpcc_distributed_successor_v10.py", BUILDER.read_bytes(), 0o755)
    helper.write(stage / "authority" / ("invalidation_" + BASE_ID + ".json"), INVALIDATION.read_bytes())
    helper.write(stage / "configuration/config_profile.json", CONFIG_PROFILE.read_bytes())
    helper.write(stage / "configuration/tpcc.xml.template", TPCC_TEMPLATE.read_bytes())
    helper.write(stage / "configuration/mysql.cnf.template", MYSQL_TEMPLATE.read_bytes())
    contract_path = stage / "authority/four_scene_successor_contract.json"
    contract = replace_layout(load(contract_path))
    storage = ((contract.get("scenarios") or {}).get("tpcc") or {}).get("storage") or {}
    if storage.get("mount") != MOUNT or storage.get("device") != "/dev/nvme3n1" or storage.get("expected_live_major_minor") != "259:2":
        raise BuildError("layout-v2 TPC-C storage contract differs")
    helper.write(contract_path, canonical(contract))
    runtime_path = stage / "distributed_runtime_contract.json"
    runtime = replace_layout(load(runtime_path))
    storage = runtime.get("storage") or {}
    if storage.get("mount") != MOUNT or storage.get("runtime_root", "").startswith(MOUNT + "/") is not True:
        raise BuildError("layout-v2 TPC-C runtime contract differs")
    runtime["schema_version"] = "mysql_algorithm_c_tpcc_distributed_runtime_contract_v10"
    runtime["supersedes_bundle_id"] = BASE_ID
    python_manifest_path = stage / "toolchain/python-3.11.11-tree-manifest.json"
    python_manifest = load(python_manifest_path)
    source_provenance = dict(python_manifest.get("source_provenance") or {})
    python_root = MOUNT + "/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-" + str(python_manifest.get("tree_content_sha256"))
    source_provenance["source_root"] = python_root
    python_manifest["source_provenance"] = source_provenance
    helper.write(python_manifest_path, canonical(python_manifest))
    runtime["toolchain"]["python_runtime_root"] = python_root
    runtime["toolchain"]["python_runtime"] = python_root + "/bin/python3.11"
    runtime["toolchain"]["python_tree_manifest"] = "toolchain/python-3.11.11-tree-manifest.json"
    runtime["toolchain"]["python_tree_manifest_sha256"] = sha256(python_manifest_path)
    runtime["toolchain"]["python_tree_content_sha256"] = python_manifest["tree_content_sha256"]
    client_python = ((runtime.get("client_runtime_mounts") or {}).get("python") or {})
    if client_python.get("destination") != "/opt/mysql-tpcc-python" or client_python.get("tree_content_sha256") != python_manifest["tree_content_sha256"]:
        raise BuildError("v8 client Python runtime contract differs")
    client_python["source"] = python_root
    helper.write(runtime_path, canonical(runtime))


def files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def verify_published(bundle: Path) -> dict[str, Any]:
    manifest = load(bundle / "bundle_manifest.json")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if bundle_id != bundle.name or sha_bytes(canonical(unsigned)) != bundle.name or manifest.get("schema_version") != "mysql_tpcc_typed_distributed_successor_bundle_v10" or manifest.get("supersedes_bundle_id") != BASE_ID or BASE_ID not in manifest.get("invalidated_bundle_ids", []):
        raise BuildError("v10 identity/invalidation differs")
    for relative, expected in (manifest.get("files") or {}).items():
        candidate = bundle / str(relative)
        if candidate.is_symlink() or not candidate.is_file() or sha256(candidate) != expected:
            raise BuildError("v10 closure differs: " + str(relative))
    sidecar = bundle / "bundle_manifest.json.sha256"
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii") != f"{sha256(bundle / 'bundle_manifest.json')}  bundle_manifest.json\n":
        raise BuildError("v10 manifest sidecar differs")
    for text_path in (bundle / "authority/mysql_tpcc_node04_adopted_service_v1.py", bundle / "authority/tpcc_v2_formal10_readiness_verifier.py", bundle / "authority/tpcc_v3_formal10_readiness_verifier.py", bundle / "authority/four_scene_successor_contract.json", bundle / "distributed_runtime_contract.json"):
        text = text_path.read_text(encoding="utf-8")
        if OLD_MOUNT in text or MOUNT not in text:
            raise BuildError("v10 sealed layout alias differs: " + str(text_path))
    runtime = load(bundle / "distributed_runtime_contract.json")
    toolchain = runtime.get("toolchain") or {}
    expected_python = MOUNT + "/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
    if toolchain.get("python_runtime_root") != expected_python or toolchain.get("python_runtime") != expected_python + "/bin/python3.11" or toolchain.get("python_tree_manifest_sha256") != sha256(bundle / "toolchain/python-3.11.11-tree-manifest.json"):
        raise BuildError("v10 content-addressed Python runtime binding differs")
    storage = runtime.get("storage") or {}
    client_python = ((runtime.get("client_runtime_mounts") or {}).get("python") or {})
    if (
        storage.get("mount") != MOUNT
        or not str(storage.get("runtime_root") or "").startswith(MOUNT + "/")
        or client_python.get("source") != expected_python
        or OLD_MOUNT in canonical(runtime).decode("utf-8")
    ):
        raise BuildError("v10 runtime contains an untrusted mount path")
    profile = load(bundle / "configuration/config_profile.json")
    if (
        profile.get("qps_resource_input_prohibited") is not True
        or profile.get("password_handling")
        != "external_0600_file_substituted_only_at_attempt_materialization_not_logged_or_manifested"
        or (bundle / "authority/mysql_tpcc_config_materialize_v1.py").read_bytes()
        != CONFIG_MATERIALIZER.read_bytes()
    ):
        raise BuildError("v10 sealed configuration authority differs")
    return manifest


def build(output_root: Path = OUTPUT_ROOT) -> Path:
    base = verify_base()
    for item in (SERVICE, READINESS, READINESS_V2, INVALIDATION, CONFIG_MATERIALIZER, CONFIG_PROFILE, TPCC_TEMPLATE, MYSQL_TEMPLATE, BUILDER):
        if item.is_symlink() or not item.is_file():
            raise BuildError("v5 input absent: " + str(item))
    helper = _v4()
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpcc-v5.", dir=str(output_root)))
    try:
        helper.copy_mutable(BASE, stage)
        patch(stage, helper)
        closure = files(stage)
        identity = {
            "schema_version": "mysql_tpcc_typed_distributed_successor_bundle_v10", "scenario": "tpcc",
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "immutable": True,
            "offline_build_only": True, "remote_actions": 0, "supersedes_bundle_id": BASE_ID,
            "source_base_bundle_id": BASE_ID, "superseded_bundle_launch_authorized": False,
            "invalidated_bundle_ids": [*base.get("invalidated_bundle_ids", []), BASE_ID],
            "base_manifest_sha256": sha256(BASE / "bundle_manifest.json"),
            "four_scene_contract_sha256": closure["authority/four_scene_successor_contract.json"],
            "runtime_contract_sha256": closure["distributed_runtime_contract.json"],
            "readiness_verifier_sha256": sha256(READINESS), "adopted_service_sha256": sha256(SERVICE),
            "builder_sha256": sha256(BUILDER), "formal_launch_authorized": False,
            "accepted_round_authorized": False, "fresh_r0_required": True,
            "real_production_trace_required": True, "same_live_container_handoff_required": True, "files": closure,
        }
        bundle_id = sha_bytes(canonical(identity))
        helper.write(stage / "bundle_manifest.json", canonical({**identity, "bundle_id": bundle_id}))
        helper.write(stage / "bundle_manifest.json.sha256", f"{sha256(stage / 'bundle_manifest.json')}  bundle_manifest.json\n".encode("ascii"))
        helper.freeze(stage)
        destination = output_root / bundle_id
        if destination.exists():
            return destination
        os.rename(stage, destination)
        return destination
    finally:
        if stage.exists():
            helper.thaw_directories(stage)
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    bundle = args.verify.resolve(strict=True) if args.verify else build(args.output_root.resolve())
    manifest = verify_published(bundle)
    print(json.dumps({"status": "PASS", "bundle_id": bundle.name, "bundle": str(bundle), "formal_launch_authorized": manifest["formal_launch_authorized"], "accepted_round_authorized": manifest["accepted_round_authorized"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
