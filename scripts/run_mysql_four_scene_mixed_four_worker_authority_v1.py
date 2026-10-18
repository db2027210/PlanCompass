#!/usr/bin/env python3
"""Four-scene point authority for the mixed-SSD four-worker successor.

The established v5 authority remains the measurement implementation.  This
successor changes only the frozen cardinality from 8/two-per-scene to
16/four-per-scene.  Runtime paths and live identities continue to come from
the sealed authority documents supplied by the caller.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PREDECESSOR = ROOT / "scripts/run_mysql_four_scene_r0_v5.py"

_source = PREDECESSOR.read_text(encoding="utf-8")
_replacements = {
    "or len(prepared.get(\"workers\") or []) != 8":
        "or len(prepared.get(\"workers\") or []) != 16",
    "or len(accounts.get(\"workers\") or []) != 8":
        "or len(accounts.get(\"workers\") or []) != 16",
    "AUTHORITY_SCENE_SCOPE is not None and len(authority_worker_ids) != 2":
        "AUTHORITY_SCENE_SCOPE is not None and len(authority_worker_ids) != 4",
}
for _old, _new in _replacements.items():
    if _source.count(_old) != 1:
        raise RuntimeError(f"mixed four-worker authority predecessor differs: {_old}")
    _source = _source.replace(_old, _new)

_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_four_scene_mixed_four_worker_authority_v1_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)

for _name, _value in _impl.items():
    if not _name.startswith("__"):
        globals()[_name] = _value


def configure(
    *, freeze: Path, freeze_schema: str, authority_root: Path,
    scene: str,
) -> None:
    required = {
        "preflight": authority_root / "transition_preflight.json",
        "prepared": authority_root / "prepared_binding.json",
        "accounts": authority_root / "benchmark_accounts_binding.json",
    }
    if any(not path.is_file() or path.is_symlink() for path in required.values()):
        raise RuntimeError("mixed four-worker authority artifact is absent")
    _impl["FREEZE"] = freeze
    _impl["FREEZE_SCHEMA"] = freeze_schema
    _impl["PREFLIGHT"] = required["preflight"]
    _impl["RUNTIME_PREFLIGHT"] = required["preflight"]
    _impl["PREPARED"] = required["prepared"]
    _impl["BENCHMARK_ACCOUNTS"] = required["accounts"]
    _impl["AUTHORITY_SCENE_SCOPE"] = scene
    globals().update({
        "FREEZE": freeze,
        "FREEZE_SCHEMA": freeze_schema,
        "PREFLIGHT": required["preflight"],
        "RUNTIME_PREFLIGHT": required["preflight"],
        "PREPARED": required["prepared"],
        "BENCHMARK_ACCOUNTS": required["accounts"],
        "AUTHORITY_SCENE_SCOPE": scene,
    })


if __name__ == "__main__":
    raise SystemExit(_impl["main"]())
