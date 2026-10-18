#!/usr/bin/env python3
"""Run the frozen XP measurement wrapper through the setlocal shadow launcher."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def main() -> int:
    wrapper = Path(os.environ["HYXP_FROZEN_MEASUREMENT_WRAPPER"]).resolve()
    shadow_root = Path(os.environ["HYXP_SETLOCAL_SHADOW_ROOT"]).resolve()
    spec = importlib.util.spec_from_file_location("hyxp_frozen_measurement", wrapper)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen measurement wrapper: {wrapper}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HYBENCH_ROOT = shadow_root
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
