#!/usr/bin/env python3
"""Adopt/read back disk1 w3/w4 against the client-affinity v4 freeze."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SOURCE = ROOT / "scripts/adopt_mysql_mixed_disk_scene_w34_v1.py"
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3/":
        "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4/",
    "94c5ff345a0b2f096e07e1d5fb19d13636a0a93c019835a1628fe814aad41063":
        "37e8944d65d3c0358d99f716a0bafd39c51e4f98cd4044df280c34f1c80decf0",
    "mysql_four_scene_mixed_disk_four_worker_successor_v3":
        "mysql_four_scene_mixed_disk_four_worker_successor_v4",
    "mixed-disk v3 freeze differs": "mixed-disk v4 freeze differs",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"w34 v4 adoption predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._adopt_mysql_mixed_disk_scene_w34_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
