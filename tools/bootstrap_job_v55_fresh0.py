#!/usr/bin/env python3
"""Create JOB v55 fresh0 with v54 technical evidence quarantined."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


B = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
T = B / "job_final_algorithm_fresh10_v36_20260731T2020"
HISTORICAL_STATE = T / "package/formal_state.json"
HISTORICAL_ACCEPTED_COUNTER = B / "formal_search_accepted_counter.txt"
V50_ROOT = T / "autonomous_target10_v50_fresh0"
V50_STATE = V50_ROOT / "package/formal_state.json"
V50_ACCEPTED_COUNTER = V50_ROOT / "formal_search_accepted_counter.txt"
V52_ROOT = T / "autonomous_target10_v52r2_fresh0"
V52_STATE = V52_ROOT / "package/formal_state.json"
V52_ACCEPTED_COUNTER = V52_ROOT / "formal_search_accepted_counter.txt"
V53_ROOT = T / "autonomous_target10_v53_fresh0"
V53_STATE = V53_ROOT / "package/formal_state.json"
V53_ACCEPTED_COUNTER = V53_ROOT / "formal_search_accepted_counter.txt"
V54_ROOT = T / "autonomous_target10_v54_fresh0"
V54_STATE = V54_ROOT / "package/formal_state.json"
V54_ACCEPTED_COUNTER = V54_ROOT / "formal_search_accepted_counter.txt"
V55_ROOT = T / "autonomous_target10_v55_fresh0"
V55_STATE = V55_ROOT / "package/formal_state.json"
V55_ACCEPTED_COUNTER = V55_ROOT / "formal_search_accepted_counter.txt"
BOOTSTRAP_AUDIT = V55_ROOT / "fresh0_bootstrap_audit.json"
GLOBAL_COUNTER = B / "replay_counter.txt"
FORMAL_COUNTER = B / "formal_search_replay_counter.txt"
EXPECTED_GLOBAL_COUNTER = 399
EXPECTED_FORMAL_COUNTER = 206
CAMPAIGN_CONTRACT = "job_v55_fresh0_v54_postrun_seed_technical_excluded_v1"
BASELINE_SCORE = 4.416709205648302
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise RuntimeError(f"invalid counter: {path}")
    return int(raw)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_counter(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(f"{value}\n", encoding="utf-8")
    os.replace(temporary, path)


def extract_fingerprints(value: Any, result: set[str]) -> None:
    if isinstance(value, Mapping):
        fingerprint = value.get("three_level_fingerprint")
        if isinstance(fingerprint, Mapping):
            identifier = fingerprint.get("id")
            if isinstance(identifier, str) and identifier:
                result.add(identifier)
        for child in value.values():
            extract_fingerprints(child, result)
    elif isinstance(value, list):
        for child in value:
            extract_fingerprints(child, result)


def technical_audit(
    root: Path,
    label: str,
    expected_batches: int,
) -> dict[str, Any]:
    summaries = sorted(root.glob("cycle_*/discovery_replay_*/replay/summary.json"))
    summaries.extend(sorted(root.glob("cycle_*/repeat_*/replay/summary.json")))
    if len(summaries) != expected_batches:
        raise RuntimeError(
            f"expected exactly {expected_batches} sealed {label} technical "
            f"batches, got {len(summaries)}"
        )
    records = []
    physical_total = 0
    fingerprints: set[str] = set()
    fingerprint_sources = []
    fingerprint_source_paths: set[Path] = set()
    for summary_path in summaries:
        summary = load_json(summary_path)
        selection_path = summary_path.parent.parent / "top5.json"
        if not selection_path.is_file():
            raise RuntimeError(f"{label} technical selection is missing: {selection_path}")
        selection = load_json(selection_path)
        before = len(fingerprints)
        extract_fingerprints(selection, fingerprints)
        if len(fingerprints) > before:
            fingerprint_sources.append(
                {
                    "path": str(selection_path),
                    "sha256": sha256_file(selection_path),
                }
            )
            fingerprint_source_paths.add(selection_path)
        physical = int(summary.get("physical_invocation_count", -1))
        valid = int(summary.get("successful_real_replay_count", -1))
        technical = int(summary.get("technical_failure_count", -1))
        if (
            (physical, valid, technical) != (1, 0, 1)
            or summary.get("diagnostic_only") is not True
            or summary.get("baseline_params_restored") is not True
            or (summary.get("baseline_restore") or {}).get("success") is not True
            or summary.get("initial_state") != summary.get("final_state")
            or summary.get("formal_update_flags")
            != {"accepted": False, "gradient": False, "incumbent": False, "range": False}
            or summary.get("selection_sha256") != sha256_file(selection_path)
        ):
            raise RuntimeError(f"{label} technical batch contract mismatch: {summary_path}")
        physical_total += physical
        records.append(
            {
                "summary_path": str(summary_path),
                "summary_sha256": sha256_file(summary_path),
                "selection_path": str(selection_path),
                "selection_sha256": sha256_file(selection_path),
                "physical_calls": physical,
                "valid_replays": valid,
                "technical_failures": technical,
            }
        )
    for selection_path in sorted(root.rglob("top5*.json")):
        if not selection_path.is_file() or selection_path in fingerprint_source_paths:
            continue
        before = len(fingerprints)
        extract_fingerprints(load_json(selection_path), fingerprints)
        if len(fingerprints) > before:
            fingerprint_sources.append(
                {
                    "path": str(selection_path),
                    "sha256": sha256_file(selection_path),
                }
            )
            fingerprint_source_paths.add(selection_path)
    if physical_total != expected_batches or len(fingerprints) < 5:
        raise RuntimeError(f"{label} technical physical/fingerprint audit is incomplete")
    return {
        "records": records,
        "physical_calls": physical_total,
        "fingerprints": sorted(fingerprints),
        "fingerprint_sources": fingerprint_sources,
    }


def validate_zero_state(
    value: Mapping[str, Any],
    accepted_counter: Path,
    label: str,
) -> None:
    expected_range = {
        name: {"min": amount, "max": amount}
        for name, amount in BASELINE_PARAMS.items()
    }
    if (
        int(value.get("accepted_count", -1)) != 0
        or float(value.get("incumbent_score", -1)) != BASELINE_SCORE
        or value.get("incumbent_params") != BASELINE_PARAMS
        or value.get("range_by_parameter") != expected_range
        or int(value.get("gradient_observation_count", -1)) != 0
        or counter(accepted_counter) != 0
    ):
        raise RuntimeError(f"{label} state is not the sealed accepted0 baseline point")


def main() -> int:
    historical_sha = sha256_file(HISTORICAL_STATE)
    v50_state_sha = sha256_file(V50_STATE)
    v52_state_sha = sha256_file(V52_STATE)
    v53_state_sha = sha256_file(V53_STATE)
    v54_state_sha = sha256_file(V54_STATE)
    if (
        int(load_json(HISTORICAL_STATE).get("accepted_count", -1)) != 3
        or counter(HISTORICAL_ACCEPTED_COUNTER) != 3
    ):
        raise RuntimeError("historical accepted=3 authority mismatch")
    v50_state = load_json(V50_STATE)
    validate_zero_state(v50_state, V50_ACCEPTED_COUNTER, "v50")
    v52_state = load_json(V52_STATE)
    validate_zero_state(v52_state, V52_ACCEPTED_COUNTER, "v52r2")
    v53_state = load_json(V53_STATE)
    validate_zero_state(v53_state, V53_ACCEPTED_COUNTER, "v53")
    v54_state = load_json(V54_STATE)
    validate_zero_state(v54_state, V54_ACCEPTED_COUNTER, "v54")
    v50_technical = technical_audit(V50_ROOT, "v50", 3)
    v52_technical = technical_audit(V52_ROOT, "v52r2", 2)
    v53_technical = technical_audit(V53_ROOT, "v53", 1)
    v54_technical = technical_audit(V54_ROOT, "v54", 1)
    global_counter = counter(GLOBAL_COUNTER)
    formal_counter = counter(FORMAL_COUNTER)
    if (
        global_counter != EXPECTED_GLOBAL_COUNTER
        or formal_counter != EXPECTED_FORMAL_COUNTER
        or int(v50_state.get("global_ordinal", -1))
        + v50_technical["physical_calls"]
        != 395
        or int(v52_state.get("global_ordinal", -1))
        + v52_technical["physical_calls"]
        != 397
        or int(v53_state.get("global_ordinal", -1))
        + v53_technical["physical_calls"]
        != 398
        or int(v54_state.get("global_ordinal", -1))
        + v54_technical["physical_calls"]
        != global_counter
        or int(v50_state.get("formal_replay_count", -1)) != formal_counter
        or int(v52_state.get("formal_replay_count", -1)) != formal_counter
        or int(v53_state.get("formal_replay_count", -1)) != formal_counter
        or int(v54_state.get("formal_replay_count", -1)) != formal_counter
    ):
        raise RuntimeError("v55 external/v50/v52r2/v53/v54 counter handoff mismatch")

    excluded_fingerprints = sorted(
        set(v50_technical["fingerprints"])
        | set(v52_technical["fingerprints"])
        | set(v53_technical["fingerprints"])
        | set(v54_technical["fingerprints"])
    )
    fresh = dict(v54_state)
    fresh.update(
        {
            "campaign_contract": CAMPAIGN_CONTRACT,
            "accepted_count": 0,
            "accepted_target": 10,
            "incumbent_score": BASELINE_SCORE,
            "incumbent_params": dict(BASELINE_PARAMS),
            "range_by_parameter": {
                name: {"min": amount, "max": amount}
                for name, amount in BASELINE_PARAMS.items()
            },
            "gradient_observation_count": 0,
            "formal_valid_replay_count": 0,
            "global_ordinal": global_counter,
            "formal_replay_count": formal_counter,
            "historical_accepted_excluded": 3,
            "historical_v50_accepted_excluded": 0,
            "historical_state_sha256": historical_sha,
            "historical_v50_state_sha256": v50_state_sha,
            "historical_v52r2_state_sha256": v52_state_sha,
            "historical_v53_state_sha256": v53_state_sha,
            "historical_v54_state_sha256": v54_state_sha,
            "historical_v50_root_used_for_technical_audit_and_exclusion_only": True,
            "historical_v52r2_root_used_for_technical_audit_and_exclusion_only": True,
            "historical_v53_root_used_for_technical_audit_and_exclusion_only": True,
            "historical_v54_root_used_for_technical_audit_and_exclusion_only": True,
            "historical_v50_fingerprint_count": len(v50_technical["fingerprints"]),
            "historical_v52r2_fingerprint_count": len(v52_technical["fingerprints"]),
            "historical_v53_fingerprint_count": len(v53_technical["fingerprints"]),
            "historical_v54_fingerprint_count": len(v54_technical["fingerprints"]),
            "historical_incumbent_or_range_inherited": False,
            "countable_formal_accepted_origin": 0,
            "snapshot_only": False,
        }
    )
    fresh.pop("last_formal_batch", None)
    bootstrap = {
        "schema_version": "job_v55_fresh0_bootstrap_audit_v1",
        "status": "complete",
        "campaign_contract": CAMPAIGN_CONTRACT,
        "historical_state_sha256": historical_sha,
        "v50_root": str(V50_ROOT),
        "v50_state_sha256": v50_state_sha,
        "v52r2_root": str(V52_ROOT),
        "v52r2_state_sha256": v52_state_sha,
        "v53_root": str(V53_ROOT),
        "v53_state_sha256": v53_state_sha,
        "v50_accepted_count": 0,
        "v50_countable_accepted_in_v52": 0,
        "v50_role": "historical_technical_audit_and_fingerprint_exclusion_only",
        "v50_technical_batches": v50_technical["records"],
        "v50_technical_physical_calls": v50_technical["physical_calls"],
        "v52r2_accepted_count": 0,
        "v52r2_countable_accepted_in_v53": 0,
        "v52r2_role": "historical_technical_audit_and_fingerprint_exclusion_only",
        "v52r2_technical_batches": v52_technical["records"],
        "v52r2_technical_physical_calls": v52_technical["physical_calls"],
        "v53_accepted_count": 0,
        "v53_countable_accepted_in_v54": 0,
        "v53_role": "historical_technical_audit_and_fingerprint_exclusion_only",
        "v53_technical_batches": v53_technical["records"],
        "v53_technical_physical_calls": v53_technical["physical_calls"],
        "v54_root": str(V54_ROOT),
        "v54_state_sha256": v54_state_sha,
        "v54_accepted_count": 0,
        "v54_countable_accepted_in_v55": 0,
        "v54_role": "historical_technical_audit_and_fingerprint_exclusion_only",
        "v54_failure_classification": (
            "technical_postrun_auxiliary_seed_after_core_measurement"
        ),
        "v54_technical_batches": v54_technical["records"],
        "v54_technical_physical_calls": v54_technical["physical_calls"],
        "excluded_fingerprint_count": len(excluded_fingerprints),
        "excluded_fingerprints": excluded_fingerprints,
        "fingerprint_sources": (
            v50_technical["fingerprint_sources"]
            + v52_technical["fingerprint_sources"]
            + v53_technical["fingerprint_sources"]
            + v54_technical["fingerprint_sources"]
        ),
        "fresh_accepted_count": 0,
        "fresh_incumbent_score": BASELINE_SCORE,
        "fresh_incumbent_params": dict(BASELINE_PARAMS),
        "fresh_range_is_baseline_point": True,
        "fresh_gradient_observation_count": 0,
        "global_ordinal": global_counter,
        "formal_replay_count": formal_counter,
        "old_roots_modified": False,
        "workload_calls": 0,
        "explain_calls": 0,
    }
    if V55_STATE.exists():
        if load_json(V55_STATE) != fresh:
            raise RuntimeError("existing v55 fresh state differs from deterministic initializer")
    else:
        atomic_json(V55_STATE, fresh)
    if V55_ACCEPTED_COUNTER.exists():
        if counter(V55_ACCEPTED_COUNTER) != 0:
            raise RuntimeError("existing v55 accepted counter is not zero")
    else:
        atomic_counter(V55_ACCEPTED_COUNTER, 0)
    bootstrap["fresh_state_sha256"] = sha256_file(V55_STATE)
    if BOOTSTRAP_AUDIT.exists():
        if load_json(BOOTSTRAP_AUDIT) != bootstrap:
            raise RuntimeError("existing v55 bootstrap audit mismatch")
    else:
        atomic_json(BOOTSTRAP_AUDIT, bootstrap)
    if (
        sha256_file(HISTORICAL_STATE) != historical_sha
        or sha256_file(V50_STATE) != v50_state_sha
        or sha256_file(V52_STATE) != v52_state_sha
        or sha256_file(V53_STATE) != v53_state_sha
        or sha256_file(V54_STATE) != v54_state_sha
        or counter(HISTORICAL_ACCEPTED_COUNTER) != 3
        or counter(V50_ACCEPTED_COUNTER) != 0
        or counter(V52_ACCEPTED_COUNTER) != 0
        or counter(V53_ACCEPTED_COUNTER) != 0
        or counter(V54_ACCEPTED_COUNTER) != 0
        or counter(GLOBAL_COUNTER) != EXPECTED_GLOBAL_COUNTER
        or counter(FORMAL_COUNTER) != EXPECTED_FORMAL_COUNTER
    ):
        raise RuntimeError("authority changed during v55 zero-call initialization")
    print(json.dumps(bootstrap, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
