#!/usr/bin/env python3
"""XP8 Algorithm-A v5 with effective mixed-SSD reset dispatch."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v3.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v3"
new = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v5"
if text.count(old) != 1:
    raise RuntimeError("XP8 A v5 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp8_v5_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)

from scripts import mysql_async_xp_mixed_ssd_post_reset_settle_v1 as mixed_settle
from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v2 as reset8


def main() -> int:
    # c49 installs single_measurement.measure_batch.  That adapter previously
    # overwrote alg.reset_xp with the disk2-only predecessor at call time.  Bind
    # its dispatch target as well as the outer runtime before any measurement.
    single_measurement.async_xp_settle = mixed_settle
    implementation["mixed_settle"] = mixed_settle
    implementation["reset8"] = reset8
    return int(implementation["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

