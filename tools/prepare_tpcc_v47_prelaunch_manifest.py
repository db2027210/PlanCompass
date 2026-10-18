#!/usr/bin/env python3
"""Build the sealed TPC-C v47/v29 manifest without starting a workload."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Mapping


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
SOURCE_MANIFEST = Path(
    "/tmp/tpcc_final_fresh0_autonomous_v46_role_aware_20260803/manifest.json"
)
SOURCE_MANIFEST_SHA256 = (
    "88d6cffb25b2e9855d8465bd6762e4eb1cfdade02ea90e042dc3594fdf2857e7"
)
OUTPUT_MANIFEST = Path(
    "/tmp/tpcc_final_fresh0_autonomous_v47_prelaunch_fingerprint_20260803/"
    "manifest.json"
)
FORMAL_STATE = PROJECT_ROOT / (
    "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/formal_state.json"
)
FORMAL_STATE_SHA256 = (
    "0996b26e26688a276a820e06474167e714786ab97919619a70f813971d26f0c7"
)
CONTROLLER_STATE = PROJECT_ROOT / (
    "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_controller_v32/controller_state.json"
)
CONTROLLER_STATE_SHA256 = (
    "5922e071539fba4015bef4db025db6ce81680522a3600ff919212bf81e80a850"
)
PARTIAL_ROOT = PROJECT_ROOT / (
    "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_controller_v32/"
    "quarantine_prepare_152_v28_zero_call_20260803T0135"
)
PIN_UPDATES = {
    str(PROJECT_ROOT / "tools/tpcc_autonomous_target10_controller_v25.py"): (
        "43da46ff124f321d681c8e86d4c3ff64e7cdaa9434b23c5ad00c05cfac9a4db7"
    ),
    str(
        PROJECT_ROOT
        / "tools/tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint.py"
    ): "7843c27ed328f0d9122dac45388b7484569e116d56320c0814ebe6efc8107359",
    str(PROJECT_ROOT / "tools/preflight_tpcc_v29_prelaunch_fingerprint.py"): (
        "ab3aad3a77ea28ec06de1aab17e55489d43a95d72e2ec8d0edc1c8f0df2607a7"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def require_boundary() -> None:
    if sha256(SOURCE_MANIFEST) != SOURCE_MANIFEST_SHA256:
        raise RuntimeError("v46 source manifest SHA mismatch")
    if sha256(FORMAL_STATE) != FORMAL_STATE_SHA256:
        raise RuntimeError("formal state SHA mismatch")
    formal = load_json(FORMAL_STATE)
    if formal.get("accepted_count") != 2 or formal.get("accepted_target") != 10:
        raise RuntimeError("formal accepted boundary is not exactly 2/10")
    if sha256(CONTROLLER_STATE) != CONTROLLER_STATE_SHA256:
        raise RuntimeError("controller state SHA mismatch")
    state = load_json(CONTROLLER_STATE)
    observed = {
        "status": state.get("status"),
        "next_mode": state.get("next_mode"),
        "next_ordinal": state.get("next_ordinal"),
        "current_run": state.get("current_run"),
    }
    expected = {
        "status": "preparing_next_batch",
        "next_mode": "diagnostic",
        "next_ordinal": 152,
        "current_run": None,
    }
    if observed != expected:
        raise RuntimeError(f"controller boundary mismatch: {observed}")
    expected_partial = {
        "evidence_manifest.json": (
            "5f0ff3cba3d708f665f1eeca1eef52fc0969462807c13d64a7738ed06d9a7c4b"
        ),
        "exact_regime_calibration.json": (
            "4ded55286f20d2aed3123bd91c507df51107638d93c3ed4a364fb9902a18f8c7"
        ),
    }
    observed_partial = {
        path.name: sha256(path) for path in PARTIAL_ROOT.iterdir() if path.is_file()
    }
    if observed_partial != expected_partial:
        raise RuntimeError("quarantined zero-call partial mismatch")


def build_manifest() -> Dict[str, Any]:
    source = load_json(SOURCE_MANIFEST)
    manifest = copy.deepcopy(source)
    manifest.update(
        {
            "controller_entrypoint": str(
                PROJECT_ROOT
                / "tools/"
                "tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint.py"
            ),
            "controller_handover_contract": (
                "clean151_zero_call_partial_quarantined_prelaunch_v29"
            ),
            "controller_source_manifest": str(SOURCE_MANIFEST),
            "controller_source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "controller_state_path": str(CONTROLLER_STATE),
            "controller_version": 29,
            "current_run": None,
            "initial_controller_state_sha256": CONTROLLER_STATE_SHA256,
            "initial_formal_state_sha256": FORMAL_STATE_SHA256,
            "next_ordinal": 152,
            "pending_completed_run_at_handover": None,
            "prelaunch_fingerprint_contract": (
                "all_structured_history_three_level_zero_call_collision_reroute_v1"
            ),
            "resumed_from_manifest": str(SOURCE_MANIFEST),
            "resumed_from_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "controller_state_boundary": {
                "status": "preparing_next_batch",
                "next_mode": "diagnostic",
                "next_ordinal": 152,
                "current_run": None,
            },
            "quarantined_zero_call_partial": {
                "path": str(PARTIAL_ROOT),
                "workload_calls": 0,
                "files": [
                    {
                        "name": "evidence_manifest.json",
                        "sha256": (
                            "5f0ff3cba3d708f665f1eeca1eef52fc0969462807c13d64a7738ed06d9a7c4b"
                        ),
                    },
                    {
                        "name": "exact_regime_calibration.json",
                        "sha256": (
                            "4ded55286f20d2aed3123bd91c507df51107638d93c3ed4a364fb9902a18f8c7"
                        ),
                    },
                ],
            },
            "v29_activation_authorized": False,
        }
    )
    pins = {
        str(item["path"]): str(item["sha256"])
        for item in manifest.get("pinned_files") or []
    }
    pins.update(PIN_UPDATES)
    manifest["pinned_files"] = [
        {"path": path, "sha256": pins[path]} for path in sorted(pins)
    ]
    return manifest


def main() -> int:
    require_boundary()
    manifest = build_manifest()
    for item in manifest["pinned_files"]:
        path = Path(item["path"]).resolve()
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"pinned file mismatch: {path}")
    atomic_json(OUTPUT_MANIFEST, manifest)
    print(
        json.dumps(
            {
                "manifest_path": str(OUTPUT_MANIFEST),
                "manifest_sha256": sha256(OUTPUT_MANIFEST),
                "workload_calls_added": 0,
                "formal_state_mutated": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
