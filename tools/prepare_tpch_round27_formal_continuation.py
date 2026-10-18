#!/usr/bin/env python3
"""Prepare the accepted=1 TPC-H round27 formal continuation inputs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


BASE = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
ROUND = BASE / "gradient_debug_round27_robust_empirical_regime_v6_20260729T1854"
LIVE_CONFIG = BASE / "config/live.json"
SELECTION = ROUND / "top5.json"
GATE_SUMMARY = ROUND / "top5_serial_real_replay_robust/summary.json"
RESUME_STATE = (
    BASE / "formal10_exact_plan_regime_round23_candidate_v2_20260729T1540/"
    "formal_state.json"
)
ACCEPTED_COUNTER = (
    BASE / "run/"
    "attempt48_resource_correction_22c1730m16r20w36w_rolling_v2."
    "accepted_shrink.counter.txt"
)
OUTPUT_CONFIG = ROUND / "formal_continuation_accepted1_config.json"
MANIFEST = ROUND / "formal_continuation_accepted1_manifest.json"
EXPECTED = {
    LIVE_CONFIG: "23da90cdb9123c76a0bbf2a39a8899b43de955ed4cdf63d7b4abb098e060b380",
    SELECTION: "8865741a7f2a7840b4a2feef97a355bee9e1fb7b4aab71c73e6558e8faa4e531",
    GATE_SUMMARY: "550a41475257dc7f5ec719e81ab3958c12d9f499d92d6dd859ef73ca7a475d9c",
    RESUME_STATE: "13829704af08c96e7c63b3b4d9515b747319af7a219af7e463ded5d1eb66fca5",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object: {path}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    if OUTPUT_CONFIG.exists() or MANIFEST.exists():
        raise FileExistsError("round27 formal continuation inputs already exist")
    for path, expected in EXPECTED.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")
    if int(ACCEPTED_COUNTER.read_text(encoding="utf-8")) != 1:
        raise RuntimeError("TPC-H accepted counter is not 1")
    state = load(RESUME_STATE)
    if (
        state.get("status") != "complete"
        or state.get("accepted_count") != 1
        or state.get("technical_failure_count") != 0
        or state.get("baseline_restored") is not True
    ):
        raise RuntimeError("TPC-H resume state is not clean")
    selection = load(SELECTION)
    roles = [item.get("selection_role") for item in selection.get("top5", [])]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise RuntimeError(f"round27 role mismatch: {roles}")
    if len({
        item["three_level_fingerprint"]["id"] for item in selection["top5"]
    }) != 5:
        raise RuntimeError("round27 fingerprints are not distinct")
    summary = load(GATE_SUMMARY)
    if (
        summary.get("successful_real_replay_count") != 5
        or summary.get("technical_failure_count") != 0
        or summary.get("baseline_params_restored") is not True
    ):
        raise RuntimeError("round27 gate summary is not clean 5/5")

    config = load(LIVE_CONFIG)
    formal = config["formal_outer_protocol"]
    formal["formal_search_accepted_count_start"] = 1
    atomic_json(OUTPUT_CONFIG, config)
    manifest = {
        "schema_version": "tpch_round27_formal_continuation_manifest_v1",
        "accepted_count_start": 1,
        "accepted_target": 10,
        "selection": str(SELECTION),
        "selection_sha256": EXPECTED[SELECTION],
        "gate_summary": str(GATE_SUMMARY),
        "gate_summary_sha256": EXPECTED[GATE_SUMMARY],
        "resume_state": str(RESUME_STATE),
        "resume_state_sha256": EXPECTED[RESUME_STATE],
        "output_config": str(OUTPUT_CONFIG),
        "output_config_sha256": sha256(OUTPUT_CONFIG),
        "maximum_physical_calls": 5,
        "qps_used_for_inner_scoring": False,
        "trusted_roles": 3,
        "probe_roles": 2,
    }
    atomic_json(MANIFEST, manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
