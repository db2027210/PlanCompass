#!/usr/bin/env python3
"""Audit single-scene or memory-pair Algorithm C incumbent chains."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v1 as predecessor


SCHEMA = "mysql_algorithm_c_global_incumbent_chain_audit_v2"
SCENES = tuple(predecessor.SCENES)


def active_scenes(state: Mapping[str, Any]) -> tuple[str, ...]:
    async_scene = str(state.get("async_scene") or "")
    if async_scene in SCENES:
        return (async_scene,)
    raw = state.get("active_scenes")
    if not isinstance(raw, list):
        raise RuntimeError("Algorithm C active scene set is absent")
    requested = tuple(scene for scene in SCENES if scene in raw)
    if (
        set(requested) != set(raw)
        or not requested
        or len(requested) > 2
        or (
            len(requested) == 2
            and (
                requested != ("job_memory", "tpch_sf4_memory")
                or state.get("memory_pair_controller") is not True
            )
        )
    ):
        raise RuntimeError("Algorithm C active scene set differs")
    return requested


def local_run_decision_paths(
    run_root: Path, state: Mapping[str, Any], scenes: tuple[str, ...],
) -> list[Path]:
    completed = int(state.get("outer_attempt") or 0)
    paths: list[Path] = []
    for outer in range(1, completed + 1):
        for scene in scenes:
            path = run_root / f"outer_{outer:03d}" / f"{scene}.decision.json"
            if path.is_file() and not path.is_symlink():
                paths.append(path.resolve())
    if not paths:
        raise RuntimeError("Algorithm C local decision evidence is absent")
    return paths


def audit_run(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state = predecessor.load(run_root / "run_state.json")
    scenes = active_scenes(state)
    paths: list[Path] = []
    if state.get("fresh_search") is not True:
        checkpoint_path = Path(str(state.get("checkpoint_path") or "")).resolve()
        paths.extend(
            path for path in predecessor.checkpoint_decision_paths(checkpoint_path)
            if str(predecessor.load(path).get("scene") or "") in scenes
        )
    seen = set(paths)
    for path in local_run_decision_paths(run_root, state, scenes):
        if path not in seen:
            paths.append(path)
            seen.add(path)
    result = predecessor.audit_paths(
        paths,
        final_incumbents=predecessor.mapping(
            state.get("incumbents"), "run incumbents",
        ),
        subject=str(run_root),
    )
    result.update({
        "schema_version": SCHEMA,
        "active_scenes": list(scenes),
        "memory_pair_supported": len(scenes) == 2,
        "memory_pair_controller": state.get("memory_pair_controller") is True,
        "decision_path_count": len(paths),
        "measurement_or_optimizer_state_mutated": False,
        "accepted_or_rejected_credit_mutated": False,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_run(args.run_root)
    predecessor.atomic(args.output.resolve(), result)
    print(predecessor.json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
