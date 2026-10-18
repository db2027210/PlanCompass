#!/usr/bin/env python3
"""Promote one completed, in-band MySQL R0 run into the readiness manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = (
    ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "readiness_manifest.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_r0_calibration_20260724_v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pointer(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": digest(path)}


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    state_path = run_dir / "campaign_runtime.json"
    result_path = run_dir / "r0_result.json"
    readback_path = run_dir / "effective_resource_readback_v2.json"
    for path in (state_path, result_path, readback_path):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"required R0 artifact absent or symlink: {path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    readback = json.loads(readback_path.read_text(encoding="utf-8"))
    if state.get("phase") != "R0_COMPLETE" or state.get("r0_complete") is not True:
        raise RuntimeError("R0 campaign is not complete")
    if readback.get("verified") is not True or readback.get("run_id") != state.get("run_id"):
        raise RuntimeError("effective resource readback does not bind this R0")
    rolling = result.get("rolling_measurement_control")
    if not isinstance(rolling, dict):
        raise RuntimeError("R0 result lacks rolling measurement control")
    selected = rolling.get("selected_observation")
    if not isinstance(selected, dict):
        raise RuntimeError("R0 result lacks selected observation")
    means = selected.get("resource_means")
    if not isinstance(means, dict) or set(means) != {"cpu", "memory", "io"}:
        raise RuntimeError("R0 resource means are incomplete")
    means = {name: float(value) for name, value in means.items()}
    target = str(state.get("target_resource"))
    failures = []
    for name, value in means.items():
        lower, upper = (0.85, 0.95) if name == target else (0.30, 0.50)
        if not lower <= value <= upper:
            failures.append(f"{name}={value:.6f} outside [{lower},{upper}]")
    stability = selected.get("stability")
    windows = stability.get("windows") if isinstance(stability, dict) else None
    if not isinstance(windows, list) or len(windows) < 6:
        failures.append("latest six stability windows are absent")
    coverage = float(selected.get("resource_sample_coverage", 0.0))
    if coverage < 0.90:
        failures.append(f"sample coverage {coverage:.6f} below 0.90")
    correctness = result.get("correctness")
    if not isinstance(correctness, dict) or correctness.get("passed") is not True:
        failures.append("workload correctness did not pass")
    if failures:
        raise RuntimeError("R0 cannot be promoted: " + "; ".join(failures))
    scenario = str(state["scenario"])
    evidence = {
        "schema_version": "mysql_sgd_r0_calibration_evidence_v1",
        "scenario": scenario,
        "benchmark": state["benchmark"],
        "target_resource": target,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "passed": True,
        "latest_window_seconds": 180,
        "latest_window_count": 6,
        "sample_coverage": coverage,
        "resource_utilization": means,
        "selected_measurement_seconds": rolling.get("selected_measurement_seconds"),
        "stop_reason": rolling.get("stop_reason"),
        "stability": stability,
        "correctness": correctness,
        "resource_envelope_requested": state["resource_envelope"],
        "resource_envelope_effective": readback["effective"],
        "source": {
            "run_id": state["run_id"],
            "campaign_runtime": pointer(state_path),
            "r0_result": pointer(result_path),
            "effective_resource_readback": pointer(readback_path),
        },
        "promoted_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    evidence_path = OUTPUT / f"{scenario}.r0_calibration_v1.json"
    payload = canonical(evidence)
    evidence_sha = hashlib.sha256(payload).hexdigest()
    print(json.dumps({
        "scenario": scenario,
        "resource_utilization": means,
        "evidence": str(evidence_path.relative_to(ROOT)),
        "sha256": evidence_sha,
        "execute": args.execute,
    }, ensure_ascii=False, sort_keys=True))
    if not args.execute:
        return 0
    atomic_write(evidence_path, payload)
    atomic_write(
        evidence_path.with_name(evidence_path.name + ".sha256"),
        f"{evidence_sha}  {evidence_path.name}\n".encode("ascii"),
    )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["scenarios"][scenario]["r0_calibration"] = {
        "passed": True,
        "evidence": {
            "path": str(evidence_path.relative_to(ROOT)),
            "sha256": evidence_sha,
        },
    }
    atomic_write(MANIFEST, canonical(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
