#!/usr/bin/env python3
"""Prepare XP v36 accepted0 from sealed v35 R0 and zero-call cont001 evidence."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

try:
    import resume_xp_fresh0_fixed_contract_target10_v36 as v36
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v36 as v36


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine"
)
SOURCE = PROJECT / "history/xp_final_algorithm_fresh10_v35_20260803T1220"
TARGET = PROJECT / "history/xp_final_algorithm_fresh10_v36_20260803"
SOURCE_SHA256 = {
    "state.json": "006a1180495c5f560ad0cc00e7d012a16346d685cdecaa68f577d689cf98f414",
    "entry_gate.json": "b6a2c1b7307499827bd764f7de82159404659a44d546f770212bcf657c7857d3",
    "batches/cont001/diagnostic_bundle/manifest.json": (
        "8309e4f731cddc5d18e198037d539582efe6eb1aa7c6cc68df54acaeb05c143b"
    ),
    "batches/cont001/diagnostic_bundle/selection.json": (
        "023b112e31d9e2ddfaf8c6aea1b9451b942ee6fed0b2d309e4e6956e3f19657c"
    ),
    "batches/cont001/explain_only/explain_only_generation.json": (
        "38dc42cb56b5e56f7daf7d458697f5e7041a218d20086e69f8ff1f39f4703367"
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def validate_source() -> tuple[dict[str, Any], dict[str, Any]]:
    for relative, expected in SOURCE_SHA256.items():
        path = SOURCE / relative
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"XP v36 source SHA mismatch: {path}")
    state = load(SOURCE / "state.json")
    manifest = load(SOURCE / "batches/cont001/diagnostic_bundle/manifest.json")
    selection = load(SOURCE / "batches/cont001/diagnostic_bundle/selection.json")
    cont = SOURCE / "batches/cont001"
    if (
        state.get("status") != "blocked_contract"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 1
        or int((state.get("gradient_state") or {}).get("update_count", -1)) != 0
        or (state.get("last_batch") or {}).get("batch_id") != "r0fresh000"
        or (state.get("fresh_runtime_baseline_revalidation") or {}).get("status")
        != "complete"
        or manifest.get("status") != "blocked_missing_input"
        or int((manifest.get("counts") or {}).get("selected_top5", -1)) != 0
        or len(selection.get("top5") or []) != 0
        or (cont / "selection.json").exists()
        or (cont / "replay_summary.json").exists()
        or (cont / "replay").exists()
    ):
        raise RuntimeError("XP v35 zero-call cont001 handoff contract mismatch")
    if [str(value) for value in manifest.get("input_gaps") or []] != [
        "transition backtracking requires at least 4 safe novel non-corner "
        "fingerprints after plan-level quarantine; got 1",
        "selection requires exactly 5 replay points; got 0",
    ]:
        raise RuntimeError("XP v35 cont001 failure is not the bounded transition shortfall")
    return state, manifest


def main() -> int:
    v36.verify_sources(BUNDLE)
    source_state, source_manifest = validate_source()
    source_hashes_before = {
        relative: sha256(SOURCE / relative) for relative in SOURCE_SHA256
    }
    if TARGET.exists():
        raise RuntimeError(f"XP v36 target already exists: {TARGET}")
    (TARGET / "batches").mkdir(parents=True)
    shutil.copytree(SOURCE / "batches/seed000", TARGET / "batches/seed000")
    shutil.copytree(SOURCE / "batches/r0fresh000", TARGET / "batches/r0fresh000")
    shutil.copy2(SOURCE / "entry_gate.json", TARGET / "entry_gate.json")

    if str(BUNDLE) not in sys.path:
        sys.path.insert(0, str(BUNDLE))
    bundle_module = importlib.import_module("xp_round10_diagnostic_bundle")
    continuation = importlib.import_module("xp_unattended_continuation")
    source = source_manifest["source"]
    risk_audit = copy.deepcopy(
        source_manifest["diagnostic_discovery"]["safe_frontier_contract"][
            "stability_risk_audit"
        ]
    )
    output_dir = TARGET / "batches/cont001/diagnostic_bundle"
    blocked = bundle_module.build_bundle(
        selection_path=Path(source["selection"]),
        replay_path=Path(source["replay"]),
        candidate_path=SOURCE / "batches/cont001/explain_only/explain_only_generation.json",
        accepted_incumbent_path=SOURCE / "batches/cont001/accepted_incumbent.json",
        output_dir=output_dir,
        extra_excluded_ids={
            str(value) for value in source_state.get("replayed_fingerprints") or []
        },
        risk_utilization_upper={},
        risk_audit=risk_audit,
    )
    ready = v36.apply_resource_safe_probe5_fallback(
        bundle_module,
        blocked,
        selection_path=Path(source["selection"]),
        replay_path=Path(source["replay"]),
        candidate_path=SOURCE / "batches/cont001/explain_only/explain_only_generation.json",
        accepted_incumbent_path=SOURCE / "batches/cont001/accepted_incumbent.json",
        output_dir=output_dir,
        extra_excluded_ids={
            str(value) for value in source_state.get("replayed_fingerprints") or []
        },
        risk_utilization_upper={},
        risk_audit=risk_audit,
    )
    if ready.get("status") != "ready":
        raise RuntimeError("XP v36 fallback did not produce a ready diagnostic Top-5")
    selection = load(output_dir / "selection.json")
    selection.update(
        {
            "score_regime": "xp_v33_fresh0_band_target_v1",
            "score_admission_eligible": True,
            "historical_calibration_score_admission_eligible": False,
            "historical_scores_used_for_fresh_admission": False,
            "batch_mode": "diagnostic_validation",
            "source_batch_id": "cont001",
            "calibration_source": {
                "selection_path": str(source["selection"]),
                "replay_path": str(source["replay"]),
                "contract": "sealed_v35_r0_plus_zero_call_cont001_audit_v1",
            },
            "affinity": {
                "cpuset_cpus": "32-51,288-307",
                "excluded_cpus": "184-199,440-455",
            },
        }
    )
    admission = dict(selection.get("formal_admission") or {})
    admission.update(
        {
            "passed": False,
            "score_regime_gate_passed": False,
            "reason": "v35_r0_resource_plan_calibration_only",
            "historical_scores_used_for_fresh_admission": False,
        }
    )
    selection["formal_admission"] = admission
    status, normalized = continuation.normalize_selection(
        selection,
        replayed_fingerprints=source_state["replayed_fingerprints"],
    )
    if status != "ready" or normalized is None:
        raise RuntimeError("XP v36 prepared selection fails continuation normalization")
    v36.atomic_json(TARGET / "batches/cont001/selection.json", selection)

    state = copy.deepcopy(source_state)
    state["status"] = "initialized_fresh0_runtime_anchor_ready"
    state.pop("last_error", None)
    state["next_batch_index"] = 1
    state["last_batch"] = copy.deepcopy(state["batch_history"][-1])
    state["fresh_runtime_baseline_revalidation"]["candidate"] = str(
        TARGET / "batches/r0fresh000/selection.json"
    )
    state["fresh_runtime_baseline_revalidation"]["summary"] = str(
        TARGET / "batches/r0fresh000/replay/summary.json"
    )
    state["v36_zero_call_handoff"] = {
        "contract": "xp_v36_v35_r0_zero_call_cont001_handoff_v1",
        "source_campaign": str(SOURCE),
        "source_state_sha256": SOURCE_SHA256["state.json"],
        "source_entry_gate_sha256": SOURCE_SHA256["entry_gate.json"],
        "source_cont001_manifest_sha256": SOURCE_SHA256[
            "batches/cont001/diagnostic_bundle/manifest.json"
        ],
        "source_cont001_selection_sha256": SOURCE_SHA256[
            "batches/cont001/diagnostic_bundle/selection.json"
        ],
        "source_candidate_pool_sha256": SOURCE_SHA256[
            "batches/cont001/explain_only/explain_only_generation.json"
        ],
        "source_cont001_physical_calls": 0,
        "r0_physical_call_inherited_for_calibration_only": 1,
        "r0_reexecuted": False,
        "accepted_inherited": 0,
        "gradient_updates_inherited": 0,
        "prepared_selection_sha256": sha256(TARGET / "batches/cont001/selection.json"),
        "workload_calls_added": 0,
        "formal_state_mutations": 0,
    }
    v36.atomic_json(TARGET / "state.json", state)
    if sha256(TARGET / "entry_gate.json") != state["entry_gate_sha256"]:
        raise RuntimeError("XP v36 copied entry gate/state SHA mismatch")
    if (
        int(state["accepted_count"]) != 0
        or int(state["accepted_target"]) != 10
        or int(state["gradient_state"]["update_count"]) != 0
        or source_hashes_before
        != {relative: sha256(SOURCE / relative) for relative in SOURCE_SHA256}
    ):
        raise RuntimeError("XP v36 zero-call preparation postcondition failed")
    output = {
        "status": "ready_for_diagnostic_probe5_replay",
        "campaign": str(TARGET),
        "accepted_count": 0,
        "accepted_target": 10,
        "prepared_selection_sha256": sha256(TARGET / "batches/cont001/selection.json"),
        "fallback_budget_sha256": sha256(
            TARGET / "v36_safe_probe_fallback_budget.json"
        ),
        "workload_calls_added": 0,
        "formal_state_mutations": 0,
        "source_v35_modified": False,
        "mysql_touched": False,
    }
    print(json.dumps(output, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
