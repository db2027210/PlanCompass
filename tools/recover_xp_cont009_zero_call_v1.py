#!/usr/bin/env python3
"""Zero-call recovery of sealed XP cont009 after range-admission fail-close."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp.%d" % os.getpid())
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("expected JSON object: %s" % path)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--expected-raw-replay-sha256", required=True)
    parser.add_argument("--expected-accepted-delta", type=int, default=1)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    bundle = args.bundle_root.resolve()
    campaign = args.campaign_root.resolve()
    state_path = campaign / "state.json"
    batch_dir = campaign / "batches" / "cont009"
    selection_path = batch_dir / "selection.json"
    raw_replay_path = batch_dir / "serial_replay" / "replay_summary.json"
    recovered_replay_path = batch_dir / "replay_summary.json"
    final_restore_path = batch_dir / "restore_finally.log"
    transaction_path = batch_dir / "zero_call_recovery_transaction_v1.json"
    lock_path = batch_dir / ".zero_call_recovery_v1.lock"

    expected = {
        state_path: args.expected_state_sha256,
        selection_path: args.expected_selection_sha256,
        raw_replay_path: args.expected_raw_replay_sha256,
    }
    for path, digest in expected.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("missing sealed input: %s" % path)
        if sha256(path) != digest:
            raise RuntimeError("sealed input SHA mismatch: %s" % path)
    if not final_restore_path.is_file() or final_restore_path.is_symlink():
        raise RuntimeError("missing final baseline restore evidence")
    if recovered_replay_path.exists() or transaction_path.exists():
        raise RuntimeError("recovery output already exists")

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
    if (
        state.get("status") != "blocked_contract"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 9
        or (state.get("last_batch") or {}).get("batch_id") != "cont008"
        or state.get("last_error")
        != "accepted candidate fell outside updated range: random_page_cost"
    ):
        raise RuntimeError("XP state does not exactly match the fail-closed boundary")

    selection = load(selection_path)
    raw_replay = load(raw_replay_path)
    if selection.get("batch_mode") != "formal":
        raise RuntimeError("cont009 is not a sealed formal selection")
    if (
        int(raw_replay.get("physical_workload_invocation_count", -1)) != 5
        or int(raw_replay.get("successful_real_replay_count", -1)) != 5
        or int(raw_replay.get("technical_failure_count", -1)) != 0
        or raw_replay.get("baseline_restore_verified") is not True
        or raw_replay.get("diagnostic_only") is not True
        or raw_replay.get("state_mutations_applied_by_this_bundle") is not False
    ):
        raise RuntimeError("raw cont009 evidence is not clean5 and state-neutral")

    selection_status, normalized = normalize_selection(
        selection,
        replayed_fingerprints=state["replayed_fingerprints"],
    )
    if selection_status != "ready" or normalized is None:
        raise RuntimeError("sealed cont009 selection is no longer admissible")
    environment = {
        "XP_INCUMBENT_SCORE": str(state["incumbent_score"]),
        "XP_INCUMBENT_PARAMS_JSON": json.dumps(
            state["incumbent_params"], sort_keys=True
        ),
        "XP_PARAMETER_RANGES_JSON": json.dumps(
            state["parameter_ranges"], sort_keys=True
        ),
        "XP_ACCEPTED_REMAINING": str(
            int(state["accepted_target"]) - int(state["accepted_count"])
        ),
    }
    recovered = _promote_continuation_acceptance(
        copy.deepcopy(raw_replay), normalized, environment
    )
    recovered["contract"] = CONTRACT
    recovered["batch_mode"] = "formal"
    recovered["diagnostic_only"] = False
    recovered["rank1_imported_without_rerun"] = False
    recovered["state_mutations_applied_by_this_bundle"] = (
        int(recovered["accepted_count_delta"]) > 0
    )
    recovered["zero_call_recovery"] = {
        "contract": "xp_cont009_sealed_zero_call_range_admission_recovery_v1",
        "physical_workload_calls_added": 0,
        "raw_replay_path": str(raw_replay_path),
        "raw_replay_sha256": sha256(raw_replay_path),
        "selection_sha256": sha256(selection_path),
    }
    checked = validate_batch_summary(recovered, normalized)
    if (
        checked["physical_calls"] != 5
        or len(checked["technical_failures"]) != 0
        or len(checked["accepted"]) != args.expected_accepted_delta
        or int(recovered["accepted_count_delta"])
        != args.expected_accepted_delta
    ):
        raise RuntimeError("recovered cont009 acceptance result is unexpected")

    next_state = copy.deepcopy(state)
    next_state["accepted_count"] = int(next_state["accepted_count"]) + len(
        checked["accepted"]
    )
    next_state["replayed_fingerprints"] = sorted(
        set(next_state["replayed_fingerprints"]) | set(checked["fingerprints"])
    )
    next_state["accepted_fingerprints"] = sorted(
        set(next_state["accepted_fingerprints"])
        | {str(item["fingerprint_id"]) for item in checked["accepted"]}
    )
    _apply_accepted_patches(next_state, checked["accepted"])
    next_state["next_batch_index"] = 10
    next_state["status"] = "running"
    next_state.pop("last_error", None)
    next_state["last_batch"] = {
        "batch_id": "cont009",
        "mode": "formal",
        "status": "complete",
        "physical_calls": checked["physical_calls"],
        "accepted": len(checked["accepted"]),
        "technical_failures": len(checked["technical_failures"]),
        "rejected": len(checked["rejected"]),
        "fingerprint_digest": hashlib.sha256(
            json.dumps(
                sorted(checked["fingerprints"]), separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        "zero_call_recovered": True,
    }
    next_state["batch_history"].append(copy.deepcopy(next_state["last_batch"]))

    preview = {
        "status": "ready_to_commit" if args.commit else "dry_run_ready",
        "accepted_delta": len(checked["accepted"]),
        "accepted_after": next_state["accepted_count"],
        "physical_workload_calls_added": 0,
        "rejected": len(checked["rejected"]),
        "technical_failures": len(checked["technical_failures"]),
        "state_sha256_before": sha256(state_path),
        "selection_sha256": sha256(selection_path),
        "raw_replay_sha256": sha256(raw_replay_path),
        "final_restore_sha256": sha256(final_restore_path),
    }
    if not args.commit:
        print(json.dumps(preview, indent=2, sort_keys=True), flush=True)
        return 0

    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(lock_fd)
        if sha256(state_path) != args.expected_state_sha256:
            raise RuntimeError("state changed immediately before commit")
        transaction = dict(preview)
        transaction["status"] = "prepared"
        atomic_write(transaction_path, transaction)
        atomic_write(recovered_replay_path, recovered)
        store.commit(next_state)
        transaction.update(
            {
                "status": "committed",
                "recovered_replay_sha256": sha256(recovered_replay_path),
                "state_sha256_after": sha256(state_path),
            }
        )
        atomic_write(transaction_path, transaction)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
    print(json.dumps(transaction, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
