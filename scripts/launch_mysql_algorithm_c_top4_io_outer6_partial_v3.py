#!/usr/bin/env python3
"""Launch IO-C using the completed companion measurement without rerun."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (
    ROOT / "scripts/launch_mysql_algorithm_c_top4_io_outer6_partial_v1.py"
).read_text(encoding="utf-8")
replacements = {
    'CHECKPOINT = HISTORY / "v250_to_c_top4_io_outer5_checkpoint_20260826/prepared_binding.json"':
        'COMPLETED = HISTORY / "v253_to_c_top4_io_outer6_partial_20260826_attempt022_xp_io"\nCHECKPOINT = HISTORY / "v250_to_c_top4_io_outer5_checkpoint_20260826/prepared_binding.json"',
    "frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v23":
        "frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v25",
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v16.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v18.py"',
    'RUN_ID = "v252_to_c_top4_io_outer6_partial_20260826_attempt021_xp_io"':
        'RUN_ID = "v256_to_c_top4_io_outer6_partial_20260826_attempt023_xp_io"',
    'HISTORY / "v252_to_c_top4_io_outer6_partial_20260826/launch.json"':
        'HISTORY / "v256_to_c_top4_io_outer6_partial_20260826/launch.json"',
    'UNIT = "mysql-algc-top4-io-v252.service"':
        'UNIT = "mysql-algc-top4-io-v256.service"',
    '"--formal-partial-root", str(SOURCE), "--formal-start-rank", "5",':
        '"--formal-partial-root", str(SOURCE), "--formal-start-rank", "5",\n        "--formal-completed-rank5-root", str(COMPLETED),',
    'and partial.get("failed_companion_wave_replayed") == [5]':
        'and partial.get("completed_companion_wave_reused") is True',
    '"failed_companion_wave_replayed": [5],':
        '"failed_companion_wave_replayed": [],\n                    "completed_companion_wave_reused": True,\n                    "candidate_measurements_replayed": [],',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"IO-C v25 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._io_c_outer6_partial_v3",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
