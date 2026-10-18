#!/usr/bin/env python3
"""Launch all four v92 Algorithm-C scenes, two workers per scene."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts/launch_mysql_algorithm_c_client_primary_four_scene_v1.py"
raw = SOURCE.read_text(encoding="utf-8")
if (
    raw.count("v91-client-primary") != 1
    or raw.count("frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v91") != 1
    or raw.count("run_mysql_algorithm_c_four_scene_client_primary_v2.py") != 1
):
    raise RuntimeError("Algorithm C v91 launcher source differs")
raw = raw.replace("v91-client-primary", "v92-client-primary")
raw = raw.replace(
    "frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v91",
    "frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v92",
)
raw = raw.replace(
    "run_mysql_algorithm_c_four_scene_client_primary_v2.py",
    "run_mysql_algorithm_c_four_scene_client_primary_v3.py",
)
_impl = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_c_client_primary_v92_impl",
    "__package__": "scripts",
}
exec(compile(raw, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
