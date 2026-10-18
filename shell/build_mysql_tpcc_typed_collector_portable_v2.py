#!/usr/bin/env python3
"""Publish a portable, content-addressed TPC-C typed collector successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
PARENT = ROOT / "artifacts/mysql_tpcc_typed_collector" / PARENT_ID
COLLECTOR_SHA = "f95c21304314474eaca1930fc3fd14931e3034f2c0ecd875813fa53b7d227994"
PARENT_TEST_SHA = "6c23587a3f05dc801e6a6dfcb7cc0dd3afadde5fd32f6646b8e64c2d8c285102"
README_SHA = "03792e4ff6724d032f07897bd33d9b8f0caccc23197fbe523b462c333963ce5d"
MANIFEST = (
    ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v13"
    / "4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
    / "predecessor/config/tpcc_offline_binding_manifest.json"
)
MANIFEST_SHA = "689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182"
TEST = ROOT / "shell/test_mysql_tpcc_typed_collector_portable_v2.py"
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpcc_typed_collector_portable_v2"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def checked(path: Path, expected: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BuildError("source absent or symlinked: " + str(path))
    payload = path.read_bytes()
    if digest(payload) != expected:
        raise BuildError("source identity differs: " + str(path))
    return payload


def verify(bundle: Path, manifest: dict) -> None:
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if bundle.name != digest(canonical(unsigned)):
        raise BuildError("bundle content address differs")
    for relative, expected in manifest["files"].items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise BuildError("bundle closure differs: " + relative)
    raw = (bundle / "bundle_manifest.json").read_bytes()
    if (
        json.loads(raw) != manifest
        or (bundle / "bundle_manifest.json.sha256").read_text(encoding="ascii")
        != digest(raw) + "  bundle_manifest.json\n"
    ):
        raise BuildError("bundle manifest sidecar differs")
    for path in [bundle, *bundle.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published bundle is mutable: " + str(path))


def main() -> int:
    files = {
        "tpcc_typed_collector.py": checked(PARENT / "tpcc_typed_collector.py", COLLECTOR_SHA),
        "parent_test_tpcc_typed_collector.py": checked(
            PARENT / "test_tpcc_typed_collector.py", PARENT_TEST_SHA
        ),
        "README.md": checked(PARENT / "README.md", README_SHA),
        "tpcc_offline_binding_manifest.json": checked(MANIFEST, MANIFEST_SHA),
        "test_tpcc_typed_collector_portable_v2.py": TEST.read_bytes(),
        "build_mysql_tpcc_typed_collector_portable_v2.py": HERE.read_bytes(),
    }
    identity = {
        "schema_version": "mysql_tpcc_typed_collector_portable_v2",
        "status": "SEALED_PORTABLE_OFFLINE_TYPED_COLLECTOR",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_payload_id": PARENT_ID,
        "predecessor_collector_sha256": COLLECTOR_SHA,
        "binding_manifest_sha256": MANIFEST_SHA,
        "binding_count": 34,
        "required_transaction_types": [
            "Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"
        ],
        "sensitive_stock_level_normalized_to": "StockLevel",
        "sql_template_hash_required": True,
        "jdbc_setter_type_position_required": True,
        "l2_transaction_rows_required": True,
        "l3_transaction_type_vectors_required": True,
        "qps_resource_input_prohibited": True,
        "portable_regression_count": 7,
        "capture_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {name: digest(payload) for name, payload in sorted(files.items())},
    }
    bundle_id = digest(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_ROOT / bundle_id
    if not target.exists():
        stage = OUTPUT_ROOT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, payload in sorted(files.items()):
                path = stage / relative
                path.write_bytes(payload)
                os.chmod(path, 0o444)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                digest(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            for path in stage.iterdir():
                if path.is_file():
                    os.chmod(path, 0o444)
            os.chmod(stage, 0o555)
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                os.chmod(stage, 0o700)
                for path in stage.iterdir():
                    if path.is_file():
                        os.chmod(path, 0o600)
                shutil.rmtree(stage)
            raise
    verify(target, manifest)
    print(json.dumps({
        "status": "PASS_IMMUTABLE_TPCC_TYPED_COLLECTOR_PORTABLE_V2",
        "bundle_id": bundle_id,
        "bundle": str(target),
        "bundle_manifest_sha256": digest((target / "bundle_manifest.json").read_bytes()),
        "portable_regression_count": 7,
        "capture_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
