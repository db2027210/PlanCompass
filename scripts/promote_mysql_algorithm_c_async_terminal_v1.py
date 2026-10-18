#!/usr/bin/env python3
"""Promote a converged async Algorithm-C scene from a global-status miss.

The predecessor wrote ``NON_CONVERGED`` because its finalizer evaluated the
four-scene convergence dictionary even though the process owned exactly one
scene.  This utility performs no workload and grants no new round credit.  It
requires the active scene and its last committed decision to already prove
terminal width, then emits a fresh, provenance-bound COMPLETE state.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def under_history(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if HISTORY.resolve() not in path.parents:
        raise argparse.ArgumentTypeError("path must be under formal history")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-state", type=under_history, required=True)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--output-run-id", required=True)
    args = parser.parse_args()
    suffix = SCENES[args.scene]
    if (
        re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_run_id) is None
        or not args.output_run_id.endswith(f"_{suffix}")
    ):
        parser.error("safe scene-suffixed output run id required")
    output = HISTORY / args.output_run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh promotion output required: {output}")

    source_path = args.source_state.resolve()
    source_root = source_path.parent
    source = load(source_path)
    scene = args.scene
    active_scenes = source.get("active_scenes")
    accepted = source.get("accepted_rounds") or {}
    rejected = source.get("rejected_outer_attempts") or {}
    converged = source.get("converged") or {}
    outer = int(source.get("outer_attempt") or 0)
    decision_path = source_root / f"outer_{outer:03d}" / f"{scene}.decision.json"
    decision = load(decision_path)
    interval = decision.get("interval_convergence") or {}
    if not isinstance(interval, Mapping):
        raise RuntimeError("last decision interval convergence object required")
    inactive = set(SCENES) - {scene}
    if (
        source.get("algorithm") != "C"
        or source.get("async_scene") != scene
        or active_scenes != [scene]
        or source.get("status") != "NON_CONVERGED"
        or source.get("stage") != "FINISHED"
        or source.get("error") not in (None, "")
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or source.get("formal_evidence_eligible") is not True
        or outer < 1
        or converged.get(scene) is not True
        or any(int(accepted.get(other) or 0) != 0 for other in inactive)
        or any(int(rejected.get(other) or 0) != 0 for other in inactive)
        or decision.get("scene") != scene
        or int(decision.get("outer_attempt") or 0) != outer
        or int(decision.get("accepted_rounds") or 0) != int(accepted.get(scene) or 0)
        or int(decision.get("rejected_outer_attempts") or 0) != int(rejected.get(scene) or 0)
        or interval.get("converged") is not True
        or float(interval.get("normalized_width") or 1.0)
        > float(interval.get("terminal_normalized_width") or 0.01)
    ):
        raise RuntimeError("async Algorithm-C terminal promotion authority differs")

    promoted = copy.deepcopy(source)
    promoted["status"] = "COMPLETE"
    promoted["stage"] = "FINISHED"
    promoted["completion_reason"] = "all_active_log_domain_widths_reached_1pct"
    promoted["completed_at"] = now()
    promoted["source_terminal_promotion"] = {
        "schema_version": "mysql_algorithm_c_async_terminal_promotion_v1",
        "reason": "predecessor_finalizer_checked_inactive_companion_scene_flags",
        "active_scene": scene,
        "source_state_path": str(source_path),
        "source_state_sha256": sha(source_path),
        "last_decision_path": str(decision_path),
        "last_decision_sha256": sha(decision_path),
        "physical_workload_started": False,
        "formal_measurement_performed": False,
        "accepted_round_credit_increment": 0,
        "rejected_round_credit_increment": 0,
        "incumbent_changed": False,
        "parameter_ranges_changed": False,
        "source_status": "NON_CONVERGED",
        "promoted_status": "COMPLETE",
    }
    promoted["terminal_promotion_source_path"] = str(Path(__file__).resolve())
    promoted["terminal_promotion_source_sha256"] = sha(Path(__file__).resolve())

    manifest = {
        "schema_version": "mysql_algorithm_c_async_terminal_promotion_manifest_v1",
        "status": "COMPLETE",
        "created_at": now(),
        "scene": scene,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_state_path": str(source_path),
        "source_state_sha256": sha(source_path),
        "last_decision_path": str(decision_path),
        "last_decision_sha256": sha(decision_path),
        "normalized_width": float(interval["normalized_width"]),
        "terminal_normalized_width": float(interval["terminal_normalized_width"]),
        "physical_workload_started": False,
        "formal_measurement_performed": False,
        "state_or_round_credit_increment": 0,
        "builder_path": str(Path(__file__).resolve()),
        "builder_sha256": sha(Path(__file__).resolve()),
    }
    atomic(output / "terminal_promotion_manifest.json", manifest)
    promoted["terminal_promotion_manifest_path"] = str(
        output / "terminal_promotion_manifest.json"
    )
    promoted["terminal_promotion_manifest_sha256"] = sha(
        output / "terminal_promotion_manifest.json"
    )
    atomic(output / "run_state.json", promoted)
    print(json.dumps({
        "status": "COMPLETE", "scene": scene,
        "output": str(output), "normalized_width": interval["normalized_width"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
