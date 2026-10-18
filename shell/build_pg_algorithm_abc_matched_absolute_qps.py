#!/usr/bin/env python3
"""Combine repeated post-terminal ABC common-domain runs into one QPS authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.verify_pg_common_domain_frozen_champions import verify as verify_common  # noqa: E402


CONTRACT = "algorithm_abc_pg_common_domain_final_validation_v1"
SOURCE_CONTRACT = "pg_four_scene_adjacent_bracketed_frozen_champion_replay_v2"
SCENES: Tuple[str, ...] = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
METHODS: Tuple[str, ...] = ("algorithm_a", "algorithm_b", "algorithm_c")
DISPLAY_METHOD = {
    "algorithm_a": "Algorithm A",
    "algorithm_b": "Algorithm B",
    "algorithm_c": "Algorithm C",
}


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def geomean(values: Sequence[float]) -> float:
    if not values or any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise RuntimeError("QPS geometric mean requires finite positive observations")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def build(run_roots: Sequence[Path]) -> Dict[str, Any]:
    roots = [path.resolve() for path in run_roots]
    if len(roots) < 2 or len(set(roots)) != len(roots):
        raise RuntimeError("at least two distinct common-domain repetitions are required")
    source_runs: List[Dict[str, Any]] = []
    observed_seeds: List[int] = []
    raw: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        scene: {method: [] for method in METHODS} for scene in SCENES
    }
    frozen_params: Dict[str, Dict[str, Any]] = {
        scene: {} for scene in SCENES
    }
    for repetition, root in enumerate(roots, start=1):
        verification = verify_common(root)
        if verification.get("passed") is not True:
            raise RuntimeError(
                f"common-domain repetition {repetition} failed verification: "
                f"{verification.get('errors')}"
            )
        authority_path = root / "authority_manifest.json"
        state_path = root / "state.json"
        contract_path = root / "contract.json"
        authority = read_json(authority_path)
        state = read_json(state_path)
        source_contract = read_json(contract_path)
        if authority.get("contract") != SOURCE_CONTRACT:
            raise RuntimeError(f"unexpected common-domain authority: {root}")
        method_order = tuple(source_contract.get("method_order") or ())
        if len(method_order) != len(METHODS) or set(method_order) != set(METHODS):
            raise RuntimeError("matched ABC builder requires an A/B/C-only method permutation")
        seed = source_contract.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise RuntimeError(f"common-domain repetition {repetition} has no integer seed")
        if seed in observed_seeds:
            raise RuntimeError(f"common-domain repetitions must use distinct seeds: {seed}")
        observed_seeds.append(seed)
        source_runs.append({
            "repetition": repetition,
            "seed": seed,
            "root": str(root),
            "authority_sha256": sha256(authority_path),
            "state_sha256": sha256(state_path),
            "contract_sha256": sha256(contract_path),
        })
        contract_scenes = source_contract.get("scenes") or {}
        for scene in SCENES:
            methods = (contract_scenes.get(scene) or {}).get("methods") or {}
            for method in METHODS:
                params = (methods.get(method) or {}).get("params") or {}
                previous = frozen_params[scene].get(method)
                if previous is not None and previous != params:
                    raise RuntimeError(f"frozen champion changed across repetitions: {scene}/{method}")
                frozen_params[scene][method] = params
                stage = ((state.get("stage_results") or {}).get(method) or {})
                scene_result = (stage.get("scene_results") or {}).get(scene) or {}
                row_paths = [Path(str(path)) for path in scene_result.get("row_paths") or []]
                if len(row_paths) != 2:
                    raise RuntimeError(f"expected two worker rows: repetition={repetition} {scene}/{method}")
                workers = set()
                for row_path in row_paths:
                    row = read_json(row_path)
                    worker = str(row.get("worker_key") or "")
                    qps = row.get("db_qps")
                    if worker not in {"w1", "w2"} or not isinstance(qps, (int, float)):
                        raise RuntimeError(f"invalid matched QPS row: {row_path}")
                    workers.add(worker)
                    raw[scene][method].append({
                        "repetition": repetition,
                        "worker": worker,
                        "qps": float(qps),
                        "source": str(row_path),
                        "source_sha256": sha256(row_path),
                    })
                if workers != {"w1", "w2"}:
                    raise RuntimeError(f"worker imbalance: repetition={repetition} {scene}/{method}")

    scenes: Dict[str, Any] = {}
    for scene in SCENES:
        scenes[scene] = {}
        for method in METHODS:
            observations = raw[scene][method]
            expected_count = 2 * len(roots)
            if len(observations) != expected_count:
                raise RuntimeError(f"observation count mismatch: {scene}/{method}")
            scenes[scene][DISPLAY_METHOD[method]] = {
                "qps_geomean": geomean([row["qps"] for row in observations]),
                "raw_qps": observations,
                "worker_count": 2,
                "repetitions_per_worker": len(roots),
                "params": frozen_params[scene][method],
            }
    return {
        "contract": CONTRACT,
        "aggregation": "balanced_worker_repetition_geometric_mean",
        "same_workers_same_workload_same_baseline_brackets": True,
        "search_performed": False,
        "algorithm_c_was_terminal_before_a_b_evidence_loaded": True,
        "source_common_domain_contract": SOURCE_CONTRACT,
        "distinct_repetition_seeds": True,
        "source_runs": source_runs,
        "scenes": scenes,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build(args.run_root)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "contract": report["contract"],
        "source_repetitions": len(report["source_runs"]),
        "output": str(args.output),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
