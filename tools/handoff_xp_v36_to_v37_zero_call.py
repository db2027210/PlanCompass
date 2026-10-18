#!/usr/bin/env python3
"""Zero-call XP v36 diagnostic -> v37 fresh-formal0 handoff."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
SOURCE = PROJECT / "history/xp_final_algorithm_fresh10_v36_20260803"
TARGET = PROJECT / "history/xp_final_algorithm_fresh10_v37_20260803"
SOURCE_FINAL_STATE_SHA256 = (
    "c0f4c52ca4ac7ffdab5ef462dc0cf5e865e29ba0dfa4fd96588e908b8742b6ac"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v37 expected JSON object: {path}")
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
        raise RuntimeError(f"XP v37 target already exists: {TARGET}")
    state_path = SOURCE / "state.json"
    selection_path = SOURCE / "batches/cont001/selection.json"
    summary_path = SOURCE / "batches/cont001/replay_summary.json"
    entry_path = SOURCE / "entry_gate.json"
    for path in (
        state_path,
        selection_path,
        summary_path,
        entry_path,
    ):
        if not path.is_file():
            raise RuntimeError(f"XP v37 handoff source missing: {path}")
    state = load(state_path)
    if sha256(state_path) != SOURCE_FINAL_STATE_SHA256:
        raise RuntimeError("XP v36 final state SHA mismatch")
    selection = load(selection_path)
    summary = load(summary_path)
    roles = [str(item.get("selection_role") or "") for item in selection.get("top5") or []]
    successful = list(summary.get("successful") or [])
    if (
        state.get("status") != "blocked_contract"
        or state.get("last_error") != "RuntimeError: XP v36 fallback budget contract mismatch"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 2
        or (state.get("last_batch") or {}).get("batch_id") != "cont001"
        or int((state.get("gradient_state") or {}).get("update_count", -1)) != 0
        or roles != ["maximin_uncertain_probe"] * 5
        or int(summary.get("physical_workload_invocation_count", -1)) != 5
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_restore_verified") is not True
        or int(summary.get("accepted_count_delta", -1)) != 0
        or len(successful) != 5
        or any(
            item.get("accepted") is not False
            or item.get("state_patch") not in ({}, None)
            or item.get("incumbent_update_applied") is not False
            or item.get("range_update_applied") is not False
            or item.get("gradient_update_applied") is not False
            for item in successful
        )
    ):
        raise RuntimeError("XP v36 clean probe5/state-neutral handoff gate failed")
    if (SOURCE / "batches/cont002/serial_replay/rank_01/workload.log").exists():
        raise RuntimeError("XP v36 cont002 physical workload already exists")

    source_hashes = {
        "state": sha256(state_path),
        "entry_gate": sha256(entry_path),
        "cont001_selection": sha256(selection_path),
        "cont001_replay": sha256(summary_path),
    }
    (TARGET / "batches").mkdir(parents=True)
    for name in ("seed000", "r0fresh000", "cont001"):
        shutil.copytree(SOURCE / "batches" / name, TARGET / "batches" / name)
    shutil.copy2(entry_path, TARGET / "entry_gate.json")
    fallback = SOURCE / "v36_safe_probe_fallback_budget.json"
    if fallback.is_file():
        shutil.copy2(fallback, TARGET / fallback.name)
    zero_call_audit = SOURCE / "batches/cont002/exact_plan_repeat"
    if zero_call_audit.is_dir():
        shutil.copytree(
            zero_call_audit,
            TARGET / "source_v36_cont002_zero_call_repeat_rejection_audit",
        )

    target_state = copy.deepcopy(state)
    target_state["status"] = "initialized_fresh0_runtime_anchor_ready"
    target_state.pop("last_error", None)
    target_state["accepted_count"] = 0
    target_state["accepted_fingerprints"] = []
    target_state["next_batch_index"] = 2
    target_state["last_batch"] = copy.deepcopy(target_state["batch_history"][-1])
    target_state["v37_zero_call_diagnostic_handoff"] = {
        "contract": "xp_v37_v36_clean_probe5_fresh_formal0_handoff_v1",
        "source_campaign": str(SOURCE),
        "source_sha256": source_hashes,
        "source_final_state_sha256_expected": SOURCE_FINAL_STATE_SHA256,
        "source_zero_call_block_reason": state.get("last_error"),
        "source_batch_ids": ["cont001"],
        "source_physical_calls": 5,
        "source_valid_replays": 5,
        "source_technical_failures": 0,
        "accepted_inherited": 0,
        "accepted_formal_start": 0,
        "gradient_updates_inherited": 0,
        "candidate_injection": False,
        "candidate_generation_policy": "v37_automatic_exact_point_repeat_then_robust_formal",
        "v36_cont002_repeat_rejection_preserved_for_audit": zero_call_audit.is_dir(),
        "workload_calls_added": 0,
        "formal_state_mutations": 0,
    }
    atomic_json(TARGET / "state.json", target_state)
    copied_selection = TARGET / "batches/cont001/selection.json"
    copied_summary = TARGET / "batches/cont001/replay_summary.json"
    if (
        sha256(copied_selection) != source_hashes["cont001_selection"]
        or sha256(copied_summary) != source_hashes["cont001_replay"]
        or int(target_state["accepted_count"]) != 0
        or int(target_state["gradient_state"]["update_count"]) != 0
    ):
        raise RuntimeError("XP v37 zero-call handoff postcondition failed")
    print(
        json.dumps(
            {
                "status": "ready_for_automatic_exact_point_repeat",
                "campaign": str(TARGET),
                "accepted_count": 0,
                "accepted_target": 10,
                "next_batch_index": 2,
                "source_state_sha256": source_hashes["state"],
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
