#!/usr/bin/env python3
"""Create fresh r63 configs and merge audited r61/r62 replay seeds."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.run_pg_algorithm_c_scene import (  # noqa: E402
    HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
    canonical_sha256,
    exact_param_signature,
    physical_valid,
)


ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809"
)
R61 = ROOT / "20260813_algorithm_c_cached_tier_redecision_clean_r61"
R62 = ROOT / "20260813_algorithm_c_same_plan_numeric_clean_r62"
R63 = ROOT / "20260813_algorithm_c_prephysical_history_clean_r63"
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def accepted_signatures(log_path: Path) -> Set[Tuple[str, ...]]:
    signatures: Set[Tuple[str, ...]] = set()
    if not log_path.is_file():
        return signatures
    with log_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            row = json.loads(raw)
            if row.get("event") != "algorithm_c_round_accepted":
                continue
            params = row.get("params")
            if isinstance(params, Mapping):
                signatures.add(exact_param_signature(params))
    return signatures


def r62_rows(scene: str) -> list[Dict[str, Any]]:
    lane = R62 / scene
    state = read_json(lane / "algorithm_c_state.json")
    baseline_by_worker = state.get("baseline_by_worker")
    canonical = state.get("canonical_baseline")
    if not isinstance(baseline_by_worker, Mapping) or not isinstance(
        canonical,
        Mapping,
    ):
        raise RuntimeError(f"r62 baseline missing for {scene}")
    accepted = accepted_signatures(lane / "algorithm_c.jsonl")
    rows: list[Dict[str, Any]] = []
    seen: Set[Tuple[str, ...]] = set()
    with (lane / "algorithm_c.jsonl").open("r", encoding="utf-8") as handle:
        for raw in handle:
            event = json.loads(raw)
            if event.get("event") != "algorithm_c_candidate_replay":
                continue
            validation = event.get("validation")
            if not isinstance(validation, Mapping) or not physical_valid(validation):
                continue
            candidate = validation.get("candidate_ranking")
            if not isinstance(candidate, Mapping):
                continue
            params = candidate.get("optimized_params")
            if not isinstance(params, Mapping):
                continue
            signature = exact_param_signature(params)
            if signature in accepted or signature in seen:
                continue
            worker_key = str(validation.get("worker_key") or "")
            worker_baseline = baseline_by_worker.get(worker_key)
            if not isinstance(worker_baseline, Mapping):
                continue
            rows.append(
                {
                    "source_round_index": int(event.get("round_index") or 0),
                    "source_label": str(validation.get("label") or ""),
                    "source_worker_key": worker_key,
                    "validation": copy.deepcopy(dict(validation)),
                    "source_worker_baseline": copy.deepcopy(
                        dict(worker_baseline)
                    ),
                    "source_canonical_baseline": copy.deepcopy(dict(canonical)),
                }
            )
            seen.add(signature)
    return rows


def merged_seed(scene: str) -> Dict[str, Any]:
    former = read_json(R62 / scene / "historical_valid_replay_seed.json")
    if former.get("contract") != HISTORICAL_VALID_REPLAY_SEED_CONTRACT:
        raise RuntimeError(f"r61 seed contract mismatch for {scene}")
    rows: list[Dict[str, Any]] = []
    seen: Set[Tuple[str, ...]] = set()
    for source_row in [*(former.get("rows") or []), *r62_rows(scene)]:
        validation = source_row.get("validation")
        candidate = (
            validation.get("candidate_ranking")
            if isinstance(validation, Mapping)
            else None
        )
        params = candidate.get("optimized_params") if isinstance(candidate, Mapping) else None
        if not isinstance(params, Mapping):
            continue
        signature = exact_param_signature(params)
        if signature in seen:
            continue
        rows.append(copy.deepcopy(source_row))
        seen.add(signature)
    payload = {
        key: copy.deepcopy(value)
        for key, value in former.items()
        if key not in {"rows", "row_count", "payload_sha256"}
    }
    payload.update(
        {
            "source_campaign": [str(R61), str(R62)],
            "row_count": len(rows),
            "rows": rows,
            "merged_source_campaigns": True,
            "accepted_state_inherited": False,
            "ranges_inherited": False,
            "gradient_state_inherited": False,
            "physical_workload_invocations": 0,
            "valid_replays_added": 0,
        }
    )
    payload["payload_sha256"] = canonical_sha256(payload)
    return payload


def main() -> int:
    if R63.exists() and any(R63.iterdir()):
        raise RuntimeError(f"r63 target exists and is non-empty: {R63}")
    R63.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, Any] = {
        "contract": "algorithm_c_fresh_prephysical_history_campaign_v1",
        "campaign_root": str(R63),
        "source_campaigns": [str(R61), str(R62)],
        "fresh_state": True,
        "copied_state": False,
        "copied_history": False,
        "copied_replays": False,
        "accepted_state_inherited": False,
        "ranges_inherited": False,
        "gradient_state_inherited": False,
        "technical_failures_inherited": False,
        "prephysical_historical_redecision": True,
        "qps_outer_only": True,
        "scenes": {},
    }
    for scene in SCENES:
        source_config = R62 / scene / "optimizer_config.json"
        config = read_json(source_config)
        target_dir = R63 / scene
        target_dir.mkdir(parents=True, exist_ok=False)
        seed_path = target_dir / "historical_valid_replay_seed.json"
        seed = merged_seed(scene)
        atomic_json(seed_path, seed)
        algorithm = config.get("algorithm_c")
        if not isinstance(algorithm, dict):
            raise RuntimeError(f"algorithm_c config missing for {scene}")
        algorithm["historical_valid_replay_seed_path"] = str(seed_path)
        config_path = target_dir / "optimizer_config.json"
        atomic_json(config_path, config)
        manifest["scenes"][scene] = {
            "config": str(config_path),
            "config_sha256": sha256(config_path),
            "seed": str(seed_path),
            "seed_sha256": sha256(seed_path),
            "seed_payload_sha256": seed["payload_sha256"],
            "seed_row_count": seed["row_count"],
            "accepted_target": int(algorithm.get("accepted_target") or 0),
            "normalized_width_threshold": float(
                algorithm.get("normalized_width_threshold") or 0.0
            ),
            "state_exists_before_launch": False,
            "history_exists_before_launch": False,
        }
    atomic_json(R63 / "fresh_campaign_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
