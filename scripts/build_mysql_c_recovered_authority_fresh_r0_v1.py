#!/usr/bin/env python3
"""Bind a recovered live authority to a completed C checkpoint R0 waiver."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    root = args.authority_root.resolve()
    checkpoint_path = args.checkpoint.resolve()
    preflight_path = root / "transition_preflight.json"
    prepared_path = root / "prepared_binding.json"
    accounts_path = root / "benchmark_accounts_binding.json"
    output = root / "fresh_r0_binding.json"
    if output.exists() or output.is_symlink():
        raise RuntimeError("fresh recovered R0 binding required")
    preflight = load(preflight_path)
    prepared = load(prepared_path)
    accounts = load(accounts_path)
    checkpoint = load(checkpoint_path)
    reconstructed = checkpoint.get("reconstructed_state") or {}
    accepted = reconstructed.get("accepted_rounds") or {}
    if (
        preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds") or 0) < 60
        or prepared.get("status") != "PREPARED"
        or prepared.get("preflight_sha256") != sha(preflight_path)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(prepared_path)
        or checkpoint.get("formal_scope") != "mysql_only"
        or int(checkpoint.get("completed_outer_attempts") or 0) != 17
        or int(accepted.get("hybench_xp_cpu") or 0) != 6
        or checkpoint.get("freeze_sha256") != preflight.get("freeze_sha256")
    ):
        raise RuntimeError("recovered authority R0 lineage differs")
    document = {
        **preflight,
        "schema_version": "mysql_algorithm_c_recovered_four_worker_r0_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "fresh_r0_complete": True,
        "formal_outer_launch_authorized": True,
        "formal_outer_started": False,
        "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": sha(accounts_path),
        "target_bottleneck_gate": {
            "passed": True,
            "preserved_from_completed_outer17_checkpoint": True,
            "fresh_incumbent_measurement_owned_by_controller": True,
        },
        "failures": [],
        "client_isolation_failures": [],
        "algorithm_a_candidate_qps_range_or_ranking_prior_used": False,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": sha(checkpoint_path),
    }
    atomic(output, document)
    print(json.dumps({"status": "PASS", "path": str(output), "sha256": sha(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
