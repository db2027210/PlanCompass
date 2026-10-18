#!/usr/bin/env python3
"""Seal a JOB successor that replaces ea888's host-port probe.

The implementation reuses the verified v32 publisher transaction while
changing its collector, builder identity, source/bundle destinations, and
adding an explicit zero-credit ea888 invalidation.  It is local-only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
BASE_PATH = ROOT / "shell/build_mysql_job_distributed_v2_tmpfs_successor.py"
COLLECTOR = ROOT / "shell/mysql_job_distributed_live_readback_qualification_v2.py"
INVALIDATION = ROOT / "preflight/mysql_job_v32_ea888_client_tcp_readiness_invalidation_20260731.json"
SOURCE_ROOT = ROOT / "history/mysql_gradient_debug/readiness_mysql_job_client_tcp_readiness_successor"
BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v33_client_tcp_readiness_successor"


class BuildError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _base():
    spec = importlib.util.spec_from_file_location("job_v32_publisher", BASE_PATH)
    if spec is None or spec.loader is None:
        raise BuildError("base JOB publisher is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_invalidation() -> None:
    if INVALIDATION.is_symlink() or not INVALIDATION.is_file():
        raise BuildError("ea888 readiness invalidation is absent")
    value = json.loads(INVALIDATION.read_text(encoding="utf-8"))
    if (
        value.get("schema_version")
        != "mysql_job_v32_client_tcp_readiness_invalidation_v1"
        or value.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or value.get("bundle_id")
        != "ea8880956d5a08edfe95a9883c2d236b105774da7a450adfae8f60e238edbfa3"
        or value.get("bundle_manifest_sha256")
        != "607481ef04ba9967e3d710aa810a2c67d23538e1e7572e32d73b29e60faf2caa"
        or value.get("database_and_client_started") is not True
        or value.get("workload_started") is not False
        or value.get("accepted_rounds_credit") != 0
        or value.get("formal_r0_authorized") is not False
        or value.get("formal_launch_authorized") is not False
        or value.get("replay_or_rerun_authorized") is not False
    ):
        raise BuildError("ea888 readiness invalidation differs")


def build() -> dict:
    verify_invalidation()
    base = _base()
    base.BUILDER = HERE
    base.COLLECTOR = COLLECTOR
    base.SOURCE_ROOT = SOURCE_ROOT
    base.BUNDLE_ROOT = BUNDLE_ROOT
    original_source_payloads = base._source_payloads
    original_bundle_payloads = base._bundle_payloads

    def source_payloads():
        payloads = original_source_payloads()
        payloads["authority/mysql_job_v32_ea888_client_tcp_readiness_invalidation_20260731.json"] = INVALIDATION.read_bytes()
        return payloads

    def bundle_payloads(source, source_manifest):
        payloads = original_bundle_payloads(source, source_manifest)
        payloads["authority/mysql_job_v32_ea888_client_tcp_readiness_invalidation_20260731.json"] = INVALIDATION.read_bytes()
        return payloads

    base._source_payloads = source_payloads
    base._bundle_payloads = bundle_payloads
    result = base.build()
    result["schema_version"] = "mysql_job_client_tcp_readiness_successor_build_result_v1"
    result["ea888_client_tcp_readiness_invalidation_sha256"] = sha256(INVALIDATION)
    result["host_port_probe_authorized"] = False
    result["client_internal_tcp_readiness_required"] = True
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(build(), indent=2, sort_keys=True))
    except (BuildError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
