#!/usr/bin/env python3
"""XP v46: v45 recovery plus safe-novel-pool selector routing."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

try:
    import resume_xp_fresh0_fixed_contract_target10_v36 as v36
    import xp_v37_robust_state_patch as robust_state
    import xp_v38_phase_state_patch as phase_state
    import xp_v39_exact_point_pending_trust_patch as pending_trust
    import xp_v40_formal_infeasible_reexploration_patch as infeasible_reroute
    import xp_v41_excluded_ids_sealed_pool_resume_patch as v41
    import xp_v42_automatic_transition_family_patch as v42
    import xp_v43_pending_lineage_gate_patch as v43
    import xp_v44_pending_post_replay_patch as v44
    import xp_v45_recovery_compat_patch as v45
    import xp_v46_safe_novel_pool_selector_patch as v46
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v36 as v36
    from tools import xp_v37_robust_state_patch as robust_state
    from tools import xp_v38_phase_state_patch as phase_state
    from tools import xp_v39_exact_point_pending_trust_patch as pending_trust
    from tools import xp_v40_formal_infeasible_reexploration_patch as infeasible_reroute
    from tools import xp_v41_excluded_ids_sealed_pool_resume_patch as v41
    from tools import xp_v42_automatic_transition_family_patch as v42
    from tools import xp_v43_pending_lineage_gate_patch as v43
    from tools import xp_v44_pending_post_replay_patch as v44
    from tools import xp_v45_recovery_compat_patch as v45
    from tools import xp_v46_safe_novel_pool_selector_patch as v46


def _bundle_arg(argv: list[str]) -> Path:
    try:
        return Path(argv[argv.index("--bundle-root") + 1]).resolve()
    except (ValueError, IndexError) as exc:
        raise RuntimeError("XP v46 requires --bundle-root") from exc


def main() -> int:
    bundle = _bundle_arg(sys.argv[1:])
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))
    base_bundle_module = importlib.import_module("xp_round10_diagnostic_bundle")
    v46.install_base_builder_fix(base_bundle_module)
    bundle_module = v36.install_bundle_fallback(bundle)
    phase_state.install_bundle(bundle_module)
    v41.install_pending_bundle(bundle_module, pending_trust)
    v42.install_bundle(bundle_module)
    v46.install_bundle(bundle_module)
    explain_module = importlib.import_module("xp_round10_explain_only_entrypoint")
    v41.install_pool_resume(explain_module)
    adapter = importlib.import_module("xp_unattended_adapters")
    continuation = importlib.import_module("xp_unattended_continuation")
    infeasible_reroute.install_adapter(adapter)
    robust_state.install(adapter)
    phase_state.install_adapter(adapter)
    pending_trust.install_adapter(adapter)
    v44.install_adapter(adapter, pending_trust)
    v45.install_adapter(adapter)
    pending_trust.install_phase_gate(phase_state)
    v42.install_phase_gate(phase_state)
    v43.install_phase_gate(phase_state, pending_trust)
    v44.install_phase_gate(phase_state)
    v46.install_phase_gate(phase_state)
    phase_state.install_controller(continuation)
    return v36.v35.main()


if __name__ == "__main__":
    raise SystemExit(main())
