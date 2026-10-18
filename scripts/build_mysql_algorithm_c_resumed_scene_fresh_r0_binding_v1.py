#!/usr/bin/env python3
"""Bind current scene authority to controller-owned fresh incumbent R0."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENES = tuple(alg.SCENES)


def load(path: Path) -> dict[str, Any]:
    return alg.load(path.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-r0", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output.parent.name) is None:
        parser.error("safe output parent required")
    if args.output.exists() or args.output.parent.exists():
        raise RuntimeError("fresh R0 output required")
    source_path = args.source_r0.resolve()
    authority = args.authority.resolve()
    preflight_path = authority / "transition_preflight.json"
    prepared_path = authority / "prepared_binding.json"
    accounts_path = authority / "benchmark_accounts_binding.json"
    source, preflight, prepared, accounts = (
        load(source_path), load(preflight_path), load(prepared_path), load(accounts_path)
    )
    freeze_sha = str(preflight.get("freeze_sha256") or "")
    if (
        source.get("status") != "PASS"
        or source.get("fresh_r0_complete") is not True
        or source.get("formal_outer_launch_authorized") is not True
        or (source.get("target_bottleneck_gate") or {}).get("passed") is not True
        or (source.get("target_bottleneck_gate") or {}).get(
            "fresh_incumbent_measurement_owned_by_controller"
        ) is not True
        or preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds") or 0) < 60.0
        or preflight.get("formal_launch_authorized") is not True
        or preflight.get("scene_scope") != args.scene
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != freeze_sha
        or prepared.get("preflight_sha256") != alg.sha(preflight_path)
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != freeze_sha
        or accounts.get("prepared_binding_sha256") != alg.sha(prepared_path)
    ):
        raise RuntimeError("resumed scene R0 authority differs")
    document = copy.deepcopy(source)
    document.update({
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "freeze_path": preflight["freeze_path"],
        "freeze_sha256": freeze_sha,
        "preflight_path": str(preflight_path),
        "preflight_sha256": alg.sha(preflight_path),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": alg.sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": alg.sha(accounts_path),
        "observed_seconds": float(preflight["observed_seconds"]),
        "rolling_dynamic_noninterference_gate": copy.deepcopy(
            preflight.get("rolling_dynamic_noninterference_gate")
        ),
        "scene_scope": args.scene,
        "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
        "source_r0_path": str(source_path),
        "source_r0_sha256": alg.sha(source_path),
        "fresh_incumbent_measurement_owned_by_controller": True,
    })
    args.output.parent.mkdir(parents=True, mode=0o700)
    alg.atomic(args.output, document)
    print(json.dumps({
        "status": "PASS", "scene": args.scene,
        "output": str(args.output), "sha256": alg.sha(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
