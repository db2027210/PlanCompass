#!/usr/bin/env python3
"""XP8 Algorithm-A v7 with unlocked eight-way scene-local reset control."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v6.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v6"
new = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v7"
if text.count(old) != 1:
    raise RuntimeError("XP8 A v7 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp8_v7_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v3 as reset8_v3


def _replace_reset_dispatch(value: Any, seen: set[int] | None = None) -> None:
    seen = seen or set()
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        if "reset8" in value:
            value["reset8"] = reset8_v3
        for child in value.values():
            if isinstance(child, dict):
                _replace_reset_dispatch(child, seen)


_replace_reset_dispatch(implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

