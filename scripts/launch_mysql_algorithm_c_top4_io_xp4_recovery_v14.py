#!/usr/bin/env python3
"""Launch v20 IO C with unified v7 gating."""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "scripts/launch_mysql_algorithm_c_top4_io_xp4_recovery_v13.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
replacements = {
    "v200_to_c_top4_io_xp4_recovery_20260826":
        "v214_to_c_top4_io_xp4_recovery_20260826",
    "v200_to_c_top4_io_xp4_20260826_attempt017_xp_io":
        "v214_to_c_top4_io_xp4_20260826_attempt018_xp_io",
    "run_mysql_algorithm_c_async_scene_xp4_v12.py":
        "run_mysql_algorithm_c_async_scene_xp4_v13.py",
    "mysql-algc-top4-io-xp4-v200.service":
        "mysql-algc-top4-io-xp4-v214.service",
}
for old, new in replacements.items():
    if _source.count(old) != 1:
        raise RuntimeError(f"IO C v20 launcher predecessor fragment differs: {old}")
    _source = _source.replace(old, new)
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_c_top4_io_xp4_recovery_v14_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)


def main() -> int:
    return int(_impl["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

