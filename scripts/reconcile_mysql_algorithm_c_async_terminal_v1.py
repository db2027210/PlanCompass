#!/usr/bin/env python3
"""Reconcile the terminal status of a completed single-scene Algorithm-C run.

The boundary-resume runtime correctly stops its search against ``SCENES`` but
v35 writes the final status against every key in the four-scene convergence
mapping.  In asynchronous single-scene mode, the three inactive keys remain
false, so an actually converged active scene is labelled ``NON_CONVERGED``.

This tool never edits or replays the source run.  It emits a content-addressed
correction that is valid only when the active scene's last outer decision
proves the frozen one-percent interval criterion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def regular_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_dir():
        raise argparse.ArgumentTypeError(f"regular directory required: {path}")
    return path


def fresh_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.exists() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"fresh output required: {path}")
    return path


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    digest = sha256(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )


def reconcile(source_run: Path) -> dict[str, Any]:
    state_path = source_run / "run_state.json"
    state = load(state_path)
    active = state.get("active_scenes")
    scene = state.get("async_scene")
    outer = int(state.get("outer_attempt") or 0)
    if (
        not isinstance(active, list)
        or len(active) != 1
        or active[0] != scene
        or scene not in {"hybench_xp_cpu", "hybench_xp_io"}
        or state.get("status") != "NON_CONVERGED"
        or state.get("stage") != "FINISHED"
        or state.get("error") not in (None, "")
        or outer <= 0
        or (state.get("converged") or {}).get(scene) is not True
        or state.get("termination_audit") not in (None, {})
    ):
        raise RuntimeError("source is not the known converged async-terminal case")
    decision_path = source_run / f"outer_{outer:03d}" / f"{scene}.decision.json"
    decision = load(decision_path)
    interval = decision.get("interval_convergence") or {}
    width = float(interval.get("normalized_width"))
    threshold = float(interval.get("terminal_normalized_width"))
    if (
        decision.get("scene") != scene
        or int(decision.get("outer_attempt") or 0) != outer
        or interval.get("converged") is not True
        or interval.get("contract")
        != "mysql_bc_qps_strict_two_reject_incumbent_shrink_v2"
        or interval.get("accepted_rounds_are_not_inferred_from_forced_shrinks")
        is not True
        or not 0 <= width <= threshold == 0.01
        or int(decision.get("accepted_rounds") or -1)
        != int((state.get("accepted_rounds") or {}).get(scene) or 0)
        or int(decision.get("rejected_outer_attempts") or -1)
        != int((state.get("rejected_outer_attempts") or {}).get(scene) or 0)
    ):
        raise RuntimeError("active-scene terminal convergence evidence differs")
    return {
        "schema_version": "mysql_algorithm_c_async_terminal_reconciliation_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "C",
        "scene": scene,
        "source_run_id": source_run.name,
        "source_run_state": {
            "path": str(state_path),
            "sha256": sha256(state_path),
            "recorded_status": state["status"],
            "recorded_completion_reason": state.get("completion_reason"),
        },
        "terminal_decision": {
            "path": str(decision_path),
            "sha256": sha256(decision_path),
            "outer_attempt": outer,
            "accepted_rounds": int(decision["accepted_rounds"]),
            "rejected_outer_attempts": int(decision["rejected_outer_attempts"]),
            "normalized_width": width,
            "terminal_normalized_width": threshold,
        },
        "effective_status": "COMPLETE",
        "effective_completion_reason": "all_active_log_domain_widths_reached_1pct",
        "source_artifacts_mutated": False,
        "new_physical_measurement_invoked": False,
        "physical_rerun_required": False,
        "inactive_scene_convergence_keys_ignored": [
            name for name, value in (state.get("converged") or {}).items()
            if name != scene and value is False
        ],
        "defect": {
            "runtime": "run_mysql_four_scene_algorithm_c_boundary_resume_v35.py",
            "search_loop_scope": "SCENES",
            "terminal_status_scope_recorded": "state.converged.values",
            "terminal_status_scope_required": "SCENES",
            "search_or_acceptance_logic_affected": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=regular_directory, required=True)
    parser.add_argument("--output", type=fresh_path, required=True)
    args = parser.parse_args()
    document = reconcile(args.source_run)
    atomic(args.output, document)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
