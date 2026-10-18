#!/usr/bin/env python3
"""Self-tests for the clean-terminal Algorithm C relay."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.wait_pg_algorithm_c_then_postterminal_abc_validation import (
    SCENES,
    clean_terminal,
    wait_for_clean_terminal,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def main() -> int:
    clean = {
        "phase": "finished",
        "returncodes": {scene: 0 for scene in SCENES},
        "deadline_signals": [],
    }
    assert clean_terminal(clean)
    assert not clean_terminal({**clean, "phase": "launched"})
    assert not clean_terminal({
        **clean,
        "returncodes": {**clean["returncodes"], "xp_io": 1},
    })
    assert not clean_terminal({**clean, "deadline_signals": ["xp_io"]})

    with tempfile.TemporaryDirectory(prefix="algorithm-c-terminal-relay-") as temporary:
        root = Path(temporary)
        formal = root / "formal"
        status = root / "relay.json"
        write_json(formal / "launcher_status.json", clean)
        observed = wait_for_clean_terminal(
            formal_root=formal,
            status_path=status,
            launcher_pid=None,
            poll_s=1.0,
            maximum_wait_s=1.0,
        )
        assert observed == clean
        receipt = json.loads(status.read_text(encoding="utf-8"))
        assert receipt["phase"] == "algorithm_c_clean_terminal_observed"
        assert receipt["a_b_authority_loaded_during_wait"] is False

    print("wait_pg_algorithm_c_then_postterminal_abc_validation: ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
