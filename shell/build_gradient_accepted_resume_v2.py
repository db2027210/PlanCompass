#!/usr/bin/env python3
"""Build a sealed workload-agnostic gradient accepted-resume checkpoint v2.

The accepted range state and persisted accepted counter are evidence, not CLI
inputs.  They are always extracted from the source optimizer history, source
configuration, and source counter file.  ``--accepted-count`` and
``--next-feedback-round`` are assertions about that evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gradient_descent_optimizer import ParameterRange  # noqa: E402
from parameter_optimizer import (  # noqa: E402
    _gradient_validate_accepted_resume_v2_state,
)


class CheckpointBuildError(RuntimeError):
    """The source evidence cannot prove a clean accepted boundary."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_object(path: Path, description: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointBuildError(
            f"{description} is not valid readable UTF-8 JSON: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise CheckpointBuildError(f"{description} must be a JSON object: {path}")
    return value


def _read_history(path: Path) -> Tuple[bytes, List[Dict[str, Any]]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CheckpointBuildError(f"source history is unreadable: {path}") from exc
    rows: List[Dict[str, Any]] = []
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckpointBuildError(
                f"source history has invalid JSON at line {line_number}: {path}"
            ) from exc
        if not isinstance(row, dict):
            raise CheckpointBuildError(
                f"source history row {line_number} is not an object: {path}"
            )
        row = dict(row)
        row["_builder_line_number"] = line_number
        rows.append(row)
    if not rows:
        raise CheckpointBuildError(f"source history is empty: {path}")
    return payload, rows


def _candidate_config_paths(attempt_root: Path) -> List[Path]:
    paths: List[Path] = []
    for directory_name in ("config", "configs"):
        directory = attempt_root / directory_name
        if directory.is_dir():
            paths.extend(directory.rglob("*.json"))
    valid: List[Path] = []
    for path in sorted(set(item.resolve() for item in paths)):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("param_ranges"), dict):
            valid.append(path)
    return valid


def resolve_sources(
    source: Path,
    *,
    source_config: Optional[Path] = None,
) -> Tuple[Path, Path, Path, Dict[str, Any]]:
    resolved_source = source.resolve()
    if resolved_source.is_file():
        history_path = resolved_source
        attempt_root = (
            history_path.parent.parent
            if history_path.parent.name == "history"
            else history_path.parent
        )
    elif resolved_source.is_dir():
        attempt_root = resolved_source
        histories = sorted(
            (attempt_root / "history").glob("*optimizer_history*.jsonl")
        )
        if len(histories) != 1:
            raise CheckpointBuildError(
                "source attempt must contain exactly one optimizer history; "
                f"found {len(histories)} under {attempt_root / 'history'}"
            )
        history_path = histories[0].resolve()
    else:
        raise CheckpointBuildError(f"source attempt/history does not exist: {source}")

    if source_config is not None:
        config_path = source_config.resolve()
        if not config_path.is_file():
            raise CheckpointBuildError(f"source config does not exist: {config_path}")
    else:
        configs = _candidate_config_paths(attempt_root)
        if len(configs) != 1:
            raise CheckpointBuildError(
                "cannot infer one source config with param_ranges; "
                f"found {len(configs)} under {attempt_root}"
            )
        config_path = configs[0]
    config = _read_json_object(config_path, "source config")
    return attempt_root, history_path, config_path, config


def _configured_active_params(config: Mapping[str, Any]) -> List[str]:
    optimizer = config.get("optimizer") or {}
    gradient = optimizer.get("gradient") if isinstance(optimizer, Mapping) else {}
    active = (
        gradient.get("active_params")
        if isinstance(gradient, Mapping)
        else None
    )
    if not isinstance(active, list) or not active:
        active = list((config.get("param_ranges") or {}).keys())
    names = [str(name) for name in active]
    if len(names) != len(set(names)) or not names:
        raise CheckpointBuildError("source config has invalid gradient active_params")
    return names


