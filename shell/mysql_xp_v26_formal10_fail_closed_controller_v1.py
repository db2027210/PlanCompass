#!/usr/bin/env python3
"""Fail-closed evidence ledger for ten accepted XP Algorithm C rounds.

The controller does not count capture, R0, debug, failed replay or rejected
attempts.  Each record operation revalidates the same live v26 adoption,
current rolling gate, fresh R0, complete 127/Top-5 debug round and a sealed
outer decision whose QPS measurement starts only after a real replay.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys


TARGET_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
TARGET_SHA = "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
MAX_ATTEMPTS = 100


class ControllerError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sealed(path: Path, label: str) -> dict:
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if path.is_symlink() or not path.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise ControllerError(label + " is not sealed")
    if sidecar.read_text(encoding="ascii") != "%s  %s\n" % (sha256(path), path.name):
        raise ControllerError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ControllerError(label + " must be an object")
    return value


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise ControllerError("output and sidecar must be fresh")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name("." + path.name + ".%d" % os.getpid())
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar.write_text(sha256(path) + "  " + path.name + "\n", encoding="ascii")


def load_target(bundle: Path):
    bundle = bundle.resolve(strict=True)
    if bundle.name != TARGET_ID or sha256(bundle / "bundle_manifest.json") != TARGET_SHA:
        raise ControllerError("target v26 identity differs")
    path = bundle / "launch/mysql_xp_node02_formal10_materializer_v1.py"
    spec = importlib.util.spec_from_file_location("xp_v26_formal10_target", path)
    if spec is None or spec.loader is None:
        raise ControllerError("target v26 materializer cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old
    module.verify_bundle(bundle)
    return module


def live_chain(target, adoption_path, gate_path, r0_path):
    unused, identity = target.verify_adoption(adoption_path, TARGET_ID)
    gate = target.load_sealed(gate_path, "ongoing dynamic gate")
    r0 = target.load_sealed(r0_path, "fresh R0")
    target._verify_gate(gate, TARGET_ID, identity)
    if (r0.get("schema_version") != "mysql_xp_fresh_r0_result_v3"
            or r0.get("status") != "R0_PASS"
            or r0.get("same_live_identity") != identity
            or r0.get("dynamic_gate_sha256") != sha256(gate_path)
            or r0.get("accepted_rounds_credit") != 0):
        raise ControllerError("fresh R0/live gate chain differs")
    return identity


def timestamp(value, label):
    if not isinstance(value, str) or not value:
        raise ControllerError(label + " timestamp differs")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ControllerError(label + " timestamp differs") from exc
    if parsed.tzinfo is None:
        raise ControllerError(label + " timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def initialize(bundle: Path, adoption: Path, gate: Path, r0: Path,
               formal10_handoff: Path, output: Path) -> dict:
    target = load_target(bundle)
    identity = live_chain(target, adoption, gate, r0)
    handoff = sealed(formal10_handoff, "formal10 handoff")
    if (handoff.get("schema_version") != "mysql_xp_node02_same_live_formal10_handoff_v1"
            or handoff.get("status") != "READY_FORMAL10_SAME_LIVE_DB_AND_CLIENT"
            or handoff.get("bundle_id") != TARGET_ID
            or handoff.get("same_live_identity") != identity
            or handoff.get("fresh_r0_sha256") != sha256(r0)
            or handoff.get("dynamic_gate_sha256") != sha256(gate)
            or handoff.get("formal10_authorized") is not True
            or handoff.get("historical_accepted_round_credit") != 0
            or handoff.get("target_accepted_rounds") != 10):
        raise ControllerError("formal10 handoff differs")
    result = {
        "schema_version": "mysql_xp_v26_formal10_fail_closed_checkpoint_v1",
        "status": "RUNNING_NOT_COMPLETE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": TARGET_ID,
        "same_live_identity": identity,
        "adopted_service_sha256": sha256(adoption),
        "fresh_r0_sha256": sha256(r0),
        "formal10_handoff_sha256": sha256(formal10_handoff),
        "target_accepted_rounds": 10,
        "attempted_rounds": 0,
        "accepted_rounds": 0,
        "accepted_round_ids": [],
        "events": [],
        "capture_r0_debug_or_failed_replay_counted": False,
        "accepted_round_authorized": False,
    }
    atomic_json(output, result)
    return result


def record(bundle: Path, adoption: Path, gate: Path, r0: Path,
           checkpoint_path: Path, debug_path: Path, decision_path: Path,
           output: Path) -> dict:
    target = load_target(bundle)
    identity = live_chain(target, adoption, gate, r0)
    checkpoint = sealed(checkpoint_path, "formal10 checkpoint")
    debug = sealed(debug_path, "sealed debug round")
    decision = sealed(decision_path, "outer acceptance decision")
    attempted = checkpoint.get("attempted_rounds")
    accepted = checkpoint.get("accepted_rounds")
    if (checkpoint.get("schema_version") != "mysql_xp_v26_formal10_fail_closed_checkpoint_v1"
            or checkpoint.get("status") != "RUNNING_NOT_COMPLETE"
            or checkpoint.get("bundle_id") != TARGET_ID
            or checkpoint.get("same_live_identity") != identity
            or checkpoint.get("fresh_r0_sha256") != sha256(r0)
            or not isinstance(attempted, int) or not isinstance(accepted, int)
            or attempted < accepted or accepted >= 10 or attempted >= MAX_ATTEMPTS):
        raise ControllerError("checkpoint is not recordable")
    target.verify_debug(debug, TARGET_ID, identity)
    journals = debug["top5_point_journals"]
    rank = decision.get("selected_rank")
    if not isinstance(rank, int) or rank not in range(1, 6):
        raise ControllerError("selected fixed Top-5 rank differs")
    selected = journals[rank - 1]
    replay_complete = timestamp(decision.get("real_replay_completed_at"), "replay completion")
    qps_started = timestamp(decision.get("qps_measurement_started_at"), "QPS start")
    qps_finished = timestamp(decision.get("qps_measurement_completed_at"), "QPS completion")
    qps = decision.get("qps")
    try:
        qps_value = float(qps)
    except (TypeError, ValueError) as exc:
        raise ControllerError("outer QPS differs") from exc
    duration = decision.get("measurement_seconds")
    stable_at = decision.get("stable_window_reached_at_seconds")
    round_id = decision.get("round_id")
    was_accepted = decision.get("accepted")
    if (decision.get("schema_version") != "mysql_xp_algorithm_c_outer_acceptance_v1"
            or decision.get("status") not in {"ACCEPTED", "REJECTED"}
            or decision.get("bundle_id") != TARGET_ID
            or decision.get("same_live_identity") != identity
            or decision.get("fresh_r0_sha256") != sha256(r0)
            or decision.get("dynamic_gate_sha256") != sha256(gate)
            or decision.get("debug_round_sha256") != sha256(debug_path)
            or decision.get("round_attempt") != attempted + 1
            or not isinstance(round_id, str) or not round_id
            or round_id in {row.get("round_id") for row in checkpoint.get("events", [])}
            or decision.get("selected_subset_id") != selected["subset_id"]
            or decision.get("selected_attempt_token") != selected["attempt_token"]
            or decision.get("real_replay") is not True
            or decision.get("failed_replay") is not False
            or decision.get("database_counter_timeout_count") != 0
            or decision.get("qps_used_for_gradient") is not False
            or decision.get("qps_role") != "outer_acceptance_after_real_replay_only"
            or qps_started < replay_complete or qps_finished < qps_started
            or not math.isfinite(qps_value) or qps_value < 0
            or not isinstance(duration, int) or duration < 180 or duration > 600
            or not isinstance(stable_at, int) or stable_at < 180 or stable_at > duration
            or (stable_at == 180 and duration != 180)
            or not isinstance(was_accepted, bool)
            or (was_accepted and decision.get("status") != "ACCEPTED")
            or (not was_accepted and decision.get("status") != "REJECTED")
            or decision.get("accepted_rounds_credit") != (1 if was_accepted else 0)):
        raise ControllerError("outer acceptance evidence differs")
    new_accepted = accepted + (1 if was_accepted else 0)
    events = list(checkpoint.get("events") or [])
    events.append({
        "round_attempt": attempted + 1,
        "round_id": round_id,
        "decision_sha256": sha256(decision_path),
        "debug_round_sha256": sha256(debug_path),
        "selected_rank": rank,
        "selected_subset_id": selected["subset_id"],
        "accepted": was_accepted,
        "accepted_round_ordinal": new_accepted if was_accepted else None,
        "qps": qps_value,
    })
    result = dict(checkpoint)
    result.update({
        "status": "COMPLETE_10_ACCEPTED_ROUNDS" if new_accepted == 10 else "RUNNING_NOT_COMPLETE",
        "latest_dynamic_gate_sha256": sha256(gate),
        "attempted_rounds": attempted + 1,
        "accepted_rounds": new_accepted,
        "accepted_round_ids": list(checkpoint.get("accepted_round_ids") or [])
                              + ([round_id] if was_accepted else []),
        "events": events,
        "accepted_round_authorized": new_accepted == 10,
        "last_checkpoint_sha256": sha256(checkpoint_path),
    })
    atomic_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("init", "record"), required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--dynamic-gate", type=Path, required=True)
    parser.add_argument("--fresh-r0", type=Path, required=True)
    parser.add_argument("--formal10-handoff", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--debug-round", type=Path)
    parser.add_argument("--outer-decision", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "init":
        if args.formal10_handoff is None:
            raise ControllerError("init requires formal10 handoff")
        result = initialize(args.bundle, args.adopted_service,
                            args.dynamic_gate, args.fresh_r0,
                            args.formal10_handoff, args.output)
    else:
        if None in (args.checkpoint, args.debug_round, args.outer_decision):
            raise ControllerError("record requires checkpoint/debug/decision")
        result = record(args.bundle, args.adopted_service, args.dynamic_gate,
                        args.fresh_r0, args.checkpoint, args.debug_round,
                        args.outer_decision, args.output)
    print(json.dumps({"status": result["status"],
                      "attempted_rounds": result["attempted_rounds"],
                      "accepted_rounds": result["accepted_rounds"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControllerError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
