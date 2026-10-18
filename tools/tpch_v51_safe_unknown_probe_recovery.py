#!/usr/bin/env python3
"""TPC-H v51 safe unknown-probe stability-evidence recovery controller."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any


REMOTE_V50 = Path("/tmp/tpch_v50_trusted_evidence_acquisition.py")
LOCAL_V50 = Path(__file__).with_name("tpch_v50_trusted_evidence_acquisition.py")
EXPECTED_V50_SHA256 = (
    "ffb8caadb0ee9738e31c0cff081e623ec3ccea7a09c824613a138183540f59fd"
)
CONTRACT = "tpch_v51_safe_unknown_probe_stability_recovery_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_v50() -> Any:
    path = REMOTE_V50 if REMOTE_V50.is_file() else LOCAL_V50
    if not path.is_file() or sha256(path) != EXPECTED_V50_SHA256:
        raise RuntimeError("v51 base v50 controller SHA mismatch")
    specification = importlib.util.spec_from_file_location("tpch_v51_v50", path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import v50 controller: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def main() -> int:
    v50 = import_v50()
    v50.CONTRACT = CONTRACT
    return v50.main()


if __name__ == "__main__":
    raise SystemExit(main())
