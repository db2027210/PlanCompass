#!/usr/bin/env python3
"""Publish a TPC-H v27 local source closure with current authority overrides."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1/"
    "source-v24-6e3baf1d78a00ce039491cb44695ad3d8578f8a1950dc4a07bfe0a55d79abd50"
)
OUTPUT_ROOT = ROOT / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def include(path: Path) -> bool:
    return (
        path.is_file()
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and not path.name.endswith((".pyc", ".pyo"))
        and path.name != "source_manifest.json"
    )


def verify(destination: Path, bundle_id: str) -> None:
    manifest = json.loads((destination / "source_manifest.json").read_text())
    if manifest.get("bundle_id") != bundle_id:
        raise RuntimeError("TPC-H v27 source bundle id differs")
    for relative, expected in manifest["files"].items():
        if digest(destination / relative) != expected:
            raise RuntimeError(f"TPC-H v27 source file mismatch: {relative}")


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    build_root = Path(
        tempfile.mkdtemp(prefix=".tpch-v27-source.", dir=OUTPUT_ROOT)
    )
    staging = build_root / "source"
    try:
        for source in sorted(BASE.rglob("*")):
            if include(source):
                copy_file(source, staging / source.relative_to(BASE))

        # BASE is only a scaffold.  Every current project/shell Python source
        # is an explicit authoritative override, including orchestration.
        for source in sorted(ROOT.glob("*.py")):
            copy_file(source, staging / source.name)
        for source in sorted((ROOT / "shell").glob("*.py")):
            copy_file(source, staging / "shell" / source.name)

        copy_file(
            ROOT / "shell/mysql_algorithm_c_local_services_v25.py",
            staging / "mysql_algorithm_c_local_services.py",
        )
        copy_file(
            ROOT / "shell/debug_mysql_gradient_round_v25.py",
            staging / "debug_mysql_gradient_round.py",
        )
        protocol = (
            ROOT
            / "preflight/frozen_formal_config_20260727_v10/"
            "measurement_protocol.json"
        )
        copy_file(
            protocol,
            staging
            / "preflight/frozen_formal_config_20260727_v10/"
            "measurement_protocol.json",
        )
        copy_file(
            ROOT / "formal_clients_20260721/tpch-seed-agent.jar",
            staging / "formal_clients_20260721/tpch-seed-agent.jar",
        )
        files = {
            str(path.relative_to(staging)): digest(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        contract = {
            "schema_version": "mysql_tpch_local_source_v27",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "transport": "local_no_ssh",
            "host": "compute00",
            "source_precedence": {
                "base_role": "scaffold_only",
                "project_root_python": "authoritative_override",
                "project_shell_python": "authoritative_override",
            },
            "stability_gate": {
                "decision_resources_from_session_evaluator": True,
                "latest_six_windows_only": True,
                "minimum_client_completions_enforced": True,
            },
            "database_counter_gate": {
                "sampler_count": 2,
                "minimum_coverage_per_sampler": 0.90,
                "maximum_success_gap_ms": 10000,
                "fail_closed": True,
            },
            "host_affinity_gate": {
                "controller_and_workload_exact_cpuset_readback": True,
                "fail_closed_before_r0_running": True,
            },
            "files": files,
        }
        bundle_id = hashlib.sha256(canonical(contract)).hexdigest()
        manifest = dict(contract)
        manifest["bundle_id"] = bundle_id
        (staging / "source_manifest.json").write_bytes(canonical(manifest))
        destination = OUTPUT_ROOT / f"source-v27-{bundle_id}"
        if destination.exists():
            verify(destination, bundle_id)
            print(f"TPCH_V27_SOURCE_ALREADY_PUBLISHED {destination}")
            return 0
        publishing = OUTPUT_ROOT / f".source-v27-{bundle_id}.{os.getpid()}"
        os.rename(staging, publishing)
        for path in sorted(publishing.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(publishing, 0o555)
        try:
            os.rename(publishing, destination)
        except FileExistsError:
            verify(destination, bundle_id)
        verify(destination, bundle_id)
        print(f"TPCH_V27_SOURCE_PUBLISHED {destination}")
        return 0
    finally:
        if build_root.exists():
            shutil.rmtree(build_root)


if __name__ == "__main__":
    raise SystemExit(main())
