#!/usr/bin/env python3
"""Build a SHA-bound TPC-H formal entry gate from a completed diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


def load_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--diagnostic-state", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--formal-state-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    analysis = load_object(args.analysis)
    diagnostic = load_object(args.diagnostic_state)
    selection = load_object(args.selection)
    formal = load_object(args.formal_state)
    observed_formal_sha = file_sha256(args.formal_state)
    if observed_formal_sha != args.formal_state_sha256:
        raise ValueError("authoritative formal state SHA mismatch")

    candidates = selection.get("top5") or []
    fingerprints = [
        str((candidate.get("three_level_fingerprint") or {}).get("id") or "")
        for candidate in candidates
    ]
    roles = [
        "probe"
        if candidate.get("selection_role") in {"probe", "uncertain_probe"}
        else candidate.get("selection_role")
        for candidate in candidates
    ]
    checks = {
        "analyzer_passed": analysis.get("passed") is True,
        "five_valid_replays": (
            int(diagnostic.get("physical_call_count", -1)) == 5
            and int(diagnostic.get("valid_replay_count", -1)) == 5
            and int(diagnostic.get("technical_failure_count", -1)) == 0
        ),
        "baseline_restored": diagnostic.get("baseline_restored") is True,
        "diagnostic_state_is_immutable": (
            diagnostic.get("diagnostic_only") is True
            and diagnostic.get("formal_state_mutation_allowed") is False
            and diagnostic.get("accepted_update_allowed") is False
            and diagnostic.get("incumbent_update_allowed") is False
            and diagnostic.get("range_update_allowed") is False
            and diagnostic.get("gradient_update_allowed") is False
        ),
        "formal_state_clean": (
            formal.get("schema_version")
            == "tpch_exact_plan_regime_formal_state_v1"
            and formal.get("status") == "complete"
            and formal.get("baseline_restored") is True
            and int(formal.get("technical_failure_count", -1)) == 0
        ),
        "diagnostic_matches_formal_state": (
            diagnostic.get("accepted_count") == formal.get("accepted_count")
            and diagnostic.get("incumbent_params") == formal.get("incumbent_params")
            and diagnostic.get("incumbent_resource")
            == formal.get("incumbent_resource")
            and diagnostic.get("incumbent_score") == formal.get("incumbent_score")
            and diagnostic.get("range_state") == formal.get("range_state")
            and diagnostic.get("resume_state_sha256")
            == args.formal_state_sha256
        ),
        "five_novel_noncorner_fingerprints": (
            len(candidates) == 5
            and len(set(fingerprints)) == 5
            and all(fingerprints)
            and int(selection.get("novel_unique_fingerprint_count", -1)) >= 5
            and all(
                candidate.get("boundary_corner") is not True
                and candidate.get("boundary_plateau") is not True
                for candidate in candidates
            )
        ),
        "trusted3_probe2": roles == ["trusted", "trusted", "trusted", "probe", "probe"],
        "qps_outer_only": (
            selection.get("qps_used_for_inner_scoring") is False
            and diagnostic.get("qps_used_for_inner_scoring") is False
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"formal entry gate failed: {checks}")

    output = {
        "schema_version": "tpch_formal_entry_gate_v1",
        "passed": True,
        "successful_real_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_params_restored": True,
        "accepted_count": int(formal["accepted_count"]),
        "incumbent_score": float(formal["incumbent_score"]),
        "qps_used_for_inner_scoring": False,
        "checks": checks,
        "stability_analysis": analysis,
        "bindings": {
            "analysis": {
                "path": str(args.analysis),
                "sha256": file_sha256(args.analysis),
            },
            "diagnostic_state": {
                "path": str(args.diagnostic_state),
                "sha256": file_sha256(args.diagnostic_state),
            },
            "selection": {
                "path": str(args.selection),
                "sha256": file_sha256(args.selection),
            },
            "formal_state": {
                "path": str(args.formal_state),
                "sha256": observed_formal_sha,
            },
        },
    }
    atomic_json(args.output, output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "output_sha256": file_sha256(args.output),
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
