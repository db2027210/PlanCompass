#!/usr/bin/env python3
"""Create an attempt57 outer-launch readiness seal without starting anything."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import tpch_attempt57_followup as contract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=contract.MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=contract.READINESS_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = contract.require_attempt57_path(args.output, field="readiness_output")
    manifest = contract.verify_manifest(args.manifest, require_calibration=True)
    manifest_sha256 = contract.sha256_path(args.manifest.resolve())
    calibration_path = Path(manifest["calibration"]["summary_path"])
    payload = {
        "schema_version": 1,
        "status": "ready",
        "phase": "outer_launch_prepared_not_started",
        "contract_id": manifest["contract_id"],
        "attempt_root": str(contract.ATTEMPT_ROOT),
        "machine": "node01",
        "profile": dict(contract.PROFILE),
        "active_parameters": list(contract.PARAMETERS),
        "manifest_path": str(args.manifest.resolve()),
        "manifest_sha256": manifest_sha256,
        "strict_r0_binding_sha256": manifest["strict_r0"]["binding_sha256"],
        "calibration_summary_path": str(calibration_path),
        "calibration_summary_sha256": contract.sha256_path(calibration_path),
        "optimizer_started": False,
        "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    contract.reject_forbidden(payload, field="readiness")
    if output.exists():
        existing = contract.read_json(output)
        comparable_existing = dict(existing)
        comparable_payload = dict(payload)
        comparable_existing.pop("prepared_at", None)
        comparable_payload.pop("prepared_at", None)
        if (
            comparable_existing != comparable_payload
            or existing.get("optimizer_started") is not False
        ):
            raise RuntimeError(f"existing readiness seal differs; refuse overwrite: {output}")
        print(
            json.dumps(
                {
                    "status": "outer_launch_already_prepared_not_started",
                    "path": str(output),
                    "sha256": contract.sha256_path(output),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    contract.atomic_json(output, payload)
    print(
        json.dumps(
            {
                "status": "outer_launch_prepared_not_started",
                "path": str(output),
                "sha256": contract.sha256_path(output),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
