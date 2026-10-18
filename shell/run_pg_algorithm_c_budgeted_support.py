#!/usr/bin/env python3
"""Keep the four-scene workload domain while budgeted lanes finish at different times."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import run_pg_algorithm_c_cohort_support_supervisor as support


BUDGETED_CLEAN_TERMINAL_PHASES = frozenset(
    {
        # Historical pilot prefix retained for old campaign compatibility.
        "budgeted_trial_finished_clean",
        # Current formal execution role used by prepare_pg_algorithm_c_budgeted_formal.py.
        "budgeted_formal_finished_clean",
    }
)


def configure_budgeted_support() -> None:
    """Teach the generic support supervisor every budgeted clean terminal.

    A lane may terminate without convergence (for example after exhausting a
    C-only recovery frontier).  It must still be replaced by workload-only
    cohort support while the other formal lanes continue.  Otherwise the next
    global wave waits forever for the exited lane and the execution domain
    silently changes from four lanes to three.
    """
    support.CLEAN_TERMINAL_PHASES = (
        support.CLEAN_TERMINAL_PHASES | BUDGETED_CLEAN_TERMINAL_PHASES
    )
    support.CONTROLLER_SCRIPT_NAME = "run_pg_algorithm_c_budgeted_scene.py"


if __name__ == "__main__":
    configure_budgeted_support()
    raise SystemExit(support.main())
