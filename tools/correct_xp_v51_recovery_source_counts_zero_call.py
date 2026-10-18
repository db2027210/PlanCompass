#!/usr/bin/env python3
"""Correct only the descriptive source-count fields in XP v51 recovery state."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import shutil


ROOT = Path("/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v38_20260803")
STATE = ROOT / "state.json"
SOURCE_SHA = "75d366f2b1b4c0c7350dbd38bc22f001aeb6ce5d3e63a81fb8f4ea3908eee102"
FORMAL_KEYS = (
    "accepted_count", "accepted_fingerprints", "incumbent", "incumbent_score",
    "incumbent_params", "parameter_ranges", "range_state", "gradient_state",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if sha(STATE) != SOURCE_SHA:
        raise RuntimeError("XP v51 recovery-count correction source SHA mismatch")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    before = {key: copy.deepcopy(state.get(key)) for key in FORMAL_KEYS}
    audit = state.get("v51_zero_call_recovery") or {}
    if not (
        audit.get("diagnostic_replays_reused") == 5
        and audit.get("physical_workload_calls_added") == 0
        and state.get("status") == "automatic_reexploration"
    ):
        raise RuntimeError("XP v51 recovery-count correction boundary mismatch")
    audit.pop("diagnostic_replays_reused")
    audit["source_diagnostic_batches"] = 2
    audit["source_diagnostic_physical_calls"] = 10
    state["v51_zero_call_recovery"] = audit
    after = {key: copy.deepcopy(state.get(key)) for key in FORMAL_KEYS}
    if before != after:
        raise RuntimeError("XP v51 recovery-count correction changed formal state")
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup = ROOT / f"state.before_v51_source_count_correction.{stamp}.json"
    shutil.copy2(STATE, backup)
    if sha(backup) != SOURCE_SHA:
        raise RuntimeError("XP v51 recovery-count correction backup mismatch")
    temporary = STATE.with_name(f".state.json.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, STATE)
    print(json.dumps({"backup": str(backup), "backup_sha256": sha(backup), "state_sha256": sha(STATE), "physical_workload_calls_added": 0}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
