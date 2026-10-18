#!/usr/bin/env python3
"""Resume Algorithm C with refreshed XP liveness during bounded drain."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v18 as v18


alg = v18.v17.v16.v15.v13.v11.v10.v4.runtime.alg
ORIGINAL_STATE_DOCUMENT = v18.state_document


def wait_for_xp_session_drain(
    workers: Mapping[str, Mapping[str, Any]], *, timeout_seconds: float = 60.0,
    poll_seconds: float = 1.0,
    liveness: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Refresh exact mysqld liveness after every failed SQL drain probe.

    A timed-out HyBench JVM can leave mysqld alive but unresponsive briefly;
    mysqld may then become a zombie under the still-running container init.
    The predecessor is discard-only, but bypassing SQL is allowed only after
    the existing exact two-inspect/process-table proof observes no live
    mysqld.  Active SQL sessions and unproven liveness remain fail-closed.
    """
    if timeout_seconds <= 0.0 or poll_seconds <= 0.0:
        raise ValueError("XP session-drain bounds must be positive")
    started = time.monotonic()
    deadline = started + timeout_seconds
    polls: list[dict[str, Any]] = []
    available_ids = tuple(
        worker_id for worker_id in alg.XP_IDS if worker_id in workers
    )
    if not available_ids:
        raise RuntimeError("XP session drain received no XP workers")
    liveness_rows = {
        worker_id: dict((liveness or {}).get(worker_id) or {})
        for worker_id in available_ids
    }
    provably_dead = {
        worker_id for worker_id, row in liveness_rows.items()
        if row.get("safe_to_discard_unresponsive_predecessor") is True
        and row.get("status") == "PROVEN_NO_LIVE_MYSQLD"
    }
    refresh_count = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            last_poll = polls[-1] if polls else {}
            raise RuntimeError(
                "XP active client sessions were not proven drained before "
                f"the {timeout_seconds:g}s bound: {last_poll}"
            )
        probe_timeout = min(15.0, remaining)
        counts: dict[str, int | None] = {}
        probe_errors: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(
            max_workers=len(available_ids), thread_name_prefix="xp-session-drain-v19",
        ) as pool:
            futures = {
                pool.submit(
                    alg.xp_active_client_session_count,
                    workers[worker_id],
                    timeout_seconds=probe_timeout,
                ): worker_id
                for worker_id in available_ids
                if worker_id not in provably_dead
            }
            for future in as_completed(futures):
                worker_id = futures[future]
                try:
                    counts[worker_id] = future.result()
                except subprocess.TimeoutExpired as error:
                    counts[worker_id] = None
                    probe_errors[worker_id] = {
                        "type": type(error).__name__,
                        "timeout_seconds": error.timeout,
                    }
                except subprocess.CalledProcessError as error:
                    counts[worker_id] = None
                    probe_errors[worker_id] = {
                        "type": type(error).__name__,
                        "returncode": error.returncode,
                    }

        refreshed: dict[str, dict[str, Any]] = {}
        for worker_id in sorted(probe_errors):
            row = alg.classify_xp_mysql_liveness(workers[worker_id])
            refresh_count += 1
            refreshed[worker_id] = copy.deepcopy(row)
            liveness_rows[worker_id] = dict(row)
            if (
                row.get("safe_to_discard_unresponsive_predecessor") is True
                and row.get("status") == "PROVEN_NO_LIVE_MYSQLD"
            ):
                provably_dead.add(worker_id)
                counts[worker_id] = 0

        counts = {
            worker_id: 0 if worker_id in provably_dead else counts.get(worker_id)
            for worker_id in available_ids
        }
        unresolved_errors = {
            worker_id: row for worker_id, row in probe_errors.items()
            if worker_id not in provably_dead
        }
        poll: dict[str, Any] = {
            "observed_at": alg.now(),
            "active_sessions": counts,
            "provably_no_live_mysqld_workers": sorted(provably_dead),
        }
        if unresolved_errors:
            poll["probe_errors"] = unresolved_errors
        if refreshed:
            poll["liveness_refresh"] = refreshed
        polls.append(poll)
        if all(counts[worker_id] == 0 for worker_id in available_ids):
            return {
                "status": "PASS",
                "release_version": "refreshed_liveness_v19",
                "timeout_seconds": timeout_seconds,
                "poll_seconds": poll_seconds,
                "probe_concurrency": len(available_ids),
                "per_probe_timeout_cap_seconds": 15.0,
                "all_live_workers_explicit_zero_required": True,
                "provably_no_live_mysqld_workers": sorted(provably_dead),
                "liveness_refresh_count": refresh_count,
                "liveness": liveness_rows,
                "elapsed_seconds": time.monotonic() - started,
                "polls": polls,
            }
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "XP active client sessions were not proven drained before "
                f"the {timeout_seconds:g}s bound: {poll}"
            )
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["xp_session_drain_liveness_refresh_release"] = {
        "release_version": "v19",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": alg.sha(Path(__file__).resolve()),
        "bounded_drain_seconds": 60,
        "exact_container_identity_required": True,
        "two_inspect_process_table_proof_required": True,
        "live_or_unproven_mysqld_remains_fail_closed": True,
        "workload_scoring_acceptance_and_convergence_changed": False,
    }
    return document


def main() -> int:
    alg.wait_for_xp_session_drain = wait_for_xp_session_drain
    v18.state_document = state_document
    return v18.main()


if __name__ == "__main__":
    raise SystemExit(main())
