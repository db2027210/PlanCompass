#!/usr/bin/env python3
"""Read-only fail-closed preflight for the TPC-C v29 handover."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

try:
    import tpcc_autonomous_target10_controller_v25 as v25
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25


ACTIVE_WORKLOAD_MARKERS = (
    "tpcc_autonomous_target10_controller_v",
    "run_tpcc_round11_calibrated_top5_strict_serial.py",
    "run_tpcc_sensitive_stable_point.py",
)


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_processes(markers: Iterable[str]) -> list[Dict[str, Any]]:
    current_pid = Path("/proc/self").resolve().name
    matches: list[Dict[str, Any]] = []
    for cmdline_path in Path("/proc").glob("[0-9]*/cmdline"):
        if cmdline_path.parent.name == current_pid:
            continue
        try:
            cmdline = cmdline_path.read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if any(marker in cmdline for marker in markers):
            matches.append(
                {"pid": int(cmdline_path.parent.name), "cmdline": cmdline}
            )
    return matches


def validate_boundary(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    formal_state_path = Path(str(manifest["formal_state_path"])).resolve()
    controller_state_path = Path(
        str(manifest["controller_state_path"])
    ).resolve()
    formal_state = load_json(formal_state_path)
    controller_state = load_json(controller_state_path)
    if sha256(formal_state_path) != manifest["initial_formal_state_sha256"]:
        raise RuntimeError("formal state SHA changed before v29 launch")
    if (
        int(formal_state.get("accepted_count", -1)) != 2
        or int(formal_state.get("accepted_target", -1)) != 10
    ):
        raise RuntimeError("formal accepted boundary is not exactly 2/10")
    if sha256(controller_state_path) != manifest[
        "initial_controller_state_sha256"
    ]:
        raise RuntimeError("controller state SHA changed before v29 launch")
    expected = manifest["controller_state_boundary"]
    observed = {
        "status": controller_state.get("status"),
        "next_mode": controller_state.get("next_mode"),
        "next_ordinal": controller_state.get("next_ordinal"),
        "current_run": controller_state.get("current_run"),
    }
    if observed != expected:
        raise RuntimeError(
            f"controller state boundary mismatch: {observed} != {expected}"
        )
    partial = manifest["quarantined_zero_call_partial"]
    partial_root = Path(str(partial["path"])).resolve()
    expected_files = {
        item["name"]: item["sha256"] for item in partial["files"]
    }
    observed_files = {
        path.name: sha256(path)
        for path in partial_root.iterdir()
        if path.is_file()
    }
    if observed_files != expected_files:
        raise RuntimeError("zero-call partial quarantine changed")
    running = active_processes(ACTIVE_WORKLOAD_MARKERS)
    if running:
        raise RuntimeError(f"TPC-C controller/workload already active: {running}")
    return {
        "status": "passed",
        "accepted_count": 2,
        "accepted_target": 10,
        "formal_state_sha256": sha256(formal_state_path),
        "controller_state_sha256": sha256(controller_state_path),
        "controller_state_boundary": observed,
        "quarantined_zero_call_partial": str(partial_root),
        "active_tpcc_processes": [],
        "workload_calls_added": 0,
        "formal_state_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    if sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("manifest SHA mismatch")
    manifest = load_json(manifest_path)
    if manifest.get("controller_version") != 29:
        raise RuntimeError("controller version is not v29")
    if not str(manifest.get("controller_entrypoint") or "").endswith(
        "tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint.py"
    ):
        raise RuntimeError("controller entrypoint is not v29")
    v25.verify_pins(manifest)
    print(json.dumps(validate_boundary(manifest), indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
