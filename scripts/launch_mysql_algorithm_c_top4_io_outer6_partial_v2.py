#!/usr/bin/env python3
"""Launch the audit-shape successor of the IO-C Outer-6 partial run."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (
    ROOT / "scripts/launch_mysql_algorithm_c_top4_io_outer6_partial_v1.py"
).read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v23":
        "frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v24",
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v16.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v17.py"',
    'RUN_ID = "v252_to_c_top4_io_outer6_partial_20260826_attempt021_xp_io"':
        'RUN_ID = "v253_to_c_top4_io_outer6_partial_20260826_attempt022_xp_io"',
    'HISTORY / "v252_to_c_top4_io_outer6_partial_20260826/launch.json"':
        'HISTORY / "v253_to_c_top4_io_outer6_partial_20260826/launch.json"',
    'UNIT = "mysql-algc-top4-io-v252.service"':
        'UNIT = "mysql-algc-top4-io-v253.service"',
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"IO-C v24 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._io_c_outer6_partial_v2",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
