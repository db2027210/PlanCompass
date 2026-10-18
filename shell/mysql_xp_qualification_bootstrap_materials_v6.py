#!/usr/bin/env python3
"""Versioned entrypoint for XP v8 systemd-219 qualification materials."""

from __future__ import annotations

import importlib.util
from pathlib import Path


IMPLEMENTATION = (
    Path(__file__).resolve().with_name(
        "mysql_xp_qualification_bootstrap_materials_v5.py"
    )
)
_SPEC = importlib.util.spec_from_file_location(
    "mysql_xp_qualification_bootstrap_materials_v6_impl",
    IMPLEMENTATION,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("XP v8 materializer implementation is unavailable")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

MaterialError = _MODULE.MaterialError
create_materials = _MODULE.create_materials
parse_systemd_run_help_fixture = _MODULE.parse_systemd_run_help_fixture


if __name__ == "__main__":
    try:
        _MODULE.main()
    except MaterialError as exc:
        raise SystemExit("NOT_READY: %s" % exc)
