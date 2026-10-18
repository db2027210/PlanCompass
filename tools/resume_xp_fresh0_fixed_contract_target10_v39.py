#!/usr/bin/env python3
"""XP v39: exact-point pending trust on top of v38 phase-aware state."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

try:
    import resume_xp_fresh0_fixed_contract_target10_v36 as v36
    import xp_v37_robust_state_patch as robust_state
    import xp_v38_phase_state_patch as phase_state
    import xp_v39_exact_point_pending_trust_patch as pending_trust
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v36 as v36
    from tools import xp_v37_robust_state_patch as robust_state
    from tools import xp_v38_phase_state_patch as phase_state
    from tools import xp_v39_exact_point_pending_trust_patch as pending_trust


def _bundle_arg(argv: list[str]) -> Path:
    try:
        return Path(argv[argv.index("--bundle-root") + 1]).resolve()
    except (ValueError, IndexError) as exc:
        raise RuntimeError("XP v39 requires --bundle-root") from exc


def main() -> int:
    bundle = _bundle_arg(sys.argv[1:])
    bundle_module = v36.install_bundle_fallback(bundle)
    phase_state.install_bundle(bundle_module)
    pending_trust.install_bundle(bundle_module)
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))
    adapter = importlib.import_module("xp_unattended_adapters")
    continuation = importlib.import_module("xp_unattended_continuation")
    robust_state.install(adapter)
    phase_state.install_adapter(adapter)
    pending_trust.install_adapter(adapter)
    pending_trust.install_phase_gate(phase_state)
    phase_state.install_controller(continuation)
    return v36.v35.main()


if __name__ == "__main__":
    raise SystemExit(main())
