#!/usr/bin/env python3
"""Seal a TPC-C v26 controller manifest without changing experiment state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


CONTRACT = "tpcc_target10_prethird_zero_call_handover_v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
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


def build(
    source: Path,
    output: Path,
    wrapper: Path,
    expected_source_sha256: str,
) -> Dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    wrapper = wrapper.resolve()
    if not source.is_file() or sha256(source) != expected_source_sha256:
        raise RuntimeError("source manifest SHA mismatch")
    if not wrapper.is_file():
        raise RuntimeError("v26 controller wrapper is missing")

    manifest = load_json(source)
    pins = list(manifest.get("pinned_files") or [])
    if not any(
        Path(str(item.get("path"))).resolve().name
        == "tpcc_autonomous_target10_controller_v25.py"
        for item in pins
    ):
        raise RuntimeError("source manifest does not pin v25 controller")
    wrapper_pin = {
        "path": str(wrapper),
        "sha256": sha256(wrapper),
    }
    pins = [
        item
        for item in pins
        if Path(str(item.get("path"))).resolve() != wrapper
    ]
    pins.append(wrapper_pin)
    manifest.update(
        {
            "pinned_files": pins,
            "controller_entrypoint": str(wrapper),
            "controller_handover_contract": CONTRACT,
            "controller_source_manifest": str(source),
            "controller_source_manifest_sha256": expected_source_sha256,
            "prethird_gate": {
                "contract": "two_observation_prethird_median_upper_bound_v1",
                "if_infeasible": (
                    "zero_call_reroute_to_novel_diagnostic_discovery"
                ),
                "formal_state_mutation_allowed": False,
                "manual_candidate_injection": False,
            },
        }
    )
    atomic_json(output, manifest)
    return {
        "contract": CONTRACT,
        "source_manifest": str(source),
        "source_manifest_sha256": expected_source_sha256,
        "output_manifest": str(output),
        "output_manifest_sha256": sha256(output),
        "controller_entrypoint": str(wrapper),
        "controller_entrypoint_sha256": wrapper_pin["sha256"],
        "physical_workload_calls": 0,
        "formal_state_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    args = parser.parse_args()
    result = build(
        args.source,
        args.output,
        args.wrapper,
        args.expected_source_sha256,
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
