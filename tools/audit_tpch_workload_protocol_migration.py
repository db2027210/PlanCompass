#!/usr/bin/env python3
"""Audit whether a TPC-H formal state is comparable to the sealed workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"{path}:{line_number} is not a JSON object")
        values.append(value)
    return values


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-launcher", type=Path, required=True)
    parser.add_argument("--legacy-wrapper", type=Path, required=True)
    parser.add_argument("--legacy-driver", type=Path, required=True)
    parser.add_argument("--legacy-events", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--sealed-wrapper", type=Path, required=True)
    parser.add_argument("--sealed-driver", type=Path, required=True)
    parser.add_argument("--sealed-preflight", type=Path, required=True)
    parser.add_argument("--current-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    legacy_launcher_text = args.legacy_launcher.read_text(encoding="utf-8")
    legacy_wrapper_text = args.legacy_wrapper.read_text(encoding="utf-8")
    legacy_driver_text = args.legacy_driver.read_text(encoding="utf-8")
    sealed_wrapper_text = args.sealed_wrapper.read_text(encoding="utf-8")
    sealed_driver_text = args.sealed_driver.read_text(encoding="utf-8")
    legacy_events = load_jsonl(args.legacy_events)
    formal_state = load_object(args.formal_state)
    sealed_preflight = load_object(args.sealed_preflight)
    current_summary = load_object(args.current_summary)

    legacy_completions = [
        item
        for item in legacy_events
        if item.get("event") == "tpch_formal_real_replay_complete"
    ]
    current_successful = current_summary.get("successful") or []
    if not isinstance(current_successful, list):
        raise RuntimeError("current summary successful field must be a list")

    legacy_wrapper_sha = sha256(args.legacy_wrapper)
    legacy_driver_sha = sha256(args.legacy_driver)
    sealed_wrapper_sha = sha256(args.sealed_wrapper)
    sealed_driver_sha = sha256(args.sealed_driver)
    checks = {
        "legacy_launcher_pins_wrapper": (
            legacy_wrapper_sha in legacy_launcher_text
        ),
        "legacy_launcher_does_not_pin_driver": (
            legacy_driver_sha not in legacy_launcher_text
            and "run_pg_weighted_sql_baseline_profile_rolling_v2.py"
            not in legacy_launcher_text
        ),
        "legacy_wrapper_uses_mutable_external_driver": (
            "$base/code/shell/run_pg_weighted_sql_baseline_profile_rolling_v2.py"
            in legacy_wrapper_text
        ),
        "legacy_driver_has_no_query_completion_gate": (
            "query_completion_gate_passed" not in legacy_driver_text
            and "minimum_successful_queries_for_early_stop" not in legacy_driver_text
        ),
        "legacy_events_do_not_bind_driver_or_summary": (
            len(legacy_completions) == 5
            and all(
                item.get("workload_driver_sha256") in (None, "")
                and item.get("measurement_protocol") in (None, {})
                for item in legacy_completions
            )
        ),
        "sealed_wrapper_uses_packaged_driver": (
            'package_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)'
            in sealed_wrapper_text
            and "$package_root/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py"
            in sealed_wrapper_text
        ),
        "sealed_driver_requires_query_completion": (
            "query_completion_gate_passed = current_successes > 0"
            in sealed_driver_text
            and "minimum_successful_queries_for_early_stop" in sealed_driver_text
        ),
        "sealed_package_preflight_passed": (
            sealed_preflight.get("status") == "passed"
            and sealed_preflight.get("maximum_physical_calls") == 5
        ),
        "current_batch_clean": (
            int(current_summary.get("physical_call_count", -1)) == 5
            and int(current_summary.get("valid_replay_count", -1)) == 5
            and int(current_summary.get("technical_failure_count", -1)) == 0
            and current_summary.get("baseline_restored") is True
        ),
        "formal_state_accepted_one": (
            int(formal_state.get("accepted_count", -1)) == 1
            and float(formal_state.get("incumbent_score", -1.0)) > 0.0
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"protocol audit evidence incomplete: {checks}")

    legacy_scores = [
        float(item["actual_score"]) for item in legacy_completions
    ]
    current_scores = [
        float(item["actual_score"]) for item in current_successful
    ]
    result = {
        "schema_version": "tpch_workload_protocol_migration_audit_v1",
        "status": "fail_closed_migration_required",
        "new_workload_calls": 0,
        "formal_state_mutated": False,
        "accepted_count_mutated": False,
        "checks": checks,
        "legacy_protocol": {
            "launcher": str(args.legacy_launcher),
            "launcher_sha256": sha256(args.legacy_launcher),
            "wrapper": str(args.legacy_wrapper),
            "wrapper_sha256": legacy_wrapper_sha,
            "driver": str(args.legacy_driver),
            "driver_sha256_observed_now_not_proven_at_legacy_run": (
                legacy_driver_sha
            ),
            "driver_content_at_legacy_run": "unverifiable_unpinned_dependency",
            "actual_scores": legacy_scores,
        },
        "sealed_protocol": {
            "wrapper": str(args.sealed_wrapper),
            "wrapper_sha256": sealed_wrapper_sha,
            "driver": str(args.sealed_driver),
            "driver_sha256": sealed_driver_sha,
            "preflight": str(args.sealed_preflight),
            "preflight_sha256": sha256(args.sealed_preflight),
            "actual_scores": current_scores,
        },
        "formal_state": {
            "path": str(args.formal_state),
            "sha256": sha256(args.formal_state),
            "accepted_count": int(formal_state["accepted_count"]),
            "incumbent_score": float(formal_state["incumbent_score"]),
            "incumbent_params": formal_state["incumbent_params"],
        },
        "conclusion": (
            "The accepted incumbent score is not proven comparable to the "
            "sealed completion-gated workload. Revalidate the incumbent under "
            "the sealed protocol, then perform a separately authorized, "
            "backed-up atomic protocol-migration transaction that preserves "
            "accepted_count and replaces only protocol-bound incumbent "
            "resource/score evidence."
        ),
    }
    atomic_json(args.output, result)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "output_sha256": sha256(args.output),
                "status": result["status"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
