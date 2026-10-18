#!/usr/bin/env python3
"""Defer v1 prestart evidence until the fresh Algorithm A directory exists."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_job_recovery_replay_excluded_resume_v1 as v1


CONTRACT_NAME = "mysql_algorithm_a_job_recovery_replay_excluded_resume_v2"
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_recovery_replay_excluded_20260823_v2"
    / "transition_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_file(value: str) -> Path:
    return v1.regular_file(value)


def validate_contract() -> dict[str, Any]:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    bindings = value.get("source_bindings") or {}
    expected = {
        "controller": Path(__file__).resolve(),
        "recovery_resume_v1": Path(v1.__file__).resolve(),
        "recovery_resume_v1_contract": v1.CONTRACT_PATH.resolve(),
    }
    if (
        CONTRACT_PATH.is_symlink()
        or value.get("schema_version") != CONTRACT_NAME
        or value.get("status") != "FROZEN"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("optimizer_semantics_changed") is not False
        or value.get("resource_envelope_changed") is not False
        or value.get("candidate_set_changed") is not False
        or value.get("prestart_evidence_deferred_until_output_creation") is not True
        or set(bindings) != set(expected)
    ):
        raise RuntimeError("JOB recovery replay exclusion v2 contract differs")
    for name, path in expected.items():
        binding = bindings[name]
        if (
            path.is_symlink()
            or not path.is_file()
            or Path(str(binding.get("path") or "")).resolve() != path
            or binding.get("sha256") != sha(path)
        ):
            raise RuntimeError(f"JOB recovery replay exclusion v2 source differs: {name}")
    return value


def run(args: argparse.Namespace) -> int:
    contract = validate_contract()
    real_atomic = v1.base.atomic
    state_path = args.output_dir.resolve() / "algorithm_a_state.json"
    prestart_path = args.output_dir.resolve() / "prestart_read_only_retry.json"
    buffered_prestart: dict[str, Any] | None = None

    def deferred_atomic(path: Path, value: Mapping[str, Any]) -> None:
        nonlocal buffered_prestart
        resolved = path.resolve()
        if resolved == prestart_path and not args.output_dir.exists():
            buffered_prestart = copy.deepcopy(dict(value))
            return
        document = copy.deepcopy(dict(value))
        if resolved == state_path:
            if buffered_prestart is not None and not prestart_path.exists():
                real_atomic(prestart_path, buffered_prestart)
            document["prestart_output_creation_fix"] = {
                "contract": CONTRACT_NAME,
                "contract_path": str(CONTRACT_PATH),
                "contract_sha256": sha(CONTRACT_PATH),
                "prestart_evidence_deferred_until_output_creation": True,
                "preworkload_attempts_excluded": [1, 2],
                "optimizer_semantics_changed": False,
                "resource_envelope_changed": False,
                "candidate_set_changed": False,
            }
        real_atomic(path, document)

    v1.base.atomic = deferred_atomic
    try:
        return v1.run(args)
    finally:
        v1.base.atomic = real_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=("job_memory",), required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--excluded-recovery-state", type=regular_file, required=True)
    parser.add_argument("--excluded-recovery-batch", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
