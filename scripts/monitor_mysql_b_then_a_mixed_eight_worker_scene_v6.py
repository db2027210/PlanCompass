#!/usr/bin/env python3
"""Monitor XP8 B and independently launch Algorithm-A v5."""

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_b_to_a_monitor_20260827_v6"
    / "handoff_contract.json"
)
SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "scripts/launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v1.py"
new = "scripts/launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v5.py"
if text.count(old) != 1:
    raise RuntimeError("XP8 B-to-A monitor v6 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_b_then_a_mixed_eight_worker_v6_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("XP8 B-to-A handoff v6 contract differs")
    for raw in (value.get("source_bindings") or {}).values():
        path = ROOT / str(raw.get("path") or "")
        if not path.is_file() or path.is_symlink() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"XP8 B-to-A handoff v6 source differs: {path}")


if __name__ == "__main__":
    validate_contract()
    raise SystemExit(implementation["implementation"]["main"]())
