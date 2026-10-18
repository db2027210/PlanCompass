#!/usr/bin/env python3
"""Recover scoped XP R0 when only the legacy whole-run bottleneck gate failed."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
THRESHOLD = 0.8


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-r0", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    source_path = args.source_r0.resolve()
    source = load(source_path)
    results = source.get("results") or {}
    old_gate = source.get("target_bottleneck_gate") or {}
    expected_ids = {"xp_io_w1", "xp_io_w2"}
    if (
        source_path.parent.parent != HISTORY
        or source.get("status") != "R0_FAILED_CLOSED"
        or source.get("authority_scene_scope") != "hybench_xp_io"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or source.get("failures") != []
        or source.get("client_isolation_failures") != []
        or (source.get("prestart_point_non_interference_gate") or {}).get("passed") is not True
        or (source.get("runtime_non_interference_gate") or {}).get("passed") is not True
        or old_gate.get("threshold") != THRESHOLD
        or old_gate.get("passed") is not False
        or set(results) != expected_ids
    ):
        raise RuntimeError("source scoped R0 is not the recoverable gate-only case")

    workers: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(expected_ids):
        result = results[worker_id]
        stability = result.get("resource_stability") or {}
        decisions = stability.get("decisions") or []
        final = decisions[-1] if decisions else {}
        windows = final.get("windows") or []
        values = [float(row.get("target_mean")) for row in windows]
        observed = math.fsum(values) / len(values) if values else math.nan
        if (
            float((result.get("metrics") or {}).get("qps") or 0) <= 0
            or (result.get("workload_coverage") or {}).get("coverage_complete") is not True
            or stability.get("stable") is not True
            or final.get("stable") is not True
            or final.get("coverage_passed") is not True
            or final.get("variability_passed") is not True
            or len(values) != 6
            or not math.isfinite(observed)
            or observed < THRESHOLD
        ):
            raise RuntimeError(f"{worker_id} selected stable window evidence differs")
        workers[worker_id] = {
            "scene": "hybench_xp_io",
            "target_resource": "io",
            "observed_fraction": observed,
            "minimum_fraction": THRESHOLD,
            "measurement_basis": "selected_stable_six_window_mean",
            "selected_measurement_seconds": stability.get("selected_measurement_seconds"),
            "passed": True,
        }

    output = HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    recovered = dict(source)
    recovered.update({
        "schema_version": "mysql_scoped_r0_stable_window_bottleneck_recovery_v1",
        "status": "PASS",
        "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "fresh_r0_complete": True,
        "formal_outer_launch_authorized": True,
        "target_bottleneck_gate": {
            "release_version": "selected_stable_six_window_v1",
            "threshold": THRESHOLD,
            "workers": workers,
            "passed": True,
        },
        "stable_window_bottleneck_recovery": {
            "source_r0_path": str(source_path),
            "source_r0_sha256": sha(source_path),
            "source_failed_only_legacy_whole_run_bottleneck_gate": True,
            "workload_rerun": False,
            "raw_results_mutated": False,
            "resource_envelope_changed": False,
            "recalibration_performed": False,
            "recovery_source_path": str(Path(__file__).resolve()),
            "recovery_source_sha256": sha(Path(__file__).resolve()),
        },
    })
    binding = output / "r0_binding.json"
    atomic(binding, recovered)
    print(json.dumps({
        "status": "PASS", "binding": str(binding), "workers": workers,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
