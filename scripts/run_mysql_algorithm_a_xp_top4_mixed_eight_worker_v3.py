#!/usr/bin/env python3
"""Run XP8 Algorithm A with parallel mixed-SSD reset and settle accounting."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v1.py"
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "Run fresh per-scene Top4 Algorithm A on four current XP workers.":
        "Run fresh per-scene Top4 Algorithm A on eight mixed-SSD XP workers.",
    "frozen_mysql_xp_top4_algorithm_a_20260826_v1":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v3",
    "range(1, 5)": "range(1, 9)",
    'physical_replay_parallelism_per_scene") != 4':
        'physical_replay_parallelism_per_scene") != 8',
    'candidate_batches") != [[1, 2, 3, 4]]':
        'candidate_batches") != [[1, 2, 3, 4, 5, 6, 7, 8]]',
    'physical_waves_per_round") != 4':
        'physical_waves_per_round") != 2',
    'physical_replay_parallelism") != 4':
        'physical_replay_parallelism") != 8',
    '"fresh_baseline_worker_count": 4':
        '"fresh_baseline_worker_count": 8',
    "base.REPLAY_PARALLELISM = 4": "base.REPLAY_PARALLELISM = 8",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 A v3 predecessor differs: {old!r} count={text.count(old)}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp8_v3_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)

from scripts import mysql_async_xp_mixed_ssd_post_reset_settle_v1 as mixed_settle
from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v1 as reset8


def install() -> None:
    predecessor = implementation
    predecessor["no_lease"].coordination = gate_v17
    predecessor["base"].c49.ASYNC_CONTRACT = (
        ROOT
        / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260827_v1"
        / "algorithm_c_measurement_adapter_contract.json"
    )
    original_configure = predecessor["resume"].configure_runtime

    def configure_runtime(*, freeze_path, workers, scene):
        original_configure(freeze_path=freeze_path, workers=workers, scene=scene)
        prepared = Path(str(predecessor["_active_authority_artifacts"]["prepared"]))
        reset8.install_for_scene(workers)
        reset8.configure(freeze=freeze_path, prepared=prepared)
        predecessor["base"].alg.reset = reset8
        predecessor["base"].alg.reset_xp = mixed_settle.reset_xp
        predecessor["base"].runtime.alg.reset_xp = mixed_settle.reset_xp

    predecessor["resume"].configure_runtime = configure_runtime


def main() -> int:
    original_validate = implementation["validate_launch_authority"]

    def validate_launch_authority(path, scene):
        authority = implementation["load"](path)
        implementation["_active_authority_artifacts"] = dict(
            authority.get("artifacts") or {}
        )
        return original_validate(path, scene)

    implementation["validate_launch_authority"] = validate_launch_authority
    install()
    return int(implementation["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
