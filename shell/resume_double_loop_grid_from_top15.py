#!/usr/bin/env python3
"""Resume grid double-loop search from an already measured fresh Top15 gate.

The wrapper reuses exactly one current-run baseline, workload-pruning result, and
15 fresh calibration measurements.  It never replays calibration.  Historical
input is restricted to parameter->resource vectors, anchored multiplicatively
to the current fresh baseline; old scores, ranks, decisions, intervals, and best
parameters are neither loaded nor trusted.
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from double_loop_scenario import (  # noqa: E402
    FreshExperience,
    FreshExperienceModel,
    FreshModelQualityThresholds,
)
from shell import run_double_loop_grid_scenarios as runner  # noqa: E402


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _guest_instability_reason(source_dir: Path) -> Optional[str]:
    """Return why a resume source is unsafe, based on its owning VM attempt.

    A transport row may have been persisted as ``status=usable`` immediately
    before the guest kernel killed one or more PostgreSQL backends.  Resume
    inputs therefore need the same fail-closed guest diagnostics check as the
    scheduler and final suite audit, rather than trusting the row flag alone.
    """
    start = source_dir.resolve()
    if start.is_file():
        start = start.parent
    for root in (start, *start.parents):
        diagnostics_path = root / "guest_exit_diagnostics.json"
        serial_path = root / "rootless_vm" / "serial.log"
        if not diagnostics_path.is_file() and not serial_path.is_file():
            continue
        outputs: List[str] = []
        try:
            if diagnostics_path.is_file():
                outputs.append(str(_read_json(diagnostics_path).get("output") or ""))
            if serial_path.is_file():
                outputs.append(serial_path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            return f"guest diagnostics unreadable: {type(exc).__name__}: {exc}"
        output = "\n".join(outputs).lower()
        if "out of memory: killed process" in output or "oom-killer" in output:
            return "guest OOM occurred during source real replay"
        if "no space left on device" in output:
            return "guest disk became full during source real replay"
        if "terminated by signal 9: killed" in output:
            return "guest process was killed by SIGKILL during source real replay"
        return None
    return None


def _assert_stable_resume_source(source_dir: Path, role: str) -> None:
    reason = _guest_instability_reason(source_dir)
    if reason:
        raise RuntimeError(f"unsafe {role} source {source_dir.resolve()}: {reason}")


def _usable_replays(path: Path) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            # A full filesystem can truncate only the final JSONL record.  A
            # partial-resume must retain the preceding durable, independently
            # replayed rows without pretending that the torn record succeeded.
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                value.get("event") == "fresh_model_calibration_replay"
                and value.get("status") == "usable"
            ):
                result.append(value)
    return result


ParamSignature = Tuple[Tuple[str, float], ...]


def _param_signature(params: Mapping[str, Any]) -> ParamSignature:
    return tuple(
        (str(name), round(float(value), 12))
        for name, value in sorted(params.items())
    )


def _load_candidate_replay_cache(
    source_dir: Path,
    scenario: str,
) -> Tuple[Dict[ParamSignature, Dict[str, Any]], Dict[str, Any]]:
    """Load usable exact-vector candidate measurements from one prior run.

    These rows are transport results only.  Their old score, rank, acceptance,
    interval, and best-point metadata are never read.  The new optimizer must
    independently regenerate and rank the same complete parameter vector before
    a cached real measurement can be returned.
    """
    root = source_dir.resolve()
    real_dir = root if root.name == "real_validations" else root / "real_validations"
    if not real_dir.is_dir():
        raise FileNotFoundError(real_dir)
    cache: Dict[ParamSignature, Dict[str, Any]] = {}
    scanned = 0
    skipped = 0
    duplicate_vectors = 0
    for path in sorted(real_dir.glob("*_double_loop_*.json")):
        scanned += 1
        raw = _read_json(path)
        params = raw.get("params")
        target = str(raw.get("target") or scenario).strip().lower()
        resources = raw.get("resources")
        usable = raw.get("double_loop_measurement_usable")
        status = str(raw.get("status") or "").strip().lower()
        if (
            not isinstance(params, Mapping)
            or not isinstance(resources, Mapping)
            or target != scenario
            or status != "ok"
            or usable is not True
        ):
            skipped += 1
            continue
        signature = _param_signature(params)
        if signature in cache:
            duplicate_vectors += 1
        cache[signature] = {
            "raw": raw,
            "source_path": str(path),
            "source_label": raw.get("label"),
            "source_kind": "prior_partial_run",
        }
    return cache, {
        "enabled": True,
        "source": str(root),
        "real_validation_dir": str(real_dir),
        "scanned_json_count": scanned,
        "usable_exact_vector_count": len(cache),
        "skipped_json_count": skipped,
        "duplicate_vector_count": duplicate_vectors,
        "matching_key": "complete_parameter_vector_rounded_to_12_decimals",
        "loaded_fields": ["params", "resources", "db_qps", "latency", "raw_transport"],
        "required_transport_status": "ok",
        "old_scores_loaded": False,
        "old_ranks_loaded": False,
        "old_decisions_loaded": False,
        "old_intervals_loaded": False,
        "old_best_params_loaded": False,
    }


def _anchored_seed_rows(
    model: FreshExperienceModel,
    seed_dir: Path,
    *,
    include_real_candidates: bool = False,
) -> Tuple[List[FreshExperience], Dict[str, Any]]:
    baseline = _read_json(seed_dir / "fresh_baseline.json")
    old_measurement = baseline.get("measurement") or {}
    current = model.objective.baseline.as_resource()
    names = ("db_qps", "cpu", "memory", "io")
    scale = {
        name: float(current[name]) / float(old_measurement[name])
        for name in names
    }
    by_signature: Dict[Tuple[Tuple[str, float], ...], FreshExperience] = {}

    def add(params: Mapping[str, Any], measurement: Mapping[str, Any]) -> None:
        anchored = {
            name: float(measurement[name]) * scale[name]
            for name in names
        }
        row = model.build_experience(params, anchored)
        by_signature[model._signature(row.params)] = row

    add(baseline["params"], old_measurement)
    source_replays = _usable_replays(seed_dir / "fresh_model_calibration.jsonl")
    for replay in source_replays:
        add(replay["params"], replay["measurement"])
    candidate_audit: Dict[str, Any] = {"enabled": False}
    candidate_row_count = 0
    if include_real_candidates:
        candidate_cache, candidate_audit = _load_candidate_replay_cache(
            seed_dir,
            seed_dir.name,
        )
        for cached in candidate_cache.values():
            raw = cached.get("raw") or {}
            measurement = dict(raw.get("resources") or raw.get("resource") or {})
            measurement["db_qps"] = raw.get("db_qps")
            add(raw["params"], measurement)
            candidate_row_count += 1
    rows = list(by_signature.values())
    return rows, {
        "enabled": True,
        "source": str(seed_dir),
        "source_baseline": old_measurement,
        "current_fresh_baseline": current,
        "baseline_anchor_scale": scale,
        "source_usable_replay_count": len(source_replays),
        "source_real_candidate_row_count": candidate_row_count,
        "real_candidate_rows_enabled": bool(include_real_candidates),
        "real_candidate_cache": candidate_audit,
        "deduplicated_training_row_count": len(rows),
        "loaded_fields": ["params", "measurement"],
        "forbidden_fields_loaded": [],
        "old_scores_loaded": False,
        "old_ranks_loaded": False,
        "old_decisions_loaded": False,
        "old_intervals_loaded": False,
        "old_best_params_loaded": False,
    }


def _scenario_source_dir(path: Path, scenario: str) -> Path:
    """Resolve either a run root or an already scenario-scoped directory."""
    root = path.resolve()
    return root if root.name == scenario else root / scenario


def _candidate_top15_replays(
    candidate_cache: Mapping[ParamSignature, Mapping[str, Any]],
    *,
    baseline_params: Mapping[str, Any],
    scenario: str,
) -> List[Dict[str, Any]]:
    """Use exactly 15 non-baseline real optimizer candidates as holdouts."""
    baseline_signature = _param_signature(baseline_params)

    def sort_key(item: Tuple[ParamSignature, Mapping[str, Any]]) -> Tuple[str, ParamSignature]:
        return str(item[1].get("source_label") or ""), item[0]

    selected: List[Dict[str, Any]] = []
    for signature, cached in sorted(candidate_cache.items(), key=sort_key):
        if signature == baseline_signature:
            continue
        raw = cached.get("raw")
        if not isinstance(raw, Mapping):
            continue
        params = raw.get("params")
        resources = raw.get("resources") or raw.get("resource")
        if not isinstance(params, Mapping) or not isinstance(resources, Mapping):
            continue
        measurement = runner.measurement_from_payload(resources, raw)
        selected.append(
            {
                "event": "fresh_model_calibration_replay",
                "calibration_id": f"candidate_top15_{len(selected) + 1:03d}",
                "phase": "resumed_real_candidate_top15_calibration",
                "status": "usable",
                "params": {name: float(value) for name, value in params.items()},
                "measurement": measurement.as_resource(),
                "source_path": cached.get("source_path"),
                "source_label": cached.get("source_label"),
                "source_kind": cached.get("source_kind"),
                "current_run_only": False,
                "used_new_real_replay": False,
                "old_score_loaded": False,
                "old_rank_loaded": False,
                "old_decision_loaded": False,
            }
        )
        if len(selected) == 15:
            break
    if len(selected) != 15:
        raise ValueError(
            f"candidate Top15 quality design needs 15 non-baseline real rows, got {len(selected)}"
        )
    return selected


def _pooled_maximin_quality_rows(
    model: FreshExperienceModel,
    source_dirs: Sequence[Path],
    *,
    scenario: str,
    baseline_params: Mapping[str, Any],
) -> Tuple[List[FreshExperience], List[FreshExperience], List[Dict[str, Any]], Dict[str, Any]]:
    """Build a parameter-only maximin Top15 from prior independent real rows.

    Only parameter vectors and measured resource vectors are loaded.  Every
    source is multiplicatively anchored to this invocation's fresh baseline;
    duplicate transports of the same measurement are collapsed before taking
    the median.  Selection depends only on parameter-space distance, never on
    an old score, rank, decision, interval, or best-point label.
    """
    names = ("db_qps", "cpu", "memory", "io")
    current = model.objective.baseline.as_resource()
    baseline_signature = model._signature(baseline_params)
    values: Dict[ParamSignature, Dict[str, List[float]]] = {}
    params_by_signature: Dict[ParamSignature, Dict[str, float]] = {}
    sources_by_signature: Dict[ParamSignature, List[str]] = {}
    seen_measurements: Dict[ParamSignature, set[Tuple[float, ...]]] = {}
    source_audit: List[Dict[str, Any]] = []

    def add(
        params: Mapping[str, Any],
        measurement: Mapping[str, Any],
        *,
        scale: Mapping[str, float],
        source: str,
    ) -> None:
        signature = _param_signature(params)
        if signature == baseline_signature:
            return
        anchored = tuple(float(measurement[name]) * float(scale[name]) for name in names)
        measurement_signature = tuple(round(value, 12) for value in anchored)
        seen = seen_measurements.setdefault(signature, set())
        if measurement_signature in seen:
            return
        seen.add(measurement_signature)
        params_by_signature[signature] = {
            name: float(value) for name, value in params.items()
        }
        resource_values = values.setdefault(
            signature,
            {name: [] for name in names},
        )
        for name, value in zip(names, anchored):
            resource_values[name].append(value)
        sources_by_signature.setdefault(signature, []).append(source)

    for requested_dir in source_dirs:
        source_dir = _scenario_source_dir(requested_dir, scenario)
        baseline_path = source_dir / "fresh_baseline.json"
        if not baseline_path.is_file():
            raise FileNotFoundError(baseline_path)
        baseline = _read_json(baseline_path)
        old_measurement = baseline.get("measurement")
        if not isinstance(old_measurement, Mapping):
            raise ValueError(f"missing baseline measurement: {baseline_path}")
        scale = {
            name: float(current[name]) / float(old_measurement[name])
            for name in names
        }
        calibration_count = 0
        calibration_path = source_dir / "fresh_model_calibration.jsonl"
        if calibration_path.is_file():
            for replay in _usable_replays(calibration_path):
                add(
                    replay["params"],
                    replay["measurement"],
                    scale=scale,
                    source=f"{calibration_path}:{replay.get('calibration_id')}",
                )
                calibration_count += 1
        candidate_count = 0
        try:
            candidate_cache, _ = _load_candidate_replay_cache(source_dir, scenario)
        except FileNotFoundError:
            candidate_cache = {}
        for cached in candidate_cache.values():
            raw = cached.get("raw")
            if not isinstance(raw, Mapping):
                continue
            params = raw.get("params")
            resources = raw.get("resources") or raw.get("resource")
            if not isinstance(params, Mapping) or not isinstance(resources, Mapping):
                continue
            measurement = runner.measurement_from_payload(resources, raw)
            add(
                params,
                measurement.as_resource(),
                scale=scale,
                source=str(cached.get("source_path") or source_dir),
            )
            candidate_count += 1
        source_audit.append(
            {
                "source": str(source_dir),
                "baseline_anchor_scale": scale,
                "usable_calibration_rows_scanned": calibration_count,
                "usable_candidate_rows_scanned": candidate_count,
            }
        )

    pooled: List[FreshExperience] = []
    row_sources: Dict[ParamSignature, List[str]] = {}
    for signature, resource_values in values.items():
        measurement = {
            name: float(statistics.median(resource_values[name]))
            for name in names
        }
        row = model.build_experience(params_by_signature[signature], measurement)
        pooled.append(row)
        row_sources[model._signature(row.params)] = sorted(sources_by_signature[signature])
    if len(pooled) < 15:
        raise ValueError(f"pooled maximin quality design needs 15 rows, got {len(pooled)}")

    remaining = list(range(len(pooled)))
    selected: List[int] = []
    first = max(
        remaining,
        key=lambda index: (
            model._distance(baseline_params, pooled[index].params),
            model._signature(pooled[index].params),
        ),
    )
    selected.append(first)
    remaining.remove(first)
    while len(selected) < 15:
        next_index = max(
            remaining,
            key=lambda index: (
                min(
                    model._distance(pooled[index].params, pooled[other].params)
                    for other in selected
                ),
                model._signature(pooled[index].params),
            ),
        )
        selected.append(next_index)
        remaining.remove(next_index)

    validation_rows = [pooled[index] for index in selected]
    fixed_training_rows = [pooled[index] for index in remaining]
    replays: List[Dict[str, Any]] = []
    for index, row in enumerate(validation_rows, start=1):
        replays.append(
            {
                "event": "fresh_model_calibration_replay",
                "calibration_id": f"pooled_maximin_top15_{index:03d}",
                "phase": "parameter_only_pooled_maximin_top15_calibration",
                "status": "usable",
                "params": copy.deepcopy(row.params),
                "measurement": row.measurement.as_resource(),
                "source_rows": copy.deepcopy(row_sources[model._signature(row.params)]),
                "current_run_only": False,
                "used_new_real_replay": False,
                "old_score_loaded": False,
                "old_rank_loaded": False,
                "old_decision_loaded": False,
            }
        )
    audit = {
        "design": "parameter_only_pooled_maximin_top15",
        "selection_uses_resources": False,
        "selection_uses_old_scores_or_decisions": False,
        "pooled_unique_parameter_count": len(pooled),
        "validation_row_count": len(validation_rows),
        "fixed_training_row_count": len(fixed_training_rows),
        "sources": source_audit,
    }
    return validation_rows, fixed_training_rows, replays, audit


def main() -> int:
    # Disable long-option abbreviation: otherwise the canonical ``--mode``
    # option is incorrectly consumed as an abbreviation of
    # ``--model-training-seed-dir`` by this bootstrap parser.
    bootstrap = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    bootstrap.add_argument("--resume-calibration-dir", required=True)
    bootstrap.add_argument("--model-training-seed-dir", required=True)
    bootstrap.add_argument("--independent-exact-training-seed-dir")
    bootstrap.add_argument(
        "--model-training-seed-include-candidates",
        action="store_true",
    )
    bootstrap.add_argument("--resume-candidate-dir")
    bootstrap.add_argument(
        "--resume-quality-design",
        choices=("coordinate_top15", "candidate_top15", "pooled_maximin_top15"),
        default="coordinate_top15",
    )
    bootstrap.add_argument(
        "--resume-quality-pool-dir",
        action="append",
        default=[],
    )
    bootstrap.add_argument(
        "--force-new-candidate-label",
        action="append",
        default=[],
        help=(
            "remove the exact vector sourced from this cached label so the "
            "new small loop must replay it; may be repeated"
        ),
    )
    known, remaining = bootstrap.parse_known_args()
    source_dir = Path(known.resume_calibration_dir).resolve()
    seed_dir = Path(known.model_training_seed_dir).resolve()
    independent_seed_dir = (
        Path(known.independent_exact_training_seed_dir).resolve()
        if known.independent_exact_training_seed_dir
        else None
    )
    _assert_stable_resume_source(source_dir, "calibration")
    source_baseline = _read_json(source_dir / "fresh_baseline.json")
    source_pruning = _read_json(source_dir / "fresh_workload_pruning.json")
    source_replays = _usable_replays(source_dir / "fresh_model_calibration.jsonl")
    if not 1 <= len(source_replays) <= 15:
        raise ValueError(
            "resume source must contain between 1 and 15 usable calibration "
            f"replays, got {len(source_replays)}"
        )
    if known.resume_quality_design != "coordinate_top15" and len(source_replays) != 15:
        raise ValueError(
            "partial calibration recovery is supported only for the fixed "
            "coordinate_top15 design"
        )
    scenario = str(source_baseline.get("physical_semantics", {}).get("scenario") or "")
    if scenario not in runner.SCENARIOS:
        raise ValueError(f"invalid resume scenario in baseline: {scenario!r}")
    candidate_cache: Dict[ParamSignature, Dict[str, Any]] = {}
    candidate_cache_audit: Dict[str, Any] = {"enabled": False}
    if known.resume_candidate_dir:
        _assert_stable_resume_source(
            Path(known.resume_candidate_dir),
            "candidate replay cache",
        )
        candidate_cache, candidate_cache_audit = _load_candidate_replay_cache(
            Path(known.resume_candidate_dir),
            scenario,
        )
        forced_labels = {str(label) for label in known.force_new_candidate_label}
        forced_signatures = [
            signature
            for signature, cached in candidate_cache.items()
            if str(cached.get("source_label")) in forced_labels
        ]
        for signature in forced_signatures:
            candidate_cache.pop(signature, None)
        candidate_cache_audit["forced_new_candidate_labels"] = sorted(forced_labels)
        candidate_cache_audit["forced_new_candidate_vector_count"] = len(
            forced_signatures
        )
    if seed_dir.name != scenario:
        seed_dir = seed_dir / scenario
    _assert_stable_resume_source(seed_dir, "model training seed")
    if not (seed_dir / "fresh_baseline.json").is_file():
        raise FileNotFoundError(seed_dir / "fresh_baseline.json")
    if independent_seed_dir is not None:
        if independent_seed_dir.name != scenario:
            independent_seed_dir = independent_seed_dir / scenario
        _assert_stable_resume_source(
            independent_seed_dir,
            "independent exact training seed",
        )
        if not (independent_seed_dir / "fresh_baseline.json").is_file():
            raise FileNotFoundError(independent_seed_dir / "fresh_baseline.json")

    original_real_validation = runner.run_real_validation_safe
    original_write_json = runner.write_json
    original_compact_result = runner.compact_result
    baseline_injected = False
    candidate_cache_hit_count = 0
    candidate_cache_current_run_add_count = 0
    new_calibration_real_replay_count = 0

    def resumed_write_json(path: Path, value: Any) -> None:
        if Path(path).name == "run_manifest.json" and isinstance(value, dict):
            value = copy.deepcopy(value)
            contract = value.setdefault("isolation_contract", {})
            contract["historical_inputs_allowed"] = [
                "baseline_anchored_parameter_to_resource_training_rows",
                "independent_exact_parameter_to_resource_replays",
            ]
            contract["fresh_top15_resumed_from"] = str(source_dir)
            contract["new_calibration_real_replays"] = (
                new_calibration_real_replay_count
            )
            contract["candidate_replay_cache"] = copy.deepcopy(candidate_cache_audit)
            contract["old_scores_ranks_decisions_intervals_best_params_loaded"] = False
        original_write_json(path, value)

    def resumed_compact_result(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        value = original_compact_result(*args, **kwargs)
        isolation = value.setdefault("isolation", {})
        isolation["fresh_baseline_only"] = True
        isolation["current_invocation_experience_only"] = False
        isolation["anchored_parameter_resource_seed_loaded"] = True
        isolation["independent_exact_training_seed"] = (
            str(independent_seed_dir) if independent_seed_dir is not None else None
        )
        isolation["fresh_top15_resumed_from"] = str(source_dir)
        isolation["new_calibration_real_replays"] = (
            new_calibration_real_replay_count
        )
        isolation["candidate_replay_cache"] = {
            **copy.deepcopy(candidate_cache_audit),
            "cache_hit_count": candidate_cache_hit_count,
            "current_run_exact_vector_add_count": candidate_cache_current_run_add_count,
            "final_exact_vector_count": len(candidate_cache),
        }
        isolation["historical_best_params_loaded"] = False
        isolation["historical_scores_or_decisions_loaded"] = False
        return value

    def resumed_real_validation(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        nonlocal baseline_injected
        nonlocal candidate_cache_hit_count
        nonlocal candidate_cache_current_run_add_count
        force_new_replay = bool(kwargs.get("force_new_replay", False))
        if not baseline_injected:
            baseline_injected = True
            raw = copy.deepcopy(source_baseline.get("raw") or {})
            raw["event"] = "double_loop_resumed_fresh_baseline"
            raw["resumed_from"] = str(source_dir)
            raw["used_real_replay"] = False
            return raw
        params = args[1] if len(args) > 1 else kwargs.get("params")
        if isinstance(params, Mapping) and not force_new_replay:
            cached = candidate_cache.get(_param_signature(params))
            if cached is not None:
                candidate_cache_hit_count += 1
                raw = copy.deepcopy(cached["raw"])
                new_label = kwargs.get("label")
                if new_label:
                    raw["label"] = new_label
                raw["event"] = "real_validation"
                raw["resumed_candidate_replay"] = {
                    "matched_by": "complete_parameter_vector",
                    "source_path": cached["source_path"],
                    "source_label": cached["source_label"],
                    "source_kind": cached.get("source_kind"),
                    "used_new_real_replay": False,
                }
                raw["used_real_replay"] = False
                return raw
        raw = original_real_validation(*args, **kwargs)
        if force_new_replay:
            raw["used_real_replay"] = True
            raw["forced_new_real_replay"] = True
        if isinstance(params, Mapping) and isinstance(raw, Mapping):
            try:
                measurement = runner.measurement_from_payload(
                    raw.get("resources") or raw.get("resource") or {},
                    raw,
                )
                invalid_reason = runner.measurement_invalid_reason(measurement, raw)
            except (KeyError, TypeError, ValueError):
                invalid_reason = "candidate_measurement_parse_failed"
            if not invalid_reason:
                signature = _param_signature(params)
                label = kwargs.get("label") or raw.get("label")
                output_dir = kwargs.get("output_dir")
                source_path = (
                    str(Path(output_dir) / f"{label}.json")
                    if output_dir is not None and label
                    else None
                )
                candidate_cache[signature] = {
                    "raw": copy.deepcopy(dict(raw)),
                    "source_path": source_path,
                    "source_label": label,
                    "source_kind": "current_resumed_run",
                }
                candidate_cache_current_run_add_count += 1
        return raw

    def resumed_pruning(*args: Any, **kwargs: Any) -> List[str]:
        output_path = Path(kwargs["output_path"])
        record = copy.deepcopy(source_pruning)
        record["event"] = "fresh_workload_pruning_resumed"
        record["resumed_from"] = str(source_dir)
        record["used_new_real_replay"] = False
        runner.write_json(output_path, runner.json_value(record))
        active = record.get("active_params") or []
        return [str(name) for name in active]

    def resumed_quality_gate(
        model: FreshExperienceModel,
        _validator: Any,
        _sql_set: Any,
        _base_params: Mapping[str, float],
        _ranges: Mapping[str, Tuple[float, float]],
        _candidates: Sequence[str],
        *,
        thresholds: FreshModelQualityThresholds,
        replay_budget: int,
        grid_points: int,
        scenario_dir: Path,
        fixed_training_rows: Optional[Sequence[FreshExperience]] = None,
        training_seed_audit: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        nonlocal new_calibration_real_replay_count
        if replay_budget != 15:
            raise ValueError("resume quality gate requires the fixed Top15 budget")
        _ = (fixed_training_rows, training_seed_audit)
        quality_design_audit: Dict[str, Any]
        if known.resume_quality_design == "candidate_top15":
            quality_replays = _candidate_top15_replays(
                candidate_cache,
                baseline_params=_base_params,
                scenario=scenario,
            )
            validation_rows = [
                model.build_experience(replay["params"], replay["measurement"])
                for replay in quality_replays
            ]
            training_rows, seed_audit = _anchored_seed_rows(
                model,
                seed_dir,
                include_real_candidates=(
                    known.model_training_seed_include_candidates
                ),
            )
            quality_design_audit = {
                "design": "resumed_real_candidate_top15",
                "source": str(known.resume_candidate_dir),
                "selection_uses_old_scores_or_decisions": False,
            }
        elif known.resume_quality_design == "pooled_maximin_top15":
            if not known.resume_quality_pool_dir:
                raise ValueError("pooled maximin quality design requires pool directories")
            for pool_dir in known.resume_quality_pool_dir:
                _assert_stable_resume_source(
                    Path(pool_dir),
                    "pooled quality",
                )
            (
                validation_rows,
                training_rows,
                quality_replays,
                quality_design_audit,
            ) = _pooled_maximin_quality_rows(
                model,
                [Path(value) for value in known.resume_quality_pool_dir],
                scenario=scenario,
                baseline_params=_base_params,
            )
            seed_audit = {
                "enabled": True,
                "source": "pooled_maximin_remaining_rows",
                "deduplicated_training_row_count": len(training_rows),
                "old_scores_loaded": False,
                "old_ranks_loaded": False,
                "old_decisions_loaded": False,
            }
        else:
            design = runner.fresh_model_calibration_points(
                _base_params,
                _ranges,
                _candidates,
                replay_budget=replay_budget,
                grid_points=grid_points,
            )
            existing_by_signature = {
                _param_signature(replay["params"]): copy.deepcopy(replay)
                for replay in source_replays
            }
            quality_replays = []
            for design_row in design:
                signature = _param_signature(design_row["params"])
                existing = existing_by_signature.get(signature)
                if existing is not None:
                    quality_replays.append(existing)
                    continue
                raw = _validator(_sql_set, dict(design_row["params"]))
                measurement = runner.measurement_from_payload(
                    raw.get("resources") or raw.get("resource") or {},
                    raw,
                )
                invalid_reason = runner.measurement_invalid_reason(measurement, raw)
                if invalid_reason:
                    raise RuntimeError(
                        "partial calibration replay is unusable for "
                        f"{design_row['calibration_id']}: {invalid_reason}"
                    )
                replay = {
                    "event": "fresh_model_calibration_replay",
                    **copy.deepcopy(design_row),
                    "status": "usable",
                    "measurement": measurement.as_resource(),
                    "raw_result": runner.json_value(raw),
                    "real_resource_score": model.objective.resource_score_details(
                        measurement
                    )["total"],
                    "current_run_only": True,
                    "used_for_optimizer_candidate_selection": False,
                    "resumed_from_interrupted_calibration": str(source_dir),
                    "used_new_real_replay": True,
                }
                quality_replays.append(replay)
                new_calibration_real_replay_count += 1
            if len(quality_replays) != 15:
                raise RuntimeError(
                    "fixed coordinate Top15 recovery produced "
                    f"{len(quality_replays)} rows instead of 15"
                )
            validation_rows = [
                model.build_experience(replay["params"], replay["measurement"])
                for replay in quality_replays
            ]
            training_rows, seed_audit = _anchored_seed_rows(
                model,
                seed_dir,
                include_real_candidates=(
                    known.model_training_seed_include_candidates
                ),
            )
            quality_design_audit = {
                "design": "resumed_coordinate_top15",
                "source": str(source_dir),
                "selection_uses_old_scores_or_decisions": False,
            }
        # Preserve the caller's hard 10% inversion limit while making the
        # resource-score resolution explicit and auditable.
        effective_thresholds = FreshModelQualityThresholds(
            min_samples=thresholds.min_samples,
            max_relative_error_p95=thresholds.max_relative_error_p95,
            max_relative_error_max=thresholds.max_relative_error_max,
            min_r2=thresholds.min_r2,
            max_pairwise_inversion_rate=thresholds.max_pairwise_inversion_rate,
            score_tie_margin=thresholds.score_tie_margin,
            signal_floor=thresholds.signal_floor,
        )
        independent_rows: List[FreshExperience] = []
        independent_seed_audit: Dict[str, Any] = {"enabled": False}
        if independent_seed_dir is not None:
            independent_rows, independent_seed_audit = _anchored_seed_rows(
                model,
                independent_seed_dir,
                include_real_candidates=False,
            )
        seed_audit["independent_exact_seed"] = independent_seed_audit
        quality = model.select_quality_mode(
            effective_thresholds,
            validation_rows=validation_rows,
            fixed_training_rows=training_rows,
            independent_exact_training_rows=independent_rows or None,
            always_training_rows=[
                model.build_experience(
                    _base_params,
                    model.objective.baseline.as_resource(),
                )
            ],
        )
        quality["event"] = "fresh_model_quality_gate_attempt"
        quality["after_calibration_id"] = quality_replays[-1]["calibration_id"]
        quality["real_replay_attempt_count"] = 15
        quality["resumed_from"] = str(source_dir)
        calibration_log = scenario_dir / "fresh_model_calibration.jsonl"
        for replay in quality_replays:
            record = copy.deepcopy(replay)
            record["resumed_from"] = (
                str(source_dir)
                if known.resume_quality_design == "coordinate_top15"
                else quality_design_audit.get("source", "pooled_real_resource_memory")
            )
            record.setdefault("used_new_real_replay", False)
            runner.append_jsonl(calibration_log, record)
        runner.append_jsonl(calibration_log, quality)
        runner.write_json(
            scenario_dir / "fresh_model_training_seed.json",
            runner.json_value(seed_audit),
        )
        summary = {
            "event": "fresh_model_quality_gate",
            "passed": bool(quality.get("passed")),
            "reason": quality.get("reason"),
            "current_run_only": bool(quality.get("current_run_only")),
            "historical_training_rows_used": bool(
                quality.get("historical_training_rows_used")
            ),
            "historical_training_rows_evaluated": bool(
                quality.get("historical_training_rows_evaluated")
            ),
            "historical_seed_discarded_after_quality_failure": bool(
                quality.get("historical_seed_discarded_after_quality_failure")
            ),
            "historical_model_artifacts_used": False,
            "old_model_labels_used": False,
            "optimizer_started": False,
            "calibration_design": [
                {
                    key: copy.deepcopy(replay.get(key))
                    for key in (
                        "calibration_id",
                        "phase",
                        "parameter",
                        "parameters",
                        "grid_value_index",
                        "params",
                    )
                    if key in replay
                }
                for replay in quality_replays
            ],
            "calibration_replay_attempt_count": 15,
            "calibration_candidate_count": 15,
            "calibration_replay_budget": 15,
            "calibration_budget_excludes_fresh_baseline": True,
            "fresh_baseline_sample_count": 1,
            "usable_sample_count_including_baseline": 16,
            "quality_attempt_count": 1,
            "selected_quality": runner.json_value(quality),
            "calibration_replays": runner.json_value(quality_replays),
            "calibration_jsonl": str(calibration_log),
            "score_formula": "sigmoid_linear_sweet_band_weighted_mean_v2",
            "estimated_and_real_score_formula_identical": True,
            "resumed_from": str(source_dir),
            "new_calibration_real_replays": new_calibration_real_replay_count,
            "training_seed": runner.json_value(seed_audit),
            "quality_design": runner.json_value(quality_design_audit),
            "preflight_soft_start": not bool(quality.get("passed")),
            "preflight_soft_start_reason": (
                quality.get("reason") if not bool(quality.get("passed")) else None
            ),
        }
        runner.write_json(
            scenario_dir / "fresh_model_quality_gate.json",
            runner.json_value(summary),
        )
        # Match the fresh-run policy: Top15 is an admission signal, not a
        # one-shot terminal gate. A failed preflight starts the optimizer in
        # soft mode; later real candidate batches retrain and re-evaluate the
        # same resource and ordering thresholds.
        # Candidate prediction may use all resource-vector experience after the
        # strict holdout gate has passed. Fresh rows overwrite exact old points.
        if quality.get("historical_training_rows_used") is True:
            for row in training_rows:
                model.add(row.params, row.measurement.as_resource())
            for row in independent_rows:
                model.add(row.params, row.measurement.as_resource())
        for row in validation_rows:
            model.add(row.params, row.measurement.as_resource())
        return summary

    runner.run_real_validation_safe = resumed_real_validation
    runner.fresh_workload_pruning = resumed_pruning
    runner.run_fresh_model_quality_gate = resumed_quality_gate
    runner.write_json = resumed_write_json
    runner.compact_result = resumed_compact_result
    sys.argv = [sys.argv[0], *remaining]
    if "--scenarios" not in remaining:
        sys.argv.extend(["--scenarios", scenario])
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
