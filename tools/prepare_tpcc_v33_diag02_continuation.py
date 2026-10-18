#!/usr/bin/env python3
"""Seal the zero-workload TPC-C v33 continuation package."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import build_tpcc_v33_diag02_trusted3_probe2 as builder_v33
from tools import tpcc_autonomous_target10_controller_v25 as v25


PROJECT = Path("/home/anonymous/program/resource_adaptive")
SOURCE_MANIFEST = Path(
    "/tmp/tpcc_final_fresh0_autonomous_v51_validation_repeat_v32_20260803/"
    "manifest.json"
)
SOURCE_ROOT = PROJECT / "history/tpcc_final_algorithm_fresh0_v32_20260803"
SOURCE_STATE = SOURCE_ROOT / "autonomous_controller/controller_state.json"
SOURCE_SELECTION = (
    SOURCE_ROOT / "autonomous_controller/prepare_02/sealed_run/top5.json"
)
SOURCE_LEDGER = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v32_20260803/autonomous_runs_v32/"
    "autonomous_diagnostic_02/strict_top5_real_replay_round12_ledger_v1.json"
)
SOURCE_SUMMARY = SOURCE_LEDGER.parent / (
    "top5_real_replay_round12_diagnostic_v1/summary.json"
)
TARGET = PROJECT / "history/tpcc_final_algorithm_fresh0_v33_20260803"
FORMAL = TARGET / "formal_state.json"
CONTROL = TARGET / "autonomous_controller"
PREPARATION = CONTROL / "prepare_01"
SELECTION = PREPARATION / "top5.json"
EXCLUSIONS = TARGET / "global_fingerprint_exclusions.json"
MANIFEST = TARGET / "manifest.json"
STATE = CONTROL / "controller_state.json"
BATCH_PARENT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v33_20260803/autonomous_runs_v33"
)


EXPECTED = {
    SOURCE_STATE: "7db83c502a3492e530e352f08bec7c3bdf9d8d4a38bbc0a47de20001ba38dfef",
    SOURCE_SELECTION: "fcc6b292b6f68fc5ec6cf397be6618820fd6451241f2b1ea9abd3d23f4d89dc6",
    SOURCE_LEDGER: "8c1ffd41999a4f76ddb35eb3c30f5fd93007d43046e1a0e222991d88cfc72e4f",
    SOURCE_SUMMARY: "33d8afdf3df77568be064fafefb499b3cc90c0f0e9bb6d8aef32ae8d3e6051c9",
    FORMAL: "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0",
    SELECTION: "3a6100109ef07741bbd289c44be68e0b0b034831305b0943378833f36045f2f8",
    EXCLUSIONS: "f42fde0fb2c7ed8053074ac0fdb2b5935756e5a9fc1c9a33d01653363224f980",
}


def _require_hashes() -> None:
    mismatches = {
        str(path): {"expected": digest, "observed": v25.sha256(path)}
        for path, digest in EXPECTED.items()
        if not path.is_file() or v25.sha256(path) != digest
    }
    if mismatches:
        raise RuntimeError(f"TPC-C v33 source SHA mismatch: {mismatches}")


def _diag02_audit(state: dict) -> dict:
    matches = [
        copy.deepcopy(item)
        for item in state.get("audits") or []
        if Path(str(item.get("root") or "")).resolve() == SOURCE_LEDGER.parent
    ]
    if len(matches) != 1:
        raise RuntimeError("TPC-C v33 requires exactly one diag02 audit")
    audit = matches[0]
    if (
        audit.get("clean") is not True
        or audit.get("mode") != "diagnostic"
        or audit.get("physical_calls") != 5
        or audit.get("valid_replays") != 5
        or audit.get("technical_failures") != 0
        or audit.get("baseline_restored") is not True
        or audit.get("trusted_count") != 0
        or audit.get("valid_rejected_probe_only_count") != 5
    ):
        raise RuntimeError("TPC-C v33 diag02 audit is not clean probe-only 5/5/0")
    return audit


def main() -> int:
    _require_hashes()
    if MANIFEST.exists() or STATE.exists() or (PREPARATION / "sealed_run").exists():
        raise RuntimeError("TPC-C v33 continuation package already exists")
    formal = v25.load_json(FORMAL)
    if (
        formal.get("accepted_count") != 0
        or formal.get("inherited_accepted") != 0
        or formal.get("formal_physical_call_count") != 0
        or formal.get("formal_valid_replay_count") != 0
        or formal.get("formal_technical_failure_count") != 0
        or formal.get("gradient_update_count") != 0
        or formal.get("gradient_state_reset") is not True
    ):
        raise RuntimeError("TPC-C v33 formal fresh0 boundary mismatch")
    source_state = v25.load_json(SOURCE_STATE)
    audit = _diag02_audit(source_state)
    source_contract = builder_v33.validate_clean_probe_source(
        v25.load_json(SOURCE_SELECTION),
        v25.load_json(SOURCE_LEDGER),
    )
    generated = v25.load_json(SELECTION)
    generation_gate = builder_v33.validate_generated_top5(
        generated,
        source_contract=source_contract,
        globally_used_ids=v25.load_json(EXCLUSIONS)["fingerprint_ids"],
    )
    if generation_gate.get("passed") is not True:
        raise RuntimeError("TPC-C v33 generated selection gate failed")

    manifest = v25.load_json(SOURCE_MANIFEST)
    manifest.update(
        {
            "controller_version": 33,
            "controller_root": str(CONTROL),
            "controller_state_path": str(STATE),
            "batch_parent": str(BATCH_PARENT),
            "formal_state_path": str(FORMAL),
            "initial_formal_state_sha256": v25.sha256(FORMAL),
            "formal_accepted_start": 0,
            "next_ordinal": 2,
            "v33_activation_authorized": True,
            "v33_algorithm_frozen": True,
            "v33_diag02_training_only": True,
            "v33_source_selection_sha256": v25.sha256(SOURCE_SELECTION),
            "v33_source_ledger_sha256": v25.sha256(SOURCE_LEDGER),
            "v33_selection_sha256": v25.sha256(SELECTION),
            "v33_no_retroactive_trust": True,
            "v33_bounded_no_solution": {
                "maximum_generation_attempts": 1,
                "maximum_builder_wall_seconds": 1800,
                "automatic_unbounded_retry": False,
            },
        }
    )
    extra_pins = [
        PROJECT / "tools/build_tpcc_v33_diag02_trusted3_probe2.py",
        PROJECT / "tools/tpcc_autonomous_target10_controller_v33_diag02.py",
        PROJECT / "tools/prepare_tpcc_v33_diag02_continuation.py",
        PROJECT / "tests/test_tpcc_v33_diag02_continuation.py",
        SOURCE_SELECTION,
        SOURCE_LEDGER,
        SOURCE_SUMMARY,
        EXCLUSIONS,
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
        {"passed": False, "source": "v33_diag02_training_only"},
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
        "fresh0_contract": "tpcc_v33_diag02_training_only_fresh0_v1",
        "historical_accepted_excluded": 2,
        "audits": [audit],
        "sources": [
            {
                "name": "v33_diag02_training_only",
                "selection_path": str(SOURCE_SELECTION),
                "selection_sha256": v25.sha256(SOURCE_SELECTION),
                "ledger_path": str(SOURCE_LEDGER),
                "ledger_sha256": v25.sha256(SOURCE_LEDGER),
                "accepted_count_eligible": False,
                "training_only": True,
                "source_probe_rows_retroactively_trusted": False,
            }
        ],
        "current_run": current,
        "next_ordinal": 2,
        "formal_state_sha256": v25.sha256(FORMAL),
        "quarantined_fingerprints": v25.load_json(EXCLUSIONS)[
            "fingerprint_ids"
        ],
        "recovery_audits": [],
        "technical_failure_audits": [],
        "acquisition_transitions": [],
    }
    manifest["current_run"] = copy.deepcopy(current)
    v25.atomic_json(STATE, state)
    manifest["initial_controller_state_sha256"] = v25.sha256(STATE)
    v25.atomic_json(MANIFEST, manifest)
    report = {
        "contract": "tpcc_v33_diag02_zero_call_preparation_v1",
        "passed": True,
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
        "formal_accepted_count": 0,
        "source_role": "probe_only_training_evidence",
        "source_probe_rows_retroactively_trusted": False,
        "selection_roles": builder_v33.EXPECTED_ROLES,
        "selection_sha256": v25.sha256(SELECTION),
        "sealed_selection_sha256": seal["selection_sha256"],
        "controller_state_sha256": v25.sha256(STATE),
        "manifest_sha256": v25.sha256(MANIFEST),
        "next_action": "preflight_then_explicit_tmux_launch",
    }
    v25.atomic_json(TARGET / "v33_zero_call_preparation_report.json", report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
