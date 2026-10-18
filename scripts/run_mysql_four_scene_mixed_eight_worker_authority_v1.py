#!/usr/bin/env python3
"""Point authority for two independent eight-worker XP scenes."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts/run_mysql_four_scene_r0_v5.py"
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    'or len(prepared.get("workers") or []) != 8':
        'or len(prepared.get("workers") or []) != 16',
    'or len(accounts.get("workers") or []) != 8':
        'or len(accounts.get("workers") or []) != 16',
    'AUTHORITY_SCENE_SCOPE is not None and len(authority_worker_ids) != 2':
        'AUTHORITY_SCENE_SCOPE is not None and len(authority_worker_ids) != 8',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"eight-worker authority predecessor differs: {old}")
    text = text.replace(old, new)

_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_mixed_eight_worker_authority_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), _impl)
for name, value in _impl.items():
    if not name.startswith("__"):
        globals()[name] = value


def configure(*, freeze: Path, freeze_schema: str, authority_root: Path, scene: str) -> None:
    required = {
        "preflight": authority_root / "transition_preflight.json",
        "prepared": authority_root / "prepared_binding.json",
        "accounts": authority_root / "benchmark_accounts_binding.json",
    }
    if any(not path.is_file() or path.is_symlink() for path in required.values()):
        raise RuntimeError("mixed eight-worker authority artifact is absent")
    values = {
        "FREEZE": freeze, "FREEZE_SCHEMA": freeze_schema,
        "PREFLIGHT": required["preflight"],
        "RUNTIME_PREFLIGHT": required["preflight"],
        "PREPARED": required["prepared"],
        "BENCHMARK_ACCOUNTS": required["accounts"],
        "AUTHORITY_SCENE_SCOPE": scene,
    }
    _impl.update(values)
    globals().update(values)


if __name__ == "__main__":
    raise SystemExit(_impl["main"]())
