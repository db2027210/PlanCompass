#!/usr/bin/env python3
"""Fail closed and quarantine TPC-C v38's zero-call partial prepare_50."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


CAMPAIGN = Path(
    "/home/anonymous/program/resource_adaptive/"
    "history/tpcc_final_algorithm_fresh10_v29_20260731T2005"
)
CONTROLLER_ROOT = CAMPAIGN / "autonomous_controller_v32"
SOURCE = CONTROLLER_ROOT / "prepare_50"
STATE = CONTROLLER_ROOT / "controller_state.json"
FORMAL_STATE = CAMPAIGN / "formal_state.json"

EXPECTED_STATE_SHA = "84c569fdd6e5bec8d54f86cf81e4ca88f160df69f70cf56a950c5a7fcc000eae"
EXPECTED_FORMAL_SHA = "8575cb37b666afda71ab0bf4ffeb85cf007ca919ca2143709d014fac34b539ca"
EXPECTED_FILES = {
    "evidence_manifest.json": "1614cb3becd70bc9e9ff32d5bded0f6849a609fe7949acc7eaa6fb4d6db44b67",
    "exact_regime_calibration.json": "45a6f5961f8b2cd46d2ecc601eb976aa432964951cb644c53eee32dc8fee1bef",
    "top5.json": "a87ca7936cfdd229c5f24ee028dc6ca2e8419c8a01bb8eefa6b885ee6cd3832c",
}


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
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    if sha256(STATE) != EXPECTED_STATE_SHA:
        raise RuntimeError("TPC-C controller state SHA changed")
    if sha256(FORMAL_STATE) != EXPECTED_FORMAL_SHA:
        raise RuntimeError("TPC-C formal state SHA changed")
    state = load_json(STATE)
    formal = load_json(FORMAL_STATE)
    if (
        state.get("current_run") is not None
        or int(state.get("next_ordinal", -1)) != 50
        or int(formal.get("accepted_count", -1)) != 2
    ):
        raise RuntimeError("TPC-C live state no longer matches partial handover")
    observed = {
        str(path.relative_to(SOURCE)): sha256(path)
        for path in SOURCE.rglob("*")
        if path.is_file()
    }
    if observed != EXPECTED_FILES:
        raise RuntimeError(f"unexpected partial prepare_50 contents: {observed}")

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    target = CONTROLLER_ROOT / f"quarantine_prepare_50_v38_role_metadata_{timestamp}"
    if target.exists():
        raise RuntimeError(f"quarantine target already exists: {target}")
    os.replace(SOURCE, target)
    if SOURCE.exists() or not target.is_dir():
        raise RuntimeError("partial prepare_50 quarantine move failed")
    if sha256(STATE) != EXPECTED_STATE_SHA or sha256(FORMAL_STATE) != EXPECTED_FORMAL_SHA:
        raise RuntimeError("state changed while quarantining partial prepare_50")

    audit = {
        "schema_version": "tpcc_partial_preparation_quarantine_v1",
        "event": "v38_prepare50_role_metadata_failure_quarantined",
        "source": str(SOURCE),
        "quarantine": str(target),
        "files": observed,
        "controller_state_sha256": EXPECTED_STATE_SHA,
        "formal_state_sha256": EXPECTED_FORMAL_SHA,
        "accepted_count": 2,
        "physical_calls_added": 0,
        "valid_replays_added": 0,
        "formal_state_mutated": False,
        "recoverable": True,
    }
    audit_path = CONTROLLER_ROOT / f"prepare50_v38_quarantine_audit_{timestamp}.json"
    atomic_json(audit_path, audit)
    print(json.dumps({**audit, "audit_path": str(audit_path)}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
