#!/usr/bin/env python3
"""Frozen entrypoint successor for XP8 B-to-A v4 handoff monitoring."""

import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_b_to_a_monitor_20260827_v5"
    / "handoff_contract.json"
)
SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v4.py"
)
text = SOURCE.read_text(encoding="utf-8")
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_b_then_a_mixed_eight_worker_v5_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("XP8 B-to-A handoff v5 contract differs")
    for raw in (value.get("source_bindings") or {}).values():
        path = ROOT / str(raw.get("path") or "")
        if not path.is_file() or path.is_symlink() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"XP8 B-to-A handoff v5 source differs: {path}")


if __name__ == "__main__":
    validate_contract()
    raise SystemExit(
        implementation["implementation"]["implementation"]["main"]()
    )