def _initial_ranges(config: Mapping[str, Any]) -> Dict[str, ParameterRange]:
    runtime_defaults = {
        "seq_page_cost": (0.0001, 100.0),
        "random_page_cost": (0.0001, 20.0),
        "cpu_tuple_cost": (0.000001, 1.0),
        "cpu_index_tuple_cost": (0.000001, 1.0),
        "cpu_operator_cost": (0.000001, 1.0),
        "parallel_setup_cost": (0.0, 10000.0),
        "parallel_tuple_cost": (0.000001, 10.0),
    }
    raw_ranges = config.get("param_ranges") or {}
    if not isinstance(raw_ranges, Mapping) or not raw_ranges:
        raise CheckpointBuildError("source config has no param_ranges")
    ranges: Dict[str, ParameterRange] = {}
    for raw_name, raw_record in raw_ranges.items():
        name = str(raw_name)
        if not isinstance(raw_record, Mapping):
            raise CheckpointBuildError(f"source config range is invalid for {name}")
        try:
            minimum = float(raw_record.get("min_value"))
            maximum = float(raw_record.get("max_value"))
        except (TypeError, ValueError) as exc:
            raise CheckpointBuildError(
                f"source config range is non-numeric for {name}"
            ) from exc
        if not all(math.isfinite(value) for value in (minimum, maximum)):
            raise CheckpointBuildError(f"source config range is non-finite for {name}")
        if minimum >= maximum:
            fallback = runtime_defaults.get(name)
            if fallback is None:
                raise CheckpointBuildError(
                    f"source config range is degenerate for {name}"
                )
            minimum, maximum = fallback
        ranges[name] = ParameterRange(minimum, maximum)
    return ranges


def _counter_evidence(
    config: Mapping[str, Any],
    accepted_count: int,
) -> Tuple[Path, str, int]:
    protocol = config.get("formal_outer_protocol") or {}
    if not isinstance(protocol, Mapping):
        raise CheckpointBuildError("source config lacks formal_outer_protocol")
    raw_path = str(protocol.get("formal_search_accepted_counter_path") or "").strip()
    if not raw_path:
        raise CheckpointBuildError(
            "source config lacks formal_search_accepted_counter_path"
        )
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise CheckpointBuildError(f"source accepted counter is missing: {path}")
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CheckpointBuildError(
            f"source accepted counter is not one integer: {path}"
        ) from exc
    if value != accepted_count:
        raise CheckpointBuildError(
            "source accepted counter mismatch: "
            f"persisted={value}, asserted={accepted_count}"
        )
    return path, _sha256(path), value


