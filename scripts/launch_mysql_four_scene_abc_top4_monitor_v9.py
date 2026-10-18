#!/usr/bin/env python3
"""Start the v264 current Top4 ABC pipeline monitor."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_four_scene_abc_top4_monitor_v8.py").read_text(
    encoding="utf-8"
)
replacements = {
    "v260_to_abc_top4_pipeline_20260826/pipeline_state.json":
        "v264_to_abc_top4_pipeline_20260826/pipeline_state.json",
    "monitor_mysql_four_scene_abc_pipeline_v8.py":
        "monitor_mysql_four_scene_abc_pipeline_v9.py",
    "mysql-abc-top4-v260-monitor.service":
        "mysql-abc-top4-v264-monitor.service",
    "v260_abc_top4_monitor.stdout.log": "v264_abc_top4_monitor.stdout.log",
    "v260_abc_top4_monitor.stderr.log": "v264_abc_top4_monitor.stderr.log",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"ABC monitor v9 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._abc_monitor_v9_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))
