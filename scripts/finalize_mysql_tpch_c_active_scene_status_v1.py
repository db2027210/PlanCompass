#!/usr/bin/env python3
"""Finalize a completed single-scene TPC-H C run after legacy global aggregation."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCENE = "tpch_sf4_memory"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    source_root = args.source_run.resolve()
    source_state_path = source_root / "run_state.json"
    state = load(source_state_path)
    outer = int(state.get("outer_attempt") or 0)
    decision_path = source_root / f"outer_{outer:03d}" / f"{SCENE}.decision.json"
    decision = load(decision_path)
    convergence = decision.get("interval_convergence")
    if not isinstance(convergence, Mapping):
        raise RuntimeError("final decision convergence object required")
    if (
        state.get("status") != "NON_CONVERGED"
        or state.get("stage") != "FINISHED"
        or state.get("active_scenes") != [SCENE]
        or (state.get("converged") or {}).get(SCENE) is not True
        or decision.get("scene") != SCENE
        or int(decision.get("outer_attempt") or 0) != outer
        or convergence.get("converged") is not True
        or float(convergence.get("normalized_width") or 1.0) > 0.01
        or float(convergence.get("terminal_normalized_width") or 0.0) != 0.01
        or int(decision.get("accepted_rounds") or -1)
        != int((state.get("accepted_rounds") or {}).get(SCENE, -2))
        or int(decision.get("rejected_outer_attempts") or -1)
        != int((state.get("rejected_outer_attempts") or {}).get(SCENE, -2))
        or state.get("algorithm_a_parameter_or_measurement_read") is not False
        or (state.get("qps_metric_contract") or {}).get("outer_progress_metric")
        != "fixed_five_lane_q1_q22_client_qps"
        or state.get("outer_acceptance_uses_same_worker_normalized_client_qps")
        is not True
    ):
        raise RuntimeError("single active TPC-H scene is not proven complete")
    output = args.output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh output required: {output}")
    output.mkdir(parents=True, mode=0o700)
    finalized = copy.deepcopy(state)
    finalized.update({
        "status": "COMPLETE",
        "completion_reason": "all_active_log_domain_widths_reached_1pct",
        "active_scene_status_aggregation": {
            "schema_version": "mysql_tpch_c_active_scene_status_finalizer_v1",
            "active_scenes": [SCENE],
            "inactive_scene_convergence_ignored": True,
            "accepted_rounds_mutation": False,
            "rejected_rounds_mutation": False,
            "params_or_incumbent_mutation": False,
            "range_or_convergence_mutation": False,
            "source_status": "NON_CONVERGED",
            "source_state": {
                "path": str(source_state_path), "sha256": sha(source_state_path),
            },
            "final_decision": {
                "path": str(decision_path), "sha256": sha(decision_path),
            },
            "finalized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        },
    })
    output_path = output / "run_state.json"
    output_path.write_text(
        json.dumps(finalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_path.with_name("run_state.json.sha256").write_text(
        f"{sha(output_path)}  run_state.json\n", encoding="ascii",
    )
    print(json.dumps({
        "status": "COMPLETE",
        "output": str(output_path),
        "sha256": sha(output_path),
        "outer_attempt": outer,
        "accepted_rounds": finalized["accepted_rounds"][SCENE],
        "rejected_outer_attempts": finalized["rejected_outer_attempts"][SCENE],
        "normalized_width": convergence["normalized_width"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
