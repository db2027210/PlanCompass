#!/usr/bin/env python3
"""Recover the exact zero-call XP cont054 v28 block for a v29 retry."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


EXPECTED_STATE_SHA256 = (
    "d14e50a3a67051752e9857fb2c65a2d569f203059c346da67df2dfb3d57795cb"
)
EXPECTED_MANIFEST_SHA256 = (
    "32b76ac889a5faa7d4aa1fd254068690cdf1c12d5e503069d7b51c92407e139e"
)
EXPECTED_ERROR = (
    "OfflineGenerationBlocked: need at least 5 predicted-safe novel non-corner "
    "validated three-level fingerprints after deterministic global probe fallback; got 4"
)
EXPECTED_CONT054_FILES = {
    "accepted_incumbent.json": (
        "a1cf9060abaf6005faa56026a77e2b424b7a6acb309118a3690b6f6a3c4d5312"
    ),
    "stability_risk_envelope.json": (
        "1002fd22f1d9390bce65a234fda20b17d28220129af51f2588ca0e332d3f97ec"
    ),
    "technical_failure_reroute.json": (
        "d11602cfe52d4d0ac6d04bb7b928193892fd00520f4ea1a5e36b3db95a6df135"
    ),
}


def sha256(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"required file missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def json_bytes(value: Dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def process_state(pid: int) -> str:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return "absent"
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("State:"):
            return line.split()[1]
    raise RuntimeError(f"cannot determine process state for PID {pid}")


def verify_inactive_controller(pid: int) -> str:
    state = process_state(pid)
    if state not in {"Z", "X", "absent"}:
        raise RuntimeError(f"controller PID {pid} is still active: state={state}")
    children_path = Path(f"/proc/{pid}/task/{pid}/children")
    children = (
        children_path.read_text(encoding="utf-8").split()
        if children_path.exists()
        else []
    )
    if children:
        raise RuntimeError(f"controller PID {pid} still has children: {children}")
    cmdline = Path(f"/proc/{pid}/cmdline")
    if state != "absent" and cmdline.exists() and cmdline.read_bytes():
        raise RuntimeError(f"inactive controller PID {pid} has a nonempty cmdline")
    return state


def verify_state(state: Dict[str, Any]) -> None:
    last = state.get("last_batch") or {}
    if not (
        state.get("accepted_count") == 4
        and state.get("accepted_target") == 10
        and state.get("next_batch_index") == 54
        and state.get("status") == "blocked_contract"
        and state.get("last_error") == EXPECTED_ERROR
        and state.get("incumbent_score") == -49.201616429624124
        and last.get("batch_id") == "cont053"
        and last.get("mode") == "diagnostic_validation"
        and last.get("status") == "complete"
        and last.get("physical_calls") == 5
        and last.get("technical_failures") == 1
        and last.get("accepted") == 0
        and last.get("rejected") == 0
    ):
        raise RuntimeError("state does not exactly match the sealed cont054 block")


def verify_cont054(batch: Path) -> List[str]:
    if not batch.is_dir():
        raise RuntimeError(f"required zero-call cont054 directory missing: {batch}")
    files = sorted(
        str(path.relative_to(batch)) for path in batch.rglob("*") if path.is_file()
    )
    jsonl = [name for name in files if name.endswith(".jsonl")]
    top_level = [name for name in files if "/" not in name]
    if len(files) != 66 or len(jsonl) != 63:
        raise RuntimeError(
            f"cont054 inventory mismatch: total={len(files)}, subset_jsonl={len(jsonl)}"
        )
    if set(top_level) != set(EXPECTED_CONT054_FILES):
        raise RuntimeError(f"cont054 top-level inventory mismatch: {top_level}")
    if any(
        not name.startswith("explain_only/subset_logs/") for name in jsonl
    ):
        raise RuntimeError("cont054 contains an unexpected JSONL location")
    forbidden_parts = {
        "selection.json",
        "replay_summary.json",
        "serial_replay",
        "workload.log",
        "warmup.log",
        "measurement_summary.json",
        "result.json",
    }
    if any(any(part in forbidden_parts for part in Path(name).parts) for name in files):
        raise RuntimeError("cont054 crossed the zero-call EXPLAIN-only boundary")
    for name, expected in EXPECTED_CONT054_FILES.items():
        if sha256(batch / name) != expected:
            raise RuntimeError(f"cont054 artifact SHA mismatch: {name}")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--controller-pid", required=True, type=int)
    parser.add_argument("--audit-name")
    parser.add_argument("--quarantine-name")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    bundle = args.bundle_root.resolve()
    state_path = campaign / "state.json"
    batch = campaign / "batches" / "cont054"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_name = args.audit_name or f"v29_cont054_recovery_{timestamp}.json"
    quarantine_name = (
        args.quarantine_name or f"cont054.v28_blocked_quarantine_{timestamp}"
    )
    audit_path = campaign / audit_name
    quarantine = campaign / "batches" / quarantine_name

    if sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("pre-recovery state SHA mismatch")
    if sha256(bundle / "bundle_manifest.json") != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError("v29 bundle manifest SHA mismatch")
    state = load_json(state_path)
    verify_state(state)
    inventory = verify_cont054(batch)
    controller_state = verify_inactive_controller(args.controller_pid)
    if audit_path.exists() or quarantine.exists():
        raise RuntimeError("recovery output path already exists")

    recovered = copy.deepcopy(state)
    recovered["status"] = "running"
    del recovered["last_error"]
    expected_projection = copy.deepcopy(recovered)
    recovered_sha = hashlib.sha256(json_bytes(recovered)).hexdigest()
    result: Dict[str, Any] = {
        "contract": "xp_v29_blocked_cont054_zero_call_recovery_v1",
        "status": "dry_run_passed",
        "campaign_root": str(campaign),
        "controller_pid": args.controller_pid,
        "controller_state": controller_state,
        "bundle_manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "pre_state_sha256": EXPECTED_STATE_SHA256,
        "projected_post_state_sha256": recovered_sha,
        "state_fields_changed": ["status", "last_error_removed"],
        "accepted_count": 4,
        "accepted_target": 10,
        "next_batch_index": 54,
        "incumbent_score": -49.201616429624124,
        "cont054_file_count": len(inventory),
        "cont054_subset_jsonl_count": 63,
        "physical_workload_calls_added": 0,
        "accepted_incumbent_range_gradient_mutated": False,
    }

    if args.apply:
        backup = campaign / (
            f"state.pre_v29_recovery_{timestamp}.{EXPECTED_STATE_SHA256[:12]}.json"
        )
        if backup.exists():
            raise RuntimeError("timestamped state backup already exists")
        shutil.copy2(state_path, backup)
        if sha256(backup) != EXPECTED_STATE_SHA256:
            raise RuntimeError("state backup SHA mismatch")
        renamed = False
        try:
            batch.rename(quarantine)
            renamed = True
            atomic_json(state_path, recovered)
            observed = load_json(state_path)
            if observed != expected_projection or sha256(state_path) != recovered_sha:
                raise RuntimeError("post-recovery state verification failed")
            result.update(
                {
                    "status": "applied_and_verified",
                    "backup": str(backup),
                    "backup_sha256": sha256(backup),
                    "quarantine": str(quarantine),
                    "post_state_sha256": sha256(state_path),
                }
            )
            atomic_json(audit_path, result)
            result["audit_path"] = str(audit_path)
            result["audit_sha256"] = sha256(audit_path)
        except Exception:
            shutil.copy2(backup, state_path)
            if renamed and quarantine.exists() and not batch.exists():
                quarantine.rename(batch)
            raise

    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
