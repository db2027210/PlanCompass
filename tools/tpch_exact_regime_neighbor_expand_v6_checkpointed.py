#!/usr/bin/env python3
"""Generate restart-safe bounded EXPLAIN-only TPC-H neighbors.

Besides validated-regime neighbors, this version deterministically enumerates
bounded coordinate steps around the exact formal incumbent.  Those points are
uncertain probes until real replay; no prediction can update optimizer state.

Every successful EXPLAIN is sealed in an input-bound checkpoint.  A process
restart replays that cache but does not reset either the deterministic
candidate schedule or the hard candidate-estimation limit.  Exhausting a
budget after enough useful neighbors have been found produces a valid partial
result instead of discarding all completed EXPLAIN work.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Optional


RESOURCES = ("cpu", "io", "memory")
DEFAULT_MAXIMUM_WALL_SECONDS = 1800
DEFAULT_MAXIMUM_EXPLAIN_INVOCATIONS = 3000
DEFAULT_MAXIMUM_CANDIDATE_ESTIMATIONS = 128
DEFAULT_EXPLAIN_TIMEOUT_SECONDS = 30
CHECKPOINT_SCHEMA_VERSION = "tpch_explain_expansion_checkpoint_v1"


class ExpansionBudgetExhausted(RuntimeError):
    """The sealed EXPLAIN-only expansion budget was exhausted."""


def validate_budgets(
    maximum_wall_seconds: int,
    maximum_explain_invocations: int,
    maximum_candidate_estimations: int,
    explain_timeout_seconds: int,
) -> None:
    """Reject missing, useless, or relaxed expansion limits."""
    if not (
        60 <= maximum_wall_seconds <= DEFAULT_MAXIMUM_WALL_SECONDS
        and 22 <= maximum_explain_invocations
        <= DEFAULT_MAXIMUM_EXPLAIN_INVOCATIONS
        and 5 <= maximum_candidate_estimations
        <= DEFAULT_MAXIMUM_CANDIDATE_ESTIMATIONS
        and 1 <= explain_timeout_seconds <= DEFAULT_EXPLAIN_TIMEOUT_SECONDS
    ):
        raise RuntimeError("EXPLAIN expansion budgets may not be relaxed")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically persist a JSON object so an interrupted EXPLAIN is resumable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}"
    )
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    values = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not an object")
        values.append(value)
    return values


def raw_three_level(
    plan: str,
    feature_ratio: Mapping[str, float],
    resource: Mapping[str, float],
    capability: Mapping[str, float],
) -> dict[str, Any]:
    feature_level = {
        name: int(round(float(feature_ratio[name]) / 0.005))
        for name in RESOURCES
    }
    resource_level = {
        name: int(
            round(
                (float(resource[name]) / float(capability[name])) / 0.005
            )
        )
        for name in RESOURCES
    }
    payload = {
        "plan": plan,
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": resource_level,
    }
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": "three-level-sha256:" + canonical_sha256(payload),
        "valid": True,
        "validation_errors": [],
        "plan_level": plan,
        "feature_ratio_level": feature_level,
        "predicted_resource_level": resource_level,
        "parameter_fallback_used": False,
    }


def query_set(
    capture: Mapping[str, Any],
    baseline_features: Mapping[str, Any],
) -> list[dict[str, Any]]:
    replay_count = {
        str(item["queryid"]): float(item.get("replay_count") or 1.0)
        for item in baseline_features.get("query_features") or []
    }
    queries: dict[str, dict[str, Any]] = {}
    for item in capture.get("items") or []:
        queryid = str(item.get("queryid"))
        query = str(item.get("query") or "").strip()
        if not queryid or not query:
            raise ValueError("baseline plan capture has an incomplete query")
        if queryid in queries:
            continue
        queries[queryid] = {
            "queryid": queryid,
            "query": query,
            "replay_count": replay_count.get(queryid, 1.0),
        }
    if set(queries) != set(replay_count):
        raise ValueError(
            "baseline capture/query-feature coverage mismatch: "
            f"capture={len(queries)} features={len(replay_count)}"
        )
    return [queries[name] for name in replay_count]


def score_resource(
    resource: Mapping[str, float],
    capability: Mapping[str, float],
) -> tuple[float, dict[str, float]]:
    utilization = {
        name: float(resource[name]) / float(capability[name])
        for name in RESOURCES
    }

    def band(value: float, weight: float, target: bool) -> float:
        low, high, hard = ((0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90))
        if target:
            if value < low:
                return weight * (1.0 - ((low - value) / low) ** 2)
            if value <= high:
                return weight * (
                    1.0 - 0.2 * (value - low) / (high - low)
                )
            span = max(hard - high, 0.05)
            gap = (value - high) / span
            excess = max(0.0, value - hard) / span
            return weight * (0.8 - gap * gap - 4.0 * excess)
        if value < low:
            return weight * 0.8 * (1.0 - ((low - value) / low) ** 2)
        if value <= high:
            return weight * (0.8 + 0.2 * (value - low) / (high - low))
        span = max(hard - high, 0.05)
        gap = (value - high) / span
        excess = max(0.0, value - hard) / span
        return weight * (1.0 - gap * gap - 4.0 * excess)

    score = sum(
        band(utilization[name], weight, name == "memory")
        for name, weight in (("cpu", 1.0), ("io", 3.0), ("memory", 1.0))
    )
    return score, utilization


def neighboring_params(
    anchor: Mapping[str, float],
    initial: Mapping[str, float],
    ranges: Mapping[str, Mapping[str, float]],
) -> list[tuple[float, dict[str, float]]]:
    candidates = []
    for factor in (0.95, 1.05, 0.90, 1.10, 0.80, 1.20):
        params = {}
        for name, raw in anchor.items():
            value = float(raw)
            baseline = float(initial[name])
            proposed = baseline + (value - baseline) * factor
            low = float(ranges[name]["min_value"])
            high = float(ranges[name]["max_value"])
            epsilon = max((high - low) * 1e-9, 1e-15)
            params[name] = min(high - epsilon, max(low + epsilon, proposed))
        if any(
            not math.isclose(
                params[name], float(anchor[name]), rel_tol=1e-12, abs_tol=1e-15
            )
            for name in params
        ):
            candidates.append((factor, params))
    return candidates


def interior_epoch_params(
    anchor: Mapping[str, float],
    ranges: Mapping[str, Mapping[str, float]],
) -> list[tuple[float, dict[str, float]]]:
    """Move stale-epoch anchors into the interior without crossing bounds."""
    candidates = []
    for blend in (0.12, 0.25, 0.50, 0.75):
        params = {}
        for name, raw in anchor.items():
            value = float(raw)
            low = float(ranges[name]["min_value"])
            high = float(ranges[name]["max_value"])
            if math.isclose(low, high, rel_tol=0.0, abs_tol=1e-15):
                params[name] = low
                continue
            if low <= 0.0 or value <= 0.0:
                midpoint = (low + high) / 2.0
                proposed = value + blend * (midpoint - value)
            else:
                midpoint_log = (math.log(low) + math.log(high)) / 2.0
                proposed = math.exp(
                    math.log(value)
                    + blend * (midpoint_log - math.log(value))
                )
            epsilon = max((high - low) * 1e-9, 1e-15)
            params[name] = min(high - epsilon, max(low + epsilon, proposed))
        candidates.append((blend, params))
    return candidates


def incumbent_coordinate_params(
    incumbent: Mapping[str, float],
    ranges: Mapping[str, Mapping[str, float]],
) -> list[tuple[str, Any, dict[str, float]]]:
    """Build a deterministic <=40% coordinate and compensation ladder.

    A long autonomous campaign can exhaust the four coarse single-coordinate
    fingerprints even though the interior parameter space is not exhausted.
    The denser ladder remains EXPLAIN-only and deterministic.  Paired moves
    let PostgreSQL cross plan thresholds that no single cost parameter crosses
    on its own.  Opposite-sign pairs also search the local plan-decision
    nullspace: one cost coefficient moves down while another moves up.  This
    is important when same-direction perturbations all leave the incumbent
    plan island.  Every resulting point is still checked by ``is_non_corner``
    and the global three-level fingerprint exclusions before real replay.
    """
    candidates = []
    seen: set[str] = set()
    names = sorted(incumbent)

    def append_candidate(label: str, factor: float, changed: tuple[str, ...]) -> None:
        params = {key: float(value) for key, value in incumbent.items()}
        for name in changed:
            value = float(incumbent[name])
            low = float(ranges[name]["min_value"])
            high = float(ranges[name]["max_value"])
            epsilon = max((high - low) * 1e-9, 1e-15)
            params[name] = min(
                high - epsilon,
                max(low + epsilon, value * factor),
            )
        if all(
            math.isclose(
                params[name], float(incumbent[name]),
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
            for name in changed
        ):
            return
        identity = canonical_sha256(params)
        if identity in seen:
            return
        seen.add(identity)
        candidates.append((label, factor, params))

    def append_compensated_candidate(
        first: str,
        second: str,
        first_factor: float,
        second_factor: float,
    ) -> None:
        params = {key: float(value) for key, value in incumbent.items()}
        for name, factor in (
            (first, first_factor),
            (second, second_factor),
        ):
            value = float(incumbent[name])
            low = float(ranges[name]["min_value"])
            high = float(ranges[name]["max_value"])
            epsilon = max((high - low) * 1e-9, 1e-15)
            params[name] = min(
                high - epsilon,
                max(low + epsilon, value * factor),
            )
        if all(
            math.isclose(
                params[name], float(incumbent[name]),
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
            for name in (first, second)
        ):
            return
        identity = canonical_sha256(params)
        if identity in seen:
            return
        seen.add(identity)
        candidates.append(
            (
                f"{first}-down+{second}-up",
                {
                    first: first_factor,
                    second: second_factor,
                    "mode": "opposite_sign_pair",
                },
                params,
            )
        )

    for name in names:
        for factor in (
            0.60,
            0.70,
            0.80,
            0.90,
            0.95,
            1.05,
            1.10,
            1.20,
            1.30,
            1.40,
        ):
            append_candidate(name, factor, (name,))

    for first_index, first in enumerate(names):
        for second in names[first_index + 1 :]:
            for factor in (0.60, 0.75, 0.85, 1.15, 1.25, 1.40):
                append_candidate(
                    f"{first}+{second}",
                    factor,
                    (first, second),
                )
            for lower, upper in (
                (0.60, 1.40),
                (0.70, 1.30),
                (0.80, 1.20),
                (0.90, 1.10),
                (0.95, 1.05),
            ):
                append_compensated_candidate(first, second, lower, upper)
                append_compensated_candidate(second, first, lower, upper)
    return candidates


def delta_payload(
    params: Mapping[str, float],
    initial: Mapping[str, float],
) -> dict[str, Any]:
    result = {}
    for name, after in params.items():
        before = float(initial[name])
        value = float(after)
        result[name] = {
            "before": before,
            "proposed_before_clip": value,
            "after_clip": value,
            "absolute": value - before,
            "relative": (value - before) / before if before != 0.0 else None,
            "clipped": False,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--evidence-top5", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--selector", required=True, type=Path)
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--resume-state", required=True, type=Path)
    parser.add_argument(
        "--checkpoint",
        required=True,
        type=Path,
        help="Input-bound atomic EXPLAIN cache used for restart-safe replay.",
    )
    parser.add_argument(
        "--allow-live-epoch-transition",
        action="store_true",
        help=(
            "Generate unknown probes from the current live plan epoch when "
            "the sealed baseline epoch no longer matches."
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--maximum-wall-seconds",
        type=int,
        default=DEFAULT_MAXIMUM_WALL_SECONDS,
    )
    parser.add_argument(
        "--maximum-explain-invocations",
        type=int,
        default=DEFAULT_MAXIMUM_EXPLAIN_INVOCATIONS,
    )
    parser.add_argument(
        "--maximum-candidate-estimations",
        type=int,
        default=DEFAULT_MAXIMUM_CANDIDATE_ESTIMATIONS,
    )
    parser.add_argument(
        "--explain-timeout-seconds",
        type=int,
        default=DEFAULT_EXPLAIN_TIMEOUT_SECONDS,
    )
    args = parser.parse_args()

    validate_budgets(
        args.maximum_wall_seconds,
        args.maximum_explain_invocations,
        args.maximum_candidate_estimations,
        args.explain_timeout_seconds,
    )
    started_at = time.monotonic()
    deadline = started_at + args.maximum_wall_seconds
    explain_invocations = 0
    candidate_estimations = 0
    physical_explain_invocations = 0
    checkpoint_cache_hits = 0
    singleflight_cache_hits = 0
    budget_exhaustion_reason: Optional[str] = None

    checkpoint_binding = {
        "history_sha256": hashlib.sha256(args.history.read_bytes()).hexdigest(),
        "evidence_top5_sha256": hashlib.sha256(
            args.evidence_top5.read_bytes()
        ).hexdigest(),
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "capture_sha256": hashlib.sha256(args.capture.read_bytes()).hexdigest(),
        "selector_sha256": hashlib.sha256(args.selector.read_bytes()).hexdigest(),
        "resume_state_sha256": hashlib.sha256(
            args.resume_state.read_bytes()
        ).hexdigest(),
        "maximum_wall_seconds": args.maximum_wall_seconds,
        "maximum_explain_invocations": args.maximum_explain_invocations,
        "maximum_candidate_estimations": args.maximum_candidate_estimations,
        "explain_timeout_seconds": args.explain_timeout_seconds,
        "allow_live_epoch_transition": args.allow_live_epoch_transition,
    }
    if args.checkpoint.exists():
        checkpoint = load_json(args.checkpoint)
        if not (
            checkpoint.get("schema_version") == CHECKPOINT_SCHEMA_VERSION
            and checkpoint.get("input_binding") == checkpoint_binding
            and isinstance(checkpoint.get("psql_results"), dict)
        ):
            raise RuntimeError("EXPLAIN checkpoint/input binding mismatch")
    else:
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "input_binding": checkpoint_binding,
            "psql_results": {},
            "live_explain_invocations": 0,
        }
        atomic_json(args.checkpoint, checkpoint)
    cached_results = checkpoint["psql_results"]
    lost_results = int(checkpoint.get("lost_checkpoint_results", 0))
    live_invocations = int(checkpoint.get("live_explain_invocations", -1))
    if not (
        lost_results >= 0
        and live_invocations >= 0
        and live_invocations == len(cached_results) + lost_results
        and live_invocations <= args.maximum_explain_invocations
        and all(
            isinstance(key, str)
            and isinstance(value, dict)
            and isinstance(value.get("sql_sha256"), str)
            and isinstance(value.get("stdout"), str)
            for key, value in cached_results.items()
        )
    ):
        raise RuntimeError("EXPLAIN checkpoint accounting/coverage mismatch")
    checkpoint_lock = threading.Lock()
    checkpoint_inflight: dict[str, threading.Event] = {}

    sys.path.insert(0, str(args.code_root))
    sys.path.insert(0, str(args.selector.parent))
    import importlib.util

    from ratio_scale_resource_estimator import RatioScaleResourceEstimator

    spec = importlib.util.spec_from_file_location(
        "exact_regime_selector", args.selector
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load selector module")
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)

    config = load_json(args.config)
    resume_state = load_json(args.resume_state)
    analysis = selector.load_analysis(args.history)
    evidence = load_json(args.evidence_top5)
    anchors = evidence.get("top5") or evidence.get("ranked_candidates") or []
    if len(anchors) != 5:
        raise ValueError("evidence Top-5 must contain exactly five candidates")
    raw_candidates = analysis["ranked_candidates"]
    reference = raw_candidates[0]
    baseline_metadata = reference["final_estimation_metadata"]
    baseline_features = baseline_metadata["baseline_features"]
    capture = load_json(args.capture)
    queries = query_set(capture, baseline_features)
    capability = {
        name: float(value) for name, value in config["capability"].items()
    }
    incumbent_params = resume_state.get("incumbent_params")
    incumbent_resource = resume_state.get("incumbent_resource")
    resume_ranges = resume_state.get("range_state")
    if not (
        resume_state.get("schema_version")
        == "tpch_exact_plan_regime_formal_state_v1"
        and resume_state.get("status") == "complete"
        and resume_state.get("baseline_restored") is True
        and isinstance(incumbent_params, Mapping)
        and set(incumbent_params) == set(config["initial_params"])
        and isinstance(incumbent_resource, Mapping)
        and set(RESOURCES).issubset(incumbent_resource)
        and isinstance(resume_ranges, Mapping)
        and set(resume_ranges) == set(incumbent_params)
    ):
        raise RuntimeError("resume state is not a clean incumbent authority")
    initial = {
        name: float(value) for name, value in incumbent_params.items()
    }
    baseline_real = {
        name: float(incumbent_resource[name]) for name in RESOURCES
    }
    if any(value <= 0.0 for value in baseline_real.values()):
        raise RuntimeError("formal incumbent resource must be positive")
    rescored_incumbent, _ = score_resource(baseline_real, capability)
    if not math.isclose(
        rescored_incumbent,
        float(resume_state["incumbent_score"]),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise RuntimeError(
            "formal incumbent resource/score binding mismatch: "
            f"rescored={rescored_incumbent} "
            f"state={resume_state['incumbent_score']}"
        )
    for name, bounds in resume_ranges.items():
        config_bounds = config["param_ranges"][name]
        low = float(bounds["min_value"])
        high = float(bounds["max_value"])
        if not (
            float(config_bounds["min_value"]) <= low <= float(initial[name])
            <= high <= float(config_bounds["max_value"])
        ):
            raise RuntimeError(
                f"formal range/config/incumbent binding mismatch: {name}"
            )
    postgres = config["postgres"]

    def psql_query(sql: str) -> str:
        nonlocal checkpoint_cache_hits
        nonlocal explain_invocations
        nonlocal physical_explain_invocations
        nonlocal singleflight_cache_hits
        sql_key = canonical_sha256({"sql": sql})
        sql_sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        with checkpoint_lock:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise ExpansionBudgetExhausted(
                    "EXPLAIN wall-time budget exhausted"
                )
            explain_invocations += 1
            cached = checkpoint["psql_results"].get(sql_key)
            if cached is not None:
                if cached.get("sql_sha256") != sql_sha:
                    raise RuntimeError("corrupt EXPLAIN checkpoint entry")
                checkpoint_cache_hits += 1
                return str(cached["stdout"])
            inflight = checkpoint_inflight.get(sql_key)
            owner = inflight is None
            if owner:
                if int(checkpoint["live_explain_invocations"]) >= (
                    args.maximum_explain_invocations
                ):
                    raise ExpansionBudgetExhausted(
                        "physical EXPLAIN invocation budget exhausted"
                    )
                inflight = threading.Event()
                checkpoint_inflight[sql_key] = inflight
                checkpoint["live_explain_invocations"] = int(
                    checkpoint["live_explain_invocations"]
                ) + 1
                checkpoint["lost_checkpoint_results"] = int(
                    checkpoint.get("lost_checkpoint_results", 0)
                ) + 1
                physical_explain_invocations += 1
                # Reserve the physical invocation before starting psql.  A
                # process death therefore remains conservatively charged and
                # is represented as a missing checkpoint result on restart.
                atomic_json(args.checkpoint, checkpoint)
        if not owner:
            assert inflight is not None
            if not inflight.wait(timeout=max(0.0, remaining)):
                raise ExpansionBudgetExhausted(
                    "EXPLAIN wall-time budget exhausted while awaiting cache"
                )
            with checkpoint_lock:
                cached = checkpoint["psql_results"].get(sql_key)
                if cached is None or cached.get("sql_sha256") != sql_sha:
                    raise RuntimeError("concurrent EXPLAIN cache fill failed")
                checkpoint_cache_hits += 1
                singleflight_cache_hits += 1
                return str(cached["stdout"])
        environment = os.environ.copy()
        environment["PGPASSWORD"] = "postgres"
        # The sealed Python 3.11 runtime carries a private libpq in
        # LD_LIBRARY_PATH that is ABI-incompatible with node01 /usr/bin/psql.
        # EXPLAIN must use the host psql and its matching system libpq.
        environment.pop("LD_LIBRARY_PATH", None)
        try:
            completed = subprocess.run(
                [
                    "/usr/bin/psql",
                    "-X",
                    "-A",
                    "-t",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    str(postgres["host"]),
                    "-p",
                    str(postgres["port"]),
                    "-U",
                    str(postgres["user"]),
                    "-d",
                    str(postgres["dbname"]),
                    "-c",
                    sql,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                timeout=max(
                    1,
                    min(
                        args.explain_timeout_seconds,
                        int(math.ceil(remaining)),
                    ),
                ),
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip())
            with checkpoint_lock:
                checkpoint["psql_results"][sql_key] = {
                    "sql_sha256": sql_sha,
                    "stdout": completed.stdout,
                }
                checkpoint["lost_checkpoint_results"] = int(
                    checkpoint.get("lost_checkpoint_results", 0)
                ) - 1
                if int(checkpoint["lost_checkpoint_results"]) < 0:
                    raise RuntimeError("EXPLAIN checkpoint lost-result underflow")
                atomic_json(args.checkpoint, checkpoint)
            return completed.stdout
        finally:
            with checkpoint_lock:
                event = checkpoint_inflight.pop(sql_key, None)
                if event is not None:
                    event.set()

    estimator = RatioScaleResourceEstimator(
        pg_host=str(postgres["host"]),
        pg_port=str(postgres["port"]),
        pg_user=str(postgres["user"]),
        pg_dbname=str(postgres["dbname"]),
        estimator_cfg=config["metrics"]["plan_estimator"],
        plan_capture_modes=["generic"],
        custom_plan_params={},
        psql_query_func=psql_query,
        cache_hit_rate=float(baseline_metadata.get("cache_hit_rate") or 0.4221093060444164),
        verbose=False,
    )

    def estimate_candidate(
        params: Mapping[str, float],
    ) -> tuple[dict[str, float], Any, dict[str, Any]]:
        nonlocal candidate_estimations
        if candidate_estimations >= args.maximum_candidate_estimations:
            raise ExpansionBudgetExhausted(
                "candidate-estimation budget exhausted"
            )
        if time.monotonic() >= deadline:
            raise ExpansionBudgetExhausted("EXPLAIN wall-time budget exhausted")
        candidate_estimations += 1
        return estimator.estimate_with_metadata(queries, params)

    calibration = estimator.calibrate(queries, initial, baseline_real)
    _incumbent_resource_check, _incumbent_stats, incumbent_metadata = (
        estimate_candidate(initial)
    )
    incumbent_plan = str(incumbent_metadata["workload_plan_fingerprint"])
    baseline_regime = selector.structural_plan_regime_id({
        "final_estimation_metadata": {
            "query_features": calibration["baseline_features"]["query_features"]
        }
    })
    expected_baseline_regime = selector.structural_plan_regime_id({
        "final_estimation_metadata": {
            "query_features": baseline_features["query_features"]
        }
    })
    baseline_epoch_transition = None
    if baseline_regime != expected_baseline_regime:
        observed_by_query = {
            str(item["queryid"]): str(item["plan_fingerprint"])
            for item in calibration["baseline_features"]["query_features"]
        }
        expected_by_query = {
            str(item["queryid"]): str(item["plan_fingerprint"])
            for item in baseline_features["query_features"]
        }
        mismatches = {
            queryid: {
                "observed": observed_by_query.get(queryid),
                "expected": expected_by_query.get(queryid),
            }
            for queryid in sorted(
                set(observed_by_query) | set(expected_by_query)
            )
            if observed_by_query.get(queryid)
            != expected_by_query.get(queryid)
        }
        baseline_epoch_transition = {
            "observed_regime": baseline_regime,
            "expected_regime": expected_baseline_regime,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }
        if not args.allow_live_epoch_transition:
            raise RuntimeError(
                "live EXPLAIN baseline regime does not match sealed analysis: "
                + json.dumps(baseline_epoch_transition, sort_keys=True)
            )

    expanded = []
    expanded_fingerprints: set[str] = {
        str((item.get("three_level_fingerprint") or {}).get("id"))
        for item in [*anchors, *raw_candidates]
        if (item.get("three_level_fingerprint") or {}).get("id")
    }
    audit = []
    ranges = resume_ranges
    for anchor in anchors:
        anchor_regime = selector.structural_plan_regime_id(anchor)
        anchor_exact_regime = selector.plan_level(anchor)
        accepted_neighbor = None
        attempts = []
        parameter_candidates = (
            interior_epoch_params(anchor["candidate_params"], ranges)
            if baseline_epoch_transition is not None
            else neighboring_params(
                anchor["candidate_params"], initial, ranges
            )
        )
        for factor, params in parameter_candidates:
            try:
                resource, _stats, metadata = estimate_candidate(params)
            except ExpansionBudgetExhausted as exc:
                budget_exhaustion_reason = str(exc)
                break
            observed_regime = selector.structural_plan_regime_id({
                "final_estimation_metadata": metadata
            })
            observed_exact_regime = str(
                metadata["workload_plan_fingerprint"]
            )
            exact_matched = (
                observed_exact_regime == anchor_exact_regime
            )
            attempts.append({
                "factor": factor,
                "params": params,
                "structural_plan_regime_id": observed_regime,
                "exact_plan_regime_id": observed_exact_regime,
                "structural_matched": observed_regime == anchor_regime,
                "exact_matched": exact_matched,
                "matched": (
                    observed_regime == anchor_regime and exact_matched
                ),
            })
            epoch_transition_probe = (
                args.allow_live_epoch_transition
                and baseline_epoch_transition is not None
            )
            if (
                not epoch_transition_probe
                and (observed_regime != anchor_regime or not exact_matched)
            ):
                continue
            candidate = copy.deepcopy(anchor)
            feature_ratio = {
                name: float(metadata["feature_ratios"][name])
                for name in RESOURCES
            }
            score, utilization = score_resource(resource, capability)
            plan = str(metadata["workload_plan_fingerprint"])
            fingerprint = raw_three_level(
                plan, feature_ratio, resource, capability
            )
            if fingerprint["id"] in expanded_fingerprints:
                continue
            candidate.update({
                "candidate_params": params,
                "optimized_params": copy.deepcopy(params),
                "predicted_resource": resource,
                "final_resource": copy.deepcopy(resource),
                "feature_ratio": feature_ratio,
                "candidate_score": score,
                "final_score": score,
                "utilization": utilization,
                "final_estimation_metadata": metadata,
                "workload_plan_fingerprint": plan,
                "three_level_fingerprint": fingerprint,
                "candidate_plan_signature": fingerprint["id"],
                "delta_param": delta_payload(params, initial),
                "eta": factor,
                "candidate_origin": (
                    "live_plan_epoch_transition_explain_probe"
                    if epoch_transition_probe
                    else "validated_exact_regime_explain_neighbor"
                ),
                "probe_kind": (
                    "live_plan_epoch_transition_unknown_probe"
                    if epoch_transition_probe
                    else "same_exact_regime_explain_only_neighbor"
                ),
                "boundary_corner": False,
                "boundary_plateau": False,
                "offline_plateau": False,
                "parameter_noop": False,
                "non_corner_verified": True,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "structural_plan_regime_id": observed_regime,
                "source_calibration_anchor_rank": int(anchor["rank"]),
                "selection_role": (
                    "probe"
                    if epoch_transition_probe
                    else anchor.get("selection_role", "trusted")
                ),
                "candidate_trust_confidence": (
                    0.0
                    if epoch_transition_probe
                    else anchor.get("candidate_trust_confidence", 1.0)
                ),
            })
            candidate["fingerprint_validation"] = {
                "resource_estimation_mode": "ratio_scale",
                "cost_parameter_free_features": True,
                "qps_used_for_inner_scoring": False,
                "query_features": [
                    {
                        "queryid": item["queryid"],
                        "fingerprint": item["plan_fingerprint"],
                    }
                    for item in metadata["query_features"]
                ],
                "expected_plannable_query_count": len(metadata["query_features"]),
                "workload_plan_fingerprint": plan,
                "feature_ratio": feature_ratio,
                "baseline_real_resource": baseline_real,
            }
            if not selector.is_non_corner(candidate, ranges):
                continue
            candidate["actual_parameter_noncorner_audit"] = {
                "reason": "actual_parameter_space_noncorner",
                "margin_fraction": 0.05,
                "verified_after_explain": True,
            }
            accepted_neighbor = candidate
            expanded_fingerprints.add(fingerprint["id"])
            break
        audit.append({
            "anchor_rank": int(anchor["rank"]),
            "anchor_regime": anchor_regime,
            "anchor_exact_regime": anchor_exact_regime,
            "attempts": attempts,
            "matched": accepted_neighbor is not None,
        })
        if budget_exhaustion_reason is not None:
            break
        if accepted_neighbor is None:
            if (
                anchor.get("selection_role") == "trusted"
                and baseline_epoch_transition is None
            ):
                raise RuntimeError(
                    f"no same-regime neighbor found for trusted anchor rank "
                    f"{anchor['rank']}"
                )
            continue
        expanded.append(accepted_neighbor)

    incumbent_expanded = []
    incumbent_audit = []
    for parameter_name, factor, params in incumbent_coordinate_params(
        incumbent_params,
        ranges,
    ):
        if budget_exhaustion_reason is not None:
            break
        try:
            resource, _stats, metadata = estimate_candidate(params)
        except ExpansionBudgetExhausted as exc:
            budget_exhaustion_reason = str(exc)
            break
        feature_ratio = {
            name: float(metadata["feature_ratios"][name])
            for name in RESOURCES
        }
        score, utilization = score_resource(resource, capability)
        plan = str(metadata["workload_plan_fingerprint"])
        new_fingerprint = raw_three_level(
            plan,
            feature_ratio,
            resource,
            capability,
        )
        audit_row = {
            "parameter": parameter_name,
            "factor": factor,
            "candidate_params": params,
            "candidate_score": score,
            "three_level_fingerprint": new_fingerprint["id"],
            "incumbent_exact_plan_preserved": plan == incumbent_plan,
            "selected": False,
        }
        if new_fingerprint["id"] in expanded_fingerprints:
            audit_row["reason"] = "duplicate_three_level_fingerprint"
            incumbent_audit.append(audit_row)
            continue
        candidate = copy.deepcopy(reference)
        observed_regime = selector.structural_plan_regime_id(
            {"final_estimation_metadata": metadata}
        )
        candidate.update(
            {
                "candidate_params": params,
                "optimized_params": copy.deepcopy(params),
                "predicted_resource": resource,
                "final_resource": copy.deepcopy(resource),
                "feature_ratio": feature_ratio,
                "candidate_score": score,
                "final_score": score,
                "predicted_score": score,
                "utilization": utilization,
                "final_estimation_metadata": metadata,
                "workload_plan_fingerprint": plan,
                "three_level_fingerprint": new_fingerprint,
                "candidate_plan_signature": new_fingerprint["id"],
                "delta_param": delta_payload(params, initial),
                "eta": factor,
                "candidate_origin": (
                    "formal_incumbent_coordinate_score_ladder_probe"
                ),
                "probe_kind": "formal_incumbent_explain_only_neighbor",
                "boundary_corner": False,
                "boundary_plateau": False,
                "offline_plateau": False,
                "parameter_noop": False,
                "non_corner_verified": True,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "structural_plan_regime_id": observed_regime,
                "source_calibration_anchor_rank": 0,
                "selection_role": "probe",
                "candidate_trust_confidence": 0.0,
                "incumbent_exact_plan_preserved": plan == incumbent_plan,
                "incumbent_exact_plan_fingerprint": incumbent_plan,
                "confidence": {
                    "exact_plan_regime": 0.0,
                    "measurement_count": 0,
                    "formal_incumbent_coordinate_probe": 1.0,
                    "incumbent_exact_plan_preserved": (
                        1.0 if plan == incumbent_plan else 0.0
                    ),
                },
                "formal_incumbent_anchor": {
                    "resume_state": str(args.resume_state.resolve()),
                    "resume_state_sha256": hashlib.sha256(
                        args.resume_state.read_bytes()
                    ).hexdigest(),
                    "incumbent_score": float(
                        resume_state["incumbent_score"]
                    ),
                    "incumbent_params": {
                        name: float(value)
                        for name, value in incumbent_params.items()
                    },
                    "parameter": parameter_name,
                    "factor": factor,
                    "maximum_relative_step": 0.4,
                    "manual_candidate_injection": False,
                },
            }
        )
        candidate["fingerprint_validation"] = {
            "resource_estimation_mode": "ratio_scale",
            "cost_parameter_free_features": True,
            "qps_used_for_inner_scoring": False,
            "query_features": [
                {
                    "queryid": item["queryid"],
                    "fingerprint": item["plan_fingerprint"],
                }
                for item in metadata["query_features"]
            ],
            "expected_plannable_query_count": len(
                metadata["query_features"]
            ),
            "workload_plan_fingerprint": plan,
            "feature_ratio": feature_ratio,
            "baseline_real_resource": baseline_real,
        }
        if not selector.is_non_corner(candidate, ranges):
            audit_row["reason"] = "parameter_space_corner"
            incumbent_audit.append(audit_row)
            continue
        candidate["actual_parameter_noncorner_audit"] = {
            "reason": "actual_parameter_space_noncorner",
            "margin_fraction": 0.05,
            "verified_after_explain": True,
        }
        expanded_fingerprints.add(new_fingerprint["id"])
        incumbent_expanded.append(candidate)
        audit_row["selected"] = True
        audit_row["reason"] = "novel_noncorner_incumbent_neighbor"
        incumbent_audit.append(audit_row)

    combined = [*incumbent_expanded, *expanded, *raw_candidates]
    output = {
        "selection_contract": "tpch_exact_regime_neighbor_expansion_v1",
        "diagnostic_only": True,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "explain_only": True,
        "new_workload_calls": 0,
        "baseline_structural_regime": baseline_regime,
        "baseline_epoch_transition": baseline_epoch_transition,
        "formal_incumbent_calibration": {
            "resume_state": str(args.resume_state.resolve()),
            "resume_state_sha256": hashlib.sha256(
                args.resume_state.read_bytes()
            ).hexdigest(),
            "incumbent_score": float(resume_state["incumbent_score"]),
            "incumbent_resource": baseline_real,
            "incumbent_exact_plan_fingerprint": incumbent_plan,
            "rescored_incumbent": rescored_incumbent,
            "incumbent_params": initial,
            "range_state": resume_ranges,
            "historical_candidate_initial_resource_used": False,
        },
        "neighbor_audit": audit,
        "incumbent_coordinate_ladder_audit": incumbent_audit,
        "incumbent_coordinate_neighbor_count": len(incumbent_expanded),
        "bounded_expansion_audit": {
            "contract": "tpch_explain_expansion_hard_budget_v1",
            "maximum_wall_seconds": args.maximum_wall_seconds,
            "elapsed_wall_seconds": time.monotonic() - started_at,
            "maximum_explain_invocations": args.maximum_explain_invocations,
            "explain_invocations": explain_invocations,
            "logical_explain_requests": explain_invocations,
            "physical_explain_invocations_this_process": (
                physical_explain_invocations
            ),
            "checkpoint_cache_hits_this_process": checkpoint_cache_hits,
            "singleflight_cache_hits_this_process": singleflight_cache_hits,
            "maximum_candidate_estimations": (
                args.maximum_candidate_estimations
            ),
            "candidate_estimations": candidate_estimations,
            "per_explain_timeout_seconds": args.explain_timeout_seconds,
            "budget_exhausted": budget_exhaustion_reason is not None,
            "budget_exhaustion_reason": budget_exhaustion_reason,
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_input_binding": checkpoint_binding,
            "checkpoint_live_explain_invocations": int(
                checkpoint.get("live_explain_invocations", 0)
            ),
            "physical_explain_budget_consumed_cumulative": int(
                checkpoint.get("live_explain_invocations", 0)
            ),
            "cache_replay_consumes_new_physical_budget": False,
            "checkpoint_prior_delivery_failure_invocations": int(
                checkpoint.get(
                    "lost_result_physical_explain_invocations",
                    0,
                )
            ),
            "checkpoint_delivery_failure_outputs_recovered": bool(
                checkpoint.get(
                    "lost_result_outputs_present_in_cache",
                    False,
                )
            ),
        },
        "ranked_candidates": combined,
    }
    atomic_json(args.output, output)
    print(json.dumps({
        "output": str(args.output),
        "same_regime_neighbors": len(expanded),
        "combined_candidates": len(combined),
        "neighbor_regimes": [
            item["structural_plan_regime_id"] for item in expanded
        ],
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
