#!/usr/bin/env python3
"""Versioned entrypoint for the immutable XP systemd-219 v8 builder."""

from __future__ import annotations

import importlib.util
from pathlib import Path


IMPLEMENTATION = (
    Path(__file__).resolve().with_name(
        "build_mysql_xp_formal10_launch_v7.py"
    )
)
_SPEC = importlib.util.spec_from_file_location(
    "build_mysql_xp_formal10_launch_v8_impl", IMPLEMENTATION
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("XP v8 builder implementation is unavailable")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


if __name__ == "__main__":
    raise SystemExit(_MODULE.main())
