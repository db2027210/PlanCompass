#!/usr/bin/env python3
"""Seal the local XP safe-frontier recovery package without launching it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import os
from typing import Any, Dict, Mapping


BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6")
BASE_MANIFEST = BUNDLE / "bundle_manifest_v12.json"
OUTPUT = BUNDLE / "bundle_manifest_v16_safe_frontier.json"
PROJECT = Path("/home/anonymous/program/resource_adaptive")
LOCAL_TOOLS = (
    PROJECT / "tools/build_xp_incumbent_revalidation_candidate.py",
    PROJECT / "tools/run_xp_incumbent_revalidation_once.py",
)
TOOL_DIRECTORY = BUNDLE / "safe_frontier_tools_v16"
EXTRA_BUNDLE_FILES = (
    "authority_refresh_accepted4_v4_20260731/query_snapshot_v3.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    if OUTPUT.exists() or TOOL_DIRECTORY.exists():
        raise FileExistsError("XP v16 package output already exists")
    base = json.loads(BASE_MANIFEST.read_text(encoding="utf-8"))
    if (
        base.get("contract") != "xp_round10_explain_only_bundle_manifest_v1"
        or not isinstance(base.get("files"), dict)
    ):
        raise RuntimeError("XP base manifest contract mismatch")
    TOOL_DIRECTORY.mkdir(mode=0o700)
    copied = []
    for source in LOCAL_TOOLS:
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = TOOL_DIRECTORY / source.name
        shutil.copy2(source, destination)
        copied.append(destination)

    names = set(base["files"])
    names.update(EXTRA_BUNDLE_FILES)
    names.update(
        str(path.relative_to(BUNDLE).as_posix())
        for path in copied
    )
    files: Dict[str, str] = {}
    for name in sorted(names):
        path = BUNDLE / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"XP package input missing or symlink: {name}")
        files[name] = sha256(path)
    manifest = {
        "contract": "xp_round10_explain_only_bundle_manifest_v1",
        "files": files,
        "algorithm_contract": (
            "xp_exact_plan_safe_frontier_incumbent_revalidation_v16"
        ),
        "diagnostic_discovery_contract": (
            "incumbent_revalidate_once_then_safe_noncorner_"
            "trusted3_probe2_v1"
        ),
        "corner_inset_contract": "excluded_from_novel_noncorner_pool_v2",
        "corner_inset_points_are_probe_only": False,
        "corner_inset_points_are_excluded": True,
        "formal_admission_score": "predicted_score_interval.lower",
        "formal_miss_routes_to_diagnostic": True,
        "maximum_relative_step": 0.80,
        "safe_frontier_utilization": {
            "cpu": 0.90,
            "io": 0.95,
            "memory": 0.90,
        },
        "incumbent_revalidation": {
            "maximum_physical_workload_invocations": 1,
            "retry_count": 0,
            "diagnostic_only": True,
            "state_neutral": True,
        },
        "affinity": {
            "cpuset_cpus": "32-51,288-307",
            "excluded_mysql_cpus": "184-199,440-455",
        },
        "launch_separate_from_deploy": True,
        "network_actions": False,
        "workload_actions": False,
    }
    atomic_json(OUTPUT, manifest)
    observed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    for name, expected in observed["files"].items():
        if sha256(BUNDLE / name) != expected:
            raise RuntimeError(f"post-write XP SHA mismatch: {name}")
    print(
        json.dumps(
            {
                "status": "sealed_no_workload_started",
                "manifest": str(OUTPUT),
                "manifest_sha256": sha256(OUTPUT),
                "file_count": len(files),
                "builder_sha256": sha256(copied[0]),
                "runner_sha256": sha256(copied[1]),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