def _round_index(row: Mapping[str, Any]) -> Optional[int]:
    raw = row.get("round_index")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _validate_clean_boundary(
    rows: Sequence[Mapping[str, Any]],
    *,
    accepted_count: int,
    next_feedback_round: int,
    discard_incomplete_next_round: bool = False,
    discard_rejected_tail: bool = False,
) -> Tuple[Mapping[str, Any], Dict[str, Any]]:
    if accepted_count < 1:
        raise CheckpointBuildError("accepted_count must be at least one")
    if next_feedback_round < accepted_count:
        raise CheckpointBuildError(
            "next_feedback_round cannot be below accepted_count"
        )
    range_events = [
        row for row in rows if row.get("event") == "gradient_parameter_range_update"
    ]
    if not range_events:
        raise CheckpointBuildError("history has no committed gradient range update")
    matching_range_events = [
        row
        for row in range_events
        if int(row.get("accepted_shrink_count", -1)) == accepted_count
    ]
    if len(matching_range_events) != 1:
        raise CheckpointBuildError(
            "history must contain exactly one range state for asserted accepted_count; "
            f"found {len(matching_range_events)}"
        )
    latest_range = matching_range_events[0]
    later_counts = [
        int(row.get("accepted_shrink_count", -1))
        for row in range_events
        if int(row.get("accepted_shrink_count", -1)) > accepted_count
    ]
    if later_counts:
        raise CheckpointBuildError(
            "asserted accepted_count is stale; history contains later accepted state"
        )

    round0_complete = any(
        (
            row.get("event") == "gradient_parameter_range_update"
            and _round_index(row) == 0
        )
        or row.get("event") == "final_feedback_initial_validation_rejected_report"
        for row in rows
    )
    if not round0_complete:
        raise CheckpointBuildError("outer round 0 has no committed completion evidence")
    completed_feedback_rounds = {
        int(row.get("round_index"))
        for row in rows
        if row.get("event") == "final_feedback_round_complete"
        and _round_index(row) is not None
    }
    missing = [
        round_index
        for round_index in range(1, next_feedback_round)
        if round_index not in completed_feedback_rounds
    ]
    if missing:
        raise CheckpointBuildError(
            "accepted boundary is incomplete; missing completed outer rounds "
            f"{missing} before next_feedback_round={next_feedback_round}"
        )

    started_next = [
        row
        for row in rows
        if _round_index(row) == next_feedback_round
        and row.get("event")
        in {
            "final_feedback_status",
            "gradient_optimization_round_start",
            "experience_table_all_subset_ranking",
            "experience_table_gradient_real_replay_plan",
            "feedback_final_validation",
            "final_feedback_round_complete",
        }
    ]
    discard_audit: Dict[str, Any] = {
        "discard_incomplete_next_round_requested": bool(
            discard_incomplete_next_round
        ),
        "discarded_incomplete_next_round": False,
        "discarded_round_index": None,
        "discarded_history_line_start": None,
        "discarded_event_count": 0,
        "discarded_event_names": [],
        "discard_rejected_tail_requested": bool(discard_rejected_tail),
        "discarded_rejected_tail": False,
    }
    if started_next and not (
        discard_incomplete_next_round or discard_rejected_tail
    ):
        first = started_next[0]
        raise CheckpointBuildError(
            "accepted boundary is not clean; next feedback round "
            f"{next_feedback_round} already started at history line "
            f"{first.get('_builder_line_number')} ({first.get('event')})"
        )
    if discard_incomplete_next_round and discard_rejected_tail:
        raise CheckpointBuildError(
            "choose only one tail-discard mode"
        )
    if discard_rejected_tail:
        if not started_next:
            raise CheckpointBuildError(
                "discard-rejected-tail requested but the source has no tail "
                f"starting at round {next_feedback_round}"
            )
        first_tail_line = int(started_next[0].get("_builder_line_number") or 0)
        tail = [
            row
            for row in rows
            if int(row.get("_builder_line_number") or 0) >= first_tail_line
        ]
        earlier_rounds = sorted(
            {
                round_index
                for round_index in (_round_index(row) for row in tail)
                if round_index is not None
                and round_index < next_feedback_round
            }
        )
        if earlier_rounds:
            raise CheckpointBuildError(
                "discardable rejected tail contains an earlier outer round: "
                f"{earlier_rounds}"
            )
        later_accepts = [
            row
            for row in tail
            if (
                row.get("event") == "gradient_parameter_range_update"
                or (
                    row.get("event") == "final_feedback_round_complete"
                    and row.get("accepted_by_actual_score") is True
                )
            )
        ]
        if later_accepts:
            first_accept = later_accepts[0]
            raise CheckpointBuildError(
                "cannot discard tail containing a later accepted shrink; "
                f"history line {first_accept.get('_builder_line_number')} has "
                f"event {first_accept.get('event')!r}"
            )
        completed_tail_rounds = [
            row
            for row in tail
            if row.get("event") == "final_feedback_round_complete"
        ]
        if any(
            row.get("accepted_by_actual_score") is not False
            for row in completed_tail_rounds
        ):
            raise CheckpointBuildError(
                "discardable completed tail rounds must be explicitly rejected"
            )
        discard_audit.update(
            {
                "discarded_rejected_tail": True,
                "discarded_round_index": next_feedback_round,
                "discarded_history_line_start": first_tail_line,
                "discarded_event_count": len(tail),
                "discarded_event_names": sorted(
                    {str(row.get("event") or "") for row in tail}
                ),
            }
        )
    elif discard_incomplete_next_round:
        if not started_next:
            raise CheckpointBuildError(
                "discard-incomplete-next-round requested but the source has no "
                f"tail for round {next_feedback_round}"
            )
        first_tail_line = int(started_next[0].get("_builder_line_number") or 0)
        tail = [
            row
            for row in rows
            if int(row.get("_builder_line_number") or 0) >= first_tail_line
        ]
        explicit_tail_rounds = {
            round_index
            for round_index in (_round_index(row) for row in tail)
            if round_index is not None
        }
        if explicit_tail_rounds != {next_feedback_round}:
            raise CheckpointBuildError(
                "discardable source tail must belong exactly to next_feedback_round; "
                f"observed rounds={sorted(explicit_tail_rounds)}"
            )
        safe_tail_events = {
            "final_feedback_status",
            "iteration_plan_changes",
            "iteration_top_sql",
            "plan_resource_estimation",
            "baseline_estimation_correction",
            "gradient_optimization_round_start",
            "experience_table_all_subset_ranking",
            "gradient_round_param_delta_clamp_skipped",
        }
        unsafe = [
            row for row in tail if str(row.get("event") or "") not in safe_tail_events
        ]
        if unsafe:
            first_unsafe = unsafe[0]
            raise CheckpointBuildError(
                "cannot discard incomplete next round after real replay/unknown work; "
                f"history line {first_unsafe.get('_builder_line_number')} has "
                f"event {first_unsafe.get('event')!r}"
            )
        discard_audit.update(
            {
                "discarded_incomplete_next_round": True,
                "discarded_round_index": next_feedback_round,
                "discarded_history_line_start": first_tail_line,
                "discarded_event_count": len(tail),
                "discarded_event_names": sorted(
                    {str(row.get("event") or "") for row in tail}
                ),
            }
        )

    accepted_round = _round_index(latest_range)
    if accepted_round is None or accepted_round >= next_feedback_round:
        raise CheckpointBuildError(
            "accepted range event is not inside the completed outer prefix"
        )
    return latest_range, discard_audit


