#!/usr/bin/env python3
"""XP v50 controller: v49 plus safe-pool pending permutation binding."""

from __future__ import annotations

try:
    import resume_xp_fresh0_fixed_contract_target10_v49 as v49_controller
    import xp_v50_safe_pool_pending_permutation_patch as v50
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v49 as v49_controller
    from tools import xp_v50_safe_pool_pending_permutation_patch as v50


def main() -> int:
    v48 = v49_controller.v48_controller
    v50.install(v48.v47, v48.pending_trust)
    return v49_controller.main()


if __name__ == "__main__":
    raise SystemExit(main())

