#!/usr/bin/env python3
"""Prepare the zero-workload TPC-C v34 heterogeneous-repeat package."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import build_tpcc_exact_plan_repeat_validation_v3 as repeat_v3
from tools import build_tpcc_exact_plan_repeat_validation_v5 as repeat_v5
from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v34_heterogeneous_repeat as v34


PROJECT = Path("/home/anonymous/program/resource_adaptive")
SOURCE = PROJECT / "history/tpcc_final_algorithm_fresh0_v33_20260803"
SOURCE_MANIFEST = SOURCE / "manifest.json"
SOURCE_STATE = SOURCE / "autonomous_controller/controller_state.json"
SOURCE_FORMAL = SOURCE / "formal_state.json"
SOURCE_SELECTION = SOURCE / "autonomous_controller/prepare_01/sealed_run/top5.json"
SOURCE_LEDGER = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v33_20260803/autonomous_runs_v33/"
    "autonomous_diagnostic_validation_01/"
    "strict_top5_real_replay_round14_ledger_v1.json"
)
SOURCE_SUMMARY = SOURCE_LEDGER.parent / (
    "top5_real_replay_round14_diagnostic_v1/summary.json"
)

TARGET = PROJECT / "history/tpcc_final_algorithm_fresh0_v34_20260803"
FORMAL = TARGET / "formal_state.json"
EXCLUSIONS = TARGET / "global_fingerprint_exclusions.json"
CONTROL = TARGET / "autonomous_controller"
PREPARATION = CONTROL / "prepare_01"
SELECTION = PREPARATION / "top5.json"
STATE = CONTROL / "controller_state.json"
MANIFEST = TARGET / "manifest.json"
BATCH_PARENT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v34_20260803/autonomous_runs_v34"
)


EXPECTED = {
    SOURCE_MANIFEST: "719a583db11815f0e51a53c4b17c75aa802de08c408c631f1b750988a5449ce9",
    SOURCE_STATE: "433196ad57438500a7464ce4348a7000f3b94148cf139fc14e6fabe6e8060eb6",
    SOURCE_FORMAL: "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0",
    SOURCE_SELECTION: "3a6100109ef07741bbd289c44be68e0b0b034831305b0943378833f36045f2f8",
    SOURCE_LEDGER: "518202075fa7f4951ad107705d1b82a9bc3700fa55d9bb3f9fa07b0b375b9931",
    SOURCE_SUMMARY: "6c4b76625a71950d67e895ecb0b854976c93b8e3783b67d60de156dc239348e5",
}


def _require_sources() -> None:
    mismatches = {
        str(path): {"expected": expected, "observed": v25.sha256(path)}
        for path, expected in EXPECTED.items()
        if not path.is_file() or v25.sha256(path) != expected
    }
    if mismatches:
        raise RuntimeError(f"TPC-C v34 source SHA mismatch: {mismatches}")


def _fresh_formal_gate(formal: dict) -> None:
    if (
        formal.get("accepted_count") != 0
        or formal.get("inherited_accepted") != 0
        or formal.get("formal_physical_call_count") != 0
        or formal.get("formal_valid_replay_count") != 0
        or formal.get("formal_technical_failure_count") != 0
        or formal.get("gradient_update_count") != 0
        or formal.get("gradient_state_reset") is not True
    ):
        raise RuntimeError("TPC-C v34 formal state is not fresh0")


def main() -> int:
    _require_sources()
    if TARGET.exists() or BATCH_PARENT.exists():
        raise RuntimeError("TPC-C v34 target already exists")
    source_state = v25.load_json(SOURCE_STATE)
    if source_state.get("status") != "preparing_next_batch":
        raise RuntimeError("TPC-C v33 did not stop at the sealed fail-closed boundary")
    audits = [
        copy.deepcopy(item)
        for item in source_state.get("audits") or []
        if Path(str(item.get("root") or "")).resolve() == SOURCE_LEDGER.parent
    ]
    if len(audits) != 1:
        raise RuntimeError("TPC-C v34 requires exactly one v33 completed audit")
    audit = audits[0]
    summary = v25.load_json(SOURCE_SUMMARY)
    if (
        audit.get("clean") is not True
        or audit.get("mode") != "diagnostic_validation"
        or audit.get("physical_calls") != 5
        or audit.get("valid_replays") != 5
        or audit.get("technical_failures") != 0
        or audit.get("baseline_restored") is not True
        or summary.get("accepted_count_delta") != 0
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or summary.get("state_commit_eligible") is not False
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
    ):
        raise RuntimeError("TPC-C v33 source audit is not clean state-neutral 5/5/0")
    if not (
        (source_state.get("last_quality_gate") or {}).get("checks", {}).get(
            "five_novel_noncorner_fingerprints"
        )
        is True
    ):
        raise RuntimeError("TPC-C v33 group origin lacks the novelty proof")

    formal = v25.load_json(SOURCE_FORMAL)
    _fresh_formal_gate(formal)
    source_selection = v25.load_json(SOURCE_SELECTION)
    provisional = repeat_v3.build(
        source_selection,
        v25.load_json(SOURCE_LEDGER),
        0.0,
    )
    repeated = repeat_v5.add_repeat_contract(
        provisional,
        source_selection,
        source_selection_path=SOURCE_SELECTION,
        source_selection_sha256=v25.sha256(SOURCE_SELECTION),
        source_ledger_path=SOURCE_LEDGER,
        source_ledger_sha256=v25.sha256(SOURCE_LEDGER),
    )
    contract = repeated["validation_repeat_contract"]
    counts = [
        item["total_exact_observation_count"]
        for item in contract["per_point_total_exact_observation_counts"]
    ]
    if sorted(counts) != [1, 2, 2, 2, 2]:
        raise RuntimeError(f"unexpected v34 heterogeneous history: {counts}")

    PREPARATION.mkdir(parents=True, mode=0o700)
    v25.atomic_text(FORMAL, SOURCE_FORMAL.read_text(encoding="utf-8"))
    if v25.sha256(FORMAL) != v25.sha256(SOURCE_FORMAL):
        raise RuntimeError("fresh formal state copy SHA mismatch")
    exclusions = {
        "contract": "tpcc_v34_global_fingerprint_exclusions_v1",
        "fingerprint_ids": sorted(
            set(source_state.get("quarantined_fingerprints") or [])
        ),
        "source_state_path": str(SOURCE_STATE),
        "source_state_sha256": v25.sha256(SOURCE_STATE),
    }
    v25.atomic_json(EXCLUSIONS, exclusions)
    v25.atomic_json(SELECTION, repeated)

    origin = copy.deepcopy(audit)
    origin["group_origin_novel_fingerprint_gate_passed"] = True
    origin = v34.annotate_same_group_batch_count(origin, [])
    if origin["complete_same_group_batch_count"] != 1:
        raise RuntimeError("TPC-C v33 origin must be same-group batch one")

    manifest = v25.load_json(SOURCE_MANIFEST)
    manifest.update(
        {
            "controller_version": 34,
            "controller_root": str(CONTROL),
            "controller_state_path": str(STATE),
            "batch_parent": str(BATCH_PARENT),
            "formal_state_path": str(FORMAL),
            "initial_formal_state_sha256": v25.sha256(FORMAL),
            "formal_accepted_start": 0,
            "next_ordinal": 2,
            "exact_repeat_builder_path": str(
                PROJECT / "tools/build_tpcc_exact_plan_repeat_validation_v5.py"
            ),
            "v34_activation_authorized": False,
            "v34_algorithm_frozen": True,
            "v34_v33_batch_training_only": True,
            "v34_repeat_count_contract": (
                "complete_same_group_batches_separate_from_point_history_v1"
            ),
            "v34_formal_gate_minimum_per_point_observations": 3,
            "v34_repeat_cap_basis": "complete_same_group_batch_count",
            "v34_source_state_sha256": v25.sha256(SOURCE_STATE),
            "v34_source_selection_sha256": v25.sha256(SOURCE_SELECTION),
            "v34_source_ledger_sha256": v25.sha256(SOURCE_LEDGER),
        }
    )
    extra_pins = [
        PROJECT / "tools/build_tpcc_exact_plan_repeat_validation_v5.py",
        PROJECT / "tools/tpcc_autonomous_target10_controller_v34_heterogeneous_repeat.py",
        PROJECT / "tools/prepare_tpcc_v34_heterogeneous_repeat.py",
        PROJECT / "tools/preflight_tpcc_v34_heterogeneous_repeat.py",
        PROJECT / "tests/test_tpcc_v34_heterogeneous_repeat.py",
        SOURCE_STATE,
        SOURCE_SELECTION,
        SOURCE_LEDGER,
        SOURCE_SUMMARY,
        FORMAL,
        EXCLUSIONS,
        SELECTION,
    ]
    pins = {
        str(Path(item["path"]).resolve()): copy.deepcopy(item)
        for item in manifest.get("pinned_files") or []
    }
    for path in extra_pins:
        pins[str(path.resolve())] = {
            "path": str(path.resolve()),
            "sha256": v25.sha256(path.resolve()),
        }
    manifest["pinned_files"] = [pins[key] for key in sorted(pins)]

    seal = v25.seal_next_run(
        manifest,
        PREPARATION,
        SELECTION,
        "diagnostic_validation",
        1,
        {"passed": False, "source": "v34_heterogeneous_repeat_training_only"},
    )
    current = {
        "mode": "diagnostic_validation",
        "root": seal["run_root"],
        "selection_path": seal["selection_path"],
        "selection_sha256": seal["selection_sha256"],
        "ledger_path": seal["ledger_path"],
        "summary_path": seal["summary_path"],
        "launcher_path": seal["launcher_path"],
        "launcher_sha256": seal["launcher_sha256"],
        "seal_path": seal["seal_path"],
        "seal_sha256": seal["seal_sha256"],
        "formal_state_sha256_before": seal["formal_state_sha256_before"],
        "launch_if_absent": True,
        "launcher_invoked": False,
    }
    state = {
        "schema_version": "tpcc_autonomous_target10_state_v1",
        "status": "waiting_current_batch",
        "dry_run": False,
        "fresh0_contract": "tpcc_v34_heterogeneous_repeat_fresh0_v1",
        "historical_accepted_excluded": 2,
        "audits": [origin],
        "sources": [
            {
                "name": "v33_clean5_training_only_group_origin",
                "selection_path": str(SOURCE_SELECTION),
                "selection_sha256": v25.sha256(SOURCE_SELECTION),
                "ledger_path": str(SOURCE_LEDGER),
                "ledger_sha256": v25.sha256(SOURCE_LEDGER),
                "accepted_count_eligible": False,
                "training_only": True,
            }
        ],
        "current_run": current,
        "next_ordinal": 2,
        "formal_state_sha256": v25.sha256(FORMAL),
        "quarantined_fingerprints": exclusions["fingerprint_ids"],
        "recovery_audits": [],
        "technical_failure_audits": [],
        "acquisition_transitions": [],
    }
    manifest["current_run"] = copy.deepcopy(current)
    v25.atomic_json(STATE, state)
    manifest["initial_controller_state_sha256"] = v25.sha256(STATE)
    v25.atomic_json(MANIFEST, manifest)
    report = {
        "contract": "tpcc_v34_heterogeneous_repeat_zero_call_preparation_v1",
        "passed": True,
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
        "formal_accepted_count": 0,
        "v33_training_batches_inherited": 1,
        "complete_same_group_batch_count": 1,
        "next_same_group_batch_ordinal": 2,
        "per_point_total_exact_observation_counts": counts,
        "selection_sha256": v25.sha256(SELECTION),
        "sealed_selection_sha256": seal["selection_sha256"],
        "formal_state_sha256": v25.sha256(FORMAL),
        "controller_state_sha256": v25.sha256(STATE),
        "manifest_sha256": v25.sha256(MANIFEST),
        "next_action": "preflight_only_no_workload_launch",
    }
    v25.atomic_json(TARGET / "v34_zero_call_preparation_report.json", report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
