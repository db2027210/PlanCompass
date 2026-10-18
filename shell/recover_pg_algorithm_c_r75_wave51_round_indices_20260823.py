#!/usr/bin/env python3
"""Recover zero-physical round-index drift after the Wave 51 tmux env fault."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict


ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260823_algorithm_c_training_boundary_clean_r75"
)
EVIDENCE = (
    ROOT
    / "cohort_support/technical_failure_evidence/"
    "20260823T1903_tmux_resume_environment_not_forwarded_final"
)
WAVE51_EVIDENCE = (
    ROOT
    / "cohort_support/technical_failure_evidence/"
    "20260823T1851_wave51_host_ssd_skew_bug"
)
EXPECTED = {
    "tpch_memory": {"round": 7, "accepted": 3, "valid": 83, "physical": 85},
    "xp_cpu": {"round": 10, "accepted": 6, "valid": 63, "physical": 63},
    "xp_io": {"round": 9, "accepted": 3, "valid": 79, "physical": 83},
}
ARCHIVED_STATE = {
    "tpch_memory": WAVE51_EVIDENCE / "tpch_state_after_wave51.json",
    "xp_io": WAVE51_EVIDENCE / "xp_io_state_after_wave51.json",
}
SEMANTIC_KEYS = (
    "accepted",
    "accepted_candidate_archive",
    "accepted_improvements",
    "incumbent_params",
    "incumbent_validation",
    "ranges",
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def semantic_digest(state: Dict[str, Any]) -> str:
    return canonical_sha256({key: state.get(key) for key in SEMANTIC_KEYS})


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    receipt: Dict[str, Any] = {
        "contract": "algorithm_c_zero_physical_round_index_recovery_v1",
        "time": time.time(),
        "cause": "tmux_server_did_not_forward_legacy_resume_environment",
        "physical_workload_invocations_added_by_failed_resume": 0,
        "accepted_unchanged": True,
        "incumbent_unchanged": True,
        "ranges_unchanged": True,
        "gradient_state_unchanged": True,
        "scenes": {},
    }
    for scene, expected in EXPECTED.items():
        scene_dir = ROOT / scene
        state_path = scene_dir / "algorithm_c_state.json"
        history_path = scene_dir / "algorithm_c.jsonl"
        before = read_json(state_path)
        before_path = EVIDENCE / f"{scene}_state_before_recovery.json"
        atomic_write_json(before_path, before)

        if int(before.get("accepted") or 0) != expected["accepted"]:
            raise RuntimeError(f"{scene}: accepted changed before recovery")
        if int(before.get("valid_replays") or 0) != expected["valid"]:
            raise RuntimeError(f"{scene}: valid replay count changed before recovery")
        if int(before.get("physical_workload_invocations") or 0) != expected["physical"]:
            raise RuntimeError(f"{scene}: physical count changed before recovery")

        if scene in ARCHIVED_STATE:
            after = read_json(ARCHIVED_STATE[scene])
            recovery_source = str(ARCHIVED_STATE[scene])
        else:
            after = copy.deepcopy(before)
            after["round_index"] = expected["round"]
            after["phase"] = "stopped_by_signal_clean"
            after.pop("signal_stop_interrupted_phase", None)
            after.pop("signal_stop_interrupted_round_index", None)
            recovery_source = "current_state_with_only_round_and_phase_metadata_repaired"

        if int(after.get("round_index") or 0) != expected["round"]:
            raise RuntimeError(f"{scene}: recovery source has wrong round")
        if str(after.get("phase") or "") != "stopped_by_signal_clean":
            raise RuntimeError(f"{scene}: recovery source is not stopped clean")
        if (after.get("signal_stop_clean_receipt") or {}).get("passed") is not True:
            raise RuntimeError(f"{scene}: recovery source lacks clean baseline receipt")
        if int(after.get("accepted") or 0) != expected["accepted"]:
            raise RuntimeError(f"{scene}: recovery source changed accepted")
        if int(after.get("valid_replays") or 0) != expected["valid"]:
            raise RuntimeError(f"{scene}: recovery source changed valid replay count")
        if int(after.get("physical_workload_invocations") or 0) != expected["physical"]:
            raise RuntimeError(f"{scene}: recovery source changed physical count")
        if semantic_digest(before) != semantic_digest(after):
            raise RuntimeError(f"{scene}: semantic optimizer state differs")

        atomic_write_json(state_path, after)
        event = {
            "event": "algorithm_c_zero_physical_round_index_recovered",
            "time": time.time(),
            "scene": scene,
            "from_round_index": int(before.get("round_index") or 0),
            "to_round_index": expected["round"],
            "from_phase": before.get("phase"),
            "to_phase": after.get("phase"),
            "recovery_source": recovery_source,
            "physical_workload_invocations_added_by_failed_resume": 0,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
            "gradient_state_unchanged": True,
            "semantic_state_sha256": semantic_digest(after),
        }
        append_jsonl(history_path, event)
        receipt["scenes"][scene] = {
            **event,
            "before_state_sha256": file_sha256(before_path),
            "recovered_state_sha256": file_sha256(state_path),
        }

    atomic_write_json(EVIDENCE / "recovery_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
