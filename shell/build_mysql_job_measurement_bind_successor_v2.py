#!/usr/bin/env python3
"""Build the immutable JOB measurement-bind authority successor.

This is local/offline.  It reuses the verified transactional JOB publisher,
adds both zero-credit predecessor invalidations, and seals the collector that
binds collect-only to the exact attempt-owned host tmpfs.
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
EA888_INVALIDATION = (
    ROOT
    / "preflight/mysql_job_v32_ea888_client_tcp_readiness_invalidation_20260731.json"
)
V33_INVALIDATION = (
    ROOT
    / "preflight/mysql_job_v33_9fe3_measurement_bind_invalidation_20260731.json"
)
SOURCE_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_job_measurement_bind_successor"
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v34_measurement_bind_successor"


class BuildError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def base_module():
    spec = importlib.util.spec_from_file_location("job_v32_publisher", BASE_PATH)
    if spec is None or spec.loader is None:
        raise BuildError("base JOB publisher is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_invalidations() -> None:
    ea888 = load(EA888_INVALIDATION)
    v33 = load(V33_INVALIDATION)
    if (
        ea888.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or ea888.get("bundle_id")
        != "ea8880956d5a08edfe95a9883c2d236b105774da7a450adfae8f60e238edbfa3"
        or ea888.get("accepted_rounds_credit") != 0
        or ea888.get("workload_started") is not False
    ):
        raise BuildError("ea888 invalidation differs")
    if (
        v33.get("schema_version")
        != "mysql_job_v33_measurement_bind_invalidation_v1"
        or v33.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or v33.get("bundle_id")
        != "9fe3ae69e07d2a445ffba52b4b033bee436d34c5b61afef09de6c6c235f433e6"
        or v33.get("bundle_manifest_sha256")
        != "cd76e59d702448389b03f729f85b3c8b29e940ea79eb9e290e357251758e8d65"
        or v33.get("database_and_client_started") is not True
        or v33.get("workload_started") is not False
        or v33.get("accepted_rounds_credit") != 0
        or v33.get("formal_launch_authorized") is not False
        or v33.get("replay_or_rerun_authorized") is not False
    ):
        raise BuildError("9fe3 invalidation differs")


def build() -> dict:
    verify_invalidations()
    base = base_module()
    base.BUILDER = HERE
    base.COLLECTOR = COLLECTOR
    base.SOURCE_ROOT = SOURCE_ROOT
    base.BUNDLE_ROOT = BUNDLE_ROOT
    original_source_payloads = base._source_payloads
    original_bundle_payloads = base._bundle_payloads

    additions = {
        "authority/mysql_job_v32_ea888_client_tcp_readiness_invalidation_20260731.json":
            EA888_INVALIDATION.read_bytes(),
        "authority/mysql_job_v33_9fe3_measurement_bind_invalidation_20260731.json":
            V33_INVALIDATION.read_bytes(),
    }

    def source_payloads():
        payloads = original_source_payloads()
        payloads.update(additions)
        return payloads

    def bundle_payloads(source, source_manifest):
        payloads = original_bundle_payloads(source, source_manifest)
        payloads.update(additions)
        return payloads

    base._source_payloads = source_payloads
    base._bundle_payloads = bundle_payloads
    result = base.build()
    result["schema_version"] = (
        "mysql_job_measurement_bind_successor_build_result_v2"
    )
    result["ea888_invalidation_sha256"] = sha256(EA888_INVALIDATION)
    result["v33_9fe3_invalidation_sha256"] = sha256(V33_INVALIDATION)
    result["collect_only_measurement_bind_required"] = True
    result["accepted_round_authorized"] = False
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(build(), indent=2, sort_keys=True))
    except (BuildError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
