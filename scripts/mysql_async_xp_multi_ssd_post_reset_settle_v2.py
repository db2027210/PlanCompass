#!/usr/bin/env python3
"""Scene-local XP settle on either disk1, disk2, or a mixed pair."""

from __future__ import annotations

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "mysql_async_xp_mixed_ssd_post_reset_settle_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = '''    if set(worker_disks.values()) != {"disk1", "disk2"}:
        raise RuntimeError("mixed-SSD reset must cover both frozen devices")'''
new = '''    if not set(worker_disks.values()).issubset({"disk1", "disk2"}):
        raise RuntimeError("multi-SSD reset worker device set differs")'''
if text.count(old) != 1:
    raise RuntimeError("multi-SSD settle predecessor differs")
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._mysql_async_xp_multi_ssd_settle_v2_impl",
    "__package__": "scripts",
}
exec(compile(text.replace(old, new), str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


__all__ = ["reset_xp"]
