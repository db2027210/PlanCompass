#!/usr/bin/env python3
"""XP v49 controller: v48 plus the fresh-selection metadata bridge."""

from __future__ import annotations

import importlib
import sys

try:
    import resume_xp_fresh0_fixed_contract_target10_v48 as v48_controller
    import xp_v49_fresh_selection_metadata_patch as v49
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v48 as v48_controller
    from tools import xp_v49_fresh_selection_metadata_patch as v49


def main() -> int:
    bundle = v48_controller.v46_controller._bundle_arg(sys.argv[1:])
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))

    # Install v48 exactly as sealed, then add only the narrow metadata bridge.
    base_bundle_module = importlib.import_module("xp_round10_diagnostic_bundle")
    v48_controller.v46.install_base_builder_fix(base_bundle_module)
    v48_controller.v48.install_v46_probe_acceptance(v48_controller.v46)
    bundle_module = v48_controller.v36.install_bundle_fallback(bundle)
    v48_controller.phase_state.install_bundle(bundle_module)
    v48_controller.v41.install_pending_bundle(
        bundle_module, v48_controller.pending_trust
    )
    v48_controller.v42.install_bundle(bundle_module)
    v48_controller.v46.install_bundle(bundle_module)
    explain_module = importlib.import_module("xp_round10_explain_only_entrypoint")
    v48_controller.v41.install_pool_resume(explain_module)

    adapter = importlib.import_module("xp_unattended_adapters")
    continuation = importlib.import_module("xp_unattended_continuation")
    v48_controller.infeasible_reroute.install_adapter(adapter)
    v48_controller.robust_state.install(adapter)
    v48_controller.phase_state.install_adapter(adapter)
    v48_controller.pending_trust.install_adapter(adapter)
    v48_controller.v44.install_adapter(adapter, v48_controller.pending_trust)
    v48_controller.v45.install_adapter(adapter)
    v49.install_adapter(adapter)
    v48_controller.pending_trust.install_phase_gate(v48_controller.phase_state)
    v48_controller.v42.install_phase_gate(v48_controller.phase_state)
    v48_controller.v43.install_phase_gate(
        v48_controller.phase_state, v48_controller.pending_trust
    )
    v48_controller.v44.install_phase_gate(v48_controller.phase_state)
    v48_controller.v46.install_phase_gate(v48_controller.phase_state)
    v48_controller.v47.install_phase_gate(
        v48_controller.phase_state, v48_controller.pending_trust
    )
    v48_controller.phase_state.install_controller(continuation)
    return v48_controller.v36.v35.main()


if __name__ == "__main__":
    raise SystemExit(main())

