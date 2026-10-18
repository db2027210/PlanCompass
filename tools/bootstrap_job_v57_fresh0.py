#!/usr/bin/env python3
"""Create JOB v57 fresh0 and quarantine the failed v55 formal fingerprint."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


B = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726")
T = B / "job_final_algorithm_fresh10_v36_20260731T2020"
V55_ROOT = T / "autonomous_target10_v55_fresh0"
V55_STATE = V55_ROOT / "package/formal_state.json"
V55_ACCEPTED = V55_ROOT / "formal_search_accepted_counter.txt"
V57_ROOT = T / "autonomous_target10_v57_fresh0"
V57_STATE = V57_ROOT / "package/formal_state.json"
V57_ACCEPTED = V57_ROOT / "formal_search_accepted_counter.txt"
GLOBAL_COUNTER = B / "replay_counter.txt"
FORMAL_COUNTER = B / "formal_search_replay_counter.txt"
AUDIT = V57_ROOT / "fresh0_bootstrap_audit.json"
QUARANTINE = V57_ROOT / "v55_failed_formal_fingerprint_quarantine.json"
CONTRACT = "job_v57_fresh0_resource_median_no_retry_v1"
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


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise RuntimeError(f"invalid counter: {path}")
    return int(raw)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_counter(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(f"{value}\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_baseline(state: Mapping[str, Any]) -> None:
    expected_range = {name: {"min": value, "max": value} for name, value in BASELINE_PARAMS.items()}
    if (
        int(state.get("accepted_count", -1)) != 0
        or float(state.get("incumbent_score", -1)) != BASELINE_SCORE
        or state.get("incumbent_params") != BASELINE_PARAMS
        or state.get("range_by_parameter") != expected_range
        or int(state.get("gradient_observation_count", -1)) != 0
        or counter(V55_ACCEPTED) != 0
    ):
        raise RuntimeError("v55 authority is not accepted0 baseline")


def fingerprint_from_failure(summary: Mapping[str, Any], selection: Mapping[str, Any]) -> tuple[int, str]:
    failures = summary.get("technical_failures") or []
    if not isinstance(failures, list) or len(failures) != 1 or not isinstance(failures[0], Mapping):
        raise RuntimeError("v55 failed formal ledger is not exactly one technical row")
    failure = failures[0]
    rank = int(failure.get("rank", -1))
    binding = failure.get("candidate_binding") or {}
    identifier = str(binding.get("fingerprint_id") or (binding.get("three_level_fingerprint") or {}).get("id") or "")
    top5 = selection.get("top5") or []
    if not isinstance(top5, list) or rank < 1 or rank > len(top5):
        raise RuntimeError("v55 failed formal rank cannot bind to selection")
    selected_identifier = str(((top5[rank - 1].get("three_level_fingerprint") or {}).get("id")) or "")
    if not identifier:
        identifier = selected_identifier
    if identifier != selected_identifier or not identifier.startswith("three-level-sha256:"):
        raise RuntimeError("v55 failed fingerprint binding mismatch")
    return rank, identifier


def locate_failed_formal() -> tuple[Path, Path, Path, dict[str, Any], str, int]:
    matches = []
    for summary_path in sorted(V55_ROOT.glob("cycle_*/formal/replay/summary.json")):
        transaction = summary_path.with_name("formal_state_commit_transaction.json")
        summary = load(summary_path)
        if transaction.exists() or int(summary.get("technical_failure_count", -1)) != 1:
            continue
        selection_path = summary_path.parents[1] / "package/top5_promoted_formal.json"
        if not selection_path.is_file():
            raise RuntimeError("v55 failed formal selection is missing")
        selection = load(selection_path)
        rank, identifier = fingerprint_from_failure(summary, selection)
        if (
            int(summary.get("physical_invocation_count", -1)) != 1
            or int(summary.get("successful_real_replay_count", -1)) != 0
            or summary.get("baseline_params_restored") is not True
            or (summary.get("baseline_restore") or {}).get("success") is not True
            or summary.get("selection_sha256") != sha(selection_path)
        ):
            raise RuntimeError("v55 failed formal is not the authoritative 1/0/1 batch")
        matches.append((summary_path.parents[1], summary_path, selection_path, summary, identifier, rank))
    if len(matches) != 1:
        raise RuntimeError(f"expected one uncommitted v55 1/0/1 formal batch, found {len(matches)}")
    return matches[0]


def main() -> int:
    v55_sha_before = sha(V55_STATE)
    v55_state = load(V55_STATE)
    validate_baseline(v55_state)
    formal_root, summary_path, selection_path, summary, fingerprint, rank = locate_failed_formal()
    global_now = counter(GLOBAL_COUNTER)
    formal_now = counter(FORMAL_COUNTER)
    # v55 completed exactly two clean5 diagnostics after its fresh0 state.
    # The sealed-selection failure happened before the bridge ordinal claim on
    # some runner paths and just after claim on others, so the external formal
    # counter delta is allowed to be 0 or 1.  The physical attempt remains
    # quarantined in either case and counters are never synthesized/decremented.
    global_delta = global_now - int(v55_state.get("global_ordinal", -1))
    formal_delta = formal_now - int(v55_state.get("formal_replay_count", -1))
    if global_delta not in (10, 11) or formal_delta not in (0, 1):
        raise RuntimeError("v55 failed formal external counter handoff mismatch")
    quarantine = {
        "schema_version": "job_v57_failed_formal_fingerprint_quarantine_v1",
        "status": "quarantined_no_retry",
        "source_campaign": "v55",
        "formal_root": str(formal_root),
        "selection_path": str(selection_path),
        "selection_sha256": sha(selection_path),
        "summary_path": str(summary_path),
        "summary_sha256": sha(summary_path),
        "rank": rank,
        "fingerprint_id": fingerprint,
        "classification": "technical_sealed_explain_binding_failure",
        "physical_calls": 1,
        "external_global_counter_delta_since_v55_fresh0": global_delta,
        "external_formal_counter_delta_since_v55_fresh0": formal_delta,
        "ordinal_claimed": formal_delta == 1,
        "valid_replays": 0,
        "accepted": 0,
        "retry_allowed": False,
        "diagnostic_evidence_role": "historical_training_only",
        "formal_state_update": False,
        "workload_calls": 0,
    }
    fresh = copy.deepcopy(v55_state)
    fresh.update(
        {
            "campaign_contract": CONTRACT,
            "accepted_count": 0,
            "accepted_target": 10,
            "incumbent_score": BASELINE_SCORE,
            "incumbent_params": copy.deepcopy(BASELINE_PARAMS),
            "range_by_parameter": {name: {"min": value, "max": value} for name, value in BASELINE_PARAMS.items()},
            "gradient_observation_count": 0,
            "formal_valid_replay_count": 0,
            "global_ordinal": global_now,
            "formal_replay_count": formal_now,
            "historical_v55_state_sha256": v55_sha_before,
            "historical_v55_accepted_excluded": 0,
            "historical_v55_diagnostics_training_only": True,
            "historical_v55_failed_formal_fingerprint": fingerprint,
            "historical_v55_failed_formal_retry_allowed": False,
            "historical_incumbent_or_range_inherited": False,
            "countable_formal_accepted_origin": 0,
            "snapshot_only": False,
        }
    )
    fresh.pop("last_formal_batch", None)
    audit = {
        "schema_version": "job_v57_fresh0_bootstrap_audit_v1",
        "status": "complete",
        "campaign_contract": CONTRACT,
        "v55_state_sha256": v55_sha_before,
        "v55_accepted_count": 0,
        "v55_countable_accepted_in_v57": 0,
        "v55_diagnostics_reused_as_training_only": True,
        "v55_failed_formal_fingerprint": fingerprint,
        "v55_failed_formal_quarantined_no_retry": True,
        "fresh_accepted_count": 0,
        "fresh_gradient_observation_count": 0,
        "fresh_range_is_baseline_point": True,
        "global_ordinal": global_now,
        "formal_replay_count": formal_now,
        "old_roots_modified": False,
        "workload_calls": 0,
        "explain_calls": 0,
    }
    if QUARANTINE.exists() and load(QUARANTINE) != quarantine:
        raise RuntimeError("existing v57 quarantine differs")
    if not QUARANTINE.exists():
        atomic_json(QUARANTINE, quarantine)
    if V57_STATE.exists() and load(V57_STATE) != fresh:
        raise RuntimeError("existing v57 fresh state differs")
    if not V57_STATE.exists():
        atomic_json(V57_STATE, fresh)
    if V57_ACCEPTED.exists() and counter(V57_ACCEPTED) != 0:
        raise RuntimeError("existing v57 accepted counter is not zero")
    if not V57_ACCEPTED.exists():
        atomic_counter(V57_ACCEPTED, 0)
    audit["quarantine_sha256"] = sha(QUARANTINE)
    audit["fresh_state_sha256"] = sha(V57_STATE)
    if AUDIT.exists() and load(AUDIT) != audit:
        raise RuntimeError("existing v57 bootstrap audit differs")
    if not AUDIT.exists():
        atomic_json(AUDIT, audit)
    if (
        sha(V55_STATE) != v55_sha_before
        or counter(V55_ACCEPTED) != 0
        or counter(GLOBAL_COUNTER) != global_now
        or counter(FORMAL_COUNTER) != formal_now
    ):
        raise RuntimeError("authority changed during v57 zero-call bootstrap")
    print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
