#!/usr/bin/env python3
"""Publish a TPC-H v24 local-no-SSH source closure with DB coverage gating."""

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
    "source-v19-189837cc94b3674551a6d366daf16b6f7bbb89c791ffe293eb135495da73c269"
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
    )


def verify(destination: Path, bundle_id: str) -> None:
    manifest = json.loads((destination / "source_manifest.json").read_text())
    if manifest.get("bundle_id") != bundle_id:
        raise RuntimeError("TPC-H v24 source bundle id differs")
    for relative, expected in manifest["files"].items():
        if digest(destination / relative) != expected:
            raise RuntimeError(f"TPC-H v24 source file mismatch: {relative}")


def main() -> int:
    build_root = Path(tempfile.mkdtemp(prefix="tpch-v24-source.", dir="/tmp"))
    staging = build_root / "source"
    try:
        for source in sorted(BASE.rglob("*")):
            if include(source):
                destination = staging / source.relative_to(BASE)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        shutil.copyfile(
            ROOT / "shell/manual_mysql_sgd_r0_campaign.py",
            staging / "shell/manual_mysql_sgd_r0_campaign.py",
        )
        shutil.copyfile(
            ROOT / "shell/test_mysql_database_counter_coverage_gate.py",
            staging / "test_mysql_database_counter_coverage_gate.py",
        )
        protocol = (
            ROOT
            / "preflight/frozen_formal_config_20260727_v10/"
            "measurement_protocol.json"
        )
        protocol_destination = (
            staging
            / "preflight/frozen_formal_config_20260727_v10/"
            "measurement_protocol.json"
        )
        protocol_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(protocol, protocol_destination)
        agent = ROOT / "formal_clients_20260721/tpch-seed-agent.jar"
        agent_destination = staging / "formal_clients_20260721/tpch-seed-agent.jar"
        agent_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(agent, agent_destination)
        files = {
            str(path.relative_to(staging)): digest(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        contract = {
            "schema_version": "mysql_tpch_local_source_v24",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "transport": "local_no_ssh",
            "host": "compute00",
            "database_counter_gate": {
                "sampler_count": 2,
                "minimum_coverage_per_sampler": 0.90,
                "maximum_success_gap_ms": 10000,
                "fail_closed": True,
            },
            "files": files,
        }
        bundle_id = hashlib.sha256(canonical(contract)).hexdigest()
        manifest = dict(contract)
        manifest["bundle_id"] = bundle_id
        (staging / "source_manifest.json").write_bytes(canonical(manifest))
        destination = OUTPUT_ROOT / f"source-v24-{bundle_id}"
        if destination.exists():
            verify(destination, bundle_id)
            print(f"TPCH_V24_SOURCE_ALREADY_PUBLISHED {destination}")
            return 0
        publishing = OUTPUT_ROOT / f".source-v24-{bundle_id}.{os.getpid()}"
        os.rename(staging, publishing)
        for path in sorted(publishing.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(publishing, 0o555)
        try:
            os.rename(publishing, destination)
        except FileExistsError:
            verify(destination, bundle_id)
        verify(destination, bundle_id)
        print(f"TPCH_V24_SOURCE_PUBLISHED {destination}")
        return 0
    finally:
        if build_root.exists():
            shutil.rmtree(build_root)


if __name__ == "__main__":
    raise SystemExit(main())