def _params_signature(params: Mapping[str, Any]) -> Tuple[Tuple[str, str], ...]:
    try:
        return tuple(
            sorted((str(name), float(value).hex()) for name, value in params.items())
        )
    except (TypeError, ValueError) as exc:
        raise CheckpointBuildError("parameter vector is non-numeric") from exc


def _params_from_hex_signature(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, list) or not raw:
        raise CheckpointBuildError("history lacks replay-scope incumbent signature")
    params: Dict[str, float] = {}
    try:
        for item in raw:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError
            name = str(item[0])
            params[name] = float.fromhex(str(item[1]))
    except (TypeError, ValueError) as exc:
        raise CheckpointBuildError(
            "history replay-scope incumbent signature is invalid"
        ) from exc
    return params


def _accepted_round_evidence(
    rows: Sequence[Mapping[str, Any]],
    *,
    accepted_round: int,
    accepted_params: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, float]], Dict[str, float], Mapping[str, Any]]:
    if accepted_round == 0:
        records = [
            row
            for row in rows
            if row.get("event")
            == "experience_table_gradient_initial_real_replay_complete"
            and _round_index(row) == 0
        ]
        if len(records) != 1:
            raise CheckpointBuildError(
                "round 0 must have exactly one completed replay record"
            )
        record = records[0]
        evaluations = list(record.get("candidate_evaluations") or [])
        scope = _params_from_hex_signature(
            (record.get("replay_batch_audit") or {}).get(
                "scope_incumbent_signature"
            )
        )
    else:
        records = [
            row
            for row in rows
            if row.get("event") == "final_feedback_round_complete"
            and _round_index(row) == accepted_round
            and row.get("accepted_by_actual_score") is True
        ]
        if len(records) != 1:
            raise CheckpointBuildError(
                f"accepted round {accepted_round} lacks one accepted completion record"
            )
        record = records[0]
        evaluations = list(record.get("candidate_evaluations") or [])
        scope = copy.deepcopy(record.get("round_start_params") or {})

    accepted_signature = _params_signature(accepted_params)
    matching = [
        evaluation
        for evaluation in evaluations
        if isinstance(evaluation, Mapping)
        and evaluation.get("accepted_by_actual_score") is True
        and _params_signature(evaluation.get("candidate_optimized_params") or {})
        == accepted_signature
    ]
    if len(matching) != 1:
        raise CheckpointBuildError(
            "accepted point params do not identify exactly one accepted replay evaluation"
        )
    accepted_evaluation = dict(matching[0])
    accepted_validation = copy.deepcopy(
        accepted_evaluation.get("candidate_final_validation") or {}
    )
    if not accepted_validation:
        raise CheckpointBuildError("accepted replay evaluation lacks final validation")

    replayed_params: List[Dict[str, float]] = []
    seen = set()
    for evaluation in evaluations:
        if not isinstance(evaluation, Mapping):
            continue
        params = evaluation.get("candidate_optimized_params") or {}
        if not isinstance(params, Mapping) or not params:
            continue
        signature = _params_signature(params)
        if signature in seen:
            continue
        seen.add(signature)
        replayed_params.append(
            {str(name): float(value) for name, value in params.items()}
        )
    if not replayed_params:
        raise CheckpointBuildError("accepted round has no replayed candidate params")
    scope_params = {str(name): float(value) for name, value in scope.items()}
    return accepted_validation, replayed_params, scope_params, record


