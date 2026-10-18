#!/usr/bin/env python3
"""Create XP v38 from v37's sealed cont002 gate failure, without workload."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
SOURCE = PROJECT / "history/xp_final_algorithm_fresh10_v37_20260803"
TARGET = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
SOURCE_STATE_SHA256 = (
    "20441df2c47af4c02eaa0b564ba4eba14152092049c8df70eb1ae497038aab7d"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v38 expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def main() -> int:
    if TARGET.exists():
        raise RuntimeError(f"XP v38 target already exists: {TARGET}")
    state_path = SOURCE / "state.json"
    entry_path = SOURCE / "entry_gate.json"
    selection_path = SOURCE / "batches/cont002/selection.json"
    summary_path = SOURCE / "batches/cont002/replay_summary.json"
    rejection_path = SOURCE / (
        "batches/cont003/exact_plan_repeat/automatic_reexploration.json"
    )
    builder_log = SOURCE / "batches/cont003/exact_plan_repeat/builder.log"
    for path in (
        state_path,
        entry_path,
        selection_path,
        summary_path,
        rejection_path,
        builder_log,
    ):
        if not path.is_file():
            raise RuntimeError(f"XP v38 handoff source missing: {path}")
    if sha256(state_path) != SOURCE_STATE_SHA256:
        raise RuntimeError("XP v37 sealed cont002 state SHA mismatch")
    state = load(state_path)
    summary = load(summary_path)
    rejection = load(rejection_path)
    if (
        state.get("status") != "blocked_contract"
        or state.get("last_error") != "RuntimeError: XP v36 fallback budget contract mismatch"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 3
        or (state.get("last_batch") or {}).get("batch_id") != "cont002"
        or int((state.get("gradient_state") or {}).get("update_count", -1)) != 0
        or int(summary.get("physical_workload_invocation_count", -1)) != 5
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_restore_verified") is not True
        or int(summary.get("accepted_count_delta", -1)) != 0
        or rejection.get("reason") != "insufficient_stable_separable_exact_regimes"
        or int(rejection.get("new_workload_calls", -1)) != 0
        or rejection.get("state_mutation") is not False
    ):
        raise RuntimeError("XP v37 cont002/cont003 zero-call handoff gate failed")
    if list((SOURCE / "batches/cont003").rglob("workload.log")):
        raise RuntimeError("XP v37 cont003 contains workload evidence")

    source_hashes = {
        "state": sha256(state_path),
        "entry_gate": sha256(entry_path),
        "cont002_selection": sha256(selection_path),
        "cont002_replay": sha256(summary_path),
        "cont003_repeat_rejection": sha256(rejection_path),
        "cont003_builder_log": sha256(builder_log),
    }
    (TARGET / "batches").mkdir(parents=True)
    for name in ("seed000", "r0fresh000", "cont001", "cont002"):
        shutil.copytree(SOURCE / "batches" / name, TARGET / "batches" / name)
    shutil.copytree(
        SOURCE / "batches/cont003/exact_plan_repeat",
        TARGET / "source_v37_cont003_zero_call_repeat_rejection_audit",
    )
    shutil.copy2(entry_path, TARGET / "entry_gate.json")

    target_state = copy.deepcopy(state)
    target_state["status"] = "offline_reexploration"
    target_state.pop("last_error", None)
    target_state["accepted_count"] = 0
    target_state["accepted_fingerprints"] = []
    target_state["next_batch_index"] = 3
    target_state["last_batch"] = copy.deepcopy(target_state["batch_history"][-1])
    target_state["active_phase"] = {
        "contract": "xp_v38_atomic_offline_reexploration_phase_state_v1",
        "stage": "offline_reexploration",
        "batch_id": "cont003",
        "reason": "insufficient_stable_separable_exact_regimes",
        "physical_workload_calls": 0,
        "manual_candidate_injection": False,
        "handoff_prepared": True,
    }
    target_state["v38_zero_call_handoff"] = {
        "contract": "xp_v38_v37_cont002_gate_failure_zero_call_handoff_v1",
        "source_campaign": str(SOURCE),
        "source_sha256": source_hashes,
        "accepted_formal_start": 0,
        "gradient_updates_inherited": 0,
        "source_cont002_physical_calls": 5,
        "source_cont002_valid_replays": 5,
        "source_cont002_technical_failures": 0,
        "source_cont003_workload_calls": 0,
        "candidate_injection": False,
        "candidate_generation_policy": (
            "existing_deterministic_explain_three_level_pipeline"
        ),
        "old_v36_fallback_budget_inherited": False,
        "workload_calls_added": 0,
        "formal_state_mutations": 0,
    }
    atomic_json(TARGET / "state.json", target_state)
    if (
        int(target_state["accepted_count"]) != 0
        or int(target_state["gradient_state"]["update_count"]) != 0
        or sha256(TARGET / "batches/cont002/selection.json")
        != source_hashes["cont002_selection"]
        or sha256(TARGET / "batches/cont002/replay_summary.json")
        != source_hashes["cont002_replay"]
        or (TARGET / "v36_safe_probe_fallback_budget.json").exists()
    ):
        raise RuntimeError("XP v38 zero-call handoff postcondition failed")
    print(
        json.dumps(
            {
                "status": "offline_reexploration",
                "campaign": str(TARGET),
                "accepted_count": 0,
                "accepted_target": 10,
                "next_batch_index": 3,
                "target_state_sha256": sha256(TARGET / "state.json"),
                "workload_calls_added": 0,
                "candidate_injection": False,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
