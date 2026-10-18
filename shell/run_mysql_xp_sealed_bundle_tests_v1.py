#!/usr/bin/env python3
"""Verify and test one immutable XP bundle without workspace import leakage."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile


BUNDLE = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BUNDLE.parents[2]
TEMP_ROOT = Path(tempfile.gettempdir()).resolve()
BUNDLE_PATHS = tuple(
    BUNDLE / name
    for name in ("python", "orchestration", "driver", "capture", "campaign", "tests")
)
TESTS = (
    BUNDLE / "tests/test_formal_runtime_estimator_baseline.py",
    BUNDLE / "tests/test_mysql_xp_formal_authority_chain_v1.py",
    BUNDLE / "tests/test_mysql_benchmark_monitor_observer_signature_v1.py",
    BUNDLE / "tests/test_mysql_four_scene_shared_snapshot_observer_v2.py",
    BUNDLE / "tests/test_mysql_xp_capture_evidence_collector_v9.py",
    BUNDLE / "capture/test_mysql_xp_local_capture_entry.py",
    BUNDLE / "capture/test_mysql_xp_capture_at_coverage_seal.py",
)
SEALED_MODULES = (
    "formal_mysql_sgd_campaign",
    "shell.build_mysql_xp_immutable_bundle_v8",
    "shell.mysql_four_scene_shared_snapshot",
    "shell.mysql_process_classification_v10",
    "shell.mysql_xp_capture_evidence_collector_v9",
    "shell.mysql_xp_formal_r0_entry_v1",
    "shell.mysql_xp_launch_contract_v2",
    "shell.seal_mysql_xp_capture_at_coverage",
)
FORBIDDEN_TEST_SOURCE_MARKERS = (
    str(TEMP_ROOT / "mysql_xp_trace_fix"),
    str(Path("history") / "mysql_gradient_debug"),
    str(WORKSPACE_ROOT),
    "sys.path" + ".insert",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _verify_bundle() -> dict:
    manifest_path = BUNDLE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("bundle_id") != BUNDLE.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("sealed test runner bundle identity differs")
    for relative, expected in manifest.get("payload_files", {}).items():
        path = BUNDLE / relative
        if not path.is_file() or _sha256(path) != expected:
            raise RuntimeError(f"sealed payload hash mismatch: {relative}")
    for test in TESTS:
        if not test.is_file() or not _inside(test, BUNDLE):
            raise RuntimeError(f"sealed test path is absent or external: {test}")
        source = test.read_text(encoding="utf-8")
        found = [
            marker for marker in FORBIDDEN_TEST_SOURCE_MARKERS
            if marker in source
        ]
        if found:
            raise RuntimeError(
                f"sealed test contains external import authority: "
                f"{test.name}: {found}"
            )
    return manifest


def _sanitize_imports() -> None:
    bundle = BUNDLE.resolve()
    retained = []
    for value in sys.path:
        if not value:
            continue
        try:
            resolved = Path(value).resolve()
        except OSError:
            continue
        if _inside(resolved, bundle):
            continue
        if (
            _inside(resolved, WORKSPACE_ROOT)
            or _inside(resolved, TEMP_ROOT)
        ):
            continue
        retained.append(str(resolved))
    sys.path[:] = [str(path.resolve()) for path in BUNDLE_PATHS] + retained
    os.environ.pop("PYTHONPATH", None)
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"


def _sealed_import_provenance() -> dict[str, str]:
    origins = {}
    for name in SEALED_MODULES:
        module = importlib.import_module(name)
        origin = Path(str(module.__file__)).resolve()
        if not _inside(origin, BUNDLE):
            raise RuntimeError(
                f"sealed module resolved outside bundle: {name}={origin}"
            )
        origins[name] = str(origin.relative_to(BUNDLE))
    return origins


def main() -> int:
    manifest = _verify_bundle()
    _sanitize_imports()
    origins = _sealed_import_provenance()
    import pytest

    with tempfile.TemporaryDirectory(prefix="mysql_xp_sealed_test_") as raw:
        clean_cwd = Path(raw)
        pytest_config = clean_cwd / "pytest.ini"
        pytest_config.write_text("[pytest]\n", encoding="utf-8")
        os.chdir(clean_cwd)
        result = pytest.main([
            "-q",
            "-c",
            str(pytest_config),
            *map(str, TESTS),
        ])
    final_origins = _sealed_import_provenance()
    if origins != final_origins:
        raise RuntimeError("sealed module provenance changed during tests")
    document = {
        "status": "PASS" if result == 0 else "FAIL",
        "bundle_id": manifest["bundle_id"],
        "manifest_sha256": _sha256(BUNDLE / "manifest.json"),
        "payload_file_count": len(manifest["payload_files"]),
        "test_file_count": len(TESTS),
        "sealed_module_origins": origins,
        "external_workspace_or_legacy_tmp_imports_allowed": False,
    }
    print(json.dumps(document, sort_keys=True))
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
