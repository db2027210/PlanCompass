#!/usr/bin/env python3
"""Build the immutable offline TPC-C v16 current-args materializer bundle."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FAMILY = ROOT / "artifacts/mysql_tpcc_v16_current_args_materializer_successor_v1"
MATERIALIZER = ROOT / "shell/mysql_tpcc_v16_current_args_materializer_v1.py"
BUILDER = Path(__file__).resolve()
TEST = ROOT / "tests/test_mysql_tpcc_v16_current_args_materializer_v1.py"
TEMPLATE = ROOT / "templates/mysql_tpcc_v16_current_args_materializer_invocation_v1.json"
DEPENDENCIES = {
    "a336": ROOT / ("artifacts/mysql_tpcc_v16_same_live_gate_successor_v1/"
                     "a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723/"
                     "bundle_manifest.json"),
    "producer_99d": ROOT / ("artifacts/mysql_tpcc_v16_fresh_four_flow_successor_v1/"
                             "99d188343e3fa552faf4b07eaf432489b7681c4782a9c81d1e738f7cea2294ba/"
                             "bundle_manifest.json"),
    "tpcc_v16": ROOT / ("artifacts/mysql_tpcc_prefault_successor_v16/"
                         "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77/"
                         "bundle_manifest.json"),
    "tpch_ff313": ROOT / ("artifacts/mysql_tpch_sql_native_percentile_successor_v1/"
                           "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4/"
                           "bundle_manifest.json"),
    "collector_feb2": ROOT / ("artifacts/mysql_tpch_inventory_retry_support_v1/"
                               "feb2e6ea9cd866a3fb82c5fbe9dea14a68cb2a6f73d97de0f4a615c410c8caff/"
                               "bundle_manifest.json"),
}


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular(path: Path, label: str) -> Path:
    path = path.resolve(strict=True)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(label + " must be a regular file")
    return path


def verify_manifest(path: Path, label: str) -> dict[str, Any]:
    path = regular(path, label + " manifest")
    sidecar = regular(Path(str(path) + ".sha256"), label + " manifest sidecar")
    if sidecar.read_text(encoding="ascii") != f"{sha256(path)}  bundle_manifest.json\n":
        raise RuntimeError(label + " manifest sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(value)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != path.parent.name
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise RuntimeError(label + " manifest identity/scope differs")
    return value


def copy(stage: Path, source: Path, relative: str) -> None:
    source = regular(source, relative)
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def main() -> int:
    sources = {
        "materializer/mysql_tpcc_v16_current_args_materializer_v1.py": MATERIALIZER,
        "builder/build_mysql_tpcc_v16_current_args_materializer_successor_v1.py": BUILDER,
        "tests/test_mysql_tpcc_v16_current_args_materializer_v1.py": TEST,
        "templates/mysql_tpcc_v16_current_args_materializer_invocation_v1.json": TEMPLATE,
    }
    dependency_identity = {}
    for label, manifest_path in DEPENDENCIES.items():
        value = verify_manifest(manifest_path, label)
        dependency_identity[label] = {
            "bundle_id": value["bundle_id"],
            "manifest_sha256": sha256(manifest_path),
        }
        sources[f"authority/{label}/bundle_manifest.json"] = manifest_path
        sources[f"authority/{label}/bundle_manifest.json.sha256"] = Path(
            str(manifest_path) + ".sha256")
    FAMILY.mkdir(parents=True, exist_ok=True)
    stage = FAMILY / (".stage-" + secrets.token_hex(8))
    stage.mkdir(mode=0o700)
    try:
        for relative, source in sources.items():
            copy(stage, source, relative)
        files = {
            str(path.relative_to(stage)): sha256(path)
            for path in sorted(stage.rglob("*")) if path.is_file()
        }
        manifest = {
            "schema_version": "mysql_tpcc_v16_current_args_materializer_successor_v1",
            "status": "SEALED_OFFLINE_MATERIALIZER_NOT_EXECUTED_NOT_FORMAL_AUTHORITY",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "dependencies": dependency_identity,
            "supersedes_bundle_id": "eec6f6bc0abe6edb81ff171a74b7a3fe3b685d765f9309a92c8f44febd97d63a",
            "entrypoint": "materializer/mysql_tpcc_v16_current_args_materializer_v1.py",
            "invocation_template": "templates/mysql_tpcc_v16_current_args_materializer_invocation_v1.json",
            "test_entrypoint": "tests/test_mysql_tpcc_v16_current_args_materializer_v1.py",
            "fresh_a336_snapshot_maximum_age_seconds": 120,
            "authority_ttl_maximum_seconds": 1800,
            "four_unique_specs_authorities_and_tokens_required": True,
            "static_pid_authority_prohibited": True,
            "authority_reuse_prohibited": True,
            "tpch_module_parent_temporarily_prepended_to_sys_path": True,
            "tpch_import_context_restored_in_finally": True,
            "tpch_import_source_hashes_exact": {
                "mysql_tpch_distributed_v2_authority.py": "120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408",
                "mysql_tpch_client_immutable_identity_v1.py": "8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3",
            },
            "real_ff313_import_integration_tested": True,
            "secret_material_in_bundle": False,
            "remote_execution_implemented": False,
            "database_or_container_mutation_implemented": False,
            "debug_only": True,
            "capture_authorized": False,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
            "files": files,
        }
        bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = bundle_id
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        sidecar = stage / "bundle_manifest.json.sha256"
        sidecar.write_text(f"{sha256(manifest_path)}  bundle_manifest.json\n", encoding="ascii")
        target = FAMILY / bundle_id
        if target.exists() or target.is_symlink():
            raise RuntimeError("refusing to overwrite existing successor bundle")
        for path in stage.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
            elif path.is_dir():
                os.chmod(path, 0o555)
        os.chmod(stage, 0o555)
        os.rename(stage, target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({
        "status": manifest["status"], "bundle": str(target),
        "bundle_id": bundle_id, "manifest_sha256": sha256(target / "bundle_manifest.json"),
        "accepted_round_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
