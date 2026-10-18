#!/usr/bin/env python3
"""Launch v19 IO C with corrected source bindings."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/launch_mysql_algorithm_c_top4_io_xp4_recovery_v12.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    "v197_to_c_top4_io_xp4_recovery_20260826":
        "v200_to_c_top4_io_xp4_recovery_20260826",
    "v197_to_c_top4_io_xp4_20260826_attempt016_xp_io":
        "v200_to_c_top4_io_xp4_20260826_attempt017_xp_io",
    "run_mysql_algorithm_c_async_scene_xp4_v11.py":
        "run_mysql_algorithm_c_async_scene_xp4_v12.py",
    "mysql-algc-top4-io-xp4-v197.service":
        "mysql-algc-top4-io-xp4-v200.service",
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"IO C v19 launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_c_top4_io_xp4_recovery_v13_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