def _validate_accepted_point(
    path: Path,
    expected_param_names: Sequence[str],
) -> Tuple[Dict[str, Any], Dict[str, float]]:
    if not path.is_file():
        raise CheckpointBuildError(f"accepted point evidence is missing: {path}")
    point = _read_json_object(path, "accepted point evidence")
    measurement = point.get("measurement") or {}
    stability = measurement.get("resource_stability") or {}
    if point.get("status") != "complete":
        raise CheckpointBuildError("accepted point evidence status is not complete")
    if measurement.get("correctness_passed") is not True:
        raise CheckpointBuildError("accepted point evidence failed correctness")
    if stability.get("passed") is not True or stability.get("stable") is not True:
        raise CheckpointBuildError("accepted point evidence failed stability")
    if point.get("oom_killed") or int(point.get("database_restart_count") or 0) != 0:
        raise CheckpointBuildError("accepted point evidence has OOM/restart evidence")
    raw_params = point.get("cost_parameters") or {}
    expected_names = set(expected_param_names)
    if not isinstance(raw_params, Mapping) or set(raw_params) != expected_names:
        raise CheckpointBuildError(
            "accepted point cost parameters do not match source config dimensions"
        )
    try:
        params = {str(name): float(value) for name, value in raw_params.items()}
    except (TypeError, ValueError) as exc:
        raise CheckpointBuildError(
            "accepted point cost parameters are non-numeric"
        ) from exc
    if any(not math.isfinite(value) for value in params.values()):
        raise CheckpointBuildError("accepted point cost parameters are non-finite")
    return point, params


