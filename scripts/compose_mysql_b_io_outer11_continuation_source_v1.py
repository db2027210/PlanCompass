#!/usr/bin/env python3
"""Compose immutable IO-B Outer11 continuation evidence without replay."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FAILED_RUN = HISTORY / "v588_to_b_io_mixed_eight_worker_v7_outer10_resume_20260827_xp_io"
CALIBRATION_RUN = HISTORY / "v580_to_b_io_mixed_eight_worker_v7_outer9_resume_20260827_xp_io"
OUTPUT = HISTORY / "v593_to_b_io_outer11_unlocked_continuation_source_20260827"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not path.is_file()
        or path.is_symlink()
        or not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii").split()[0] != sha(path)
    ):
        raise RuntimeError(f"sealed continuation evidence differs: {path}")


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh continuation source required: {OUTPUT}")
    failed_state = FAILED_RUN / "run_state.json"
    design = CALIBRATION_RUN / "calibration_design.json"
    baseline = CALIBRATION_RUN / "formal_independent_baseline"
    calibration = CALIBRATION_RUN / "fresh_calibration"
    checked(failed_state)
    checked(design)
    if not baseline.is_dir() or baseline.is_symlink():
        raise RuntimeError("baseline evidence directory differs")
    if not calibration.is_dir() or calibration.is_symlink():
        raise RuntimeError("calibration evidence directory differs")
    state = json.loads(failed_state.read_text(encoding="utf-8"))
    if (
        state.get("status") != "FAILED_CLOSED"
        or int(state.get("outer_attempt") or 0) != 11
        or int((state.get("accepted_rounds") or {}).get("hybench_xp_io") or 0) != 2
        or int((state.get("rejected_outer_attempts") or {}).get("hybench_xp_io") or 0) != 9
    ):
        raise RuntimeError("failed IO-B Outer11 boundary differs")
    OUTPUT.mkdir(parents=True, mode=0o700)
    shutil.copy2(failed_state, OUTPUT / failed_state.name)
    shutil.copy2(failed_state.with_name(failed_state.name + ".sha256"), OUTPUT / (failed_state.name + ".sha256"))
    shutil.copy2(design, OUTPUT / design.name)
    shutil.copy2(design.with_name(design.name + ".sha256"), OUTPUT / (design.name + ".sha256"))
    shutil.copytree(baseline, OUTPUT / baseline.name, symlinks=False)
    shutil.copytree(calibration, OUTPUT / calibration.name, symlinks=False)
    manifest = {
        "schema_version": "mysql_b_io_outer11_continuation_evidence_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "committed_outer": 11,
        "accepted_rounds": 2,
        "rejected_outer_attempts": 9,
        "failed_run_state": {"path": str(failed_state), "sha256": sha(failed_state)},
        "calibration_source": str(CALIBRATION_RUN),
        "candidate_measurements_replayed": 0,
        "accepted_or_rejected_credit_added": 0,
        "partial_outer_012_measurements_reused": 0,
    }
    path = OUTPUT / "continuation_source_manifest.json"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": "PASS", "output": str(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

