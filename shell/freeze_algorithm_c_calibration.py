#!/usr/bin/env python3
"""Publish one completed Algorithm C calibration as a reusable release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_calibration_release import (  # noqa: E402
    DEFAULT_RELEASE_ROOT,
    publish_calibration_release,
)
from shell.run_pg_algorithm_c_scene import validate_experience_artifact  # noqa: E402


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    cfg_path = source_dir / "optimizer_config.json"
    state_path = source_dir / "calibration_state.json"
    if not cfg_path.is_file() or not state_path.is_file():
        raise FileNotFoundError("source calibration config/state missing")
    cfg = read_json(cfg_path)
    state = read_json(state_path)
    artifact_path = Path(
        str((cfg.get("algorithm_c") or {}).get("experience_table_path") or "")
    )
    if not artifact_path.is_file():
        raise FileNotFoundError(artifact_path)
    artifact = read_json(artifact_path)
    target = str(
        (((cfg.get("algorithm_c") or {}).get("workload_contract") or {}).get(
            "target_resource"
        ))
        or ""
    )
    validate_experience_artifact(artifact, target)
    if state.get("phase") not in {
        "calibration_complete_formal_ready",
        "calibration_reused_formal_ready",
        "fresh_parallel_baseline",
        "outer_round",
        "complete",
    } and not (source_dir / "algorithm_c_state.json").is_file():
        raise RuntimeError(
            f"calibration has not reached formal handoff: phase={state.get('phase')}"
        )
    release = publish_calibration_release(
        cfg=cfg,
        source_dir=source_dir,
        artifact_path=artifact_path,
        release_root=args.release_root.resolve(),
    )
    print(json.dumps(release, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
