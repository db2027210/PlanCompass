#!/usr/bin/env python3
"""TPC-C v38 correctness and no-duplicate replay scheduling policy."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from tpcc_round11_current_correctness_policy import (
        classify_transaction_abort_gate,
    )
except ModuleNotFoundError:
    from tools.tpcc_round11_current_correctness_policy import (
        classify_transaction_abort_gate,
    )


CORRECTNESS_POLICY_SHA256 = (
    "e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7"
)


def invoked_bindings(
    successful: Sequence[Mapping[str, Any]],
    technical_failures: Sequence[Mapping[str, Any]],
    audited_noncounting: Sequence[Mapping[str, Any]],
) -> set[Tuple[int, int]]:
    return {
        (int(item["rank"]), int(item["attempt"]))
        for item in [*successful, *technical_failures, *audited_noncounting]
    }


def invoked_ranks_without_retry(
    successful: Sequence[Mapping[str, Any]],
    technical_failures: Sequence[Mapping[str, Any]],
    audited_noncounting: Sequence[Mapping[str, Any]],
) -> set[int]:
    """Any physical attempt permanently consumes that rank in this batch."""
    return {
        rank
        for rank, _attempt in invoked_bindings(
            successful,
            technical_failures,
            audited_noncounting,
        )
    }


def select_uninvoked_unique_candidates(
    candidates: Iterable[Dict[str, Any]],
    *,
    successful: Sequence[Mapping[str, Any]],
    technical_failures: Sequence[Mapping[str, Any]],
    audited_noncounting: Sequence[Mapping[str, Any]],
    remaining_physical_budget: int,
) -> List[Dict[str, Any]]:
    """Select each rank at most once; technical calls are never retried."""
    if remaining_physical_budget < 0:
        raise RuntimeError("remaining physical budget is negative")
    invoked = invoked_ranks_without_retry(
        successful,
        technical_failures,
        audited_noncounting,
    )
    available = sorted(
        (dict(candidate) for candidate in candidates if int(candidate["rank"]) not in invoked),
        key=lambda candidate: int(candidate["rank"]),
    )
    return available[:remaining_physical_budget]


__all__ = [
    "CORRECTNESS_POLICY_SHA256",
    "classify_transaction_abort_gate",
    "invoked_bindings",
    "invoked_ranks_without_retry",
    "select_uninvoked_unique_candidates",
]
