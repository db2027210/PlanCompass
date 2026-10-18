#!/usr/bin/env python3
"""Launch one closure-bound deterministic four-terminal TPC-H v33 fresh R0."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback
from typing import Any, Mapping


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"required regular file is absent: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def write(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--launch-token", required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    manifest = load(bundle / "launch_manifest.json")
    files = manifest.get("files") or {}
    if (
        bundle.name != manifest.get("bundle_id")
        or manifest.get("schema_version") != "mysql_tpch_v33_r0_launch_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("fresh_r0_authorized") is not True
        or manifest.get("outer_execution_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or not isinstance(files, Mapping)
    ):
        raise RuntimeError("TPC-H v33 launch bundle identity/authorization differs")
    for relative, expected in files.items():
        path = bundle / str(relative)
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v33 launch content differs: {relative}")

    source = Path(str(manifest["source_bundle"]["directory"])).resolve(strict=True)
    source_manifest = load(source / "source_manifest.json")
    if (
        source.name != f"source-v33-{source_manifest.get('bundle_id')}"
        or source_manifest.get("schema_version")
        != "mysql_tpch_local_deterministic_source_v33"
        or source_manifest.get("formal_scope") != "mysql_only"
        or source_manifest.get("cross_engine_comparison_prohibited") is not True
        or (source_manifest.get("runtime_contract") or {}).get("terminals") != 4
        or (source_manifest.get("runtime_contract") or {}).get(
            "benchbase_jar_sha256"
        )
        != "6f3fa2c886a2fb8ed8149825b40e337e835e44ebbaf6c667866d73ffed22890e"
    ):
        raise RuntimeError("TPC-H v33 deterministic source authority differs")
    for relative, expected in source_manifest["files"].items():
        path = source / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v33 source content differs: {relative}")

    config = dict(load(bundle / "config.json"))
    profile = load(bundle / "profile.json")
    config["profile"] = str((bundle / "profile.json").resolve(strict=True))
    output = Path(str((config.get("output") or {}).get("directory") or ""))
    if (
        config.get("formal_scope") != "mysql_only"
        or config.get("cross_engine_comparison_prohibited") is not True
        or config.get("fresh_r0_only") is not True
        or config.get("outer_execution_authorized") is not False
        or (config.get("workload") or {}).get("tpch_terminals") != 4
        or profile.get("cpu_cores") != 4.5
        or profile.get("read_bps") != 603979776
        or profile.get("write_bps") != 603979776
        or output.exists()
    ):
        raise RuntimeError("TPC-H v33 config/profile/output freshness differs")

    sys.path.insert(0, str(source))
    from mysql_algorithm_c_local_services import build

    output.mkdir(parents=True, exist_ok=False)
    state_path = output / "v33_r0_driver_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_tpch_v33_r0_driver_state_v1",
        "state": "STARTING_FRESH_R0",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "launch_token": args.launch_token,
        "launch_bundle_id": bundle.name,
        "source_bundle_id": source_manifest["bundle_id"],
        "outer_execution_authorized": False,
        "accepted_round_authorized": False,
    }
    write(state_path, state)
    runtime = build(config)
    try:
        result = runtime["r0_measurement"](config)
        result_path = output / "v33_r0_result.json"
        write(result_path, result)
        state.update(
            {
                "state": "R0_COMPLETED",
                "result": str(result_path),
                "result_sha256": sha256(result_path),
            }
        )
        return 0
    except BaseException as error:
        state.update(
            {
                "state": "R0_FAILED",
                "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc(),
            }
        )
        raise
    finally:
        try:
            cleanup = runtime.get("campaign_cleanup")
            if callable(cleanup):
                state["cleanup"] = cleanup()
        finally:
            write(state_path, state)


if __name__ == "__main__":
    raise SystemExit(main())
