#!/usr/bin/env python3
"""Audit and rebind Algorithm C source provenance before formal start.

This helper is deliberately fail-closed.  It preserves immutable calibration
replay evidence only when the lane has accepted no formal point, has published
a clean signal-stop receipt, and all source differences are from the small
controller allowlist used for systemic repairs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Mapping

from pg_algorithm_c_lane_entry import SOURCE_RELATIVE_PATHS


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PRE_FORMAL_SOURCE_CHANGES = {
    "shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py",
    "shell/run_pg_algorithm_c_calibrate_then_scene.py",
    "shell/run_pg_algorithm_c_scene.py",
}


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    """Read a JSON object and fail if the top level is not a mapping."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace a JSON authority file."""

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def require_clean_preformal_scene(scene_dir: Path) -> Dict[str, Any]:
    """Return auditable lane facts after enforcing pre-formal cleanliness."""

    calibration_path = scene_dir / "calibration_state.json"
    calibration = read_json(calibration_path)
    if int(calibration.get("formal_accepted_count") or 0) != 0:
        raise RuntimeError(f"formal accepted state is nonzero: {calibration_path}")
    if calibration.get("phase") != "stopped_by_signal_clean":
        raise RuntimeError(
            "pre-formal source rebind requires stopped_by_signal_clean: "
            f"{calibration_path} phase={calibration.get('phase')}"
        )
    clean_receipt = calibration.get("signal_stop_clean_receipt")
    if not isinstance(clean_receipt, Mapping):
        raise RuntimeError(f"missing clean signal-stop receipt: {calibration_path}")

    formal_path = scene_dir / "algorithm_c_state.json"
    formal: Dict[str, Any] = read_json(formal_path) if formal_path.is_file() else {}
    if formal.get("formal_started") is True:
        raise RuntimeError(f"formal lane already started: {formal_path}")
    if int(formal.get("accepted") or formal.get("accepted_count") or 0) != 0:
        raise RuntimeError(f"formal accepted state is nonzero: {formal_path}")

    replay_path = scene_dir / "calibration_replays.jsonl"
    replay_rows = 0
    if replay_path.is_file():
        replay_rows = sum(
            1
            for line in replay_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if line.strip()
        )
    return {
        "scene_dir": str(scene_dir),
        "calibration_phase": calibration.get("phase"),
        "calibration_physical_workload_invocations": int(
            calibration.get("physical_workload_invocations") or 0
        ),
        "calibration_valid_replays": int(calibration.get("valid_replays") or 0),
        "calibration_technical_failures": int(
            calibration.get("technical_failures") or 0
        ),
        "durable_calibration_jsonl_rows": replay_rows,
        "completed_batches": list(calibration.get("completed_batches") or []),
        "clean_receipt_sha256": hashlib.sha256(
            json.dumps(
                clean_receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "formal_started": False,
        "formal_accepted_count": 0,
    }


def rebind(run_root: Path, reason: str) -> Dict[str, Any]:
    """Rebind one stopped pre-formal run root to current audited sources."""

    manifest_path = run_root / "launch_manifest.json"
    manifest = read_json(manifest_path)
    scenes = manifest.get("scenes")
    if not isinstance(scenes, Mapping) or not scenes:
        raise RuntimeError(f"launch manifest has no scenes: {manifest_path}")
    scene_facts = {
        str(scene): require_clean_preformal_scene(run_root / str(scene))
        for scene in scenes
    }

    provenance = manifest.get("source_provenance")
    declared = provenance.get("files") if isinstance(provenance, Mapping) else None
    if not isinstance(declared, Mapping):
        raise RuntimeError(f"launch manifest has no source declarations: {manifest_path}")
    expected_names = set(SOURCE_RELATIVE_PATHS)
    if set(str(name) for name in declared) != expected_names:
        raise RuntimeError("source declaration set does not match lane entry contract")

    observed = {
        relative_name: sha256(ROOT / relative_name)
        for relative_name in sorted(expected_names)
    }
    changed = {
        relative_name: {
            "previous_sha256": str(declared.get(relative_name) or ""),
            "current_sha256": observed_sha,
        }
        for relative_name, observed_sha in observed.items()
        if str(declared.get(relative_name) or "") != observed_sha
    }
    if not changed:
        raise RuntimeError("source provenance already matches; rebind is unnecessary")
    unexpected = sorted(set(changed) - ALLOWED_PRE_FORMAL_SOURCE_CHANGES)
    if unexpected:
        raise RuntimeError(f"unexpected source changes cannot be rebound: {unexpected}")

    previous_manifest_sha256 = sha256(manifest_path)
    record = {
        "contract": "algorithm_c_preformal_source_rebind_v1",
        "recorded_at_epoch_s": time.time(),
        "reason": reason,
        "changed_sources": changed,
        "scene_facts": scene_facts,
        "previous_manifest_sha256": previous_manifest_sha256,
        "formal_state_mutation": False,
        "formal_accepted_inherited": False,
        "calibration_replay_evidence_preserved": True,
        "calibration_replay_evidence_replayed": False,
        "database_mutation": False,
        "technical_failure": False,
    }
    provenance = dict(provenance)
    provenance["files"] = observed
    provenance.setdefault("preformal_rebinds", []).append(record)
    manifest["source_provenance"] = provenance
    write_json_atomic(manifest_path, manifest)

    audit = dict(record)
    audit["updated_manifest_sha256"] = sha256(manifest_path)
    write_json_atomic(run_root / "preformal_source_rebind_audit.json", audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    result = rebind(args.run_root.resolve(), args.reason)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
