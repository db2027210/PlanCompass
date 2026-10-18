#!/usr/bin/env python3
"""XP v51 controller: v50 plus lossless formal promotion routing."""

from __future__ import annotations

import importlib
import sys

try:
    import resume_xp_fresh0_fixed_contract_target10_v49 as v49_controller
    import xp_v50_safe_pool_pending_permutation_patch as v50
    import xp_v51_formal_promotion_routing_patch as v51
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v49 as v49_controller
    from tools import xp_v50_safe_pool_pending_permutation_patch as v50
    from tools import xp_v51_formal_promotion_routing_patch as v51


def main() -> int:
    v48 = v49_controller.v48_controller
    bundle = v48.v46_controller._bundle_arg(sys.argv[1:])
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))

    v50.install(v48.v47, v48.pending_trust)
    base_bundle_module = importlib.import_module("xp_round10_diagnostic_bundle")
    v48.v46.install_base_builder_fix(base_bundle_module)
    v48.v48.install_v46_probe_acceptance(v48.v46)
    bundle_module = v48.v36.install_bundle_fallback(bundle)
    v48.phase_state.install_bundle(bundle_module)
    v48.v41.install_pending_bundle(bundle_module, v48.pending_trust)
    v48.v42.install_bundle(bundle_module)
    v48.v46.install_bundle(bundle_module)
    explain_module = importlib.import_module("xp_round10_explain_only_entrypoint")
    v48.v41.install_pool_resume(explain_module)

    adapter = importlib.import_module("xp_unattended_adapters")
    continuation = importlib.import_module("xp_unattended_continuation")
    v48.infeasible_reroute.install_adapter(adapter)
    v48.robust_state.install(adapter)
    v48.phase_state.install_adapter(adapter)
    v48.pending_trust.install_adapter(adapter)
    # Install immediately around v39: later v44/v45 wrappers must observe the
    # corrected formal object, not sit inside the bypassed closure.
    v51.install_adapter(adapter, v48.pending_trust, v48.robust_state)
    v48.v44.install_adapter(adapter, v48.pending_trust)
    v48.v45.install_adapter(adapter)
    v49_controller.v49.install_adapter(adapter)

    v48.pending_trust.install_phase_gate(v48.phase_state)
    v48.v42.install_phase_gate(v48.phase_state)
    v48.v43.install_phase_gate(v48.phase_state, v48.pending_trust)
    v48.v44.install_phase_gate(v48.phase_state)
    v48.v46.install_phase_gate(v48.phase_state)
    v48.v47.install_phase_gate(v48.phase_state, v48.pending_trust)
    v51.install_phase_gate(v48.phase_state, v48.robust_state)
    v48.phase_state.install_controller(continuation)
    return v48.v36.v35.main()


if __name__ == "__main__":
    raise SystemExit(main())
