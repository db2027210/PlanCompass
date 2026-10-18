#!/usr/bin/env python3
"""Launch the XP final algorithm from accepted=0 after a strict seed gate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


RESOURCES = ("cpu", "io", "memory")


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    output = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and math.isclose(
            values[order[cursor]], values[order[end]], rel_tol=0.0, abs_tol=1e-12
        ):
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index in order[cursor:end]:
            output[index] = rank
        cursor = end
    return output


def spearman(predicted: list[float], actual: list[float]) -> float | None:
    left = ranks(predicted)
    right = ranks(actual)
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left[index] - left_mean) * (right[index] - right_mean)
        for index in range(len(left))
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return numerator / (left_norm * right_norm)


def gate(selection: Mapping[str, Any], replay: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = selection.get("top5") or []
    successful = replay.get("successful") or []
    if len(candidates) != 5 or len(successful) != 5:
        raise RuntimeError("XP seed gate requires Top-5 clean5 evidence")
    if (
        int(replay.get("physical_workload_invocation_count", -1)) != 5
        or int(replay.get("successful_real_replay_count", -1)) != 5
        or int(replay.get("technical_failure_count", -1)) != 0
        or replay.get("baseline_restore_verified") is not True
    ):
        raise RuntimeError("XP seed replay is not clean5 with baseline restored")
    for field in (
        "accepted_update_applied",
        "gradient_update_applied",
        "incumbent_updated",
        "range_updated",
        "state_mutations_applied_by_this_bundle",
    ):
        if replay.get(field) is not False:
            raise RuntimeError(f"XP seed mutated formal state: {field}")

    observations = {
        str(item.get("fingerprint_id") or ""): item for item in successful
    }
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in candidates
    ]
    if len(set(identifiers)) != 5 or "" in identifiers:
        raise RuntimeError("XP seed fingerprints are incomplete or duplicated")
    rows = []
    for candidate, identifier in zip(candidates, identifiers):
        observation = observations.get(identifier)
        if observation is None:
            raise RuntimeError(f"XP seed observation missing: {identifier}")
        predicted_score = float(candidate["candidate_score"])
        actual_score = float(observation["actual_score"])
        factors = {
            name: max(
                float(candidate["predicted_resource"][name])
                / float(observation["actual_resource"][name]),
                float(observation["actual_resource"][name])
                / float(candidate["predicted_resource"][name]),
            )
            for name in RESOURCES
        }
        rows.append(
            {
                "role": str(candidate.get("selection_role") or ""),
                "predicted_score": predicted_score,
                "actual_score": actual_score,
                "absolute_score_error": abs(actual_score - predicted_score),
                "resource_factor_error": factors,
            }
        )
    trusted = [item for item in rows if item["role"].startswith("trusted")]
    probes = [item for item in rows if "probe" in item["role"]]
    trusted_rho = spearman(
        [item["predicted_score"] for item in trusted],
        [item["actual_score"] for item in trusted],
    )
    score_mae = sum(item["absolute_score_error"] for item in rows) / len(rows)
    noise_bound = 2.0 * score_mae
    score_span = max(item["predicted_score"] for item in rows) - min(
        item["predicted_score"] for item in rows
    )
    actual_best_predicted_rank = max(
        range(len(rows)), key=lambda index: rows[index]["actual_score"]
    ) + 1
    maximum_resource_factor = max(
        value for item in trusted for value in item["resource_factor_error"].values()
    )
    checks = {
        "five_unique_three_level_fingerprints": len(set(identifiers)) == 5,
        "trusted3_probe2": len(trusted) == 3 and len(probes) == 2,
        "top5_no_technical_failure": replay.get("technical_failure_count") == 0,
        "trusted_only_spearman_positive": trusted_rho is not None and trusted_rho > 0.0,
        "actual_best_in_predicted_top3": actual_best_predicted_rank <= 3,
        "trusted_resource_no_order_of_magnitude_error": maximum_resource_factor < 10.0,
        "score_span_exceeds_replay_noise_bound": score_span > noise_bound,
        "baseline_restored": replay.get("baseline_restore_verified") is True,
    }
    return {
        "schema_version": "xp_fresh0_entry_gate_v1",
        "passed": all(checks.values()),
        "checks": checks,
        "trusted_only_spearman": trusted_rho,
        "actual_best_predicted_rank": actual_best_predicted_rank,
        "score_mae": score_mae,
        "score_noise_bound_2mae": noise_bound,
        "predicted_score_span": score_span,
        "maximum_trusted_resource_factor_error": maximum_resource_factor,
        "selection_sha256": sha256(args_global.selection),
        "replay_sha256": sha256(args_global.replay),
    }


def main() -> int:
    global args_global
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--training-state", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--formal-manifest", type=Path, required=True)
    parser.add_argument("--authority-counter", type=Path, required=True)
    parser.add_argument("--authority-history", type=Path, required=True)
    parser.add_argument("--canonical-state", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--acceptance-manifest", type=Path, required=True)
    parser.add_argument("--query-snapshot", type=Path, required=True)
    parser.add_argument("--restore-script", type=Path, required=True)
    args_global = parser.parse_args()
    args = args_global

    bundle = args.bundle_root.resolve()
    sys.path.insert(0, str(bundle))
    from xp_unattended_adapters import (  # type: ignore
        FormalPaths,
        generate_selection,
        restore_baseline,
        run_batch,
    )
    from xp_unattended_continuation import (  # type: ignore
        AtomicStateStore,
        UnattendedContinuationController,
        enforce_process_cpuset,
        new_state,
    )

    enforce_process_cpuset("32-51,288-307")
    campaign = args.campaign_root.resolve()
    if campaign.exists():
        raise RuntimeError(f"fresh XP campaign already exists: {campaign}")
    seed_gate = gate(load(args.selection), load(args.replay))
    if seed_gate["passed"] is not True:
        raise RuntimeError(f"XP fresh0 entry gate failed: {seed_gate['checks']}")

    seed_dir = campaign / "batches" / "seed000"
    seed_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.selection, seed_dir / "selection.json")
    shutil.copy2(args.replay, seed_dir / "replay_summary.json")
    (campaign / "entry_gate.json").write_text(
        json.dumps(seed_gate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    training = load(args.training_state)
    state = new_state(accepted_target=10)
    for field in (
        "incumbent",
        "incumbent_score",
        "incumbent_params",
        "parameter_ranges",
        "range_state",
    ):
        state[field] = copy.deepcopy(training[field])
    seed_ids = [str(item["fingerprint_id"]) for item in load(args.replay)["successful"]]
    seed_batch = {
        "batch_id": "seed000",
        "mode": "diagnostic_validation",
        "status": "complete",
        "physical_calls": 5,
        "accepted": 0,
        "technical_failures": 0,
        "rejected": 0,
        "fingerprint_digest": hashlib.sha256(
            json.dumps(sorted(seed_ids), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "training_seed_only": True,
    }
    state.update(
        {
            "accepted_count": 0,
            "accepted_fingerprints": [],
            "replayed_fingerprints": sorted(seed_ids),
            "batch_history": [seed_batch],
            "last_batch": seed_batch,
            "next_batch_index": 1,
            "status": "initialized_fresh0",
            "historical_accepted_excluded": int(training.get("accepted_count", 0)),
            "training_state_sha256": sha256(args.training_state),
            "entry_gate_sha256": sha256(campaign / "entry_gate.json"),
        }
    )
    state_path = campaign / "state.json"
    store = AtomicStateStore(state_path)
    store.commit(state)

    paths = FormalPaths(
        root=bundle,
        state=state_path,
        batch_root=campaign / "batches",
        formal_manifest=args.formal_manifest.resolve(),
        authority_counter=args.authority_counter.resolve(),
        authority_history=args.authority_history.resolve(),
        canonical_state=args.canonical_state.resolve(),
        canonical_manifest=args.canonical_manifest.resolve(),
        acceptance_manifest=args.acceptance_manifest.resolve(),
        query_snapshot=args.query_snapshot.resolve(),
        replay_audit=(seed_dir / "replay_summary.json").resolve(),
        rank1_evidence=(seed_dir / "selection.json").resolve(),
        restore_script=args.restore_script.resolve(),
    )
    paths.validate()
    controller = UnattendedContinuationController(
        state_store=store,
        batch_root=campaign / "batches",
        candidate_factory=lambda batch_id, current, batch_dir: generate_selection(
            paths, batch_id, current, batch_dir
        ),
        batch_runner=lambda selection, batch_dir, environment: run_batch(
            paths, selection, batch_dir, environment
        ),
        baseline_restore=lambda batch_dir, phase: restore_baseline(
            paths, batch_dir, phase
        ),
    )
    final_state = controller.run()
    print(
        json.dumps(
            {
                "status": final_state["status"],
                "accepted_count": final_state["accepted_count"],
                "accepted_target": final_state["accepted_target"],
                "campaign_root": str(campaign),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if final_state["status"] == "completed" else 2


args_global: argparse.Namespace


if __name__ == "__main__":
    raise SystemExit(main())
