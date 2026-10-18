#!/usr/bin/env python3
"""Quarantine the exact zero-call, fail-closed XP v29 cont054 attempt."""

from __future__ import annotations

import argparse
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
EXPECTED_BUNDLE_MANIFEST_SHA256 = (
    "32b76ac889a5faa7d4aa1fd254068690cdf1c12d5e503069d7b51c92407e139e"
)
EXPECTED_TOP = {
    "accepted_incumbent.json": "a1cf9060abaf6005faa56026a77e2b424b7a6acb309118a3690b6f6a3c4d5312",
    "stability_risk_envelope.json": "1002fd22f1d9390bce65a234fda20b17d28220129af51f2588ca0e332d3f97ec",
    "technical_failure_reroute.json": "d11602cfe52d4d0ac6d04bb7b928193892fd00520f4ea1a5e36b3db95a6df135",
}
EXPECTED_GRADIENT_AUDIT_SHA256 = (
    "1632843d4cde46d96e68206dc4f13db7a32662841bb5d56ecda66a5269a7a8c8"
)


def sha256(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"required file missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def verify_pid_inactive(pid: int) -> str:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return "absent"
    lines = status.read_text(encoding="utf-8").splitlines()
    state = next(line.split()[1] for line in lines if line.startswith("State:"))
    if state not in {"Z", "X"}:
        raise RuntimeError(f"launch PID {pid} is still active: {state}")
    children_path = Path(f"/proc/{pid}/task/{pid}/children")
    if children_path.exists() and children_path.read_text().split():
        raise RuntimeError(f"launch PID {pid} still has children")
    return state


def read_last_jsonl(path: Path) -> Dict[str, Any]:
    last = ""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = line
    if not last:
        raise RuntimeError(f"empty subset log: {path}")
    value = json.loads(last)
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid last subset event: {path}")
    return value


def verify_batch(batch: Path, pid: int) -> List[str]:
    files = sorted(
        str(path.relative_to(batch)) for path in batch.rglob("*") if path.is_file()
    )
    logs = [name for name in files if name.endswith(".jsonl")]
    top = [name for name in files if "/" not in name]
    if len(files) != 67 or len(logs) != 63 or set(top) != set(EXPECTED_TOP):
        raise RuntimeError(
            f"launch-attempt inventory mismatch: total={len(files)} logs={len(logs)} top={top}"
        )
    if any(f"_{pid}/" not in name for name in logs):
        raise RuntimeError("subset logs do not all belong to the sealed launch PID")
    observed_orders = []
    for relative in logs:
        last = read_last_jsonl(batch / relative)
        if not (
            last.get("event") == "experience_table_subset_complete"
            and last.get("status") == "success"
        ):
            raise RuntimeError(f"subset log lacks a successful terminal event: {relative}")
        observed_orders.append(int(last.get("canonical_subset_order", -1)))
    if sorted(observed_orders) != list(range(1, 64)):
        raise RuntimeError("subset terminal events do not cover canonical orders 1..63")
    for name, expected in EXPECTED_TOP.items():
        if sha256(batch / name) != expected:
            raise RuntimeError(f"top-level artifact SHA mismatch: {name}")
    gradient_audit = batch / "explain_only" / "gradient_geometric_escape_audit.json"
    if sha256(gradient_audit) != EXPECTED_GRADIENT_AUDIT_SHA256:
        raise RuntimeError("v29 gradient escape audit SHA mismatch")
    gradient = load_json(gradient_audit)
    if not (
        gradient.get("safe_ids_before") == 4
        and gradient.get("safe_ids_after") == 4
        and gradient.get("generated_candidates") == 0
        and len(gradient.get("rays") or []) == 88
    ):
        raise RuntimeError("v29 gradient escape result is not the exact got4 failure")
    forbidden = {"selection.json", "replay_summary.json", "serial_replay", "workload.log", "result.json"}
    if any(any(part in forbidden for part in Path(name).parts) for name in files):
        raise RuntimeError("launch attempt crossed the zero-call boundary")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--launch-pid", type=int, default=2570286)
    parser.add_argument("--audit-name")
    parser.add_argument("--quarantine-name")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    bundle = args.bundle_root.resolve()
    state_path = campaign / "state.json"
    batch = campaign / "batches" / "cont054"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = campaign / (
        args.audit_name or f"v29_launch_attempt_quarantine_{timestamp}.json"
    )
    quarantine = campaign / "batches" / (
        args.quarantine_name or f"cont054.launch_pid2570286_zero_call_{timestamp}"
    )

    if sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("state SHA is not the exact fail-closed v29 boundary")
    state = load_json(state_path)
    if not (
        state.get("status") == "blocked_contract"
        and state.get("accepted_count") == 4
        and state.get("accepted_target") == 10
        and state.get("next_batch_index") == 54
        and state.get("incumbent_score") == -49.201616429624124
        and str(state.get("last_error") or "").endswith("got 4")
    ):
        raise RuntimeError("state content is not the exact clean retry boundary")
    if sha256(bundle / "bundle_manifest.json") != EXPECTED_BUNDLE_MANIFEST_SHA256:
        raise RuntimeError("v29 bundle manifest SHA mismatch")
    pid_state = verify_pid_inactive(args.launch_pid)
    inventory = verify_batch(batch, args.launch_pid)
    if audit_path.exists() or quarantine.exists():
        raise RuntimeError("quarantine output already exists")

    result: Dict[str, Any] = {
        "contract": "xp_v29_launch_attempt_zero_call_quarantine_v1",
        "status": "dry_run_passed",
        "launch_pid": args.launch_pid,
        "launch_pid_state": pid_state,
        "state_sha256": EXPECTED_STATE_SHA256,
        "bundle_manifest_sha256": EXPECTED_BUNDLE_MANIFEST_SHA256,
        "subset_count": 63,
        "subset_success_terminal_event_count": 63,
        "inventory_file_count": len(inventory),
        "selection_exists": False,
        "serial_replay_exists": False,
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
        "diagnosis": "v29_completed_normally_and_failed_safe_novel_diversity_gate",
        "gradient_audit_sha256": EXPECTED_GRADIENT_AUDIT_SHA256,
    }
    if args.apply:
        backup = campaign / f"state.pre_v29_launch_attempt_quarantine_{timestamp}.json"
        shutil.copy2(state_path, backup)
        if sha256(backup) != EXPECTED_STATE_SHA256:
            raise RuntimeError("state backup SHA mismatch")
        batch.rename(quarantine)
        result.update(
            {
                "status": "applied_and_verified",
                "state_backup": str(backup),
                "state_backup_sha256": sha256(backup),
                "quarantine": str(quarantine),
            }
        )
        atomic_json(audit_path, result)
        if sha256(state_path) != EXPECTED_STATE_SHA256:
            raise RuntimeError("quarantine unexpectedly changed state")
        result["audit_path"] = str(audit_path)
        result["audit_sha256"] = sha256(audit_path)

    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