def build_checkpoint(
    source: Path,
    accepted_point_path: Path,
    *,
    accepted_count: int,
    next_feedback_round: int,
    source_config: Optional[Path] = None,
    discard_incomplete_next_round: bool = False,
    discard_rejected_tail: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    attempt_root, history_path, config_path, config = resolve_sources(
        source,
        source_config=source_config,
    )
    history_payload, rows = _read_history(history_path)
    history_sha = hashlib.sha256(history_payload).hexdigest()
    initial_ranges = _initial_ranges(config)
    active_params = _configured_active_params(config)
    unknown_active = [name for name in active_params if name not in initial_ranges]
    if unknown_active:
        raise CheckpointBuildError(
            f"source config active params lack ranges: {unknown_active}"
        )
    range_event, discard_audit = _validate_clean_boundary(
        rows,
        accepted_count=accepted_count,
        next_feedback_round=next_feedback_round,
        discard_incomplete_next_round=discard_incomplete_next_round,
        discard_rejected_tail=discard_rejected_tail,
    )
    # Audit the immutable history boundary before asking for mutable counter or
    # point files.  A live/incomplete round must be reported as the primary
    # refusal reason rather than being masked by a not-yet-prepared resume
    # counter in the destination workflow.
    counter_path, counter_sha, counter_value = _counter_evidence(
        config,
        accepted_count,
    )
    accepted_point_path = accepted_point_path.resolve()
    _, accepted_params = _validate_accepted_point(
        accepted_point_path,
        list(initial_ranges),
    )
    accepted_round = int(range_event.get("round_index"))
    (
        accepted_validation,
        replayed_candidate_params,
        replayed_scope_params,
        accepted_round_record,
    ) = _accepted_round_evidence(
        rows,
        accepted_round=accepted_round,
        accepted_params=accepted_params,
    )
    if set(replayed_scope_params) != set(initial_ranges):
        raise CheckpointBuildError(
            "history replay-scope incumbent does not match source config dimensions"
        )
    max_replay = int(
        ((config.get("formal_outer_protocol") or {}).get(
            "formal_search_max_replay_top_k", 10
        ))
    )
    if len(replayed_candidate_params) > max(1, max_replay):
        raise CheckpointBuildError(
            "accepted round replay count exceeds configured max replay top-k"
        )
    range_state = copy.deepcopy(range_event.get("range_state") or {})
    # Only active dimensions participate in accepted binary shrink.  Older
    # histories may have serialized a compatibility range for a fixed
    # parameter (for example parallel_setup_cost=0..10000) even though the
    # source config freezes it to one value.  Resume must restore the source
    # fixed range, not the compatibility range.
    for name, configured_range in initial_ranges.items():
        if name in active_params:
            continue
        range_state[name] = {
            "min_value": float(configured_range.min_value),
            "max_value": float(configured_range.max_value),
            "transform": str(
                (range_state.get(name) or {}).get("transform") or "linear"
            ),
        }
    checkpoint: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "gradient_accepted_resume_checkpoint_v2",
        "source_attempt_path": str(attempt_root),
        "source_history_path": str(history_path),
        "source_history_sha256": history_sha,
        "source_config_path": str(config_path),
        "source_config_sha256": _sha256(config_path),
        "source_accepted_counter_path": str(counter_path),
        "source_accepted_counter_sha256": counter_sha,
        "accepted_point_result_path": str(accepted_point_path),
        "accepted_point_result_sha256": _sha256(accepted_point_path),
        "accepted_params": accepted_params,
        "accepted_validation": accepted_validation,
        "accepted_estimated_resource": copy.deepcopy(
            (next(
                evaluation
                for evaluation in (accepted_round_record.get("candidate_evaluations") or [])
                if isinstance(evaluation, Mapping)
                and evaluation.get("accepted_by_actual_score") is True
                and _params_signature(
                    evaluation.get("candidate_optimized_params") or {}
                ) == _params_signature(accepted_params)
            )).get("candidate_estimated_resource")
            or {}
        ),
        "replayed_scope_incumbent_params": replayed_scope_params,
        "replayed_candidate_params": replayed_candidate_params,
        "expected_range_state_after_accept": range_state,
        "counters": {
            "accepted_shrink_count": counter_value,
            "completed_outer_explorations": next_feedback_round,
        },
        "next_feedback_round": next_feedback_round,
        "resume_contract": {
            "resume_at_accepted_boundary": True,
            "direct_range_restore": True,
            "do_not_rerun_prior_rounds": True,
            "do_not_repeat_accepted_shrink": True,
        },
        "builder_provenance": {
            "range_event_history_line": int(
                range_event.get("_builder_line_number") or 0
            ),
            "accepted_round_record_history_line": int(
                accepted_round_record.get("_builder_line_number") or 0
            ),
            "accepted_round_index": accepted_round,
            "range_and_counter_are_source_derived": True,
            "cli_range_override_supported": False,
            "cli_counter_override_supported": False,
            **discard_audit,
        },
    }
    _gradient_validate_accepted_resume_v2_state(
        checkpoint,
        initial_ranges,
        active_params,
        persisted_accepted_count=counter_value,
    )

    if hashlib.sha256(history_path.read_bytes()).hexdigest() != history_sha:
        raise CheckpointBuildError(
            "source history changed while building; retry only at a quiescent boundary"
        )
    if _sha256(counter_path) != counter_sha:
        raise CheckpointBuildError(
            "source accepted counter changed while building; retry at a clean boundary"
        )
    audit = {
        "status": "ready",
        "dry_run_safe": True,
        "source_attempt_path": str(attempt_root),
        "source_history_path": str(history_path),
        "source_history_sha256": history_sha,
        "source_config_path": str(config_path),
        "source_config_sha256": checkpoint["source_config_sha256"],
        "source_accepted_counter_path": str(counter_path),
        "source_accepted_counter_sha256": counter_sha,
        "accepted_point_result_path": str(accepted_point_path),
        "accepted_point_result_sha256": checkpoint["accepted_point_result_sha256"],
        "accepted_shrink_count": counter_value,
        "next_feedback_round": next_feedback_round,
        "accepted_round_index": accepted_round,
        "range_event_history_line": checkpoint["builder_provenance"][
            "range_event_history_line"
        ],
        "replayed_candidate_count": len(replayed_candidate_params),
        **discard_audit,
    }
    return checkpoint, audit


