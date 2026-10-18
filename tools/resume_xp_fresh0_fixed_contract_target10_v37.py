#!/usr/bin/env python3
"""XP v37: v36 discovery with robust resource/score-aligned formal state."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

try:
    import resume_xp_fresh0_fixed_contract_target10_v36 as v36
    import xp_v37_robust_state_patch as robust_state
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v36 as v36
    from tools import xp_v37_robust_state_patch as robust_state


def _bundle_arg(argv: list[str]) -> Path:
    try:
        return Path(argv[argv.index("--bundle-root") + 1]).resolve()
    except (ValueError, IndexError) as exc:
        raise RuntimeError("XP v37 requires --bundle-root") from exc


def main() -> int:
    bundle = _bundle_arg(sys.argv[1:])
    v36.install_bundle_fallback(bundle)
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))
    adapter = importlib.import_module("xp_unattended_adapters")
    robust_state.install(adapter)
    return v36.v35.main()


if __name__ == "__main__":
    raise SystemExit(main())
