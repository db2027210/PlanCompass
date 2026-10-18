#!/usr/bin/env python3
"""Attempt-specific entrypoint for the immutable XP v17 stage runner."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
loader = importlib.util.spec_from_file_location(
    "xp_stage_runner_core", HERE / "xp_v16_attempt_stage_runner.py"
)
core = importlib.util.module_from_spec(loader)
loader.loader.exec_module(core)

core.MATERIALS = Path(
    "/tmp/mysql-xp-v17-materials-final-20260804T1700Z/"
    "xp-formal-qual-20260804T084742Z-4b4c9a883ca3ca5231fd678f"
)
core.MATERIALS_MANIFEST_SHA256 = (
    "82200ab2549a43c86395e7435f0844c5da4aeea21eab88b02b9caa214e4ea24d"
)
core.SUPPORT_BUNDLE_ID = (
    "b8f365fba6a741006423ca6430aed2c36b6b7e4f6c7a6ca9332c54443dafeaf4"
)
core.SUPPORT_BUNDLE_MANIFEST_SHA256 = (
    "30be0966ea1abf0193231fc3b3a00f317e4ae7ead7dc591db24598b1f87f6f41"
)
core.ATTEMPT = "xp-formal-qual-20260804T084742Z-4b4c9a883ca3ca5231fd678f"
core.RUNNER_SCHEMA = "mysql_xp_v17_attempt_stage_runner_receipt_v1"


if __name__ == "__main__":
    try:
        raise SystemExit(core.main())
    except (core.GateError, OSError, core.subprocess.SubprocessError) as error:
        print(
            "FAIL_CLOSED_INVALIDATE_ATTEMPT_ZERO_CREDIT: %s" % error,
            file=sys.stderr,
        )
        raise SystemExit(1)
