#!/usr/bin/env python3
"""Repair XP cont009's missing accepted ledger state_patch with zero calls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("expected JSON object: %s" % path)
    return value


def atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp.%d" % os.getpid())
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-recovered-replay-sha256", required=True)
    parser.add_argument("--expected-recovery-transaction-sha256", required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--expected-raw-replay-sha256", required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    bundle = args.bundle_root.resolve()
    campaign = args.campaign_root.resolve()
    state_path = campaign / "state.json"
    batch_dir = campaign / "batches" / "cont009"
    selection_path = batch_dir / "selection.json"
    raw_replay_path = batch_dir / "serial_replay" / "replay_summary.json"
    recovered_replay_path = batch_dir / "replay_summary.json"
    old_transaction_path = batch_dir / "zero_call_recovery_transaction_v1.json"
    repair_transaction_path = batch_dir / "state_patch_repair_transaction_v2.json"
    backup_state_path = batch_dir / "state_before_state_patch_repair_v2.json"
    backup_replay_path = batch_dir / "replay_summary_before_state_patch_repair_v2.json"
    backup_transaction_path = batch_dir / "recovery_transaction_before_state_patch_repair_v2.json"
    lock_path = batch_dir / ".state_patch_repair_v2.lock"

    expected = {
        state_path: args.expected_state_sha256,
        recovered_replay_path: args.expected_recovered_replay_sha256,
        old_transaction_path: args.expected_recovery_transaction_sha256,
        selection_path: args.expected_selection_sha256,
        raw_replay_path: args.expected_raw_replay_sha256,
    }
    for path, digest in expected.items():
        if not path.is_file() or path.is_symlink() or sha256(path) != digest:
            raise RuntimeError("sealed input missing or SHA mismatch: %s" % path)
    for path in (
        repair_transaction_path,
        backup_state_path,
        backup_replay_path,
        backup_transaction_path,
    ):
        if path.exists():
            raise RuntimeError("repair output already exists: %s" % path)

    sys.path.insert(0, str(bundle))
    from xp_unattended_adapters import _promote_continuation_acceptance  # type: ignore
    from xp_unattended_continuation import (  # type: ignore
        CONTRACT,
        AtomicStateStore,
        _apply_accepted_patches,
        normalize_selection,
        validate_batch_summary,
    )

    store = AtomicStateStore(state_path)
    state = store.load()
    old_recovered = load(recovered_replay_path)
    if (
        state.get("status") != "running"
        or int(state.get("accepted_count", -1)) != 1
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 10
        or (state.get("last_batch") or {}).get("batch_id") != "cont009"
        or (state.get("last_batch") or {}).get("zero_call_recovered") is not True
        or int(old_recovered.get("accepted_count_delta", -1)) != 1
    ):
        raise RuntimeError("state does not match the incomplete cont009 commit")
    old_accepted = [
        item
        for item in old_recovered.get("ledger") or []
        if item.get("accepted") is True
    ]
    if len(old_accepted) != 1 or old_accepted[0].get("state_patch") not in (None, {}):
        raise RuntimeError("missing-ledger-state_patch defect is not reproduced")

    selection = load(selection_path)
    raw_replay = load(raw_replay_path)
    selection_status, normalized = normalize_selection(
        selection,
        replayed_fingerprints=state["replayed_fingerprints"],
    )
    if selection_status != "ready" or normalized is None:
        raise RuntimeError("sealed cont009 selection is not admissible")
    environment = {
        "XP_INCUMBENT_SCORE": str(state["incumbent_score"]),
        "XP_INCUMBENT_PARAMS_JSON": json.dumps(
            state["incumbent_params"], sort_keys=True
        ),
        "XP_PARAMETER_RANGES_JSON": json.dumps(
            state["parameter_ranges"], sort_keys=True
        ),
        "XP_ACCEPTED_REMAINING": "10",
    }
    corrected_replay = _promote_continuation_acceptance(
        copy.deepcopy(raw_replay), normalized, environment
    )
    corrected_replay["contract"] = CONTRACT
    corrected_replay["batch_mode"] = "formal"
    corrected_replay["diagnostic_only"] = False
    corrected_replay["rank1_imported_without_rerun"] = False
    corrected_replay["state_mutations_applied_by_this_bundle"] = True
    corrected_replay["zero_call_recovery"] = {
        "contract": "xp_cont009_sealed_zero_call_range_admission_recovery_v2",
        "physical_workload_calls_added": 0,
        "raw_replay_path": str(raw_replay_path),
        "raw_replay_sha256": sha256(raw_replay_path),
        "selection_sha256": sha256(selection_path),
        "state_patch_repair": True,
    }
    checked = validate_batch_summary(corrected_replay, normalized)
    if (
        checked["physical_calls"] != 5
        or len(checked["technical_failures"]) != 0
        or len(checked["accepted"]) != 1
        or len(checked["rejected"]) != 2
    ):
        raise RuntimeError("corrected replay classification is unexpected")

    corrected_state = copy.deepcopy(state)
    _apply_accepted_patches(corrected_state, checked["accepted"])
    corrected_state["last_batch"]["state_patch_repaired"] = True
    if (
        int(corrected_state["accepted_count"]) != 1
        or corrected_state["incumbent_score"] == state["incumbent_score"]
        or corrected_state["incumbent_params"] == state["incumbent_params"]
        or corrected_state["parameter_ranges"] == state["parameter_ranges"]
    ):
        raise RuntimeError("corrected state patch did not update all formal fields")

    preview = {
        "status": "ready_to_commit" if args.commit else "dry_run_ready",
        "accepted_count_before": state["accepted_count"],
        "accepted_count_after": corrected_state["accepted_count"],
        "incumbent_score_before": state["incumbent_score"],
        "incumbent_score_after": corrected_state["incumbent_score"],
        "physical_workload_calls_added": 0,
        "state_sha256_before": sha256(state_path),
        "recovered_replay_sha256_before": sha256(recovered_replay_path),
    }
    if not args.commit:
        print(json.dumps(preview, indent=2, sort_keys=True), flush=True)
        return 0

    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(lock_fd)
        if sha256(state_path) != args.expected_state_sha256:
            raise RuntimeError("state changed immediately before repair")
        shutil.copy2(state_path, backup_state_path)
        shutil.copy2(recovered_replay_path, backup_replay_path)
        shutil.copy2(old_transaction_path, backup_transaction_path)
        transaction = dict(preview)
        transaction.update(
            {
                "status": "prepared",
                "backup_state_sha256": sha256(backup_state_path),
                "backup_replay_sha256": sha256(backup_replay_path),
                "backup_transaction_sha256": sha256(backup_transaction_path),
            }
        )
        atomic_write(repair_transaction_path, transaction)
        atomic_write(recovered_replay_path, corrected_replay)
        store.commit(corrected_state)
        transaction.update(
            {
                "status": "committed",
                "state_sha256_after": sha256(state_path),
                "recovered_replay_sha256_after": sha256(recovered_replay_path),
            }
        )
        atomic_write(repair_transaction_path, transaction)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
    print(json.dumps(transaction, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
