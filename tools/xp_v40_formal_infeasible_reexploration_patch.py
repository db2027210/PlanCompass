#!/usr/bin/env python3
"""Route an infeasible XP formal promotion back to deterministic EXPLAIN.

The sealed two-observation acceptance bound is intentionally fail closed: if
no trusted point can beat the incumbent, a third replay cannot make the
three-observation median acceptable.  The v33 adapter correctly computes that
bound, but then terminates before reaching its existing deterministic novel
EXPLAIN discovery path.  This patch removes only that terminal transition.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "xp_v40_formal_infeasible_routes_to_novel_explain_v1"
EXPECTED_ADAPTER_SHA256 = (
    "7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a"
)
EXPECTED_GENERATE_SOURCE_SHA256 = (
    "788ac4b68aa061f711c3e25f67f1105354420ef4a0b1312439068c5114942636"
)
TERMINAL_BLOCK = '''        raise ContinuationBlocked(
            "formal_acceptance_infeasible_no_fresh_lcb_above_incumbent"
        )'''
FALLTHROUGH_BLOCK = '''        formal_infeasible_routes_to_novel_explain = True'''


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def replace_terminal_block(source: str) -> str:
    """Replace exactly one terminal block; useful for isolated state tests."""

    if source.count(TERMINAL_BLOCK) != 1:
        raise RuntimeError("XP v40 terminal formal-infeasible block mismatch")
    transformed = source.replace(TERMINAL_BLOCK, FALLTHROUGH_BLOCK, 1)
    if TERMINAL_BLOCK in transformed or transformed.count(FALLTHROUGH_BLOCK) != 1:
        raise RuntimeError("XP v40 source transformation postcondition failed")
    return transformed


def transformed_generate_source(adapter: Any) -> str:
    """Return the pinned one-block source transformation or fail closed."""

    source_path = Path(str(adapter.__file__ or "")).resolve()
    if not source_path.is_file() or _sha256(source_path) != EXPECTED_ADAPTER_SHA256:
        raise RuntimeError("XP v40 adapter module SHA mismatch")
    source = inspect.getsource(adapter.generate_selection)
    if _sha256_bytes(source.encode("utf-8")) != EXPECTED_GENERATE_SOURCE_SHA256:
        raise RuntimeError("XP v40 generate_selection source SHA mismatch")
    transformed = replace_terminal_block(source)
    if (
        transformed.count("repeat_after_probe5 =") != 1
    ):
        raise RuntimeError("XP v40 source transformation postcondition failed")
    return transformed


def install_adapter(adapter: Any) -> Any:
    """Install the exact fail-closed-to-reexploration state transition."""

    transformed = transformed_generate_source(adapter)
    namespace = adapter.__dict__
    exec(
        compile(transformed, "<xp_v40_formal_infeasible_reexploration>", "exec"),
        namespace,
    )
    generated = namespace["generate_selection"]

    def generate_selection(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        selection = copy.deepcopy(dict(generated(*args, **kwargs)))
        batch_dir_value = kwargs.get("batch_dir")
        if batch_dir_value is None and len(args) >= 4:
            batch_dir_value = args[3]
        batch_dir = Path(str(batch_dir_value or ""))
        reroute = batch_dir / "formal_acceptance_infeasible_reroute.json"
        if reroute.is_file():
            audit = json.loads(reroute.read_text(encoding="utf-8"))
            feasibility = audit.get("acceptance_feasibility_gate") or {}
            if (
                audit.get("reason")
                != "no_trusted_candidate_can_beat_incumbent_after_one_additional_observation"
                or feasibility.get("passed") is not False
                or feasibility.get("feasible_trusted_ranks") != []
                or audit.get("state_mutation") is not False
                or int(audit.get("new_workload_calls", -1)) != 0
                or selection.get("batch_mode") != "diagnostic_validation"
            ):
                raise RuntimeError("XP v40 formal-infeasible reroute audit mismatch")
            selection["v40_formal_infeasible_reexploration"] = {
                "contract": CONTRACT,
                "source_audit": str(reroute.resolve()),
                "source_audit_sha256": _sha256(reroute),
                "candidate_generation": (
                    "existing_deterministic_explain_three_level_pipeline"
                ),
                "manual_candidate_injection": False,
                "formal_replay_skipped_as_mathematically_infeasible": True,
                "formal_state_mutation": False,
                "workload_calls_before_new_selection": 0,
            }
        return selection

    generate_selection.__name__ = "generate_selection"
    generate_selection.__doc__ = generated.__doc__
    adapter.generate_selection = generate_selection
    adapter.XP_V40_FORMAL_INFEASIBLE_REEXPLORATION_CONTRACT = CONTRACT
    return adapter
