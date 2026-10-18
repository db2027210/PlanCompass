#!/usr/bin/env python3
"""Export valid, never-accepted replay evidence for a fresh campaign."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.run_pg_algorithm_c_scene import (  # noqa: E402
    HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
    canonical_sha256,
    exact_param_signature,
    physical_valid,
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL {path}:{line_number}") from exc
            if isinstance(value, dict):
                yield value


def build_seed(
    *,
    state: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    experience_table_path: Path,
    source_campaign: str,
    target: str,
) -> Dict[str, Any]:
    algorithm = config.get("algorithm_c")
    if not isinstance(algorithm, Mapping):
        raise RuntimeError("algorithm_c config missing")
    baseline_by_worker = state.get("baseline_by_worker")
    canonical_baseline = state.get("canonical_baseline")
    if not isinstance(baseline_by_worker, Mapping) or not isinstance(
        canonical_baseline,
        Mapping,
    ):
        raise RuntimeError("source baseline evidence missing")
    event_rows = list(events)
    accepted_signatures: Set[Tuple[str, ...]] = set()
    initial_params = algorithm.get("initial_params")
    if not isinstance(initial_params, Mapping):
        raise RuntimeError("algorithm_c initial_params missing")
    baseline_signature = exact_param_signature(initial_params)
    for event in event_rows:
        if event.get("event") != "algorithm_c_round_accepted":
            continue
        params = event.get("params")
        if isinstance(params, Mapping):
            accepted_signatures.add(exact_param_signature(params))

    rows: List[Dict[str, Any]] = []
    seen_signatures: Set[Tuple[str, ...]] = set()
    excluded = {
        "technical_or_invalid": 0,
        "candidate_missing": 0,
        "previously_accepted_parameter_vector": 0,
        "baseline_parameter_vector": 0,
        "diagnostic_or_uncertain_probe": 0,
        "duplicate_parameter_vector": 0,
        "worker_baseline_missing": 0,
    }
    for event in event_rows:
        if event.get("event") != "algorithm_c_candidate_replay":
            continue
        validation = event.get("validation")
        if not isinstance(validation, Mapping) or not physical_valid(validation):
            excluded["technical_or_invalid"] += 1
            continue
        candidate = validation.get("candidate_ranking")
        if not isinstance(candidate, Mapping) or not isinstance(
            candidate.get("optimized_params"),
            Mapping,
        ):
            excluded["candidate_missing"] += 1
            continue
        signature = exact_param_signature(candidate["optimized_params"])
        if signature == baseline_signature:
            excluded["baseline_parameter_vector"] += 1
            continue
        origin = str(candidate.get("candidate_origin") or "").lower()
        probe_kind = str(candidate.get("probe_kind") or "").lower()
        if any(
            marker in f"{origin}:{probe_kind}"
            for marker in ("baseline_control", "uncertain_probe")
        ):
            excluded["diagnostic_or_uncertain_probe"] += 1
            continue
        if signature in accepted_signatures:
            excluded["previously_accepted_parameter_vector"] += 1
            continue
        if signature in seen_signatures:
            excluded["duplicate_parameter_vector"] += 1
            continue
        key_worker = str(validation.get("worker_key") or "")
        worker_baseline = baseline_by_worker.get(key_worker)
        if not isinstance(worker_baseline, Mapping):
            excluded["worker_baseline_missing"] += 1
            continue
        rows.append(
            {
                "source_round_index": int(event.get("round_index") or 0),
                "source_label": str(validation.get("label") or ""),
                "source_worker_key": key_worker,
                "validation": copy.deepcopy(dict(validation)),
                "source_worker_baseline": copy.deepcopy(dict(worker_baseline)),
                "source_canonical_baseline": copy.deepcopy(
                    dict(canonical_baseline)
                ),
            }
        )
        seen_signatures.add(signature)
    payload: Dict[str, Any] = {
        "contract": HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
        "source_algorithm": "algorithm_c",
        "source_campaign": source_campaign,
        "target": target,
        "workload_contract_sha256": canonical_sha256(
            algorithm.get("workload_contract") or {}
        ),
        "experience_table_path": str(experience_table_path),
        "experience_table_sha256": hashlib.sha256(
            experience_table_path.read_bytes()
        ).hexdigest(),
        "source_accepted": int(state.get("accepted") or 0),
        "source_round_index": int(state.get("round_index") or 0),
        "source_technical_failures": int(state.get("technical_failures") or 0),
        "row_count": len(rows),
        "rows": rows,
        "excluded": excluded,
        "technical_failures_excluded": True,
        "previously_accepted_parameter_vectors_excluded": True,
        "accepted_state_inherited": False,
        "ranges_inherited": False,
        "gradient_state_inherited": False,
        "qps_outer_only": True,
        "resource_prediction_used": False,
        "physical_workload_invocations": 0,
        "valid_replays_added": 0,
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--experience-table", required=True, type=Path)
    parser.add_argument("--source-campaign", required=True)
    parser.add_argument("--target", required=True, choices=("cpu", "memory", "io"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build_seed(
        state=read_json(args.state),
        events=read_jsonl(args.log),
        config=read_json(args.config),
        experience_table_path=args.experience_table,
        source_campaign=args.source_campaign,
        target=args.target,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    print(json.dumps({key: value for key, value in payload.items() if key != "rows"}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
