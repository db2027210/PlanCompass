#!/usr/bin/env python3
"""Bind an unchanged fresh-R0 result to a verified container-identity successor."""

from __future__ import annotations

import argparse
from datetime import datetime
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
    path.parent.mkdir(parents=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-fresh-r0", type=Path, required=True)
    parser.add_argument("--successor-authority", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_path = args.source_fresh_r0.resolve()
    authority = args.successor_authority.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError("fresh successor output required")
    source = load(source_path)
    prepared_path = authority / "prepared_binding.json"
    accounts_path = authority / "benchmark_accounts_binding.json"
    preflight_path = authority / "transition_preflight.json"
    audit_path = authority / "container_identity_rebind_audit.json"
    prepared = load(prepared_path)
    accounts = load(accounts_path)
    preflight = load(preflight_path)
    audit = load(audit_path)
    if (
        source.get("status") != "PASS"
        or source.get("fresh_r0_complete") is not True
        or source.get("formal_outer_launch_authorized") is not True
        or source.get("formal_outer_started") is not False
        or source.get("accepted_state_credit_increment") != 0
        or any((source.get("accepted_rounds") or {}).values())
        or source.get("recalibration_performed") is not False
        or prepared.get("status") != "PREPARED"
        or prepared.get("preflight_sha256") != sha(preflight_path)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(prepared_path)
        or preflight.get("status") != "PASS"
        or audit.get("status") != "PASS"
        or audit.get("workload_started") is not False
        or audit.get("accepted_rounds_mutated") is not False
    ):
        raise RuntimeError("fresh-R0 identity successor authority differs")
    result = dict(source)
    result.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": sha(accounts_path),
        "source_authority_path": str(authority),
        "container_identity_rebind_audit_path": str(audit_path),
        "container_identity_rebind_audit_sha256": sha(audit_path),
        "predecessor_fresh_r0_path": str(source_path),
        "predecessor_fresh_r0_sha256": sha(source_path),
        "identity_only_successor": True,
        "r0_measurement_reused_without_mutation": True,
        "recalibration_performed": False,
    })
    atomic(output, result)
    print(json.dumps({
        "status": "PASS", "output": str(output), "sha256": sha(output),
        "predecessor_sha256": sha(source_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
