#!/usr/bin/env python3
"""Run attempt57 endpoint calibration after validating its sealed manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping

import tpch_attempt57_followup as contract


def read_completed(path: Path, manifest_sha256: str) -> Dict[str, Dict[str, Any]]:
    completed: Dict[str, Dict[str, Any]] = {}
    if not path.is_file():
        return completed
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("manifest_sha256") != manifest_sha256:
            raise RuntimeError(
                f"calibration ledger manifest mismatch at line {line_number}: {path}"
            )
        calibration_id = str(row.get("calibration_id") or "")
        if calibration_id in completed:
            raise RuntimeError(f"duplicate calibration id in ledger: {calibration_id}")
        if row.get("status") == "usable":
            result_path = Path(str(row.get("point_result_path") or ""))
            if not result_path.is_file():
                raise RuntimeError(f"completed calibration result is absent: {result_path}")
            if contract.sha256_path(result_path) != row.get("point_result_sha256"):
                raise RuntimeError(f"completed calibration result changed: {result_path}")
            completed[calibration_id] = row
    return completed


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def point_runner_path(manifest: Mapping[str, Any]) -> Path:
    for artifact in manifest.get("runtime_artifacts") or []:
        if artifact.get("role") == "attempt57_calibration_point_runner":
            return Path(str(artifact["path"]))
    raise RuntimeError("attempt57 point runner is not bound in the manifest")


def usable_result(
    result: Mapping[str, Any],
    *,
    calibration_id: str,
    params: Mapping[str, float],
    manifest_sha256: str,
) -> bool:
    protocol = result.get("candidate_measurement_protocol")
    protocol = protocol if isinstance(protocol, dict) else {}
    cleanup = result.get("cleanup")
    cleanup = cleanup if isinstance(cleanup, dict) else {}
    application = result.get("candidate_parameter_application")
    application = application if isinstance(application, dict) else {}
    return (
        result.get("status") == "complete"
        and result.get("calibration_id") == calibration_id
        and result.get("manifest_sha256") == manifest_sha256
        and result.get("effective_parameters") == dict(params)
        and protocol.get("stable") is True
        and protocol.get("correctness_passed") is True
        and cleanup.get("database_backends_fully_released") is True
        and cleanup.get("defaults_restored") is True
        and cleanup.get("global_defaults_unchanged") is True
        and application.get("mode") == "transaction_local_set_local"
        and application.get("alter_system_used") is False
        and application.get("global_settings_changed") is False
        and application.get("immediate_readback_required") is True
        and application.get("workers_expected") == contract.PROFILE["workers"]
        and application.get("workers_verified") == contract.PROFILE["workers"]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=contract.MANIFEST_PATH)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = contract.verify_manifest(args.manifest)
    manifest_sha256 = contract.sha256_path(args.manifest.resolve())
    calibration = manifest["calibration"]
    root = contract.require_attempt57_path(
        Path(calibration["root"]),
        field="calibration_root",
    )
    ledger_path = root / "fresh_model_calibration.jsonl"
    completed = read_completed(ledger_path, manifest_sha256)
    probes: List[Dict[str, Any]] = list(calibration["probes"])
    pending = [
        probe for probe in probes if probe["calibration_id"] not in completed
    ]
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "manifest_sha256": manifest_sha256,
                    "completed": len(completed),
                    "pending": [probe["calibration_id"] for probe in pending],
                    "execute_required": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    if completed and not args.resume:
        raise RuntimeError("existing attempt57 calibration requires --resume")
    runner = point_runner_path(manifest)
    for probe in pending:
        calibration_id = probe["calibration_id"]
        point_root = root / "points" / calibration_id
        if point_root.exists():
            raise RuntimeError(
                f"unsealed point directory already exists; refuse overwrite: {point_root}"
            )
        command = [
            str(runner),
            "--manifest",
            str(args.manifest.resolve()),
            "--calibration-id",
            calibration_id,
            "--output-dir",
            str(point_root),
            "--params-json",
            json.dumps(probe["params"], sort_keys=True, separators=(",", ":")),
        ]
        completed_process = subprocess.run(command, check=False)
        if completed_process.returncode != 0:
            raise RuntimeError(
                f"attempt57 calibration point failed: {calibration_id}, "
                f"returncode={completed_process.returncode}"
            )
        result_path = point_root / "point_result.json"
        result = contract.read_json(result_path)
        if not usable_result(
            result,
            calibration_id=calibration_id,
            params=probe["params"],
            manifest_sha256=manifest_sha256,
        ):
            raise RuntimeError(f"attempt57 calibration point is not usable: {result_path}")
        row = {
            "event": "fresh_model_calibration_replay",
            "phase": "coordinate_axis_direction_calibration",
            "design": calibration["design"],
            "calibration_id": calibration_id,
            "parameter": probe["parameter"],
            "direction": probe["direction"],
            "changed_parameters": [probe["parameter"]],
            "params": probe["params"],
            "status": "usable",
            "manifest_sha256": manifest_sha256,
            "point_result_path": str(result_path.resolve()),
            "point_result_sha256": contract.sha256_path(result_path),
        }
        append_jsonl(ledger_path, row)
        completed[calibration_id] = row
    points = [completed[probe["calibration_id"]] for probe in probes]
    contract.atomic_json(
        Path(calibration["summary_path"]),
        {
            "schema_version": 1,
            "status": "complete",
            "manifest_path": str(args.manifest.resolve()),
            "manifest_sha256": manifest_sha256,
            "expected_usable_points": len(probes),
            "usable_points": len(points),
            "points": points,
        },
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "usable_points": len(points),
                "summary": calibration["summary_path"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
