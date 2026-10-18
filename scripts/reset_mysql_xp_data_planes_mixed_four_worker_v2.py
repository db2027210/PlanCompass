#!/usr/bin/env python3
"""Mixed-SSD XP reset with run-scoped, collision-free quarantine targets."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREDECESSOR = ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v1.py"
_source = PREDECESSOR.read_text(encoding="utf-8")
_marker = "_source = _source.replace(_old_disk_guard, _new_disk_guard)\n"
if _source.count(_marker) != 1:
    raise RuntimeError("mixed reset v2 predecessor marker differs")
_insertion = _marker + '''_old_quarantine_target = """    old_target = (\n        Path(str(storage[\"runtime_root\"]))\n        / \"quarantine\" / quarantine_root.name / worker_id\n    )"""\n_new_quarantine_target = """    old_target = (\n        Path(str(storage[\"runtime_root\"]))\n        / \"quarantine\" / quarantine_root.parent.name\n        / quarantine_root.name / worker_id\n    )"""\nif _source.count(_old_quarantine_target) != 1:\n    raise RuntimeError(\"mixed reset v2 quarantine predecessor differs\")\n_source = _source.replace(_old_quarantine_target, _new_quarantine_target)\n'''
_source = _source.replace(_marker, _insertion)
_implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._reset_mysql_xp_data_planes_mixed_four_worker_v2_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _implementation)
_implementation["AUTHORITY_VERSION"] = "xp_mixed_four_worker_v2_run_scoped_quarantine"
_implementation["_impl"]["AUTHORITY_VERSION"] = _implementation["AUTHORITY_VERSION"]
for _name, _value in _implementation.items():
    if not _name.startswith("__"):
        globals()[_name] = _value