def _canonical_payload(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        help="Source attempt directory or exact optimizer history JSONL file.",
    )
    parser.add_argument("--accepted-point", required=True, type=Path)
    parser.add_argument("--accepted-count", required=True, type=int)
    parser.add_argument("--next-feedback-round", required=True, type=int)
    parser.add_argument(
        "--source-config",
        type=Path,
        help="Exact config path; required only when the attempt has multiple configs.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print an audit without writing a checkpoint.",
    )
    parser.add_argument(
        "--discard-incomplete-next-round",
        action="store_true",
        help=(
            "Ignore a tail belonging exactly to next_feedback_round only when "
            "it contains subset/estimation events and no real replay evidence."
        ),
    )
    parser.add_argument(
        "--discard-rejected-tail",
        action="store_true",
        help=(
            "Resume from the last accepted boundary after proving that every "
            "later completed round was rejected and no later range shrink was "
            "committed."
        ),
    )
    args = parser.parse_args(argv)
    if not args.dry_run and args.output is None:
        parser.error("--output is required unless --dry-run is used")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        output = args.output.resolve() if args.output is not None else None
        if not args.dry_run and output is not None and output.exists():
            raise CheckpointBuildError(
                f"refuse to overwrite existing output: {output}"
            )
        checkpoint, audit = build_checkpoint(
            args.source,
            args.accepted_point,
            accepted_count=args.accepted_count,
            next_feedback_round=args.next_feedback_round,
            source_config=args.source_config,
            discard_incomplete_next_round=args.discard_incomplete_next_round,
            discard_rejected_tail=args.discard_rejected_tail,
        )
    except CheckpointBuildError as exc:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "dry_run": bool(args.dry_run),
                    "reason": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2

    payload = _canonical_payload(checkpoint)
    audit["checkpoint_sha256"] = hashlib.sha256(payload).hexdigest()
    audit["dry_run"] = bool(args.dry_run)
    if args.dry_run:
        audit["checkpoint_written"] = False
        print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        return 0

    assert output is not None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
    except OSError as exc:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "dry_run": False,
                    "reason": f"cannot write checkpoint output {output}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 2
    audit["checkpoint_written"] = True
    audit["checkpoint_path"] = str(output)
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
