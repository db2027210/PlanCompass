#!/usr/bin/env python3
"""Verify and run the sealed, self-contained TPC-C bundle test closure."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> None:
    raise SystemExit(f"SEALED_TPCC_BUNDLE_TEST_FAIL: {message}")


def main() -> int:
    bundle = Path(__file__).resolve().parents[1]
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        bundle.is_symlink()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or sidecar.is_symlink()
        or not sidecar.is_file()
    ):
        fail("bundle or manifest authority is absent/symlinked")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if (
        manifest.get("bundle_id") != bundle.name
        or manifest.get("immutable") is not True
        or not isinstance(files, dict)
    ):
        fail("manifest identity differs")
    expected_sidecar = f"{sha256(manifest_path)}  bundle_manifest.json\n"
    if sidecar.read_text(encoding="ascii") != expected_sidecar:
        fail("manifest sidecar differs")
    for relative, digest in sorted(files.items()):
        target = bundle / str(relative)
        if (
            not isinstance(digest, str)
            or target.is_symlink()
            or not target.is_file()
            or sha256(target) != digest
        ):
            fail(f"sealed file differs: {relative}")

    required_payload = {
        "collector/tpcc_typed_collector.py",
        "config/tpcc_offline_binding_manifest.json",
        "capture/mysql_tpcc_typed_capture_entry_v1.py",
        "capture/mysql_tpcc_typed_interference_gate_v1.py",
        "capture/mysql_process_classification.py",
        "runtime/benchbase-formal.jar",
        "source/TpccTypedTrace.java",
        "tests/run_sealed_bundle_tests.py",
    }
    if not required_payload.issubset(files):
        fail(
            "required payload is absent: "
            + ", ".join(sorted(required_payload - set(files)))
        )
    test_paths = sorted((bundle / "tests").glob("test_*.py"))
    if len(test_paths) != 10:
        fail(f"expected exactly 10 sealed test modules, got {len(test_paths)}")
    prohibited = (
        "/home/anonymous/program/resource_adaptive_mysql",
        "artifacts/mysql_tpcc_",
        "formal_benchbase_src_",
        'ROOT / "shell',
        "shell.mysql_",
    )
    for test in test_paths:
        source = test.read_text(encoding="utf-8")
        leaked = [marker for marker in prohibited if marker in source]
        if leaked:
            fail(f"{test.name} contains external path markers: {leaked}")

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        str(bundle),
        *(str(path) for path in test_paths),
    ]
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        fail(f"pytest returned {completed.returncode}")
    print(
        json.dumps(
            {
                "schema_version":
                    "mysql_tpcc_typed_sealed_bundle_tests_v1",
                "status": "PASS",
                "bundle_id": bundle.name,
                "caller_cwd": str(Path.cwd()),
                "test_module_count": len(test_paths),
                "manifest_sha256": sha256(manifest_path),
                "external_workspace_paths_used": [],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
