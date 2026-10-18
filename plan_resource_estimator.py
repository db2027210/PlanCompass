from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from resource_feature_predictor import ResourceFeaturePredictor


ResourceLog = Dict[str, Any]
PlanNodeFeature = Dict[str, Any]
QueryEstimate = Dict[str, Any]
COST_PARAM_SET = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}

# Keep this contract aligned with the audited AP/JOB plan-diversity collectors.
# Cost/cardinality fields are deliberately excluded: this is an exact physical
# structure identity, not a near-plan or cost-distance heuristic.
EXACT_PHYSICAL_PLAN_KEYS = (
    "Node Type",
    "Parent Relationship",
    "Join Type",
    "Relation Name",
    "Alias",
    "Index Name",
    "Scan Direction",
    "Strategy",
    "Partial Mode",
    "Parallel Aware",
    "Async Capable",
    "Workers Planned",
    "Subplan Name",
)
EXACT_PLAN_FINGERPRINT_CONTRACT = "full_plannable_workload_exact_physical_structure_v1"
_RELATION_STATS_CACHE: Dict[Tuple[str, str, str], Dict[str, Dict[str, float]]] = {}
_RELATION_STATS_CACHE_LOCK = threading.Lock()


def _stats_from_values(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "p99": 0.0}
    sorted_vals = sorted(float(v) for v in values)
    p99_idx = min(int(len(sorted_vals) * 0.99), len(sorted_vals) - 1)
    return {
        "min": float(sorted_vals[0]),
        "max": float(sorted_vals[-1]),
        "avg": float(sum(sorted_vals) / len(sorted_vals)),
        "p99": float(sorted_vals[p99_idx]),
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _sql_code_mask(sql: str) -> str:
    """Return a same-length mask with SQL literals/comments blanked out."""

    masked = list(sql)
    length = len(sql)
    index = 0

    def blank(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if not sql[position].isspace():
                masked[position] = " "

    while index < length:
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            if end < 0:
                end = length
            blank(index, end)
            index = end
            continue
        if sql.startswith("/*", index):
            start = index
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            blank(start, index)
            continue
        if sql[index] in {"'", '"'}:
            quote = sql[index]
            start = index
            index += 1
            while index < length:
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            blank(start, index)
            continue
        if sql[index] == "$":
            tag_match = re.match(
                r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$",
                sql[index:],
            )
            if tag_match:
                tag = tag_match.group(0)
                start = index
                end = sql.find(tag, index + len(tag))
                index = length if end < 0 else end + len(tag)
                blank(start, index)
                continue
        index += 1
    return "".join(masked)


def rewrite_pgss_query_for_generic_explain(
    query_text: str,
) -> Tuple[str, Dict[str, Any]]:
    """Repair only grammar slots whose required type is proven by context."""

    original = str(query_text)
    mask = _sql_code_mask(original)
    replacements: List[Tuple[int, int, str, str]] = []

    extract_pattern = re.compile(
        r"\bextract\s*\(\s*(\$\d+)\s+from\b",
        flags=re.IGNORECASE,
    )
    for match in extract_pattern.finditer(mask):
        placeholder = original[match.start(1):match.end(1)]
        replacements.append(
            (
                match.start(),
                match.end(),
                f"date_part(CAST({placeholder} AS text),",
                "extract_parameter_field_to_date_part_text",
            )
        )

    date_offset_pattern = re.compile(
        r"cast\(\s*\$\d+\s+as\s+date\s*\)\s*\+\s*(\$\d+)\b",
        flags=re.IGNORECASE,
    )
    for match in date_offset_pattern.finditer(mask):
        placeholder = original[match.start(1):match.end(1)]
        replacements.append(
            (
                match.start(1),
                match.end(1),
                f"CAST({placeholder} AS integer)",
                "date_offset_parameter_to_integer",
            )
        )

    date_column_duration_offset_pattern = re.compile(
        r"\bdate\s*\(\s*[A-Za-z_][A-Za-z0-9_.]*\s*\)"
        r"\s*\+\s*duration\s*\+\s*(\$\d+)\b",
        flags=re.IGNORECASE,
    )
    for match in date_column_duration_offset_pattern.finditer(mask):
        placeholder = original[match.start(1):match.end(1)]
        replacements.append(
            (
                match.start(1),
                match.end(1),
                f"CAST({placeholder} AS integer)",
                "date_column_duration_offset_parameter_to_integer",
            )
        )

    date_literal_pattern = re.compile(
        r"\bdate\s+(\$\d+)\b",
        flags=re.IGNORECASE,
    )
    for match in date_literal_pattern.finditer(mask):
        placeholder = original[match.start(1):match.end(1)]
        replacements.append(
            (
                match.start(),
                match.end(),
                f"CAST({placeholder} AS date)",
                "date_literal_parameter_to_cast",
            )
        )

    parameter_addition_pattern = re.compile(
        r"(\$\d+)\s*\+\s*(\$\d+)\b",
        flags=re.IGNORECASE,
    )
    for match in parameter_addition_pattern.finditer(mask):
        left = original[match.start(1):match.end(1)]
        right = original[match.start(2):match.end(2)]
        replacements.append(
            (
                match.start(),
                match.end(),
                f"CAST({left} AS numeric) + CAST({right} AS numeric)",
                "ambiguous_parameter_addition_to_numeric",
            )
        )

    sum_case_pattern = re.compile(
        r"\bsum\s*\(\s*case\b",
        flags=re.IGNORECASE,
    )
    case_token_pattern = re.compile(r"\b(case|end)\b", flags=re.IGNORECASE)
    branch_pattern = re.compile(
        r"\b(?:then|else)\s+(\$\d+)\b(?=\s+(?:when|else|end)\b)",
        flags=re.IGNORECASE,
    )
    for aggregate_match in sum_case_pattern.finditer(mask):
        case_start = mask.lower().find("case", aggregate_match.start())
        if case_start < 0:
            continue
        depth = 0
        case_end: Optional[int] = None
        for token in case_token_pattern.finditer(mask, case_start):
            if token.group(1).lower() == "case":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    case_end = token.end()
                    break
        if case_end is None:
            continue
        for branch_match in branch_pattern.finditer(mask, case_start, case_end):
            placeholder = original[
                branch_match.start(1):branch_match.end(1)
            ]
            replacements.append(
                (
                    branch_match.start(1),
                    branch_match.end(1),
                    f"CAST({placeholder} AS numeric)",
                    "sum_case_scalar_parameter_to_numeric",
                )
            )

    replacements.sort(key=lambda item: (item[0], item[1]))
    for previous, current in zip(replacements, replacements[1:]):
        if current[0] < previous[1]:
            raise RuntimeError(
                "generic EXPLAIN SQL rewrites overlap; refusing unsafe rewrite"
            )
    rewritten = original
    for start, end, replacement, _rule in reversed(replacements):
        rewritten = rewritten[:start] + replacement + rewritten[end:]
    rules = [rule for _start, _end, _replacement, rule in replacements]
    return rewritten, {
        "contract": "pgss_generic_explain_rewrite_v1",
        "original_sql_sha256": hashlib.sha256(
            original.encode("utf-8")
        ).hexdigest(),
        "rewritten_sql_sha256": hashlib.sha256(
            rewritten.encode("utf-8")
        ).hexdigest(),
        "rewrite_rules": rules,
        "rewrite_count": len(rules),
        "changed": rewritten != original,
        "literals_and_comments_masked": True,
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return default


def _ceil_div(a: float, b: float) -> int:
    if b <= 0:
        return 0
    return max(0, int(math.ceil(max(0.0, a) / b)))


def _statement_kind(query_text: str) -> str:
    text = str(query_text or "").strip().lower()
    if not text:
        return "unknown"
    # A parenthesized SELECT followed by UNION/INTERSECT/EXCEPT is a valid
    # PostgreSQL query.  HyBench uses this form for one native AP statement.
    # Strip only leading grouping parentheses for statement classification;
    # the SQL sent to EXPLAIN remains byte-for-byte unchanged.
    text = text.lstrip("(").lstrip()
    if text.startswith("with"):
        lower = text
        if " insert " in lower or lower.startswith("with insert"):
            return "insert"
        if " update " in lower or lower.startswith("with update"):
            return "update"
        if " delete " in lower or lower.startswith("with delete"):
            return "delete"
        return "select"
    return text.split(None, 1)[0]


def _is_plannable_query(query_text: str) -> bool:
    return _statement_kind(query_text) in {"select", "insert", "update", "delete"}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _exact_physical_plan_structure(node: Any) -> Dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    result = {
        key: node[key]
        for key in EXACT_PHYSICAL_PLAN_KEYS
        if key in node
    }
    children = node.get("Plans")
    if isinstance(children, list):
        result["Plans"] = [
            _exact_physical_plan_structure(child)
            for child in children
            if isinstance(child, dict)
        ]
    return result


def _normalized_plan_query_text(query_text: Any) -> str:
    return re.sub(r"\s+", " ", str(query_text or "").strip().rstrip(";"))


def _query_has_unproven_dynamic_binding(query_text: Any) -> bool:
    text = str(query_text or "")
    # A generic or guessed custom plan is useful for resource estimation, but it
    # is not proof of the physical plan for the real runtime bind values.
    return bool(re.search(r"\$\d+\b", text) or "?" in text)


def _build_exact_plan_fingerprint_metadata(
    *,
    workload_query_count: int,
    plan_queries: Sequence[Dict[str, Any]],
    explained_queries: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build exact plan identities only with complete, fully bound evidence."""

    expected_count = len(plan_queries)
    successful = [
        item
        for item in explained_queries
        if not item.get("error") and isinstance(item.get("plan_root"), dict)
    ]
    # Missing result rows count as failures too; coverage must never look better
    # merely because a collector returned fewer rows than it was asked for.
    failed_count = max(0, expected_count - len(successful))
    dynamic_binding_count = sum(
        1
        for item in plan_queries
        if _query_has_unproven_dynamic_binding(item.get("query"))
    )

    fingerprints: Dict[str, str] = {}
    duplicate_query_identity_count = 0
    for item in successful:
        normalized_sql = _normalized_plan_query_text(item.get("query"))
        query_identity = "sql-sha256:" + hashlib.sha256(
            normalized_sql.encode("utf-8")
        ).hexdigest()
        if query_identity in fingerprints:
            duplicate_query_identity_count += 1
            continue
        structure = _exact_physical_plan_structure(item.get("plan_root"))
        fingerprints[query_identity] = (
            "exact-physical-sha256:" + _canonical_sha256(structure)
        )

    complete = bool(
        expected_count > 0
        and len(explained_queries) == expected_count
        and len(successful) == expected_count
        and failed_count == 0
        and dynamic_binding_count == 0
        and duplicate_query_identity_count == 0
        and len(fingerprints) == expected_count
    )
    if expected_count <= 0:
        reason = "no_plannable_workload_queries"
    elif dynamic_binding_count:
        reason = "dynamic_binding_evidence_missing"
    elif duplicate_query_identity_count:
        reason = "duplicate_query_identity_is_ambiguous"
    elif len(explained_queries) != expected_count or failed_count:
        reason = "incomplete_workload_explain_coverage"
    elif len(fingerprints) != expected_count:
        reason = "incomplete_plan_fingerprint_coverage"
    else:
        reason = "complete"

    coverage = {
        "contract": EXACT_PLAN_FINGERPRINT_CONTRACT,
        "workload_query_count": max(0, int(workload_query_count)),
        "plannable_query_count": expected_count,
        "explain_result_count": len(explained_queries),
        "successful_explain_count": len(successful),
        "failed_explain_count": failed_count,
        "dynamic_binding_query_count": dynamic_binding_count,
        "dynamic_binding_evidence_complete": dynamic_binding_count == 0,
        "duplicate_query_identity_count": duplicate_query_identity_count,
        "fingerprint_count": len(fingerprints),
        "complete": complete,
        "reason": reason,
        "missing_policy": "fail_open_unique_cluster",
    }
    metadata: Dict[str, Any] = {
        "plan_fingerprint_contract": EXACT_PLAN_FINGERPRINT_CONTRACT,
        "plan_fingerprint_coverage": coverage,
    }
    if complete:
        workload_fingerprint = (
            "workload-exact-physical-sha256:" + _canonical_sha256(fingerprints)
        )
        metadata.update(
            {
                "plan_fingerprints": fingerprints,
                "plan_fingerprint": workload_fingerprint,
                "workload_plan_fingerprint": workload_fingerprint,
                "plan_similarity_cluster_id": workload_fingerprint,
            }
        )
    return metadata


class PlanResourceEstimator:
    """PlanResourceEstimator."""

    def __init__(
        self,
        *,
        pg_host: str,
        pg_port: str,
        pg_user: str,
        pg_dbname: str,
        plan_capture_modes: Optional[Sequence[str]] = None,
        custom_plan_params: Optional[Dict[str, List[Any]]] = None,
        estimator_cfg: Optional[Dict[str, Any]] = None,
        psql_query_func: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.pg_host = pg_host
        self.pg_port = pg_port
        self.pg_user = pg_user
        self.pg_dbname = pg_dbname
        self.plan_capture_modes = [
            str(mode).strip().lower()
            for mode in (plan_capture_modes or ["generic"])
            if str(mode).strip()
        ] or ["generic"]
        self.custom_plan_params = custom_plan_params or {}
        self.estimator_cfg = estimator_cfg or {}
        self.psql_query_func = psql_query_func
        self.io_model_artifact = self._load_io_model_artifact()
        self.cpu_model_artifact = self._load_cpu_model_artifact()
        self.memory_model_artifact = self._load_memory_model_artifact()
        self.feature_predictor = ResourceFeaturePredictor(
            root=Path.cwd(),
            estimator_cfg=self.estimator_cfg,
        )
        self._joblib_model_cache: Dict[str, Any] = {}
        self._relation_stats_cache: Optional[Dict[str, Dict[str, float]]] = None
        self._physical_baseline_anchors: Dict[str, Dict[str, Any]] = {}

    def _physical_model_cfg(self) -> Dict[str, Any]:
        """Return the opt-in executor-physics model configuration."""
        configured = self.estimator_cfg.get("physical_model")
        if isinstance(configured, dict):
            return configured
        shared_cfg = self.estimator_cfg.get("shared") or {}
        configured = shared_cfg.get("physical_model")
        return configured if isinstance(configured, dict) else {}

    def _physical_model_enabled(self) -> bool:
        return _safe_bool(self._physical_model_cfg().get("enabled"), False)

    def _physical_pruning_calibrated(self) -> bool:
        return _safe_bool(
            self._physical_model_cfg().get("error_bounds_calibrated"),
            False,
        )

    def _load_relation_stats(self) -> Dict[str, Dict[str, float]]:
        """
        Load planner-independent table/index size statistics once per estimator.

        Both qualified and unqualified names are retained.  Ambiguous unqualified
        names are deliberately removed so a same-named relation in another schema
        cannot silently provide the wrong physical size.
        """
        if self._relation_stats_cache is not None:
            return self._relation_stats_cache
        cache_key = (self.pg_host, self.pg_port, self.pg_dbname)
        with _RELATION_STATS_CACHE_LOCK:
            cached = _RELATION_STATS_CACHE.get(cache_key)
            if cached is not None:
                self._relation_stats_cache = copy.deepcopy(cached)
                return self._relation_stats_cache

        sql = """
SELECT n.nspname, c.relname, c.relpages, c.reltuples, c.relallvisible, c.relkind,
       COALESCE(s.n_live_tup, 0), COALESCE(s.n_mod_since_analyze, 0),
       COALESCE(
           EXTRACT(EPOCH FROM (
               clock_timestamp() - COALESCE(s.last_autoanalyze, s.last_analyze)
           )),
           -1
       )
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_all_tables AS s
  ON s.schemaname = n.nspname AND s.relname = c.relname
WHERE n.nspname <> 'information_schema'
  AND c.relkind IN ('r', 'p', 'i', 'I', 'm')
ORDER BY n.nspname, c.relname
""".strip()
        stats: Dict[str, Dict[str, float]] = {}
        short_names: Dict[str, List[Dict[str, float]]] = {}
        try:
            output = self._psql_query(sql)
        except Exception:
            output = ""
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            schema_name = parts[0].strip()
            relation_name = parts[1].strip()
            if not schema_name or not relation_name:
                continue
            record = {
                "relpages": max(0.0, _safe_float(parts[2], 0.0)),
                "reltuples": max(0.0, _safe_float(parts[3], 0.0)),
                "relallvisible": max(0.0, _safe_float(parts[4], 0.0)),
                "relkind": parts[5].strip(),
                "n_live_tup": max(0.0, _safe_float(parts[6], 0.0)),
                "n_mod_since_analyze": max(0.0, _safe_float(parts[7], 0.0)),
                "analyze_age_seconds": _safe_float(parts[8], -1.0),
            }
            stats[f"{schema_name}.{relation_name}"] = record
            short_names.setdefault(relation_name, []).append(record)
        for relation_name, records in short_names.items():
            if len(records) == 1:
                stats[relation_name] = records[0]
        self._relation_stats_cache = stats
        with _RELATION_STATS_CACHE_LOCK:
            _RELATION_STATS_CACHE[cache_key] = copy.deepcopy(stats)
        return stats

    def _apply_physical_delta_anchor(
        self,
        *,
        resource_type: str,
        formula_value: float,
        workload_context: Dict[str, Any],
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Anchor physical-feature deltas at the first measured baseline.

        The measured baseline supplies the absolute level.  Candidate plans only
        contribute their physical-work delta, which avoids division by a zero
        operator-memory baseline.
        """
        if not self._physical_model_enabled():
            return formula_value, {"applied": False, "source": "disabled"}
        physical_cfg = self._physical_model_cfg()
        if not _safe_bool(
            physical_cfg.get("capture_first_point_as_baseline"),
            True,
        ):
            return formula_value, {"applied": False, "source": "capture_disabled"}

        runtime_raw = workload_context.get("runtime_resource_raw") or {}
        actual_value = (
            _safe_float(runtime_raw.get(resource_type), 0.0)
            if isinstance(runtime_raw, dict)
            else 0.0
        )
        if actual_value <= 0.0:
            explicit = workload_context.get("baseline_runtime_resource_raw") or {}
            if isinstance(explicit, dict):
                actual_value = _safe_float(explicit.get(resource_type), 0.0)
        if actual_value <= 0.0:
            return formula_value, {"applied": False, "source": "missing_baseline_actual"}

        anchor = self._physical_baseline_anchors.get(resource_type)
        explicit_anchors = workload_context.get("physical_baseline_anchors") or {}
        if anchor is None and isinstance(explicit_anchors, dict):
            explicit_anchor = explicit_anchors.get(resource_type)
            if isinstance(explicit_anchor, dict):
                explicit_actual = _safe_float(
                    explicit_anchor.get(
                        "actual_value",
                        explicit_anchor.get("baseline_actual"),
                    ),
                    0.0,
                )
                explicit_formula = _safe_float(
                    explicit_anchor.get(
                        "formula_value",
                        explicit_anchor.get("baseline_formula"),
                    ),
                    -1.0,
                )
                explicit_scale = _safe_float(
                    explicit_anchor.get("delta_scale"),
                    -1.0,
                )
                if (
                    explicit_actual > 0.0
                    and explicit_formula >= 0.0
                    and explicit_scale >= 0.0
                ):
                    anchor = {
                        "actual_value": explicit_actual,
                        "formula_value": explicit_formula,
                        "delta_scale": explicit_scale,
                    }
                    baseline_fingerprint = str(
                        explicit_anchor.get("baseline_plan_fingerprint") or ""
                    ).strip()
                    if baseline_fingerprint:
                        anchor["baseline_plan_fingerprint"] = baseline_fingerprint
                    self._physical_baseline_anchors[resource_type] = anchor
        if anchor is None:
            configured_scale = (physical_cfg.get("delta_scale") or {}).get(resource_type)
            if configured_scale not in (None, ""):
                delta_scale = max(0.0, _safe_float(configured_scale, 1.0))
            elif resource_type == "memory":
                delta_scale = 1.0
            else:
                delta_scale = actual_value / max(float(formula_value), 1e-12)
            anchor = {
                "actual_value": actual_value,
                "formula_value": max(0.0, float(formula_value)),
                "delta_scale": delta_scale,
            }
            baseline_fingerprint = str(
                workload_context.get("current_physical_plan_fingerprint") or ""
            ).strip()
            if baseline_fingerprint:
                anchor["baseline_plan_fingerprint"] = baseline_fingerprint
            self._physical_baseline_anchors[resource_type] = anchor

        current_fingerprint = str(
            workload_context.get("current_physical_plan_fingerprint") or ""
        ).strip()
        same_physical_plan = bool(
            current_fingerprint
            and anchor.get("baseline_plan_fingerprint")
            and current_fingerprint == anchor.get("baseline_plan_fingerprint")
        )
        relative_formula_delta = abs(
            float(formula_value) - anchor["formula_value"]
        ) / max(abs(anchor["formula_value"]), 1e-12)
        configured_min_signal = (
            physical_cfg.get("min_direction_relative_delta") or {}
        )
        default_min_signal = {
            "cpu": 0.005,
            "memory": 0.01,
            "io": 0.01,
            "io_read": 0.01,
            "io_write": 0.01,
        }.get(resource_type, 0.01)
        min_direction_relative_delta = max(
            0.0,
            _safe_float(
                configured_min_signal.get(resource_type)
                if isinstance(configured_min_signal, dict)
                else None,
                default_min_signal,
            ),
        )
        direction_signal_above_floor = (
            relative_formula_delta >= min_direction_relative_delta
        )
        if same_physical_plan or not direction_signal_above_floor:
            predicted = anchor["actual_value"]
        else:
            predicted = max(
                0.0,
                anchor["actual_value"]
                + (float(formula_value) - anchor["formula_value"])
                * anchor["delta_scale"],
            )
        return predicted, {
            "applied": True,
            "source": "first_measured_baseline_delta_anchor",
            "resource_type": resource_type,
            "baseline_actual": anchor["actual_value"],
            "baseline_formula": anchor["formula_value"],
            "candidate_formula": float(formula_value),
            "delta_scale": anchor["delta_scale"],
            "baseline_plan_fingerprint": anchor.get(
                "baseline_plan_fingerprint"
            ),
            "candidate_plan_fingerprint": current_fingerprint or None,
            "same_physical_plan": same_physical_plan,
            "relative_formula_delta": relative_formula_delta,
            "min_direction_relative_delta": min_direction_relative_delta,
            "direction_signal_above_floor": direction_signal_above_floor,
            "prediction": predicted,
        }

    def estimate_resources_for_queries(
        self,
        queries: Sequence[Dict[str, Any]],
        *,
        params: Optional[Dict[str, float]] = None,
        workload_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]], Dict[str, Any]]:
        workload_context = dict(workload_context or {})
        if params:
            workload_context["plan_cost_parameters"] = {
                name: _safe_float(params.get(name), 0.0)
                for name in sorted(COST_PARAM_SET)
                if name in params
            }
        cfg = self.estimator_cfg
        logging_cfg = cfg.get("logging") or {}
        log_topn = max(0, int(logging_cfg.get("log_topn", 10)))
        resource_types = [
            str(item).strip()
            for item in cfg.get(
                "resource_types",
                ["memory", "cpu", "io"],
            )
            if str(item).strip()
        ]
        shared_cfg = cfg.get("shared") or {}
        explain_workers = max(1, int(shared_cfg.get("explain_workers", 8)))
        calibration_factor = _safe_float(shared_cfg.get("calibration_factor"), 1.0)

        plan_queries: List[Dict[str, Any]] = []
        for item in queries:
            query_text = str(item.get("query") or "").strip().rstrip(";")
            if not query_text:
                continue
            if not _is_plannable_query(query_text):
                continue
            plan_queries.append(item)

        explained_queries = self._collect_query_plans(
            plan_queries,
            explain_workers=explain_workers,
            params=params,
        )
        if plan_queries and explain_workers > 1 and explained_queries:
            failed_explains = [item for item in explained_queries if item.get("error")]
            if len(failed_explains) == len(explained_queries):
                serial_retry = self._collect_query_plans(
                    plan_queries,
                    explain_workers=1,
                    params=params,
                )
                serial_failed = [item for item in serial_retry if item.get("error")]
                if serial_retry and len(serial_failed) < len(serial_retry):
                    explained_queries = serial_retry
        plan_fingerprint_metadata = _build_exact_plan_fingerprint_metadata(
            workload_query_count=len(queries),
            plan_queries=plan_queries,
            explained_queries=explained_queries,
        )
        current_plan_fingerprint = plan_fingerprint_metadata.get(
            "workload_plan_fingerprint"
        )
        if current_plan_fingerprint:
            workload_context["current_physical_plan_fingerprint"] = (
                current_plan_fingerprint
            )
        query_estimates = self._build_query_estimates(
            explained_queries=explained_queries,
            workload_context=workload_context,
        )
        failed_estimates = [item for item in query_estimates if item.get("error")]
        if plan_queries and len(failed_estimates) == len(query_estimates):
            error_samples = self._query_errors(query_estimates)[:3]
            raise RuntimeError(
                "Plan resource estimator failed to explain every plannable query; "
                f"plannable_query_count={len(plan_queries)}, "
                f"failed_query_count={len(failed_estimates)}, "
                f"errors={error_samples}"
            )
        if (
            failed_estimates
            and self._physical_model_enabled()
            and _safe_bool(
                self._physical_model_cfg().get("require_complete_explain"),
                True,
            )
        ):
            raise RuntimeError(
                "Physical direction estimator requires complete EXPLAIN coverage; "
                f"plannable_query_count={len(plan_queries)}, "
                f"failed_query_count={len(failed_estimates)}, "
                f"errors={self._query_errors(failed_estimates)[:3]}"
            )
        replay_query_count = sum(
            max(1, _safe_int(item.get("replay_count", item.get("calls", 1)), 1))
            for item in plan_queries
        )
        distinct_query_count = len(plan_queries)

        resource_avg: Dict[str, float] = {}
        resource_stats: Dict[str, Dict[str, float]] = {}
        resource_breakdown: Dict[str, Dict[str, Any]] = {}
        resource_logs: List[ResourceLog] = []
        estimated_resource_raw: Dict[str, float] = {}
        estimated_resource_units: Dict[str, str] = {}
        estimated_resource_raw_units: Dict[str, str] = {}
        physical_direction_support: Dict[str, bool] = {}

        for resource_type in resource_types:
            result = self._build_resource_result(
                resource_type=resource_type,
                query_estimates=query_estimates,
                workload_context=workload_context,
                calibration_factor=calibration_factor,
                explain_workers=explain_workers,
            )
            normalized_value = float(result.get("value", result.get("raw_value", 0.0)))
            raw_value = float(result.get("raw_value", normalized_value))
            resource_avg[resource_type] = normalized_value
            estimated_resource_raw[resource_type] = raw_value
            estimated_resource_units[resource_type] = str(result.get("normalized_unit") or result.get("unit") or "")
            estimated_resource_raw_units[resource_type] = str(result.get("unit") or "")
            stats_values = [normalized_value]
            resource_stats[resource_type] = _stats_from_values(stats_values)
            summarized = self._summarize_result_for_logging(result, topn=log_topn)
            resource_breakdown[resource_type] = summarized
            resource_logs.append(self._build_resource_estimation_log(summarized))
            physical_direction_support[resource_type] = bool(
                result.get("physical_direction_supported", True)
            )

        if "memory" in resource_breakdown:
            resource_stats["memory_source"] = {"value": "plan-estimator"}
        resource_stats["query_count"] = {"value": replay_query_count}
        concurrency_value = self._concurrency_value(workload_context)
        resource_stats["concurrency"] = {
            "min": float(concurrency_value),
            "max": float(concurrency_value),
            "avg": float(concurrency_value),
            "p99": float(concurrency_value),
        }

        metadata = {
            "resource_estimation_source": "plan-estimator",
            "resource_estimation_logs": resource_logs,
            "resource_breakdown": resource_breakdown,
            "estimated_resource_raw": estimated_resource_raw,
            "estimated_resource_units": estimated_resource_units,
            "estimated_resource_raw_units": estimated_resource_raw_units,
            "query_count": replay_query_count,
            "distinct_query_count": distinct_query_count,
            "log_topn": log_topn,
            "workload_context": workload_context,
            "physical_model_enabled": self._physical_model_enabled(),
            "physical_direction_support": physical_direction_support,
            "optimistic_interval_pruning_allowed": (
                all(physical_direction_support.values())
                and self._physical_pruning_calibrated()
                if self._physical_model_enabled()
                else True
            ),
        }
        if self._physical_model_enabled():
            metadata["physical_query_work"] = self._physical_query_work_snapshot(
                query_estimates
            )
        metadata.update(plan_fingerprint_metadata)
        return resource_avg, resource_stats, metadata

    def _build_query_estimates(
        self,
        *,
        explained_queries: Sequence[Dict[str, Any]],
        workload_context: Dict[str, Any],
    ) -> List[QueryEstimate]:
        query_estimates: List[QueryEstimate] = []
        relation_stats: Dict[str, Dict[str, float]] = {}
        if self._physical_model_enabled():
            configured_stats = workload_context.get("relation_stats")
            if isinstance(configured_stats, dict):
                relation_stats = configured_stats
            else:
                relation_stats = self._load_relation_stats()
        for explained in explained_queries:
            query_text = str(explained.get("query") or "").strip().rstrip(";")
            replay_count = max(
                1,
                _safe_int(explained.get("replay_count", explained.get("calls", 1)), 1),
            )
            queryid = explained.get("queryid")
            if explained.get("error"):
                query_estimates.append(
                    {
                        "queryid": queryid,
                        "query": query_text,
                        "replay_count": replay_count,
                        "statement_kind": _statement_kind(query_text),
                        "error": explained.get("error"),
                    }
                )
                continue

            plan_root = explained.get("plan_root")
            node_features = self._extract_plan_node_features(
                plan_root,
                relation_stats=relation_stats,
            )
            normalized_sql = _normalized_plan_query_text(query_text)
            query_identity = str(queryid or "").strip() or (
                "sql-sha256:"
                + hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()
            )
            physical_plan_fingerprint = (
                "exact-physical-sha256:"
                + _canonical_sha256(
                    _exact_physical_plan_structure(plan_root)
                )
            )
            query_estimates.append(
                {
                    "queryid": queryid,
                    "query_identity": query_identity,
                    "query": query_text,
                    "replay_count": replay_count,
                    "statement_kind": _statement_kind(query_text),
                    "plan_mode": explained.get("plan_mode"),
                    "plan_root": plan_root.get("Node Type") if isinstance(plan_root, dict) else None,
                    "physical_plan_fingerprint": physical_plan_fingerprint,
                    "nodes": node_features,
                    "query_resources": self._estimate_query_resources(
                        query_text=query_text,
                        node_features=node_features,
                        workload_context=workload_context,
                    ),
                }
            )
        return query_estimates

    def _build_resource_result(
        self,
        *,
        resource_type: str,
        query_estimates: Sequence[QueryEstimate],
        workload_context: Dict[str, Any],
        calibration_factor: float,
        explain_workers: int,
    ) -> Dict[str, Any]:
        if resource_type == "memory":
            return self._build_memory_result(
                query_estimates=query_estimates,
                workload_context=workload_context,
                calibration_factor=calibration_factor,
                explain_workers=explain_workers,
            )
        if resource_type in ("io", "io_read", "io_write", "iops_read", "iops_write"):
            return self._build_io_result(
                resource_type=resource_type,
                query_estimates=query_estimates,
                workload_context=workload_context,
                calibration_factor=calibration_factor,
                explain_workers=explain_workers,
            )
        if resource_type == "cpu":
            return self._build_cpu_result(
                query_estimates=query_estimates,
                workload_context=workload_context,
                calibration_factor=calibration_factor,
                explain_workers=explain_workers,
            )
        return self._estimate_placeholder_resource(
            resource_type=resource_type,
            calibration_factor=calibration_factor,
            workload_context=workload_context,
        )

    def _physical_baseline_query_resource(
        self,
        *,
        query_estimate: QueryEstimate,
        resource_type: str,
        workload_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return frozen baseline work when this query's physical plan is unchanged."""
        if not self._physical_model_enabled():
            return {}
        baseline_work = workload_context.get("physical_baseline_query_work") or {}
        if not isinstance(baseline_work, dict):
            return {}
        query_identity = str(query_estimate.get("query_identity") or "").strip()
        baseline = baseline_work.get(query_identity)
        if not isinstance(baseline, dict):
            return {}
        if (
            str(baseline.get("physical_plan_fingerprint") or "")
            != str(query_estimate.get("physical_plan_fingerprint") or "")
        ):
            return {}
        resource = baseline.get(resource_type)
        return dict(resource) if isinstance(resource, dict) else {}

    def _physical_query_work_snapshot(
        self,
        query_estimates: Sequence[QueryEstimate],
    ) -> Dict[str, Dict[str, Any]]:
        snapshot: Dict[str, Dict[str, Any]] = {}
        for query_estimate in query_estimates:
            if query_estimate.get("error"):
                continue
            identity = str(query_estimate.get("query_identity") or "").strip()
            if not identity:
                continue
            resources = query_estimate.get("query_resources") or {}
            cpu = resources.get("cpu") or {}
            memory = resources.get("memory") or {}
            io = resources.get("io") or {}
            snapshot[identity] = {
                "physical_plan_fingerprint": query_estimate.get(
                    "physical_plan_fingerprint"
                ),
                "cpu": {
                    key: copy.deepcopy(cpu.get(key))
                    for key in (
                        "cpu_work_per_query",
                        "cpu_seconds_per_query",
                        "physical_direction_supported",
                    )
                },
                "memory": {
                    key: copy.deepcopy(memory.get(key))
                    for key in (
                        "mem_query_upper_bytes",
                        "mem_query_parallel_bytes",
                        "derived_memory_mb",
                        "physical_direction_supported",
                    )
                },
                "io": {
                    key: copy.deepcopy(io.get(key))
                    for key in (
                        "logical_read_blocks_per_query",
                        "seq_read_blocks_per_query",
                        "random_read_blocks_per_query",
                        "data_read_blocks_per_query",
                        "temp_read_blocks_per_query",
                        "temp_write_blocks_per_query",
                        "wal_write_blocks_per_query",
                        "read_blocks_per_query",
                        "write_blocks_per_query",
                        "read_bytes_per_query",
                        "write_bytes_per_query",
                        "physical_direction_supported",
                    )
                },
            }
        return snapshot

    def _load_io_model_artifact(self) -> Dict[str, Any]:
        io_cfg = self.estimator_cfg.get("io") or {}
        model_cfg = io_cfg.get("learned_model") or {}
        if not bool(model_cfg.get("enabled", False)):
            return {}
        model_path = str(model_cfg.get("path") or "config/generated/io_resource_models.json")
        path = Path(model_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            with path.open("r", encoding="utf-8") as f:
                artifact = json.load(f)
            if isinstance(artifact, dict):
                return artifact
        except (OSError, json.JSONDecodeError):
            if bool(model_cfg.get("required", False)):
                raise
        return {}

    def _load_cpu_model_artifact(self) -> Dict[str, Any]:
        cpu_cfg = self.estimator_cfg.get("cpu") or {}
        model_cfg = cpu_cfg.get("learned_model") or {}
        if not bool(model_cfg.get("enabled", False)):
            return {}
        model_path = str(model_cfg.get("path") or "config/generated/cpu_resource_models.json")
        path = Path(model_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            with path.open("r", encoding="utf-8") as f:
                artifact = json.load(f)
            if isinstance(artifact, dict):
                return artifact
        except (OSError, json.JSONDecodeError):
            if bool(model_cfg.get("required", False)):
                raise
        return {}

    def _load_memory_model_artifact(self) -> Dict[str, Any]:
        memory_cfg = self.estimator_cfg.get("memory") or {}
        model_cfg = memory_cfg.get("learned_model") or {}
        if not bool(model_cfg.get("enabled", False)):
            return {}
        model_path = str(model_cfg.get("path") or "config/generated/memory_resource_models.json")
        path = Path(model_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            with path.open("r", encoding="utf-8") as f:
                artifact = json.load(f)
            if isinstance(artifact, dict):
                return artifact
        except (OSError, json.JSONDecodeError):
            if bool(model_cfg.get("required", False)):
                raise
        return {}

    def _memory_model_set_name(self, workload_context: Dict[str, Any]) -> str:
        memory_cfg = self.estimator_cfg.get("memory") or {}
        model_cfg = memory_cfg.get("learned_model") or {}
        configured = str(model_cfg.get("model_set") or "").strip()
        if configured and configured.lower() != "auto":
            return configured
        explicit = str(workload_context.get("memory_model_set") or "").strip()
        if explicit:
            return explicit
        family = self._infer_workload_family(workload_context)
        if family == "H":
            return "AP_only"
        return str(self.memory_model_artifact.get("default_model_set") or "TP_Hybrid")

    def _ap_concurrency_value(self, workload_context: Dict[str, Any]) -> float:
        hybench_runtime = workload_context.get("hybench_runtime") or {}
        if isinstance(hybench_runtime, dict):
            mode = str(hybench_runtime.get("mode") or "").strip().lower()
            props = hybench_runtime.get("props") or {}
            if isinstance(props, dict):
                if mode == "runxp":
                    value = _safe_float(props.get("xapclient"), 0.0)
                    if value > 0.0:
                        return value
                if mode in ("runap", "run"):
                    value = _safe_float(props.get("apclient"), 0.0)
                    if value > 0.0:
                        return value
        if self._infer_workload_family(workload_context) == "H":
            return self._concurrency_value(workload_context)
        return 0.0

    def _predict_memory_model_mb(
        self,
        *,
        aggregate_features: Dict[str, float],
        workload_context: Dict[str, Any],
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        if self.feature_predictor:
            prediction = self.feature_predictor.predict_memory(
                features=aggregate_features,
                workload_context=workload_context,
            )
            if prediction.get("available"):
                return _safe_float(prediction.get("prediction_memory_mb"), 0.0), dict(prediction)
        if not self.memory_model_artifact:
            return None, {}
        model_set = self._memory_model_set_name(workload_context)
        model = ((self.memory_model_artifact.get("models") or {}).get(model_set) or {})
        if not model:
            return None, {}
        features = list(model.get("features") or [])
        coef = list(model.get("coef") or [])
        pred = _safe_float(model.get("intercept"), 0.0)
        contributions: List[Dict[str, float]] = []
        if model.get("model_type") != "constant":
            if not features or len(features) != len(coef):
                return None, {}
            for idx, feature in enumerate(features):
                raw = _safe_float(aggregate_features.get(feature), 0.0)
                contrib = _safe_float(coef[idx], 0.0) * raw
                pred += contrib
                contributions.append(
                    {
                        "feature": feature,
                        "raw": raw,
                        "contribution": contrib,
                    }
                )
        clip_min = model.get("clip_min")
        if clip_min not in (None, ""):
            pred = max(_safe_float(clip_min), pred)
        pred_before_anchor_calibration = pred
        pred, anchor_calibration = self._apply_baseline_anchor_calibration(model=model, prediction=pred)
        pred_before_error_bound = pred
        error_bound = self._prediction_error_bound(model=model)
        pred = max(0.0, pred + _safe_float(error_bound.get("value"), 0.0))
        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        return pred, {
            "model_set": model_set,
            "model_type": model.get("model_type"),
            "prediction_memory_mb": pred,
            "prediction_before_error_bound": pred_before_error_bound,
            "prediction_before_anchor_calibration": pred_before_anchor_calibration,
            "baseline_anchor_calibration": anchor_calibration,
            "error_bound": error_bound,
            "feature_count": len(features),
            "metrics": model.get("metrics"),
            "features": dict(aggregate_features),
            "top_contributions": contributions[:10],
            "notes": model.get("notes"),
        }

    def _cpu_model_set_name(self, workload_context: Dict[str, Any]) -> str:
        cpu_cfg = self.estimator_cfg.get("cpu") or {}
        model_cfg = cpu_cfg.get("learned_model") or {}
        configured = str(model_cfg.get("model_set") or "").strip()
        if configured and configured.lower() != "auto":
            return configured
        explicit = str(workload_context.get("cpu_model_set") or "").strip()
        if explicit:
            return explicit
        family = self._infer_workload_family(workload_context)
        if family == "H":
            return "H_serial"
        if family == "CH" and "CH_only" in (self.cpu_model_artifact.get("models") or {}):
            return "CH_only"
        if family == "C":
            return "C_only"
        return str(self.cpu_model_artifact.get("default_model_set") or "C_only")

    def _predict_cpu_model_seconds(
        self,
        *,
        aggregate_features: Dict[str, float],
        workload_context: Dict[str, Any],
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        def usable_prediction(prediction: Dict[str, Any]) -> bool:
            shared_cfg = self.estimator_cfg.get("shared") or {}
            epsilon = max(
                0.0,
                _safe_float(
                    shared_cfg.get("learned_cpu_min_raw_prediction_cpu_seconds_per_window"),
                    1e-9,
                ),
            )
            raw_prediction = prediction.get("prediction_before_anchor_calibration")
            if raw_prediction in (None, ""):
                raw_prediction = prediction.get("prediction_before_error_bound")
            raw_value = _safe_float(raw_prediction, float("nan"))
            fallback_reason = ""
            if not math.isfinite(raw_value) or raw_value <= epsilon:
                fallback_reason = "learned_cpu_raw_prediction_nonpositive_or_nonfinite"

            anchor = prediction.get("baseline_anchor_calibration")
            if not fallback_reason and isinstance(anchor, dict) and anchor.get("applied"):
                unclamped_delta_scale = _safe_float(
                    anchor.get("unclamped_delta_scale"),
                    float("nan"),
                )
                scale_min = max(
                    0.0,
                    _safe_float(
                        shared_cfg.get("learned_cpu_anchor_delta_scale_min"),
                        0.25,
                    ),
                )
                scale_max = max(
                    scale_min,
                    _safe_float(
                        shared_cfg.get("learned_cpu_anchor_delta_scale_max"),
                        4.0,
                    ),
                )
                if (
                    not math.isfinite(unclamped_delta_scale)
                    or unclamped_delta_scale < scale_min
                    or unclamped_delta_scale > scale_max
                ):
                    fallback_reason = "learned_cpu_anchor_delta_scale_out_of_bounds"
                    prediction["anchor_delta_scale_checked"] = unclamped_delta_scale
                    prediction["anchor_delta_scale_min"] = scale_min
                    prediction["anchor_delta_scale_max"] = scale_max

            if not fallback_reason:
                return True
            prediction.update(
                {
                    "available": False,
                    "fallback_to_plan_formula": True,
                    "fallback_reason": fallback_reason,
                    "raw_prediction_checked": raw_value,
                    "raw_prediction_minimum": epsilon,
                }
            )
            return False

        if self.feature_predictor:
            prediction = self.feature_predictor.predict_cpu(
                features=aggregate_features,
                workload_context=workload_context,
            )
            if prediction.get("available"):
                prediction = dict(prediction)
                if usable_prediction(prediction):
                    return _safe_float(prediction.get("prediction_cpu_seconds_per_window"), 0.0), prediction
                return None, prediction
        if not self.cpu_model_artifact:
            return None, {}
        model_set = self._cpu_model_set_name(workload_context)
        model = ((self.cpu_model_artifact.get("models") or {}).get(model_set) or {})
        artifact_pred, artifact_breakdown = self._predict_joblib_artifact(
            model=model,
            aggregate_features=aggregate_features,
            prediction_key="prediction_cpu_seconds_per_window",
        )
        if artifact_pred is not None:
            artifact_breakdown["model_set"] = model_set
            artifact_breakdown["metrics"] = model.get("metrics")
            if usable_prediction(artifact_breakdown):
                return artifact_pred, artifact_breakdown
            return None, artifact_breakdown
        features = list(model.get("features") or [])
        coef = list(model.get("coef") or [])
        means = list(model.get("feature_mean") or [])
        scales = list(model.get("feature_scale") or [])
        if not features or len(features) != len(coef):
            return None, {}
        pred = _safe_float(model.get("intercept"), 0.0)
        contributions: List[Dict[str, float]] = []
        for idx, feature in enumerate(features):
            raw = _safe_float(aggregate_features.get(feature), 0.0)
            if model.get("model_type") == "ridge_standardized":
                mean = _safe_float(means[idx] if idx < len(means) else 0.0, 0.0)
                scale = _safe_float(scales[idx] if idx < len(scales) else 1.0, 1.0)
                if abs(scale) < 1e-12:
                    scale = 1.0
                model_value = (raw - mean) / scale
            else:
                model_value = raw
            contrib = _safe_float(coef[idx], 0.0) * model_value
            pred += contrib
            contributions.append(
                {
                    "feature": feature,
                    "raw": raw,
                    "model_value": model_value,
                    "contribution": contrib,
                }
            )
        if model.get("target_transform") == "log1p_expm1":
            pred = math.expm1(min(50.0, pred))
        clip_min = model.get("clip_min")
        if clip_min not in (None, ""):
            pred = max(_safe_float(clip_min), pred)
        pred_before_anchor_calibration = pred
        pred, anchor_calibration = self._apply_baseline_anchor_calibration(model=model, prediction=pred)
        pred_before_error_bound = pred
        error_bound = self._prediction_error_bound(model=model)
        pred = max(0.0, pred + _safe_float(error_bound.get("value"), 0.0))
        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        breakdown = {
            "model_set": model_set,
            "model_type": model.get("model_type"),
            "prediction_cpu_seconds_per_window": pred,
            "prediction_before_error_bound": pred_before_error_bound,
            "prediction_before_anchor_calibration": pred_before_anchor_calibration,
            "baseline_anchor_calibration": anchor_calibration,
            "error_bound": error_bound,
            "feature_count": len(features),
            "metrics": model.get("metrics"),
            "top_contributions": contributions[:10],
        }
        if usable_prediction(breakdown):
            return pred, breakdown
        return None, breakdown

    def _load_joblib_artifact(self, model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        artifact_path = str(model.get("artifact_path") or "").strip()
        if not artifact_path:
            return None
        path = Path(artifact_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        key = str(path)
        if key in self._joblib_model_cache:
            return self._joblib_model_cache[key]
        try:
            import joblib  # type: ignore

            artifact = joblib.load(path)
        except Exception:
            return None
        if not isinstance(artifact, dict):
            return None
        self._joblib_model_cache[key] = artifact
        return artifact

    def _predict_joblib_artifact(
        self,
        *,
        model: Dict[str, Any],
        aggregate_features: Dict[str, float],
        prediction_key: str,
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        artifact = self._load_joblib_artifact(model)
        if not artifact:
            return None, {}
        estimator = artifact.get("model")
        features = list(artifact.get("features") or model.get("features") or [])
        if estimator is None or not features:
            return None, {}

        values = [_safe_float(aggregate_features.get(feature), 0.0) for feature in features]
        scale_by = artifact.get("feature_scale_by")
        if isinstance(scale_by, dict):
            values = [
                value / max(_safe_float(scale_by.get(feature), 1.0), 1e-12)
                for feature, value in zip(features, values)
            ]
        try:
            try:
                import pandas as pd  # type: ignore

                x_input = pd.DataFrame([values], columns=features)
            except Exception:
                x_input = [values]
            pred = float(estimator.predict(x_input)[0])
        except Exception:
            return None, {}
        if artifact.get("target_transform") == "log1p_expm1":
            pred = math.expm1(min(50.0, pred))
        pred = max(_safe_float(artifact.get("clip_min"), 0.0), pred)
        pred_before_anchor_calibration = pred
        pred, anchor_calibration = self._apply_baseline_anchor_calibration(model=model, prediction=pred)
        pred_before_error_bound = pred
        error_bound = self._prediction_error_bound(model=model, artifact=artifact)
        pred = max(0.0, pred + _safe_float(error_bound.get("value"), 0.0))

        feature_values = [
            {"feature": feature, "value": value}
            for feature, value in sorted(
                zip(features, values),
                key=lambda item: abs(float(item[1])),
                reverse=True,
            )[:10]
        ]
        return pred, {
            "model_type": artifact.get("model_type") or model.get("model_type") or "joblib",
            "artifact_path": model.get("artifact_path"),
            prediction_key: pred,
            "prediction_before_error_bound": pred_before_error_bound,
            "prediction_before_anchor_calibration": pred_before_anchor_calibration,
            "baseline_anchor_calibration": anchor_calibration,
            "error_bound": error_bound,
            "feature_count": len(features),
            "features": dict(aggregate_features),
            "target_transform": artifact.get("target_transform"),
            "top_feature_values": feature_values,
        }

    def _apply_baseline_anchor_calibration(
        self,
        *,
        model: Dict[str, Any],
        prediction: float,
    ) -> Tuple[float, Dict[str, Any]]:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        if not _safe_bool(shared_cfg.get("apply_baseline_anchor_calibration", True), True):
            return prediction, {"applied": False, "source": "disabled"}
        record = (model or {}).get("baseline_anchor_calibration")
        if not isinstance(record, dict):
            return prediction, {"applied": False, "source": "missing"}

        baseline_pred = _safe_float(record.get("predicted_target_before_error_bound"), 0.0)
        actual_target = _safe_float(record.get("actual_target"), 0.0)
        delta_scale = _safe_float(record.get("delta_scale"), 1.0)
        if actual_target <= 0.0:
            return prediction, {"applied": False, "source": "invalid_actual", "detail": record}

        calibrated = max(0.0, actual_target + (float(prediction) - baseline_pred) * delta_scale)
        detail = dict(record)
        detail.update(
            {
                "applied": True,
                "input_prediction": float(prediction),
                "calibrated_prediction": calibrated,
            }
        )
        return calibrated, detail

    def _select_learned_point_prediction(
        self,
        *,
        prediction: float,
        breakdown: Dict[str, Any],
    ) -> float:
        """Keep residual bounds in ranking metadata instead of adding them to point estimates."""
        shared_cfg = self.estimator_cfg.get("shared") or {}
        stage = str(
            shared_cfg.get("learned_point_prediction_stage") or "after_error_bound"
        ).strip().lower()
        before_error_bound_stages = {
            "after_baseline_anchor_before_error_bound",
            "after_baseline_anchor_and_before_error_bound",
            "before_error_bound",
        }
        selected = float(prediction)
        if stage in before_error_bound_stages:
            candidate = _safe_float(
                breakdown.get("prediction_before_error_bound"),
                float("nan"),
            )
            if math.isfinite(candidate) and candidate > 0.0:
                selected = candidate
        breakdown["prediction_after_error_bound"] = float(prediction)
        breakdown["point_prediction_stage"] = stage
        breakdown["point_prediction_value"] = selected
        return selected

    def _prediction_error_bound(
        self,
        *,
        model: Dict[str, Any],
        artifact: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        if not _safe_bool(shared_cfg.get("apply_model_error_bound", True), True):
            return {"value": 0.0, "source": "disabled", "applied": False, "candidates": []}
        candidates: List[Dict[str, Any]] = []
        for source, container in (
            ("artifact_error_bound", artifact or {}),
            ("model_error_bound", model or {}),
            ("baseline_error_bound", model or {}),
        ):
            key = "baseline_error_bound" if source == "baseline_error_bound" else "error_bound"
            record = (container or {}).get(key)
            if isinstance(record, dict):
                candidates.append(
                    {
                        "source": source,
                        "value": _safe_float(record.get("value"), 0.0),
                        "unit": record.get("unit"),
                        "detail": record,
                    }
                )
        if not candidates:
            return {"value": 0.0, "source": "missing", "applied": False, "candidates": []}
        selected = max(candidates, key=lambda item: _safe_float(item.get("value"), 0.0))
        return {
            "value": _safe_float(selected.get("value"), 0.0),
            "unit": selected.get("unit"),
            "source": selected.get("source"),
            "applied": _safe_float(selected.get("value"), 0.0) > 0.0,
            "candidates": candidates,
        }

    def _io_model_set_name(self, workload_context: Dict[str, Any]) -> str:
        io_cfg = self.estimator_cfg.get("io") or {}
        model_cfg = io_cfg.get("learned_model") or {}
        configured = str(model_cfg.get("model_set") or "").strip()
        if configured and configured.lower() != "auto":
            return configured
        explicit = str(workload_context.get("io_model_set") or "").strip().upper()
        if explicit in ("H", "C", "CH"):
            return explicit
        family = self._infer_workload_family(workload_context)
        if family in ("H", "C", "CH"):
            return family
        return str(self.io_model_artifact.get("default_model_set") or "CH")

    def _infer_workload_family(self, workload_context: Dict[str, Any]) -> str:
        """Infer H/C/CH from workload metadata, then from runtime shape."""
        for key in ("model_set", "workload_type", "scenario", "workload_family"):
            value = str(workload_context.get(key) or "").strip()
            normalized = value.lower().replace("-", "_")
            if normalized in ("h", "tpch", "tpc_h", "ap", "runap", "h_serial"):
                return "H"
            if normalized in ("c", "tpcc", "tpc_c", "tp", "runtp", "c_only"):
                return "C"
            if normalized in ("ch", "hc", "mixed", "xp", "runxp", "hybench_xp"):
                return "CH"

        hybench_runtime = workload_context.get("hybench_runtime") or {}
        if isinstance(hybench_runtime, dict):
            mode = str(hybench_runtime.get("mode") or "").strip().lower()
            props = hybench_runtime.get("props") or {}
            configured = _safe_float(hybench_runtime.get("configured_concurrency"), 0.0)
            if mode == "runxp":
                return "CH"
            if mode == "runtp":
                return "C"
            if mode == "runap":
                return "H" if configured <= 1.5 else "CH"
            if mode == "run":
                tp = _safe_float(props.get("tpclient") if isinstance(props, dict) else 0.0, 0.0)
                ap = _safe_float(props.get("apclient") if isinstance(props, dict) else 0.0, 0.0)
                if tp > 0.0 and ap > 0.0:
                    return "CH"
                if tp > 0.0:
                    return "C"
                if ap > 0.0:
                    return "H" if configured <= 1.5 else "CH"

        benchmark = workload_context.get("benchmark") or {}
        if isinstance(benchmark, dict):
            has_tp = any(_safe_float(benchmark.get(key), 0.0) > 0.0 for key in ("tp_tps", "xp_tps", "tps"))
            has_ap = any(_safe_float(benchmark.get(key), 0.0) > 0.0 for key in ("ap_qps", "xp_qps", "qps"))
            if has_tp and has_ap:
                return "CH"
            if has_tp:
                return "C"
            if has_ap:
                return "H"

        command_text = " ".join(
            str(workload_context.get(key) or "")
            for key in ("workload_cmd", "benchmark_cmd", "command")
        ).lower()
        if "runxp" in command_text:
            return "CH"
        if "runtp" in command_text or "tpcc" in command_text:
            return "C"
        if "runap" in command_text or "tpcds" in command_text or "tpch" in command_text:
            concurrency = self._concurrency_value(workload_context)
            return "H" if concurrency <= 1.5 else "CH"

        concurrency = self._concurrency_value(workload_context)
        sql_count = _safe_int(workload_context.get("sql_count"), 0)
        if concurrency <= 1.5:
            return "H"
        if sql_count > 200:
            return "CH"
        return "C"

    def _predict_io_model_mb(
        self,
        *,
        target_name: str,
        aggregate_features: Dict[str, float],
        workload_context: Dict[str, Any],
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        def usable_prediction(prediction: Dict[str, Any]) -> bool:
            shared_cfg = self.estimator_cfg.get("shared") or {}
            epsilon = max(
                0.0,
                _safe_float(
                    shared_cfg.get("learned_io_min_raw_prediction_mb_per_window"),
                    1e-9,
                ),
            )
            raw_prediction = prediction.get("prediction_before_anchor_calibration")
            if raw_prediction in (None, ""):
                raw_prediction = prediction.get("prediction_before_error_bound")
            raw_value = _safe_float(raw_prediction, float("nan"))
            if math.isfinite(raw_value) and raw_value > epsilon:
                return True
            prediction.update(
                {
                    "available": False,
                    "fallback_to_plan_formula": True,
                    "fallback_reason": "learned_io_raw_prediction_nonpositive_or_nonfinite",
                    "raw_prediction_checked": raw_value,
                    "raw_prediction_minimum": epsilon,
                }
            )
            return False

        io_cfg = self.estimator_cfg.get("io") or {}
        model_cfg = io_cfg.get("learned_model") or {}
        configured = str(model_cfg.get("model_set") or "").strip().lower()
        auto_select = (
            configured == "auto"
            and _safe_bool(model_cfg.get("auto_select_by_baseline_actual"), False)
        )

        if self.feature_predictor and not (auto_select and self.io_model_artifact):
            prediction = self.feature_predictor.predict_io(
                target_name=target_name,
                features=aggregate_features,
                workload_context=workload_context,
            )
            if prediction.get("available"):
                prediction = dict(prediction)
                if usable_prediction(prediction):
                    return _safe_float(prediction.get("prediction_mb_per_window"), 0.0), prediction
                return None, prediction
        if not self.io_model_artifact:
            return None, {}

        def predict_for_model_set(model_set_name: str) -> Tuple[Optional[float], Dict[str, Any]]:
            model_group = ((self.io_model_artifact.get("models") or {}).get(model_set_name) or {})
            model = ((model_group.get("targets") or {}).get(target_name) or {})
            artifact_pred, artifact_breakdown = self._predict_joblib_artifact(
                model=model,
                aggregate_features=aggregate_features,
                prediction_key="prediction_mb_per_window",
            )
            if artifact_pred is not None:
                artifact_breakdown["model_set"] = model_set_name
                artifact_breakdown["target"] = target_name
                artifact_breakdown["metrics"] = model.get("metrics")
                artifact_breakdown["notes"] = model_group.get("notes")
                if usable_prediction(artifact_breakdown):
                    return artifact_pred, artifact_breakdown
                return None, artifact_breakdown
            features = list(model.get("features") or [])
            coef = list(model.get("coef") or [])
            means = list(model.get("feature_mean") or [])
            scales = list(model.get("feature_scale") or [])
            if not features or len(features) != len(coef):
                return None, {}
            pred = _safe_float(model.get("intercept"), 0.0)
            contributions: List[Dict[str, float]] = []
            for idx, feature in enumerate(features):
                raw = _safe_float(aggregate_features.get(feature), 0.0)
                mean = _safe_float(means[idx] if idx < len(means) else 0.0, 0.0)
                scale = _safe_float(scales[idx] if idx < len(scales) else 1.0, 1.0)
                if abs(scale) < 1e-12:
                    scale = 1.0
                z = (raw - mean) / scale
                contrib = _safe_float(coef[idx], 0.0) * z
                pred += contrib
                contributions.append(
                    {
                        "feature": feature,
                        "raw": raw,
                        "z": z,
                        "contribution": contrib,
                    }
                )
            clip_min = model.get("clip_min")
            if clip_min not in (None, ""):
                pred = max(_safe_float(clip_min), pred)
            pred_before_error_bound = pred
            error_bound = self._prediction_error_bound(model=model)
            pred = max(0.0, pred + _safe_float(error_bound.get("value"), 0.0))
            contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
            breakdown = {
                "model_set": model_set_name,
                "target": target_name,
                "model_type": model.get("model_type"),
                "prediction_mb_per_window": pred,
                "prediction_before_anchor_calibration": pred_before_error_bound,
                "prediction_before_error_bound": pred_before_error_bound,
                "error_bound": error_bound,
                "feature_count": len(features),
                "features": dict(aggregate_features),
                "metrics": model.get("metrics"),
                "top_contributions": contributions[:10],
                "notes": model_group.get("notes"),
            }
            if usable_prediction(breakdown):
                return pred, breakdown
            return None, breakdown

        def baseline_actual_io_mb_s() -> float:
            raw = workload_context.get("runtime_resource_raw") or {}
            stats = workload_context.get("runtime_resource_stats") or {}
            keys = ["io"]
            if target_name == "read":
                keys = ["io_read", "read_io", "io"]
            elif target_name == "write":
                keys = ["io_write", "write_io", "io"]
            for key in keys:
                value = _safe_float(raw.get(key) if isinstance(raw, dict) else None, float("nan"))
                if math.isfinite(value) and value > 0.0:
                    return value / (1024.0 * 1024.0)
                stat = stats.get(key) if isinstance(stats, dict) else None
                if isinstance(stat, dict):
                    value = _safe_float(stat.get("avg"), float("nan"))
                    if math.isfinite(value) and value > 0.0:
                        return value / (1024.0 * 1024.0)
            return float("nan")

        model_set = self._io_model_set_name(workload_context)
        if auto_select:
            actual_mb_s = baseline_actual_io_mb_s()
            if math.isfinite(actual_mb_s) and actual_mb_s > 0.0:
                seen: Dict[str, bool] = {}
                candidates: List[Dict[str, Any]] = []
                configured_order = [
                    str(name).strip().upper()
                    for name in model_cfg.get("auto_candidate_model_sets", ["C", "CH", "H"])
                    if str(name).strip()
                ]
                artifact_sets = [
                    str(name).strip().upper()
                    for name in (self.io_model_artifact.get("models") or {}).keys()
                    if str(name).strip()
                ]
                for candidate_set in configured_order + artifact_sets:
                    if candidate_set in seen:
                        continue
                    seen[candidate_set] = True
                    pred, breakdown = predict_for_model_set(candidate_set)
                    if pred is None or not breakdown:
                        continue
                    raw_prediction = breakdown.get("prediction_before_anchor_calibration")
                    if raw_prediction in (None, ""):
                        raw_prediction = breakdown.get("prediction_before_error_bound", pred)
                    raw_mb_s = _safe_float(raw_prediction, float("nan")) / max(
                        _safe_float(
                            breakdown.get("prediction_window_seconds"),
                            self._feature_window_seconds(workload_context),
                        ),
                        1e-9,
                    )
                    if not math.isfinite(raw_mb_s) or raw_mb_s <= 0.0:
                        continue
                    candidates.append(
                        {
                            "model_set": candidate_set,
                            "raw_prediction_mb_s": raw_mb_s,
                            "actual_mb_s": actual_mb_s,
                            "relative_error": abs(raw_mb_s - actual_mb_s) / max(actual_mb_s, 1e-9),
                            "prediction_mb_per_window": pred,
                            "model_type": breakdown.get("model_type"),
                        }
                    )
                if candidates:
                    selected = min(candidates, key=lambda item: _safe_float(item.get("relative_error"), float("inf")))
                    selected_pred, selected_breakdown = predict_for_model_set(str(selected.get("model_set")))
                    if selected_pred is not None and selected_breakdown:
                        selected_breakdown.update(
                            {
                                "auto_selected_by_baseline_actual": True,
                                "auto_selection_actual_mb_s": actual_mb_s,
                                "auto_selection_relative_error": selected.get("relative_error"),
                                "auto_selection_candidates": candidates,
                                "inferred_model_set_before_auto": model_set,
                            }
                        )
                        return selected_pred, selected_breakdown

        pred, breakdown = predict_for_model_set(model_set)
        if pred is not None and breakdown:
            return pred, breakdown
        return None, breakdown

    def _aggregate_io_model_features(
        self,
        query_estimates: Sequence[QueryEstimate],
        workload_context: Dict[str, Any],
    ) -> Dict[str, float]:
        aggregate: Dict[str, float] = {}
        window_scale = self._model_feature_window_scale(workload_context)
        active_sql_count = 0.0
        for query_estimate in query_estimates:
            if query_estimate.get("error"):
                continue
            io = ((query_estimate.get("query_resources") or {}).get("io") or {})
            features = io.get("model_features") or {}
            replay_count = max(1, _safe_int(query_estimate.get("replay_count"), 1))
            active_sql_count += replay_count * window_scale
            for name, value in features.items():
                aggregate[name] = aggregate.get(name, 0.0) + (
                    _safe_float(value, 0.0) * replay_count * window_scale
                )
        explicit_active = self._explicit_active_sql_count(workload_context)
        active = explicit_active if explicit_active > 0.0 else max(1.0, active_sql_count)
        aggregate["active_sql_count"] = active
        aggregate["log_active_sql_count"] = math.log1p(active)
        aggregate["feature_window_seconds"] = self._feature_window_seconds(workload_context)
        aggregate["feature_window_scale"] = window_scale
        aggregate.update(self._aggregate_plan_shape_features(query_estimates, workload_context))
        aggregate.update(self._cost_parameter_model_features(workload_context))
        return self._complete_io_model_features(aggregate)

    def _build_io_result(
        self,
        *,
        resource_type: str,
        query_estimates: Sequence[QueryEstimate],
        workload_context: Dict[str, Any],
        calibration_factor: float,
        explain_workers: int,
    ) -> Dict[str, Any]:
        io_cfg = self.estimator_cfg.get("io") or {}
        read_overlap_factor = _safe_float(io_cfg.get("read_overlap_factor"), 1.0)
        write_overlap_factor = _safe_float(io_cfg.get("write_overlap_factor"), 1.0)
        window_s = self._workload_window_seconds(workload_context)
        errors = self._query_errors(query_estimates)

        total_read_bytes = 0.0
        total_write_bytes = 0.0
        total_read_blocks = 0.0
        total_write_blocks = 0.0
        query_breakdown: List[Dict[str, Any]] = []
        operator_breakdown: List[Dict[str, Any]] = []
        physical_direction_supported = True

        for query_estimate in query_estimates:
            if query_estimate.get("error"):
                continue
            query_resources = query_estimate.get("query_resources") or {}
            io = query_resources.get("io") or {}
            baseline_io = self._physical_baseline_query_resource(
                query_estimate=query_estimate,
                resource_type="io",
                workload_context=workload_context,
            )
            same_plan_work_frozen = bool(baseline_io)
            if baseline_io:
                io = baseline_io
            if (
                self._physical_model_enabled()
                and not bool(io.get("physical_direction_supported", False))
            ):
                physical_direction_supported = False
            replay_count = max(1, _safe_int(query_estimate.get("replay_count"), 1))
            read_blocks_per_query = _safe_float(io.get("read_blocks_per_query"), 0.0)
            write_blocks_per_query = _safe_float(io.get("write_blocks_per_query"), 0.0)
            read_bytes_per_query = _safe_float(io.get("read_bytes_per_query"), 0.0)
            write_bytes_per_query = _safe_float(io.get("write_bytes_per_query"), 0.0)
            total_replay_read_blocks = read_blocks_per_query * replay_count
            total_replay_write_blocks = write_blocks_per_query * replay_count
            total_replay_read_bytes = read_bytes_per_query * replay_count
            total_replay_write_bytes = write_bytes_per_query * replay_count
            total_blocks_per_query = read_blocks_per_query + write_blocks_per_query
            total_bytes_per_query = read_bytes_per_query + write_bytes_per_query
            total_replay_blocks = total_replay_read_blocks + total_replay_write_blocks
            total_replay_bytes = total_replay_read_bytes + total_replay_write_bytes
            total_read_bytes += total_replay_read_bytes
            total_write_bytes += total_replay_write_bytes
            total_read_blocks += total_replay_read_blocks
            total_write_blocks += total_replay_write_blocks
            query_breakdown.append(
                {
                    "queryid": query_estimate.get("queryid"),
                    "query": query_estimate.get("query"),
                    "replay_count": replay_count,
                    "read_blocks_per_query": read_blocks_per_query,
                    "write_blocks_per_query": write_blocks_per_query,
                    "total_blocks_per_query": total_blocks_per_query,
                    "read_bytes_per_query": read_bytes_per_query,
                    "write_bytes_per_query": write_bytes_per_query,
                    "total_bytes_per_query": total_bytes_per_query,
                    "total_replay_read_blocks": total_replay_read_blocks,
                    "total_replay_write_blocks": total_replay_write_blocks,
                    "total_replay_blocks": total_replay_blocks,
                    "total_replay_read_bytes": total_replay_read_bytes,
                    "total_replay_write_bytes": total_replay_write_bytes,
                    "total_replay_bytes": total_replay_bytes,
                    "data_read_blocks_per_query": io.get("data_read_blocks_per_query"),
                    "temp_read_blocks_per_query": io.get("temp_read_blocks_per_query"),
                    "temp_write_blocks_per_query": io.get("temp_write_blocks_per_query"),
                    "wal_write_blocks_per_query": io.get("wal_write_blocks_per_query"),
                    "model_features": io.get("model_features"),
                    "physical_direction_supported": io.get(
                        "physical_direction_supported"
                    ),
                    "same_physical_plan_baseline_work_frozen": (
                        same_plan_work_frozen
                    ),
                }
            )
            for node in (io.get("operator_breakdown") or []):
                op = dict(node)
                op["queryid"] = query_estimate.get("queryid")
                op["query"] = query_estimate.get("query")
                operator_breakdown.append(op)

        total_read_bytes *= read_overlap_factor
        total_write_bytes *= write_overlap_factor
        total_read_blocks *= read_overlap_factor
        total_write_blocks *= write_overlap_factor

        read_bytes_s = total_read_bytes / window_s
        write_bytes_s = total_write_bytes / window_s
        read_iops = total_read_blocks / window_s
        write_iops = total_write_blocks / window_s
        learned_breakdown: Dict[str, Any] = {}
        learned_fallback_breakdown: Dict[str, Any] = {}
        baseline_scaled_plan_formula_used = False
        if (
            self.io_model_artifact
            and not self._physical_model_enabled()
            and resource_type in ("io", "io_read", "io_write")
        ):
            target_name = {
                "io": "total",
                "io_read": "read",
                "io_write": "write",
            }[resource_type]
            aggregate_features = self._aggregate_io_model_features(
                query_estimates=query_estimates,
                workload_context=workload_context,
            )
            feature_window_s = self._feature_window_seconds(workload_context)
            aggregate_features.update(
                {
                    "job_plan_formula_io_total_mb_per_window": (
                        (read_bytes_s + write_bytes_s)
                        * feature_window_s
                        / (1024.0 ** 2)
                    ),
                    "job_plan_formula_io_read_mb_per_window": (
                        read_bytes_s * feature_window_s / (1024.0 ** 2)
                    ),
                    "job_plan_formula_io_write_mb_per_window": (
                        write_bytes_s * feature_window_s / (1024.0 ** 2)
                    ),
                }
            )
            predicted_mb, learned_breakdown = self._predict_io_model_mb(
                target_name=target_name,
                aggregate_features=aggregate_features,
                workload_context=workload_context,
            )
            if predicted_mb is not None and learned_breakdown:
                predicted_mb = self._select_learned_point_prediction(
                    prediction=predicted_mb,
                    breakdown=learned_breakdown,
                )
            if predicted_mb is not None and predicted_mb > 0.0:
                prediction_window_s = _safe_float(
                    learned_breakdown.get("prediction_window_seconds"),
                    self._feature_window_seconds(workload_context),
                )
                predicted_bytes_s = max(0.0, predicted_mb) * (1024.0 ** 2) / prediction_window_s
                if resource_type == "io":
                    plan_formula_bytes_s = max(0.0, read_bytes_s + write_bytes_s)
                elif resource_type == "io_read":
                    plan_formula_bytes_s = max(0.0, read_bytes_s)
                else:
                    plan_formula_bytes_s = max(0.0, write_bytes_s)
                shared_cfg = self.estimator_cfg.get("shared") or {}
                anchor = learned_breakdown.get("baseline_anchor_calibration")
                use_formula_on_plateau = _safe_bool(
                    shared_cfg.get("learned_io_plateau_use_baseline_scaled_plan_formula", True),
                    True,
                )
                use_formula_on_direction_disagreement = _safe_bool(
                    shared_cfg.get(
                        "learned_io_direction_disagreement_use_baseline_scaled_plan_formula",
                        True,
                    ),
                    True,
                )
                if (
                    (use_formula_on_plateau or use_formula_on_direction_disagreement)
                    and isinstance(anchor, dict)
                    and anchor.get("applied")
                ):
                    baseline_learned_target = _safe_float(
                        anchor.get("predicted_target_before_error_bound"),
                        float("nan"),
                    )
                    current_learned_target = _safe_float(
                        learned_breakdown.get("prediction_before_anchor_calibration"),
                        float("nan"),
                    )
                    baseline_formula_target = _safe_float(
                        anchor.get("plan_formula_predicted_target_before_error_bound"),
                        float("nan"),
                    )
                    formula_delta_scale = _safe_float(
                        anchor.get("plan_formula_delta_scale"),
                        float("nan"),
                    )
                    current_formula_target = (
                        plan_formula_bytes_s * prediction_window_s / (1024.0 ** 2)
                    )
                    plateau_abs_tolerance = max(
                        0.0,
                        _safe_float(
                            shared_cfg.get("learned_io_plateau_abs_tolerance_mb_per_window"),
                            1e-9,
                        ),
                    )
                    plateau_rel_tolerance = max(
                        0.0,
                        _safe_float(
                            shared_cfg.get("learned_io_plateau_rel_tolerance"),
                            1e-9,
                        ),
                    )
                    learned_tolerance = max(
                        plateau_abs_tolerance,
                        abs(baseline_learned_target) * plateau_rel_tolerance,
                    )
                    formula_tolerance = max(
                        plateau_abs_tolerance,
                        abs(baseline_formula_target) * plateau_rel_tolerance,
                    )
                    learned_plateau = (
                        math.isfinite(current_learned_target)
                        and math.isfinite(baseline_learned_target)
                        and abs(current_learned_target - baseline_learned_target)
                        <= learned_tolerance
                    )
                    formula_changed = (
                        math.isfinite(current_formula_target)
                        and math.isfinite(baseline_formula_target)
                        and abs(current_formula_target - baseline_formula_target)
                        > formula_tolerance
                    )
                    learned_delta = current_learned_target - baseline_learned_target
                    formula_delta = current_formula_target - baseline_formula_target
                    direction_disagreement = (
                        math.isfinite(learned_delta)
                        and math.isfinite(formula_delta)
                        and abs(learned_delta) > learned_tolerance
                        and abs(formula_delta) > formula_tolerance
                        and learned_delta * formula_delta < 0.0
                    )
                    valid_formula_scale = (
                        math.isfinite(formula_delta_scale)
                        and formula_delta_scale > 0.0
                    )
                    use_plateau_fallback = (
                        use_formula_on_plateau
                        and learned_plateau
                        and formula_changed
                    )
                    use_disagreement_fallback = (
                        use_formula_on_direction_disagreement
                        and direction_disagreement
                    )
                    if (
                        (use_plateau_fallback or use_disagreement_fallback)
                        and valid_formula_scale
                    ):
                        predicted_mb = max(0.0, current_formula_target * formula_delta_scale)
                        predicted_bytes_s = (
                            predicted_mb * (1024.0 ** 2) / prediction_window_s
                        )
                        baseline_scaled_plan_formula_used = True
                        learned_breakdown["baseline_scaled_plan_formula_fallback"] = {
                            "applied": True,
                            "reason": (
                                "learned_io_direction_disagrees_with_plan_formula"
                                if use_disagreement_fallback
                                else "learned_io_prediction_plateau_with_plan_change"
                            ),
                            "current_learned_target": current_learned_target,
                            "baseline_learned_target": baseline_learned_target,
                            "learned_delta": learned_delta,
                            "current_plan_formula_target": current_formula_target,
                            "baseline_plan_formula_target": baseline_formula_target,
                            "plan_formula_delta": formula_delta,
                            "plan_formula_delta_scale": formula_delta_scale,
                            "scaled_prediction_mb_per_window": predicted_mb,
                        }
                ratio_floor = max(
                    0.0,
                    _safe_float(
                        shared_cfg.get("learned_io_vs_plan_formula_ratio_floor"),
                        1e-9,
                    ),
                )
                ratio_min = max(
                    0.0,
                    _safe_float(
                        shared_cfg.get("learned_io_vs_plan_formula_ratio_min"),
                        0.25,
                    ),
                )
                ratio_max = max(
                    ratio_min,
                    _safe_float(
                        shared_cfg.get("learned_io_vs_plan_formula_ratio_max"),
                        4.0,
                    ),
                )
                learned_vs_plan_ratio = (
                    predicted_bytes_s / max(plan_formula_bytes_s, ratio_floor)
                    if plan_formula_bytes_s > ratio_floor
                    else float("nan")
                )
                anchor_applied = bool(
                    isinstance(anchor, dict) and anchor.get("applied")
                )
                bypass_plan_ratio_guard = bool(
                    anchor_applied
                    or learned_breakdown.get("auto_selected_by_baseline_actual")
                    or learned_breakdown.get(
                        "benchmark_scoped_absolute_calibration"
                    )
                )
                if (
                    not bypass_plan_ratio_guard
                    and
                    plan_formula_bytes_s > ratio_floor
                    and (
                        not math.isfinite(learned_vs_plan_ratio)
                        or learned_vs_plan_ratio < ratio_min
                        or learned_vs_plan_ratio > ratio_max
                    )
                ):
                    learned_breakdown.update(
                        {
                            "available": False,
                            "fallback_to_plan_formula": True,
                            "fallback_reason": (
                                "learned_io_vs_plan_formula_ratio_out_of_bounds"
                            ),
                            "learned_io_bytes_s_checked": predicted_bytes_s,
                            "plan_formula_io_bytes_s_checked": plan_formula_bytes_s,
                            "learned_vs_plan_formula_ratio": learned_vs_plan_ratio,
                            "learned_vs_plan_formula_ratio_min": ratio_min,
                            "learned_vs_plan_formula_ratio_max": ratio_max,
                        }
                    )
                    learned_fallback_breakdown = dict(learned_breakdown)
                    learned_breakdown = {}
                    predicted_mb = None
                    predicted_bytes_s = 0.0
                else:
                    learned_breakdown["learned_io_bytes_s"] = predicted_bytes_s
                    learned_breakdown["plan_formula_io_bytes_s"] = plan_formula_bytes_s
                    learned_breakdown["learned_vs_plan_formula_ratio"] = (
                        learned_vs_plan_ratio
                    )
                    if bypass_plan_ratio_guard:
                        learned_breakdown[
                            "learned_vs_plan_formula_ratio_guard_bypassed"
                        ] = True
                        if anchor_applied:
                            bypass_reason = "baseline_anchor_calibration"
                        elif learned_breakdown.get(
                            "benchmark_scoped_absolute_calibration"
                        ):
                            bypass_reason = "benchmark_scoped_absolute_calibration"
                        else:
                            bypass_reason = "auto_selected_by_current_baseline_actual"
                        learned_breakdown[
                            "learned_vs_plan_formula_ratio_guard_bypass_reason"
                        ] = bypass_reason
            if predicted_mb is not None and predicted_mb > 0.0:
                if resource_type == "io":
                    total_bytes_s = max(0.0, read_bytes_s + write_bytes_s)
                    if total_bytes_s > 0:
                        read_ratio = max(0.0, read_bytes_s) / total_bytes_s
                    else:
                        read_ratio = 0.5
                    read_bytes_s = predicted_bytes_s * read_ratio
                    write_bytes_s = predicted_bytes_s * (1.0 - read_ratio)
                elif resource_type == "io_read":
                    read_bytes_s = predicted_bytes_s
                else:
                    write_bytes_s = predicted_bytes_s
                aggregate_features_top10 = sorted(
                    (
                        {"feature": name, "value": _safe_float(value, 0.0)}
                        for name, value in aggregate_features.items()
                    ),
                    key=lambda item: abs(item["value"]),
                    reverse=True,
                )[:10]
                if learned_breakdown:
                    learned_breakdown["aggregate_features_top10"] = aggregate_features_top10
                elif learned_fallback_breakdown:
                    learned_fallback_breakdown["aggregate_features_top10"] = aggregate_features_top10
            elif not learned_fallback_breakdown:
                learned_fallback_breakdown = dict(learned_breakdown)
                learned_breakdown = {}

        physical_anchor: Dict[str, Any] = {}
        if self._physical_model_enabled() and resource_type in (
            "io",
            "io_read",
            "io_write",
        ):
            if resource_type == "io":
                formula_bytes_s = read_bytes_s + write_bytes_s
            elif resource_type == "io_read":
                formula_bytes_s = read_bytes_s
            else:
                formula_bytes_s = write_bytes_s
            anchored_bytes_s, physical_anchor = self._apply_physical_delta_anchor(
                resource_type=resource_type,
                formula_value=formula_bytes_s,
                workload_context=workload_context,
            )
            if resource_type == "io":
                read_ratio = (
                    read_bytes_s / formula_bytes_s
                    if formula_bytes_s > 0.0
                    else 0.5
                )
                read_bytes_s = anchored_bytes_s * read_ratio
                write_bytes_s = anchored_bytes_s * (1.0 - read_ratio)
            elif resource_type == "io_read":
                read_bytes_s = anchored_bytes_s
            else:
                write_bytes_s = anchored_bytes_s

        if resource_type == "io":
            raw_value = (read_bytes_s + write_bytes_s) * calibration_factor
            value = raw_value / (1024.0 ** 2)
            unit = "bytes/s"
            normalized_unit = "MB/s"
            sort_field = "total_replay_bytes"
            if self._physical_model_enabled():
                formula_name = "io_executor_physical_delta_v1"
            elif baseline_scaled_plan_formula_used:
                formula_name = "io_baseline_scaled_plan_formula_v1"
            else:
                formula_name = "io_learned_linear_v1" if learned_breakdown else "io_plan_estimator_v1"
        elif resource_type == "io_read":
            raw_value = read_bytes_s * calibration_factor
            value = raw_value / (1024.0 ** 2)
            unit = "bytes/s"
            normalized_unit = "MB/s"
            sort_field = "total_replay_read_bytes"
            if self._physical_model_enabled():
                formula_name = "io_read_executor_physical_delta_v1"
            elif baseline_scaled_plan_formula_used:
                formula_name = "io_read_baseline_scaled_plan_formula_v1"
            else:
                formula_name = "io_read_learned_linear_v1" if learned_breakdown else "io_read_plan_estimator_v1"
        elif resource_type == "io_write":
            raw_value = write_bytes_s * calibration_factor
            value = raw_value / (1024.0 ** 2)
            unit = "bytes/s"
            normalized_unit = "MB/s"
            sort_field = "total_replay_write_bytes"
            if self._physical_model_enabled():
                formula_name = "io_write_executor_physical_delta_v1"
            elif baseline_scaled_plan_formula_used:
                formula_name = "io_write_baseline_scaled_plan_formula_v1"
            else:
                formula_name = "io_write_learned_linear_v1" if learned_breakdown else "io_write_plan_estimator_v1"
        elif resource_type == "iops_read":
            raw_value = read_iops * calibration_factor
            value = raw_value
            unit = "ops/s"
            normalized_unit = "ops/s"
            sort_field = "read_blocks_per_query"
            formula_name = "iops_read_plan_estimator_v1"
        else:
            raw_value = write_iops * calibration_factor
            value = raw_value
            unit = "ops/s"
            normalized_unit = "ops/s"
            sort_field = "write_blocks_per_query"
            formula_name = "iops_write_plan_estimator_v1"

        return {
            "resource_type": resource_type,
            "value": value,
            "raw_value": raw_value,
            "unit": unit,
            "normalized_unit": normalized_unit,
            "query_count": len(query_breakdown),
            "replay_query_count": sum(
                max(1, _safe_int(item.get("replay_count"), 1))
                for item in query_breakdown
            ),
            "query_breakdown": query_breakdown,
            "query_sort_field": sort_field,
            "operator_breakdown": operator_breakdown,
            "planning_breakdown": {
                "window_seconds": window_s,
                "cache_hit_rate": self._cache_hit_rate(workload_context),
                "seq_page_cost": self._seq_page_cost(),
                "random_page_cost": self._random_page_cost(),
                "bitmap_page_cost": self._bitmap_page_cost(),
                "block_size": self._block_size(),
            },
            "concurrency_breakdown": {
                "read_overlap_factor": read_overlap_factor,
                "write_overlap_factor": write_overlap_factor,
                "window_seconds": window_s,
            },
            "calibration_breakdown": {
                "calibration_factor": calibration_factor,
                "total_read_bytes": total_read_bytes,
                "total_write_bytes": total_write_bytes,
                "total_io_bytes": total_read_bytes + total_write_bytes,
                "total_read_blocks": total_read_blocks,
                "total_write_blocks": total_write_blocks,
                "total_io_blocks": total_read_blocks + total_write_blocks,
                "baseline_scaled_plan_formula_used": baseline_scaled_plan_formula_used,
                "learned_model": learned_breakdown,
                "learned_model_fallback": learned_fallback_breakdown,
                "physical_delta_anchor": physical_anchor,
            },
            "execution_breakdown": {
                "explain_workers": explain_workers,
                "explained_query_count": len(query_breakdown),
                "replay_execution_count": sum(
                    max(1, _safe_int(item.get("replay_count"), 1))
                    for item in query_breakdown
                ),
                "failed_query_count": len(errors),
                "workload_context": workload_context,
            },
            "formula_name": formula_name,
            "physical_direction_supported": physical_direction_supported,
            "optimistic_interval_pruning_allowed": (
                physical_direction_supported
                and self._physical_pruning_calibrated()
                if self._physical_model_enabled()
                else True
            ),
            "errors": errors,
        }

    def _build_cpu_result(
        self,
        *,
        query_estimates: Sequence[QueryEstimate],
        workload_context: Dict[str, Any],
        calibration_factor: float,
        explain_workers: int,
    ) -> Dict[str, Any]:
        cpu_cfg = self.estimator_cfg.get("cpu") or {}
        window_s = self._workload_window_seconds(workload_context)
        cpu_alpha = _safe_float(cpu_cfg.get("cpu_seconds_per_work"), 1e-6)
        cpu_beta = _safe_float(cpu_cfg.get("cpu_seconds_bias"), 0.0)
        cpu_overlap_factor = _safe_float(cpu_cfg.get("overlap_factor"), 1.0)
        errors = self._query_errors(query_estimates)

        total_cpu_work = 0.0
        total_cpu_seconds = 0.0
        query_breakdown: List[Dict[str, Any]] = []
        operator_breakdown: List[Dict[str, Any]] = []
        physical_direction_supported = True
        for query_estimate in query_estimates:
            if query_estimate.get("error"):
                continue
            cpu = ((query_estimate.get("query_resources") or {}).get("cpu") or {})
            baseline_cpu = self._physical_baseline_query_resource(
                query_estimate=query_estimate,
                resource_type="cpu",
                workload_context=workload_context,
            )
            same_plan_work_frozen = bool(baseline_cpu)
            if baseline_cpu:
                cpu = baseline_cpu
            if (
                self._physical_model_enabled()
                and not bool(cpu.get("physical_direction_supported", False))
            ):
                physical_direction_supported = False
            replay_count = max(1, _safe_int(query_estimate.get("replay_count"), 1))
            cpu_work_per_query = _safe_float(cpu.get("cpu_work_per_query"), 0.0)
            cpu_seconds_per_query = _safe_float(cpu.get("cpu_seconds_per_query"), 0.0)
            total_replay_cpu_work = cpu_work_per_query * replay_count
            total_replay_cpu_seconds = cpu_seconds_per_query * replay_count
            total_cpu_work += total_replay_cpu_work
            total_cpu_seconds += total_replay_cpu_seconds
            query_breakdown.append(
                {
                    "queryid": query_estimate.get("queryid"),
                    "query": query_estimate.get("query"),
                    "replay_count": replay_count,
                    "cpu_work_per_query": cpu_work_per_query,
                    "cpu_seconds_per_query": cpu_seconds_per_query,
                    "total_replay_cpu_work": total_replay_cpu_work,
                    "total_replay_cpu_seconds": total_replay_cpu_seconds,
                    "model_features": cpu.get("model_features"),
                    "physical_direction_supported": cpu.get(
                        "physical_direction_supported"
                    ),
                    "same_physical_plan_baseline_work_frozen": (
                        same_plan_work_frozen
                    ),
                }
            )
            for node in (cpu.get("operator_breakdown") or []):
                op = dict(node)
                op["queryid"] = query_estimate.get("queryid")
                op["query"] = query_estimate.get("query")
                operator_breakdown.append(op)

        total_cpu_seconds = total_cpu_seconds * cpu_overlap_factor + cpu_beta
        plan_formula_total_cpu_seconds = total_cpu_seconds
        plan_formula_cpu_cores = (
            plan_formula_total_cpu_seconds / max(window_s, 1e-9)
        ) * calibration_factor
        learned_breakdown: Dict[str, Any] = {}
        learned_fallback_breakdown: Dict[str, Any] = {}
        if self.cpu_model_artifact and not self._physical_model_enabled():
            aggregate_features = self._aggregate_cpu_model_features(
                query_estimates=query_estimates,
                workload_context=workload_context,
            )
            aggregate_features["job_plan_formula_cpu_seconds_per_window"] = (
                plan_formula_total_cpu_seconds
                * self._feature_window_seconds(workload_context)
                / max(window_s, 1e-9)
            )
            predicted_seconds, learned_breakdown = self._predict_cpu_model_seconds(
                aggregate_features=aggregate_features,
                workload_context=workload_context,
            )
            if predicted_seconds is not None and learned_breakdown:
                predicted_seconds = self._select_learned_point_prediction(
                    prediction=predicted_seconds,
                    breakdown=learned_breakdown,
                )
            if predicted_seconds is not None and predicted_seconds > 0.0:
                learned_window_s = _safe_float(
                    learned_breakdown.get("prediction_window_seconds"),
                    self._feature_window_seconds(workload_context),
                )
                learned_total_cpu_seconds = predicted_seconds * cpu_overlap_factor + cpu_beta
                learned_cpu_cores = (
                    learned_total_cpu_seconds / max(learned_window_s, 1e-9)
                ) * calibration_factor
                shared_cfg = self.estimator_cfg.get("shared") or {}
                ratio_floor = max(
                    0.0,
                    _safe_float(
                        shared_cfg.get("learned_cpu_vs_plan_formula_ratio_floor"),
                        1e-9,
                    ),
                )
                ratio_min = max(
                    0.0,
                    _safe_float(
                        shared_cfg.get("learned_cpu_vs_plan_formula_ratio_min"),
                        0.25,
                    ),
                )
                ratio_max = max(
                    ratio_min,
                    _safe_float(
                        shared_cfg.get("learned_cpu_vs_plan_formula_ratio_max"),
                        4.0,
                    ),
                )
                learned_vs_plan_ratio = (
                    learned_cpu_cores / max(plan_formula_cpu_cores, ratio_floor)
                    if plan_formula_cpu_cores > ratio_floor
                    else float("nan")
                )
                ratio_guard_failed = (
                    plan_formula_cpu_cores > ratio_floor
                    and (
                        not math.isfinite(learned_vs_plan_ratio)
                        or learned_vs_plan_ratio < ratio_min
                        or learned_vs_plan_ratio > ratio_max
                    )
                )
                ratio_guard_mode = str(
                    shared_cfg.get("learned_cpu_vs_plan_formula_ratio_guard_mode")
                    or "fallback"
                ).strip().lower()
                if ratio_guard_failed:
                    learned_breakdown.update(
                        {
                            "learned_cpu_cores_checked": learned_cpu_cores,
                            "plan_formula_cpu_cores_checked": plan_formula_cpu_cores,
                            "learned_vs_plan_formula_ratio": learned_vs_plan_ratio,
                            "learned_vs_plan_formula_ratio_min": ratio_min,
                            "learned_vs_plan_formula_ratio_max": ratio_max,
                            "learned_vs_plan_formula_ratio_guard_mode": ratio_guard_mode,
                        }
                    )
                    if ratio_guard_mode in {
                        "baseline_sanity",
                        "defer_to_baseline_sanity",
                        "warn",
                    }:
                        learned_breakdown.update(
                            {
                                "ratio_guard_warning": (
                                    "learned_cpu_vs_plan_formula_ratio_out_of_bounds"
                                ),
                                "ratio_guard_action": "keep_learned_for_baseline_sanity",
                            }
                        )
                        total_cpu_seconds = learned_total_cpu_seconds
                    elif ratio_guard_mode not in {"disabled", "off", "none"}:
                        learned_breakdown.update(
                            {
                                "available": False,
                                "fallback_to_plan_formula": True,
                                "fallback_reason": (
                                    "learned_cpu_vs_plan_formula_ratio_out_of_bounds"
                                ),
                            }
                        )
                        learned_fallback_breakdown = dict(learned_breakdown)
                        learned_breakdown = {}
                    else:
                        total_cpu_seconds = learned_total_cpu_seconds
                else:
                    total_cpu_seconds = learned_total_cpu_seconds
                    learned_breakdown["learned_cpu_cores"] = learned_cpu_cores
                    learned_breakdown["plan_formula_cpu_cores"] = plan_formula_cpu_cores
                    learned_breakdown["learned_vs_plan_formula_ratio"] = learned_vs_plan_ratio
                aggregate_features_top10 = sorted(
                    (
                        {"feature": name, "value": _safe_float(value, 0.0)}
                        for name, value in aggregate_features.items()
                    ),
                    key=lambda item: abs(item["value"]),
                    reverse=True,
                )[:10]
                if learned_breakdown:
                    learned_breakdown["aggregate_features_top10"] = aggregate_features_top10
                elif learned_fallback_breakdown:
                    learned_fallback_breakdown["aggregate_features_top10"] = aggregate_features_top10
            else:
                learned_fallback_breakdown = dict(learned_breakdown)
                learned_breakdown = {}
        cpu_window_s = window_s
        if learned_breakdown:
            cpu_window_s = _safe_float(
                learned_breakdown.get("prediction_window_seconds"),
                self._feature_window_seconds(workload_context),
            )
        cpu_cores = (total_cpu_seconds / cpu_window_s) * calibration_factor
        physical_anchor: Dict[str, Any] = {}
        if self._physical_model_enabled():
            cpu_cores, physical_anchor = self._apply_physical_delta_anchor(
                resource_type="cpu",
                formula_value=cpu_cores,
                workload_context=workload_context,
            )
        return {
            "resource_type": "cpu",
            "value": cpu_cores,
            "raw_value": cpu_cores,
            "unit": "cores",
            "normalized_unit": "cores",
            "query_count": len(query_breakdown),
            "replay_query_count": sum(
                max(1, _safe_int(item.get("replay_count"), 1))
                for item in query_breakdown
            ),
            "query_breakdown": query_breakdown,
            "query_sort_field": "total_replay_cpu_seconds",
            "operator_breakdown": operator_breakdown,
            "planning_breakdown": {
                "cpu_seconds_per_work": cpu_alpha,
                "cpu_seconds_bias": cpu_beta,
                "window_seconds": window_s,
                "prediction_window_seconds": cpu_window_s,
                "plan_formula_total_cpu_seconds": plan_formula_total_cpu_seconds,
                "plan_formula_cpu_cores": plan_formula_cpu_cores,
            },
            "concurrency_breakdown": {
                "overlap_factor": cpu_overlap_factor,
                "window_seconds": window_s,
                "feature_window_seconds": self._feature_window_seconds(workload_context),
                "feature_window_scale": self._model_feature_window_scale(workload_context),
            },
            "calibration_breakdown": {
                "calibration_factor": calibration_factor,
                "total_cpu_work": total_cpu_work,
                "total_cpu_seconds": total_cpu_seconds,
                "plan_formula_total_cpu_seconds": plan_formula_total_cpu_seconds,
                "plan_formula_cpu_cores": plan_formula_cpu_cores,
                "final_cpu_cores": cpu_cores,
                "learned_model": learned_breakdown,
                "learned_model_fallback": learned_fallback_breakdown,
                "physical_delta_anchor": physical_anchor,
            },
            "execution_breakdown": {
                "explain_workers": explain_workers,
                "explained_query_count": len(query_breakdown),
                "replay_execution_count": sum(
                    max(1, _safe_int(item.get("replay_count"), 1))
                    for item in query_breakdown
                ),
                "failed_query_count": len(errors),
                "workload_context": workload_context,
            },
            "formula_name": (
                "cpu_executor_physical_delta_v1"
                if self._physical_model_enabled()
                else (
                    "cpu_learned_linear_v1"
                    if learned_breakdown
                    else "cpu_plan_estimator_v1"
                )
            ),
            "physical_direction_supported": physical_direction_supported,
            "optimistic_interval_pruning_allowed": (
                physical_direction_supported
                and self._physical_pruning_calibrated()
                if self._physical_model_enabled()
                else True
            ),
            "errors": errors,
        }

    def _build_memory_result(
        self,
        *,
        query_estimates: Sequence[QueryEstimate],
        workload_context: Dict[str, Any],
        calibration_factor: float,
        explain_workers: int,
    ) -> Dict[str, Any]:
        memory_cfg = self.estimator_cfg.get("memory") or {}
        overlap_factor = _safe_float(memory_cfg.get("overlap_factor"), 1.0)
        concurrency = self._concurrency_value(workload_context)
        errors = self._query_errors(query_estimates)
        total_replay = sum(
            max(1, _safe_int(item.get("replay_count"), 1))
            for item in query_estimates
            if not item.get("error")
        ) or 1

        query_breakdown: List[Dict[str, Any]] = []
        operator_breakdown: List[Dict[str, Any]] = []
        weighted_memory_bytes = 0.0
        weighted_ap_memory_bytes = 0.0
        weighted_derived_memory_mb = 0.0
        ap_replay = 0
        for query_estimate in query_estimates:
            if query_estimate.get("error"):
                continue
            memory = ((query_estimate.get("query_resources") or {}).get("memory") or {})
            baseline_memory = self._physical_baseline_query_resource(
                query_estimate=query_estimate,
                resource_type="memory",
                workload_context=workload_context,
            )
            same_plan_work_frozen = bool(baseline_memory)
            if baseline_memory:
                memory = baseline_memory
            replay_count = max(1, _safe_int(query_estimate.get("replay_count"), 1))
            query_memory_bytes = _safe_float(memory.get("mem_query_parallel_bytes"), 0.0)
            query_derived_memory_mb = _safe_float(memory.get("derived_memory_mb"), 0.0)
            weighted_memory_bytes += (
                query_memory_bytes
                * replay_count
            ) / total_replay
            weighted_derived_memory_mb += (query_derived_memory_mb * replay_count) / total_replay
            if str(query_estimate.get("statement_kind") or "").lower() == "select":
                weighted_ap_memory_bytes += query_memory_bytes * replay_count
                ap_replay += replay_count
            query_breakdown.append(
                {
                    "queryid": query_estimate.get("queryid"),
                    "query": query_estimate.get("query"),
                    "replay_count": replay_count,
                    "mem_query_upper_bytes": memory.get("mem_query_upper_bytes"),
                    "mem_query_parallel_bytes": memory.get("mem_query_parallel_bytes"),
                    "derived_memory_mb": query_derived_memory_mb,
                    "total_weighted_memory_bytes": (
                        _safe_float(memory.get("mem_query_parallel_bytes"), 0.0)
                        * replay_count
                    ),
                    "same_physical_plan_baseline_work_frozen": (
                        same_plan_work_frozen
                    ),
                }
            )
            for node in (memory.get("operator_breakdown") or []):
                op = dict(node)
                op["queryid"] = query_estimate.get("queryid")
                op["query"] = query_estimate.get("query")
                operator_breakdown.append(op)

        resident_baseline_memory = self._resident_memory_baseline_bytes(workload_context)
        concurrent_working_memory = weighted_memory_bytes * concurrency * overlap_factor
        final_memory = (resident_baseline_memory + concurrent_working_memory) * calibration_factor
        plan_formula_memory_mb = final_memory / (1024.0 ** 2)
        learned_breakdown: Dict[str, Any] = {}
        baseline_scaled_plan_formula_used = False
        if self.memory_model_artifact and not self._physical_model_enabled():
            ap_concurrency = self._ap_concurrency_value(workload_context)
            ap_memory_mb = (
                weighted_ap_memory_bytes / max(1, ap_replay) / (1024.0 ** 2)
                if ap_replay > 0
                else weighted_memory_bytes / (1024.0 ** 2)
            )
            derived = weighted_derived_memory_mb * concurrency
            aggregate_features = {
                "AP_E_mem_mb": ap_memory_mb,
                "AP_Concurrency": ap_concurrency,
                "AP_E_mem_x_AP_Concurrency": ap_memory_mb * ap_concurrency,
                "derived": derived,
                "weighted_derived_memory_mb": weighted_derived_memory_mb,
                "weighted_memory_mb": weighted_memory_bytes / (1024.0 ** 2),
                "concurrent_working_memory_mb": concurrent_working_memory / (1024.0 ** 2),
                "resident_baseline_memory_mb": resident_baseline_memory / (1024.0 ** 2),
                "job_plan_formula_memory_mb": plan_formula_memory_mb,
            }
            aggregate_features.update(
                self._aggregate_cpu_model_features(
                    query_estimates=query_estimates,
                    workload_context=workload_context,
                )
            )
            predicted_mb, learned_breakdown = self._predict_memory_model_mb(
                aggregate_features=aggregate_features,
                workload_context=workload_context,
            )
            if predicted_mb is not None and learned_breakdown:
                predicted_mb = self._select_learned_point_prediction(
                    prediction=predicted_mb,
                    breakdown=learned_breakdown,
                )
            if predicted_mb is not None and predicted_mb > 0.0:
                shared_cfg = self.estimator_cfg.get("shared") or {}
                anchor = learned_breakdown.get("baseline_anchor_calibration")
                use_formula_on_plateau = _safe_bool(
                    shared_cfg.get(
                        "learned_memory_plateau_use_baseline_scaled_plan_formula",
                        True,
                    ),
                    True,
                )
                if use_formula_on_plateau and isinstance(anchor, dict) and anchor.get("applied"):
                    baseline_learned_target = _safe_float(
                        anchor.get("predicted_target_before_error_bound"),
                        float("nan"),
                    )
                    current_learned_target = _safe_float(
                        learned_breakdown.get("prediction_before_anchor_calibration"),
                        float("nan"),
                    )
                    baseline_formula_target = _safe_float(
                        anchor.get("plan_formula_predicted_target_before_error_bound"),
                        float("nan"),
                    )
                    formula_delta_scale = _safe_float(
                        anchor.get("plan_formula_delta_scale"),
                        float("nan"),
                    )
                    plateau_abs_tolerance = max(
                        0.0,
                        _safe_float(
                            shared_cfg.get(
                                "learned_memory_plateau_abs_tolerance_mb",
                            ),
                            1e-9,
                        ),
                    )
                    plateau_rel_tolerance = max(
                        0.0,
                        _safe_float(
                            shared_cfg.get("learned_memory_plateau_rel_tolerance"),
                            0.01,
                        ),
                    )
                    learned_tolerance = max(
                        plateau_abs_tolerance,
                        abs(baseline_learned_target) * plateau_rel_tolerance,
                    )
                    formula_tolerance = max(
                        plateau_abs_tolerance,
                        abs(baseline_formula_target) * plateau_rel_tolerance,
                    )
                    learned_plateau = (
                        math.isfinite(current_learned_target)
                        and math.isfinite(baseline_learned_target)
                        and abs(current_learned_target - baseline_learned_target)
                        <= learned_tolerance
                    )
                    formula_changed = (
                        math.isfinite(plan_formula_memory_mb)
                        and math.isfinite(baseline_formula_target)
                        and abs(plan_formula_memory_mb - baseline_formula_target)
                        > formula_tolerance
                    )
                    valid_formula_scale = (
                        math.isfinite(formula_delta_scale)
                        and formula_delta_scale > 0.0
                    )
                    if learned_plateau and formula_changed and valid_formula_scale:
                        predicted_mb = max(0.0, plan_formula_memory_mb * formula_delta_scale)
                        baseline_scaled_plan_formula_used = True
                        learned_breakdown["baseline_scaled_plan_formula_fallback"] = {
                            "applied": True,
                            "reason": "learned_memory_prediction_plateau_with_plan_change",
                            "current_learned_target": current_learned_target,
                            "baseline_learned_target": baseline_learned_target,
                            "current_plan_formula_target": plan_formula_memory_mb,
                            "baseline_plan_formula_target": baseline_formula_target,
                            "plan_formula_delta_scale": formula_delta_scale,
                            "scaled_prediction_mb": predicted_mb,
                        }
                final_memory = predicted_mb * (1024.0 ** 2) * calibration_factor
            else:
                learned_breakdown = {}
        physical_anchor: Dict[str, Any] = {}
        if self._physical_model_enabled():
            final_memory, physical_anchor = self._apply_physical_delta_anchor(
                resource_type="memory",
                formula_value=final_memory,
                workload_context=workload_context,
            )
        return {
            "resource_type": "memory",
            "value": final_memory / (1024.0 ** 3),
            "raw_value": final_memory,
            "unit": "bytes",
            "normalized_unit": "GB",
            "query_count": len(query_breakdown),
            "replay_query_count": sum(
                max(1, _safe_int(item.get("replay_count"), 1))
                for item in query_breakdown
            ),
            "query_breakdown": query_breakdown,
            "query_sort_field": "mem_query_parallel_bytes",
            "operator_breakdown": operator_breakdown,
            "planning_breakdown": {
                "work_mem_bytes": self._work_mem_bytes(),
                "hash_mem_bytes": self._hash_mem_bytes(),
                "beta_sort": _safe_float(memory_cfg.get("beta_sort"), 1.0),
                "beta_hash": _safe_float(memory_cfg.get("beta_hash"), 1.0),
                "beta_hashagg": _safe_float(memory_cfg.get("beta_hashagg"), 1.0),
                "beta_materialize": _safe_float(memory_cfg.get("beta_materialize"), 1.0),
                "beta_memoize": _safe_float(memory_cfg.get("beta_memoize"), 0.0),
            },
            "concurrency_breakdown": {
                "formula": str(memory_cfg.get("concurrency_formula", "baseline_resident_plus_weighted_working")),
                "overlap_factor": overlap_factor,
                "avg_concurrency": concurrency,
                "ap_concurrency": self._ap_concurrency_value(workload_context),
                "ap_replay_query_count": ap_replay,
                "weighted_ap_memory_bytes": weighted_ap_memory_bytes / max(1, ap_replay) if ap_replay > 0 else 0.0,
                "weighted_memory_bytes": weighted_memory_bytes,
                "weighted_derived_memory_mb": weighted_derived_memory_mb,
                "derived_memory_mb": weighted_derived_memory_mb * concurrency,
                "resident_baseline_memory_bytes": resident_baseline_memory,
                "concurrent_working_memory_bytes": concurrent_working_memory,
                "concurrent_memory_bytes": resident_baseline_memory + concurrent_working_memory,
            },
            "calibration_breakdown": {
                "calibration_factor": calibration_factor,
                "final_memory_bytes": final_memory,
                "plan_formula_memory_mb": plan_formula_memory_mb,
                "baseline_scaled_plan_formula_used": baseline_scaled_plan_formula_used,
                "learned_model": learned_breakdown,
                "physical_delta_anchor": physical_anchor,
            },
            "execution_breakdown": {
                "explain_workers": explain_workers,
                "explained_query_count": len(query_breakdown),
                "replay_execution_count": sum(
                    max(1, _safe_int(item.get("replay_count"), 1))
                    for item in query_breakdown
                ),
                "failed_query_count": len(errors),
                "workload_context": workload_context,
            },
            "formula_name": (
                "memory_executor_pipeline_delta_v1"
                if self._physical_model_enabled()
                else (
                    "memory_learned_formula_v1"
                    if learned_breakdown
                    else "memory_plan_estimator_v3"
                )
            ),
            "physical_direction_supported": True,
            "optimistic_interval_pruning_allowed": (
                self._physical_pruning_calibrated()
                if self._physical_model_enabled()
                else True
            ),
            "errors": errors,
        }

    def _estimate_placeholder_resource(
        self,
        *,
        resource_type: str,
        calibration_factor: float,
        workload_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "resource_type": resource_type,
            "value": 0.0,
            "raw_value": 0.0,
            "unit": "placeholder",
            "normalized_unit": "placeholder",
            "query_count": 0,
            "query_breakdown": [],
            "query_sort_field": "value",
            "operator_breakdown": [],
            "planning_breakdown": {},
            "concurrency_breakdown": {},
            "calibration_breakdown": {
                "calibration_factor": calibration_factor,
                "final_value": 0.0,
            },
            "execution_breakdown": {
                "workload_context": workload_context,
            },
            "formula_name": f"{resource_type}_placeholder",
            "is_placeholder": True,
            "errors": [],
        }

    def _build_resource_estimation_log(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event": "resource_estimation",
            "resource_type": result.get("resource_type"),
            "value": result.get("value"),
            "raw_value": result.get("raw_value"),
            "unit": result.get("unit"),
            "normalized_unit": result.get("normalized_unit"),
            "query_count": result.get("query_count"),
            "formula_name": result.get("formula_name"),
            "is_placeholder": bool(result.get("is_placeholder", False)),
            "query_breakdown": result.get("query_breakdown"),
            "operator_breakdown": result.get("operator_breakdown"),
            "planning_breakdown": result.get("planning_breakdown"),
            "concurrency_breakdown": result.get("concurrency_breakdown"),
            "calibration_breakdown": result.get("calibration_breakdown"),
            "execution_breakdown": result.get("execution_breakdown"),
            "errors": result.get("errors"),
        }

    def _summarize_result_for_logging(
        self,
        result: Dict[str, Any],
        *,
        topn: int,
    ) -> Dict[str, Any]:
        summarized = dict(result)
        query_breakdown = list(result.get("query_breakdown") or [])
        operator_breakdown = list(result.get("operator_breakdown") or [])
        errors = list(result.get("errors") or [])
        query_sort_field = str(result.get("query_sort_field") or "value")
        operator_sort_field = self._operator_sort_field(result.get("resource_type"))

        query_breakdown.sort(
            key=lambda item: _safe_float(item.get(query_sort_field), 0.0),
            reverse=True,
        )
        operator_breakdown.sort(
            key=lambda item: _safe_float(item.get(operator_sort_field), 0.0),
            reverse=True,
        )

        summarized["query_breakdown_total_count"] = len(query_breakdown)
        summarized["operator_breakdown_total_count"] = len(operator_breakdown)
        summarized["error_total_count"] = len(errors)
        summarized["logged_topn"] = topn
        summarized["query_breakdown"] = query_breakdown[:topn]
        summarized["operator_breakdown"] = operator_breakdown[:topn]
        summarized["errors"] = errors[:topn]
        return summarized

    def _operator_sort_field(self, resource_type: Any) -> str:
        if resource_type == "memory":
            return "memory_bytes"
        if resource_type == "io":
            return "total_blocks"
        if resource_type in ("io_read", "io_write", "iops_read", "iops_write"):
            return "read_blocks" if resource_type in ("io_read", "iops_read") else "write_blocks"
        if resource_type == "cpu":
            return "cpu_work"
        return "value"

    def _query_errors(self, query_estimates: Sequence[QueryEstimate]) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []
        for item in query_estimates:
            if not item.get("error"):
                continue
            errors.append(
                {
                    "queryid": item.get("queryid"),
                    "query": item.get("query"),
                    "replay_count": item.get("replay_count"),
                    "error": item.get("error"),
                }
            )
        return errors

    def _collect_query_plans(
        self,
        queries: Sequence[Dict[str, Any]],
        *,
        explain_workers: int,
        params: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        explain_mode = self.plan_capture_modes[0] if self.plan_capture_modes else "generic"
        indexed_queries = [
            item
            for item in queries
            if _is_plannable_query(str(item.get("query") or "").strip().rstrip(";"))
        ]
        if not indexed_queries:
            return []

        results: List[Optional[Dict[str, Any]]] = [None] * len(indexed_queries)
        max_workers = min(explain_workers, len(indexed_queries))
        if max_workers <= 1:
            for idx, item in enumerate(indexed_queries):
                results[idx] = self._explain_single_query(
                    item,
                    explain_mode=explain_mode,
                    params=params,
                )
            return [item for item in results if item is not None]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._explain_single_query,
                    item,
                    explain_mode=explain_mode,
                    params=params,
                ): idx
                for idx, item in enumerate(indexed_queries)
            }
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    item = indexed_queries[idx]
                    results[idx] = {
                        "queryid": item.get("queryid"),
                        "query": str(item.get("query") or "").strip().rstrip(";"),
                        "replay_count": item.get("replay_count", item.get("calls", 1)),
                        "error": str(exc),
                    }
        return [item for item in results if item is not None]

    def _explain_single_query(
        self,
        item: Dict[str, Any],
        *,
        explain_mode: str,
        params: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        query_text = str(item.get("query") or "").strip().rstrip(";")
        _rewritten_query, rewrite_audit = (
            rewrite_pgss_query_for_generic_explain(query_text)
        )
        try:
            plan_doc, plan_mode = self._explain_query_json(
                query_text,
                mode=explain_mode,
                queryid=item.get("queryid"),
                params=params,
            )
            root = plan_doc.get("Plan") if isinstance(plan_doc, dict) else plan_doc
            return {
                "queryid": item.get("queryid"),
                "query": query_text,
                "calls": item.get("calls"),
                "replay_count": item.get("replay_count", item.get("calls", 1)),
                "plan_mode": plan_mode,
                "query_binding_audit": rewrite_audit,
                "plan_doc": plan_doc,
                "plan_root": root,
            }
        except Exception as exc:
            return {
                "queryid": item.get("queryid"),
                "query": query_text,
                "calls": item.get("calls"),
                "replay_count": item.get("replay_count", item.get("calls", 1)),
                "query_binding_audit": rewrite_audit,
                "error": str(exc),
            }

    def _extract_plan_node_features(
        self,
        plan_root: Any,
        *,
        relation_stats: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> List[PlanNodeFeature]:
        features: List[PlanNodeFeature] = []
        next_id = 0
        stats_by_name = relation_stats or {}

        def relation_record(node: Dict[str, Any], name_key: str) -> Dict[str, float]:
            relation_name = str(node.get(name_key) or "").strip()
            if not relation_name:
                return {}
            schema_name = str(node.get("Schema") or "").strip()
            qualified = f"{schema_name}.{relation_name}" if schema_name else ""
            record = stats_by_name.get(qualified) if qualified else None
            if not isinstance(record, dict):
                record = stats_by_name.get(relation_name)
            return dict(record) if isinstance(record, dict) else {}

        def walk(
            node: Any,
            *,
            parent_id: Optional[str],
            depth: int,
            below_gather: bool,
            query_parallel_factor: int,
        ) -> Tuple[float, Optional[str]]:
            nonlocal next_id
            if not isinstance(node, dict):
                return 0.0, None

            node_id = f"n{next_id}"
            next_id += 1
            node_type = str(node.get("Node Type") or "Unknown")
            current_below_gather = below_gather
            workers_planned = max(0, _safe_int(node.get("Workers Planned"), 0))
            gather_workers = max(1, workers_planned + 1)
            child_below_gather = current_below_gather or node_type in ("Gather", "Gather Merge")
            child_parallel_factor = query_parallel_factor
            if node_type in ("Gather", "Gather Merge"):
                child_parallel_factor = max(query_parallel_factor, gather_workers)

            child_ids: List[str] = []
            child_total_cost = 0.0
            for child in node.get("Plans", []) or []:
                child_cost, child_id = walk(
                    child,
                    parent_id=node_id,
                    depth=depth + 1,
                    below_gather=child_below_gather,
                    query_parallel_factor=child_parallel_factor,
                )
                child_total_cost += child_cost
                if child_id is not None:
                    child_ids.append(child_id)

            startup_cost = _safe_float(node.get("Startup Cost"), 0.0)
            total_cost = _safe_float(node.get("Total Cost"), 0.0)
            self_cost = max(0.0, total_cost - child_total_cost)
            rows = max(1.0, _safe_float(node.get("Plan Rows"), 1.0))
            width = max(8.0, _safe_float(node.get("Plan Width"), 8.0))
            bytes_est = rows * width
            pages_est = float(_ceil_div(bytes_est, self._block_size()))
            relation_physical_stats = relation_record(node, "Relation Name")
            index_physical_stats = relation_record(node, "Index Name")
            child_rows = [
                _safe_float(
                    next(
                        (
                            feature.get("rows")
                            for feature in features
                            if feature.get("node_id") == child_id
                        ),
                        0.0,
                    ),
                    0.0,
                )
                for child_id in child_ids
            ]
            if child_rows:
                input_rows = child_rows[0]
                if node_type in ("Hash Join", "Merge Join"):
                    input_rows = sum(child_rows)
            elif node_type in ("Seq Scan", "Parallel Seq Scan"):
                input_rows = max(
                    rows,
                    _safe_float(relation_physical_stats.get("reltuples"), rows),
                )
            else:
                input_rows = rows

            feature: PlanNodeFeature = {
                "node_id": node_id,
                "parent_id": parent_id,
                "depth": depth,
                "node_type": node_type,
                "rows": rows,
                "width": width,
                "bytes": bytes_est,
                "pages": pages_est,
                "startup_cost": startup_cost,
                "total_cost": total_cost,
                "self_cost": self_cost,
                "workers_planned": workers_planned,
                "below_gather": current_below_gather,
                "query_parallel_factor": query_parallel_factor,
                "input_rows": max(1.0, input_rows),
                "execution_multiplier": 1.0,
                "relation_name": node.get("Relation Name"),
                "relation_schema": node.get("Schema"),
                "index_name": node.get("Index Name"),
                "parent_relationship": node.get("Parent Relationship"),
                "relation_stats": relation_physical_stats,
                "index_stats": index_physical_stats,
                "join_type": node.get("Join Type"),
                "strategy": node.get("Strategy"),
                "operation": node.get("Operation"),
                "filter": node.get("Filter"),
                "index_cond": node.get("Index Cond"),
                "recheck_cond": node.get("Recheck Cond"),
                "join_filter": node.get("Join Filter"),
                "hash_cond": node.get("Hash Cond"),
                "merge_cond": node.get("Merge Cond"),
                "sort_key": node.get("Sort Key"),
                "hash_batches": node.get("Hash Batches"),
                "hash_buckets": node.get("Hash Buckets"),
                "original_hash_batches": node.get("Original Hash Batches"),
                "original_hash_buckets": node.get("Original Hash Buckets"),
                "parallel_aware": bool(node.get("Parallel Aware")),
                "startup_cost_raw": node.get("Startup Cost"),
                "total_cost_raw": node.get("Total Cost"),
                "children": child_ids,
            }
            features.append(feature)
            return total_cost, node_id

        walk(
            plan_root,
            parent_id=None,
            depth=0,
            below_gather=False,
            query_parallel_factor=1,
        )
        self._annotate_execution_multipliers(features)
        return features

    def _annotate_execution_multipliers(
        self,
        node_features: Sequence[PlanNodeFeature],
    ) -> None:
        """Propagate estimated executor loop counts through nested-loop inner trees."""
        node_by_id = {
            str(node.get("node_id")): node
            for node in node_features
            if node.get("node_id") is not None
        }

        def visit(node_id: str, multiplier: float) -> None:
            node = node_by_id.get(node_id)
            if not node:
                return
            safe_multiplier = max(1.0, float(multiplier))
            node["execution_multiplier"] = safe_multiplier
            children = [
                str(child_id)
                for child_id in (node.get("children") or [])
                if str(child_id) in node_by_id
            ]
            node_type = str(node.get("node_type") or "").lower()
            if node_type == "nested loop" and children:
                visit(children[0], safe_multiplier)
                outer_rows = max(
                    1.0,
                    _safe_float(node_by_id[children[0]].get("rows"), 1.0),
                )
                for child_id in children[1:]:
                    visit(child_id, safe_multiplier * outer_rows)
                return
            for child_id in children:
                visit(child_id, safe_multiplier)

        for node in node_features:
            if node.get("parent_id") is None and node.get("node_id") is not None:
                visit(str(node["node_id"]), 1.0)

    def _estimate_query_resources(
        self,
        *,
        query_text: str,
        node_features: Sequence[PlanNodeFeature],
        workload_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        io_operator_breakdown: List[Dict[str, Any]] = []
        cpu_operator_breakdown: List[Dict[str, Any]] = []
        memory_operator_breakdown: List[Dict[str, Any]] = []

        block_size = self._block_size()
        work_mem_bytes = self._work_mem_bytes()
        hash_mem_bytes = self._hash_mem_bytes()
        cache_hit_rate = self._cache_hit_rate(workload_context)
        statement_kind = _statement_kind(query_text)

        node_by_id = {node["node_id"]: node for node in node_features}

        logical_read_blocks = 0.0
        seq_read_blocks = 0.0
        random_read_blocks = 0.0
        data_read_blocks = 0.0
        temp_read_blocks = 0.0
        temp_write_blocks = 0.0
        wal_write_blocks = 0.0
        physical_direction_supported = True
        cpu_work_total = 0.0
        cpu_direction_supported = True
        mem_above_gather = 0.0
        mem_below_gather = 0.0
        derived_memory_mb = self._estimate_path_memory_mb(node_features)

        for node in node_features:
            node_type = str(node.get("node_type") or "")
            node_type_lower = node_type.lower()
            io_self = self._estimate_node_io(
                node=node,
                node_by_id=node_by_id,
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
                statement_kind=statement_kind,
                cache_hit_rate=cache_hit_rate,
            )
            logical_read_blocks += _safe_float(io_self.get("logical_read_blocks"), 0.0)
            seq_read_blocks += _safe_float(io_self.get("seq_read_blocks"), 0.0)
            random_read_blocks += _safe_float(io_self.get("random_read_blocks"), 0.0)
            data_read_blocks += _safe_float(io_self.get("data_read_blocks"), 0.0)
            temp_read_blocks += _safe_float(io_self.get("temp_read_blocks"), 0.0)
            temp_write_blocks += _safe_float(io_self.get("temp_write_blocks"), 0.0)
            wal_write_blocks += _safe_float(io_self.get("wal_write_blocks"), 0.0)
            if (
                self._physical_model_enabled()
                and _safe_float(io_self.get("physical_direction_supported"), 1.0) <= 0.0
            ):
                physical_direction_supported = False
            io_operator_breakdown.append(
                {
                    "node_type": node_type,
                    "depth": node.get("depth"),
                    "self_cost": node.get("self_cost"),
                    "rows": node.get("rows"),
                    "width": node.get("width"),
                    "read_blocks": (
                        _safe_float(io_self.get("data_read_blocks"), 0.0)
                        + _safe_float(io_self.get("temp_read_blocks"), 0.0)
                    ),
                    "write_blocks": (
                        _safe_float(io_self.get("temp_write_blocks"), 0.0)
                        + _safe_float(io_self.get("wal_write_blocks"), 0.0)
                    ),
                    "total_blocks": (
                        _safe_float(io_self.get("data_read_blocks"), 0.0)
                        + _safe_float(io_self.get("temp_read_blocks"), 0.0)
                        + _safe_float(io_self.get("temp_write_blocks"), 0.0)
                        + _safe_float(io_self.get("wal_write_blocks"), 0.0)
                    ),
                    "logic_read_blocks": io_self.get("logical_read_blocks"),
                    "seq_read_blocks": io_self.get("seq_read_blocks"),
                    "random_read_blocks": io_self.get("random_read_blocks"),
                    "data_read_blocks": io_self.get("data_read_blocks"),
                    "temp_read_blocks": io_self.get("temp_read_blocks"),
                    "temp_write_blocks": io_self.get("temp_write_blocks"),
                    "wal_write_blocks": io_self.get("wal_write_blocks"),
                }
            )

            cpu_self = self._estimate_node_cpu(node=node, node_by_id=node_by_id)
            cpu_work_total += _safe_float(cpu_self.get("cpu_work"), 0.0)
            if (
                self._physical_model_enabled()
                and _safe_float(cpu_self.get("physical_direction_supported"), 1.0) <= 0.0
            ):
                cpu_direction_supported = False
            cpu_operator_breakdown.append(
                {
                    "node_type": node_type,
                    "depth": node.get("depth"),
                    "rows": node.get("rows"),
                    "width": node.get("width"),
                    "self_cost": node.get("self_cost"),
                    "cpu_work": cpu_self.get("cpu_work"),
                    "execution_multiplier": node.get("execution_multiplier"),
                    "input_rows": node.get("input_rows"),
                    "physical_direction_supported": cpu_self.get(
                        "physical_direction_supported"
                    ),
                }
            )

            memory_bytes = self._estimate_node_memory(
                node=node,
                node_by_id=node_by_id,
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
            )
            node_derived_memory_mb = self._estimate_node_derived_memory_mb(
                node=node,
                work_mem_kb=work_mem_bytes / 1024.0,
            )
            derived_memory_mb += node_derived_memory_mb
            if node.get("below_gather"):
                mem_below_gather += memory_bytes
            else:
                mem_above_gather += memory_bytes
            memory_operator_breakdown.append(
                {
                    "node_type": node_type,
                    "depth": node.get("depth"),
                    "rows": node.get("rows"),
                    "width": node.get("width"),
                    "memory_bytes": memory_bytes,
                    "derived_memory_mb": node_derived_memory_mb,
                    "below_gather": bool(node.get("below_gather")),
                    "parallel_factor": node.get("query_parallel_factor", 1),
                }
            )

        if statement_kind in ("insert", "update", "delete"):
            # A statement emits one base WAL allowance, not one allowance per
            # plan node.  Tuple-dependent WAL can be supplied by calibration.
            wal_write_blocks += self._wal_blocks_per_write_statement()

        read_blocks_per_query = data_read_blocks + temp_read_blocks
        write_blocks_per_query = temp_write_blocks + wal_write_blocks
        read_bytes_per_query = read_blocks_per_query * block_size
        write_bytes_per_query = write_blocks_per_query * block_size

        cpu_alpha = _safe_float((self.estimator_cfg.get("cpu") or {}).get("cpu_seconds_per_work"), 1e-6)
        cpu_seconds_per_query = cpu_work_total * cpu_alpha
        cpu_model_features = self._extract_cpu_model_features(node_features)
        query_parallel_factor = max(
            1,
            max((_safe_int(node.get("query_parallel_factor"), 1) for node in node_features), default=1),
        )
        if self._physical_model_enabled():
            mem_query_upper = self._estimate_pipeline_memory_bytes(
                node_features=node_features,
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
                include_parallel_workers=False,
            )
            mem_query_parallel = self._estimate_pipeline_memory_bytes(
                node_features=node_features,
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
                include_parallel_workers=True,
            )
        else:
            mem_query_upper = mem_above_gather + mem_below_gather
            mem_query_parallel = mem_above_gather + query_parallel_factor * mem_below_gather
        io_model_features = self._extract_io_model_features(
            node_features=node_features,
            workload_context=workload_context,
        )

        return {
            "io": {
                "logical_read_blocks_per_query": logical_read_blocks,
                "seq_read_blocks_per_query": seq_read_blocks,
                "random_read_blocks_per_query": random_read_blocks,
                "data_read_blocks_per_query": data_read_blocks,
                "temp_read_blocks_per_query": temp_read_blocks,
                "temp_write_blocks_per_query": temp_write_blocks,
                "wal_write_blocks_per_query": wal_write_blocks,
                "read_blocks_per_query": read_blocks_per_query,
                "write_blocks_per_query": write_blocks_per_query,
                "read_bytes_per_query": read_bytes_per_query,
                "write_bytes_per_query": write_bytes_per_query,
                "operator_breakdown": io_operator_breakdown,
                "model_features": io_model_features,
                "physical_direction_supported": physical_direction_supported,
            },
            "cpu": {
                "cpu_work_per_query": cpu_work_total,
                "cpu_seconds_per_query": cpu_seconds_per_query,
                "model_features": cpu_model_features,
                "operator_breakdown": cpu_operator_breakdown,
                "physical_direction_supported": cpu_direction_supported,
            },
            "memory": {
                "mem_query_upper_bytes": mem_query_upper,
                "mem_query_parallel_bytes": mem_query_parallel,
                "derived_memory_mb": derived_memory_mb,
                "operator_breakdown": memory_operator_breakdown,
                "physical_direction_supported": True,
            },
        }

    def _count_expression_ops(self, expr: Any) -> int:
        if not expr:
            return 0
        return len(
            re.findall(
                r"=|>=|<=|<>|!=|>|<|\bAND\b|\bOR\b|\bLIKE\b|\bIN\b",
                str(expr),
                flags=re.IGNORECASE,
            )
        )

    def _list_count(self, value: Any) -> int:
        return len(value) if isinstance(value, list) else 0

    def _extract_cpu_model_features(
        self,
        node_features: Sequence[PlanNodeFeature],
    ) -> Dict[str, float]:
        feature_names = [
            "F_scan_tuple", "F_nl_tuple", "F_join_tuple", "F_agg_tuple",
            "F_sort_tuple", "F_materialize_tuple", "F_proj_tuple",
            "F_index_tuple", "F_hash_build", "F_hash_probe",
            "F_scan_filter", "F_index_cond", "F_join_filter",
            "F_hash_cond", "F_merge_cond", "F_agg_trans",
            "F_agg_final", "F_sort_cmp", "F_materialize_oper",
        ]
        node_by_id = {str(node.get("node_id")): node for node in node_features}
        roots = [
            node for node in node_features
            if node.get("parent_id") in (None, "")
        ]

        def empty() -> Dict[str, float]:
            return {name: 0.0 for name in feature_names}

        def add_into(dst: Dict[str, float], src: Dict[str, float]) -> None:
            for name, value in src.items():
                dst[name] = dst.get(name, 0.0) + _safe_float(value, 0.0)

        def child_nodes(node: PlanNodeFeature) -> List[PlanNodeFeature]:
            return [
                node_by_id[child_id]
                for child_id in (node.get("children") or [])
                if child_id in node_by_id
            ]

        def walk(node: PlanNodeFeature, multiplier: float = 1.0) -> Dict[str, float]:
            feats = empty()
            node_type = str(node.get("node_type") or "")
            node_type_lower = node_type.lower()
            rows = max(0.0, _safe_float(node.get("rows"), 0.0))
            total_rows = rows * multiplier
            children = child_nodes(node)

            if node_type_lower == "nested loop":
                outer = children[0] if len(children) > 0 else {}
                inner = children[1] if len(children) > 1 else {}
                if outer:
                    add_into(feats, walk(outer, multiplier))
                inner_multiplier = _safe_float(outer.get("rows"), 0.0) * multiplier if outer else multiplier
                if inner:
                    add_into(feats, walk(inner, inner_multiplier))
                feats["F_nl_tuple"] += total_rows
                eval_count = _safe_float(outer.get("rows"), 0.0) * _safe_float(inner.get("rows"), 0.0) * multiplier
                feats["F_join_filter"] += eval_count * self._count_expression_ops(node.get("join_filter"))
                for extra in children[2:]:
                    add_into(feats, walk(extra, multiplier))
                return feats

            if node_type_lower == "hash join":
                outer = children[0] if len(children) > 0 else {}
                inner = children[1] if len(children) > 1 else {}
                outer_total = _safe_float(outer.get("rows"), 0.0) * multiplier
                inner_total = _safe_float(inner.get("rows"), 0.0) * multiplier
                feats["F_join_tuple"] += total_rows
                feats["F_hash_build"] += inner_total
                feats["F_hash_probe"] += outer_total
                feats["F_hash_cond"] += outer_total * self._count_expression_ops(node.get("hash_cond"))
                feats["F_join_filter"] += total_rows * self._count_expression_ops(node.get("join_filter"))
                for child in children:
                    add_into(feats, walk(child, multiplier))
                return feats

            if node_type_lower == "merge join":
                outer = children[0] if len(children) > 0 else {}
                inner = children[1] if len(children) > 1 else {}
                outer_total = _safe_float(outer.get("rows"), 0.0) * multiplier
                inner_total = _safe_float(inner.get("rows"), 0.0) * multiplier
                feats["F_join_tuple"] += total_rows
                feats["F_merge_cond"] += (outer_total + inner_total) * self._count_expression_ops(node.get("merge_cond"))
                feats["F_join_filter"] += total_rows * self._count_expression_ops(node.get("join_filter"))
                for child in children:
                    add_into(feats, walk(child, multiplier))
                return feats

            if node_type_lower in ("sort", "incremental sort"):
                feats["F_sort_tuple"] += total_rows
                if total_rows > 1:
                    feats["F_sort_cmp"] += (
                        total_rows
                        * math.log2(total_rows)
                        * max(self._list_count(node.get("sort_key")), 1)
                    )
            elif node_type_lower in ("aggregate", "hashaggregate", "hash aggregate"):
                child = children[0] if children else {}
                input_total = _safe_float(child.get("rows"), 0.0) * multiplier
                feats["F_agg_tuple"] += total_rows
                feats["F_agg_trans"] += input_total
                feats["F_agg_final"] += total_rows
            elif node_type_lower == "seq scan":
                feats["F_scan_tuple"] += total_rows
                feats["F_scan_filter"] += total_rows * self._count_expression_ops(node.get("filter"))
            elif "index scan" in node_type_lower:
                feats["F_index_tuple"] += total_rows
                feats["F_index_cond"] += total_rows * self._count_expression_ops(node.get("index_cond"))
                if node_type_lower == "index scan":
                    feats["F_scan_tuple"] += total_rows
                    feats["F_scan_filter"] += total_rows * self._count_expression_ops(node.get("filter"))
            elif node_type_lower == "bitmap heap scan":
                feats["F_scan_tuple"] += total_rows
                feats["F_scan_filter"] += total_rows * (
                    self._count_expression_ops(node.get("recheck_cond"))
                    + self._count_expression_ops(node.get("filter"))
                )
            elif node_type_lower == "bitmap index scan":
                feats["F_index_tuple"] += total_rows
                feats["F_index_cond"] += total_rows * self._count_expression_ops(node.get("index_cond"))
            elif node_type_lower == "materialize":
                feats["F_materialize_tuple"] += total_rows
                feats["F_materialize_oper"] += 2.0 * total_rows
            elif node_type_lower in ("result", "projection"):
                feats["F_proj_tuple"] += total_rows

            if "join" not in node_type_lower:
                for child in children:
                    add_into(feats, walk(child, multiplier))
            return feats

        result = empty()
        for root in roots:
            add_into(result, walk(root, 1.0))
        return result

    def _aggregate_cpu_model_features(
        self,
        query_estimates: Sequence[QueryEstimate],
        workload_context: Dict[str, Any],
    ) -> Dict[str, float]:
        aggregate: Dict[str, float] = {}
        window_scale = self._model_feature_window_scale(workload_context)
        active_sql_count = 0.0
        for query_estimate in query_estimates:
            if query_estimate.get("error"):
                continue
            cpu = ((query_estimate.get("query_resources") or {}).get("cpu") or {})
            features = cpu.get("model_features") or {}
            replay_count = max(1, _safe_int(query_estimate.get("replay_count"), 1))
            active_sql_count += replay_count * window_scale
            for name, value in features.items():
                aggregate[name] = aggregate.get(name, 0.0) + (
                    _safe_float(value, 0.0) * replay_count * window_scale
                )
        explicit_active = self._explicit_active_sql_count(workload_context)
        active = explicit_active if explicit_active > 0.0 else max(1.0, active_sql_count)
        aggregate["active_sql_count"] = active
        aggregate["log_active_sql_count"] = math.log1p(active)
        aggregate["feature_window_seconds"] = self._feature_window_seconds(workload_context)
        aggregate["feature_window_scale"] = window_scale
        base_features = [
            "F_scan_tuple", "F_nl_tuple", "F_join_tuple", "F_agg_tuple",
            "F_sort_tuple", "F_materialize_tuple", "F_proj_tuple",
            "F_index_tuple", "F_hash_build", "F_hash_probe",
            "F_scan_filter", "F_index_cond", "F_join_filter",
            "F_hash_cond", "F_merge_cond", "F_agg_trans",
            "F_agg_final", "F_sort_cmp", "F_materialize_oper",
        ]
        for name in base_features:
            aggregate[f"{name}_per_active"] = _safe_float(aggregate.get(name), 0.0) / active
        aggregate.update(self._aggregate_plan_shape_features(query_estimates, workload_context))
        aggregate.update(self._cost_parameter_model_features(workload_context))
        return aggregate

    def _cost_parameter_model_features(
        self,
        workload_context: Dict[str, Any],
    ) -> Dict[str, float]:
        """Expose the applied PostgreSQL cost vector to benchmark-scoped models."""
        params = workload_context.get("plan_cost_parameters") or {}
        if not isinstance(params, dict):
            return {}
        raw_features = {
            f"cost_param_{name}": _safe_float(params.get(name), 0.0)
            for name in sorted(COST_PARAM_SET)
            if name in params
        }
        raw_features.update(
            {
                f"log_cost_param_{name}": math.log(
                    max(_safe_float(params.get(name), 0.0), 1e-300)
                )
                for name in sorted(COST_PARAM_SET)
                if name in params
            }
        )
        return raw_features

    def _aggregate_plan_shape_features(
        self,
        query_estimates: Sequence[QueryEstimate],
        workload_context: Dict[str, Any],
    ) -> Dict[str, float]:
        """Aggregate online-safe plan tree shape features for ranking and future model training."""
        del workload_context
        aggregate: Dict[str, float] = {
            "plan_node_count": 0.0,
            "plan_max_depth": 0.0,
            "plan_total_rows": 0.0,
            "plan_total_width": 0.0,
            "plan_total_cost": 0.0,
            "plan_startup_cost": 0.0,
            "plan_self_cost": 0.0,
            "plan_workers_planned": 0.0,
            "plan_parallel_aware_nodes": 0.0,
            "plan_seq_scan_count": 0.0,
            "plan_index_scan_count": 0.0,
            "plan_bitmap_heap_scan_count": 0.0,
            "plan_hash_join_count": 0.0,
            "plan_merge_join_count": 0.0,
            "plan_nested_loop_count": 0.0,
            "plan_sort_count": 0.0,
            "plan_hash_count": 0.0,
            "plan_hashagg_count": 0.0,
            "plan_materialize_count": 0.0,
            "plan_memoize_count": 0.0,
            "plan_gather_count": 0.0,
            "plan_spill_pressure_proxy": 0.0,
            "plan_random_io_proxy": 0.0,
            "plan_seq_io_proxy": 0.0,
            "plan_cpu_tuple_proxy": 0.0,
            "plan_total_input_rows": 0.0,
            "plan_total_output_bytes": 0.0,
            "plan_total_input_bytes": 0.0,
            "plan_loop_weighted_rows": 0.0,
            "plan_parallel_loop_weighted_rows": 0.0,
            "plan_max_execution_multiplier": 1.0,
            "plan_max_parallel_factor": 1.0,
            "plan_nested_loop_inner_rows_proxy": 0.0,
            "plan_parent_child_ratio_sum": 0.0,
            "plan_parent_child_ratio_max": 0.0,
            "plan_parent_child_ratio_count": 0.0,
            "plan_relation_pages": 0.0,
            "plan_relation_tuples": 0.0,
            "plan_relation_allvisible_pages": 0.0,
            "plan_relation_stats_scan_count": 0.0,
            "plan_missing_relation_stats_scan_count": 0.0,
            "plan_stats_modification_fraction_max": 0.0,
            "plan_stats_analyze_age_seconds_max": 0.0,
            "plan_sort_spill_bytes_proxy": 0.0,
            "plan_hash_spill_bytes_proxy": 0.0,
            "plan_hash_join_spill_bytes_proxy": 0.0,
            "plan_spill_batches_proxy": 0.0,
        }
        for query_estimate in query_estimates:
            if query_estimate.get("error"):
                continue
            replay_count = max(1, _safe_int(query_estimate.get("replay_count"), 1))
            nodes = query_estimate.get("nodes") or []
            if not isinstance(nodes, list):
                continue
            node_by_id = {
                str(node.get("node_id")): node
                for node in nodes
                if isinstance(node, dict) and node.get("node_id") is not None
            }
            aggregate["plan_node_count"] += len(nodes) * replay_count
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("node_type") or "").lower()
                rows = max(0.0, _safe_float(node.get("rows"), 0.0))
                width = max(0.0, _safe_float(node.get("width"), 0.0))
                input_rows = max(rows, _safe_float(node.get("input_rows"), rows))
                loops = max(1.0, _safe_float(node.get("execution_multiplier"), 1.0))
                parallel_factor = (
                    max(1.0, _safe_float(node.get("query_parallel_factor"), 1.0))
                    if node.get("below_gather")
                    else 1.0
                )
                output_bytes = rows * width
                input_bytes = input_rows * width
                self_cost = max(0.0, _safe_float(node.get("self_cost"), 0.0))
                total_cost = max(0.0, _safe_float(node.get("total_cost"), 0.0))
                startup_cost = max(0.0, _safe_float(node.get("startup_cost"), 0.0))
                depth = max(0.0, _safe_float(node.get("depth"), 0.0))
                aggregate["plan_max_depth"] = max(aggregate["plan_max_depth"], depth)
                aggregate["plan_total_rows"] += rows * replay_count
                aggregate["plan_total_width"] += width * replay_count
                aggregate["plan_total_cost"] += total_cost * replay_count
                aggregate["plan_startup_cost"] += startup_cost * replay_count
                aggregate["plan_self_cost"] += self_cost * replay_count
                aggregate["plan_total_input_rows"] += input_rows * replay_count
                aggregate["plan_total_output_bytes"] += output_bytes * replay_count
                aggregate["plan_total_input_bytes"] += input_bytes * replay_count
                aggregate["plan_loop_weighted_rows"] += rows * loops * replay_count
                aggregate["plan_parallel_loop_weighted_rows"] += (
                    rows * loops * parallel_factor * replay_count
                )
                aggregate["plan_max_execution_multiplier"] = max(
                    aggregate["plan_max_execution_multiplier"], loops
                )
                aggregate["plan_max_parallel_factor"] = max(
                    aggregate["plan_max_parallel_factor"], parallel_factor
                )
                aggregate["plan_workers_planned"] += _safe_float(node.get("workers_planned"), 0.0) * replay_count
                aggregate["plan_parallel_aware_nodes"] += (1.0 if node.get("parallel_aware") else 0.0) * replay_count
                children = [
                    node_by_id.get(str(child_id), {})
                    for child_id in (node.get("children") or [])
                ]
                if children:
                    child_input_rows = sum(
                        max(0.0, _safe_float(child.get("rows"), 0.0))
                        for child in children
                    )
                    ratio = rows / max(child_input_rows, 1.0)
                    aggregate["plan_parent_child_ratio_sum"] += ratio * replay_count
                    aggregate["plan_parent_child_ratio_max"] = max(
                        aggregate["plan_parent_child_ratio_max"], ratio
                    )
                    aggregate["plan_parent_child_ratio_count"] += replay_count
                relation_stats = (
                    node.get("relation_stats")
                    if isinstance(node.get("relation_stats"), dict)
                    else {}
                )
                if node_type in {
                    "seq scan",
                    "parallel seq scan",
                    "index scan",
                    "index only scan",
                    "bitmap heap scan",
                }:
                    if relation_stats:
                        aggregate["plan_relation_stats_scan_count"] += replay_count
                        relation_pages = max(
                            0.0, _safe_float(relation_stats.get("relpages"), 0.0)
                        )
                        relation_tuples = max(
                            0.0, _safe_float(relation_stats.get("reltuples"), 0.0)
                        )
                        aggregate["plan_relation_pages"] += relation_pages * replay_count
                        aggregate["plan_relation_tuples"] += relation_tuples * replay_count
                        aggregate["plan_relation_allvisible_pages"] += max(
                            0.0,
                            _safe_float(relation_stats.get("relallvisible"), 0.0),
                        ) * replay_count
                        modification_fraction = max(
                            0.0,
                            _safe_float(
                                relation_stats.get("n_mod_since_analyze"), 0.0
                            ) / max(relation_tuples, 1.0),
                        )
                        aggregate["plan_stats_modification_fraction_max"] = max(
                            aggregate["plan_stats_modification_fraction_max"],
                            modification_fraction,
                        )
                        analyze_age = _safe_float(
                            relation_stats.get("analyze_age_seconds"), -1.0
                        )
                        if analyze_age >= 0.0:
                            aggregate["plan_stats_analyze_age_seconds_max"] = max(
                                aggregate["plan_stats_analyze_age_seconds_max"],
                                analyze_age,
                            )
                    else:
                        aggregate["plan_missing_relation_stats_scan_count"] += replay_count
                if node_type == "seq scan":
                    aggregate["plan_seq_scan_count"] += replay_count
                    aggregate["plan_seq_io_proxy"] += _safe_float(node.get("pages"), 0.0) * replay_count
                elif node_type in ("index scan", "index only scan"):
                    aggregate["plan_index_scan_count"] += replay_count
                    aggregate["plan_random_io_proxy"] += _safe_float(node.get("pages"), 0.0) * replay_count
                elif node_type == "bitmap heap scan":
                    aggregate["plan_bitmap_heap_scan_count"] += replay_count
                    aggregate["plan_random_io_proxy"] += 0.5 * _safe_float(node.get("pages"), 0.0) * replay_count
                    aggregate["plan_seq_io_proxy"] += 0.5 * _safe_float(node.get("pages"), 0.0) * replay_count
                elif node_type == "hash join":
                    aggregate["plan_hash_join_count"] += replay_count
                elif node_type == "merge join":
                    aggregate["plan_merge_join_count"] += replay_count
                elif node_type == "nested loop":
                    aggregate["plan_nested_loop_count"] += replay_count
                    if len(children) > 1:
                        inner_rows = sum(
                            max(0.0, _safe_float(child.get("rows"), 0.0))
                            for child in children[1:]
                        )
                        outer_rows = max(
                            1.0, _safe_float(children[0].get("rows"), 1.0)
                        )
                        aggregate["plan_nested_loop_inner_rows_proxy"] += (
                            inner_rows * outer_rows * loops * replay_count
                        )
                elif node_type in ("sort", "incremental sort"):
                    aggregate["plan_sort_count"] += replay_count
                    spill_bytes = max(0.0, input_bytes - self._work_mem_bytes())
                    weighted_spill = spill_bytes * loops * parallel_factor * replay_count
                    aggregate["plan_sort_spill_bytes_proxy"] += weighted_spill
                    aggregate["plan_spill_pressure_proxy"] += weighted_spill
                    if spill_bytes > 0.0:
                        aggregate["plan_spill_batches_proxy"] += math.ceil(
                            input_bytes / max(self._work_mem_bytes(), 1.0)
                        ) * loops * parallel_factor * replay_count
                elif node_type == "hash":
                    aggregate["plan_hash_count"] += replay_count
                    spill_bytes = max(0.0, input_bytes * 2.5 - self._hash_mem_bytes())
                    weighted_spill = spill_bytes * loops * parallel_factor * replay_count
                    aggregate["plan_hash_spill_bytes_proxy"] += weighted_spill
                    aggregate["plan_spill_pressure_proxy"] += weighted_spill
                    if spill_bytes > 0.0:
                        aggregate["plan_spill_batches_proxy"] += math.ceil(
                            input_bytes * 2.5 / max(self._hash_mem_bytes(), 1.0)
                        ) * loops * parallel_factor * replay_count
                elif node_type in ("aggregate", "hashaggregate", "hash agg"):
                    strategy = str(node.get("strategy") or "").lower()
                    if "hash" in strategy or "hash" in node_type:
                        aggregate["plan_hashagg_count"] += replay_count
                        spill_bytes = max(0.0, input_bytes * 2.5 - self._hash_mem_bytes())
                        weighted_spill = spill_bytes * loops * parallel_factor * replay_count
                        aggregate["plan_hash_spill_bytes_proxy"] += weighted_spill
                        aggregate["plan_spill_pressure_proxy"] += weighted_spill
                        if spill_bytes > 0.0:
                            aggregate["plan_spill_batches_proxy"] += math.ceil(
                                input_bytes * 2.5 / max(self._hash_mem_bytes(), 1.0)
                            ) * loops * parallel_factor * replay_count
                if node_type == "hash join" and len(children) > 1:
                    build_child = children[1]
                    build_bytes = max(
                        0.0,
                        _safe_float(build_child.get("input_rows"), build_child.get("rows"))
                        * max(0.0, _safe_float(build_child.get("width"), 0.0))
                        * 2.5,
                    )
                    spill_bytes = max(0.0, build_bytes - self._hash_mem_bytes())
                    weighted_spill = spill_bytes * loops * parallel_factor * replay_count
                    aggregate["plan_hash_join_spill_bytes_proxy"] += weighted_spill
                    aggregate["plan_spill_pressure_proxy"] += weighted_spill
                    if spill_bytes > 0.0:
                        aggregate["plan_spill_batches_proxy"] += math.ceil(
                            build_bytes / max(self._hash_mem_bytes(), 1.0)
                        ) * loops * parallel_factor * replay_count
                elif node_type == "materialize":
                    aggregate["plan_materialize_count"] += replay_count
                elif node_type == "memoize":
                    aggregate["plan_memoize_count"] += replay_count
                elif node_type in ("gather", "gather merge"):
                    aggregate["plan_gather_count"] += replay_count
                aggregate["plan_cpu_tuple_proxy"] += rows * max(1.0, self_cost) * replay_count
        node_count = max(aggregate["plan_node_count"], 1.0)
        for name in [
            "plan_seq_scan_count",
            "plan_index_scan_count",
            "plan_bitmap_heap_scan_count",
            "plan_hash_join_count",
            "plan_merge_join_count",
            "plan_nested_loop_count",
            "plan_sort_count",
            "plan_hash_count",
            "plan_hashagg_count",
            "plan_materialize_count",
            "plan_memoize_count",
            "plan_gather_count",
            "plan_parallel_aware_nodes",
        ]:
            aggregate[f"{name}_share"] = aggregate[name] / node_count
        aggregate["plan_random_vs_seq_io_proxy"] = aggregate["plan_random_io_proxy"] / max(
            aggregate["plan_seq_io_proxy"],
            1.0,
        )
        aggregate["plan_cost_per_node"] = aggregate["plan_total_cost"] / node_count
        aggregate["plan_rows_per_node"] = aggregate["plan_total_rows"] / node_count
        aggregate["plan_parent_child_ratio_mean"] = (
            aggregate["plan_parent_child_ratio_sum"]
            / max(aggregate["plan_parent_child_ratio_count"], 1.0)
        )
        scan_count = (
            aggregate["plan_relation_stats_scan_count"]
            + aggregate["plan_missing_relation_stats_scan_count"]
        )
        aggregate["plan_relation_stats_scan_coverage"] = (
            aggregate["plan_relation_stats_scan_count"] / scan_count
            if scan_count > 0.0
            else 1.0
        )
        aggregate["plan_allvisible_fraction"] = (
            aggregate["plan_relation_allvisible_pages"]
            / max(aggregate["plan_relation_pages"], 1.0)
        )
        return aggregate

    def _extract_io_model_features(
        self,
        *,
        node_features: Sequence[PlanNodeFeature],
        workload_context: Dict[str, Any],
    ) -> Dict[str, float]:
        """Build query-level IO features compatible with exported linear models."""
        block_size = float(self._block_size())
        work_mem_bytes = self._work_mem_bytes()
        params: Dict[str, float] = {
            "pred_cache": self._cache_hit_rate(workload_context),
        }
        total_cost = 0.0
        run_cost = 0.0
        max_depth = 0.0
        filter_ops = 0.0

        def add(name: str, value: float) -> None:
            params[name] = params.get(name, 0.0) + float(value)

        for node in node_features:
            node_type = str(node.get("node_type") or "")
            node_type_lower = node_type.lower()
            rows = max(0.0, _safe_float(node.get("rows"), 0.0))
            width = max(0.0, _safe_float(node.get("width"), 0.0))
            bytes_est = rows * width
            pages_est = float(_ceil_div(bytes_est, block_size))
            total_cost += max(0.0, _safe_float(node.get("total_cost"), 0.0))
            run_cost += max(0.0, _safe_float(node.get("self_cost"), 0.0))
            max_depth = max(max_depth, _safe_float(node.get("depth"), 0.0))
            for expr_key in ("filter", "index_cond", "recheck_cond", "join_filter", "hash_cond", "merge_cond"):
                filter_ops += self._count_expression_ops(node.get(expr_key))

            if node_type_lower == "seq scan":
                add("io_N_page", pages_est)
                add("io_oper_seq_scan", 1.0)
                params["io_Selectivity"] = max(params.get("io_Selectivity", 0.0), 1.0)
            elif node_type_lower in ("index scan", "index only scan"):
                add("io_N_index_page", pages_est)
                add("io_oper_idx_scan", 1.0)
                params["io_Selectivity"] = max(params.get("io_Selectivity", 0.0), 0.3)
                if node_type_lower == "index scan":
                    add("io_N_page", pages_est)
            elif node_type_lower == "bitmap heap scan":
                add("io_pages_fetched", pages_est)
                add("io_N_page", pages_est)
                add("io_oper_bm_heap", 1.0)
            elif node_type_lower == "bitmap index scan":
                add("io_N_index_page", pages_est)
                add("io_oper_bm_idx", 1.0)
            elif node_type_lower in ("sort", "incremental sort"):
                add("io_oper_sort", 1.0)
                if bytes_est > 0:
                    add("io_npages", pages_est)
                    if bytes_est > work_mem_bytes:
                        add("io_is_spill_sort", 1.0)
                        nruns = max(1.0, bytes_est / max(work_mem_bytes, 1.0))
                        add("io_nruns", nruns)
                        params["io_M"] = max(params.get("io_M", 0.0), 6.0)
                        depth = math.ceil(math.log(max(nruns, 1.000001), 6.0)) if nruns > 1.0 else 1.0
                        add("io_npageaccesses", 2.0 * pages_est * depth)
                        add("io_Spill_Pages", pages_est * depth)
                    else:
                        add("io_nruns", 1.0)
            elif node_type_lower in ("aggregate", "hashaggregate", "hash aggregate"):
                add("io_oper_hashagg", 1.0)
                if bytes_est > 0:
                    add("io_pages_spill", pages_est)
                    if bytes_est > self._hash_mem_bytes():
                        add("io_is_spill_hashagg", 1.0)
                        batches = max(1.0, bytes_est / max(self._hash_mem_bytes(), 1.0))
                        depth = math.ceil(math.log(max(batches, 1.000001), 128.0)) if batches > 1.0 else 1.0
                        params["io_depth"] = max(params.get("io_depth", 0.0), float(depth))
                        add("io_Spill_Pages", pages_est * depth)
            elif node_type_lower in ("hash join", "nested loop", "merge join"):
                children = [n for n in (node.get("children") or [])]
                node_by_id = {item["node_id"]: item for item in node_features}
                outer = node_by_id.get(children[0], {}) if children else {}
                inner = node_by_id.get(children[1], {}) if len(children) > 1 else {}
                outer_rows = _safe_float(outer.get("rows"), 0.0)
                inner_rows = _safe_float(inner.get("rows"), 0.0)
                outer_pages = _safe_float(outer.get("pages"), 0.0)
                inner_pages = _safe_float(inner.get("pages"), 0.0)
                if node_type_lower == "hash join":
                    add("io_Outer_Pages", outer_pages)
                    add("io_Inner_Pages", inner_pages)
                    add("io_oper_hj", 1.0)
                    if inner_rows * _safe_float(inner.get("width"), 0.0) > self._hash_mem_bytes():
                        add("io_is_spill_hj", 1.0)
                        add("io_Spill_Pages", outer_pages + inner_pages)
                elif node_type_lower == "nested loop":
                    add("io_Outer_Rows", outer_rows)
                    add("io_oper_nl", 1.0)
                else:
                    add("io_oper_mj", 1.0)
                    total_rows = outer_rows + inner_rows
                    if total_rows > 0:
                        sel = min(1.0, rows / total_rows)
                        params["io_Outer_Sel_Ratio"] = max(params.get("io_Outer_Sel_Ratio", 0.0), sel)
                        params["io_Inner_Sel_Ratio"] = max(params.get("io_Inner_Sel_Ratio", 0.0), sel)
            elif node_type_lower == "materialize":
                add("io_oper_mat", 1.0)
                if bytes_est > work_mem_bytes:
                    add("io_is_spill_mat", 1.0)
                    add("io_Spill_Pages", pages_est)

        params["io_pg_total_cost"] = total_cost
        params["io_pg_run_cost"] = run_cost
        params["io_tree_max_depth"] = max_depth
        params["io_tree_node_count"] = float(len(node_features))
        params["io_filter_ops"] = filter_ops
        params["io_indexCorrelation"] = params.get("io_indexCorrelation", 0.5) or 0.5
        params["io_T"] = max(params.get("io_N_page", 0.0), 1.0)
        return self._complete_io_model_features(params)

    def _complete_io_model_features(self, params: Dict[str, float]) -> Dict[str, float]:
        n_page = max(0.0, _safe_float(params.get("io_N_page"), 0.0))
        n_index = max(0.0, _safe_float(params.get("io_N_index_page"), 0.0))
        selectivity = min(1.0, max(0.0, _safe_float(params.get("io_Selectivity"), 0.0)))
        corr = min(1.0, max(0.0, _safe_float(params.get("io_indexCorrelation"), 0.5)))
        pages_fetched = max(0.0, _safe_float(params.get("io_pages_fetched"), 0.0))
        total_pages = max(1.0, _safe_float(params.get("io_T"), max(n_page, 1.0)))
        npageaccesses = max(0.0, _safe_float(params.get("io_npageaccesses"), 0.0))
        spill_pages_raw = max(0.0, _safe_float(params.get("io_pages_spill"), 0.0))
        spill_pages = max(0.0, _safe_float(params.get("io_Spill_Pages"), 0.0))
        sort_pages = max(0.0, _safe_float(params.get("io_npages"), 0.0))
        depth = max(0.0, _safe_float(params.get("io_depth"), 0.0))
        outer_pages = max(0.0, _safe_float(params.get("io_Outer_Pages"), 0.0))
        inner_pages = max(0.0, _safe_float(params.get("io_Inner_Pages"), 0.0))
        outer_rows = max(0.0, _safe_float(params.get("io_Outer_Rows"), 0.0))
        total_cost = max(1e-6, _safe_float(params.get("io_pg_total_cost"), 0.0))
        pred_cache = min(1.0, max(0.0, _safe_float(params.get("pred_cache"), 0.5)))
        cache_miss = 1.0 - pred_cache

        params["io_I_idx_rand_pages"] = math.ceil(selectivity * n_index)
        params["io_I_idx_seq_part"] = corr ** 2 * max(math.ceil(selectivity * n_page) - 1, 0)
        params["io_I_idx_rand_part"] = math.ceil(selectivity * n_index) + n_page + corr ** 2 * (1.0 - n_page)
        params["io_I_bm_seq_part"] = pages_fetched * math.sqrt(pages_fetched / total_pages) if pages_fetched > 0 else 0.0
        params["io_I_bm_rand_part"] = pages_fetched * (1.0 - math.sqrt(pages_fetched / total_pages)) if pages_fetched > 0 else 0.0
        params["io_I_sort_seq_part"] = 0.75 * npageaccesses
        params["io_I_sort_rand_part"] = 0.25 * npageaccesses
        params["io_I_spill_seq_part"] = spill_pages_raw * depth * 2.0
        params["io_I_spill_rand_part"] = spill_pages_raw * depth * 2.0
        params["io_I_hj_spill"] = 2.0 * outer_pages + inner_pages

        total_ops = max(
            1.0,
            sum(
                _safe_float(params.get(name), 0.0)
                for name in (
                    "io_oper_seq_scan", "io_oper_idx_scan", "io_oper_bm_heap", "io_oper_bm_idx",
                    "io_oper_sort", "io_oper_hashagg", "io_oper_hj", "io_oper_nl",
                    "io_oper_mj", "io_oper_mat",
                )
            ),
        )
        params["io_seq_scan_ratio"] = _safe_float(params.get("io_oper_seq_scan"), 0.0) / total_ops
        params["io_idx_scan_ratio"] = _safe_float(params.get("io_oper_idx_scan"), 0.0) / total_ops
        params["io_hj_ratio"] = _safe_float(params.get("io_oper_hj"), 0.0) / total_ops
        params["io_nl_ratio"] = _safe_float(params.get("io_oper_nl"), 0.0) / total_ops
        params["io_sort_ratio"] = _safe_float(params.get("io_oper_sort"), 0.0) / total_ops
        params["io_cost_per_page"] = total_cost / total_pages
        params["io_cost_per_index_page"] = total_cost / max(n_index, 1.0)
        params["io_ratio_npage_per_cost"] = n_page / total_cost
        params["io_ratio_nipage_per_cost"] = n_index / total_cost
        params["io_ratio_idx_vs_table"] = n_index / max(n_page, 1.0)
        params["io_ratio_outer_vs_inner_rows"] = max(outer_rows, 1.0) / max(inner_pages, 1.0)
        params["io_ratio_outer_vs_inner_pages"] = outer_pages / max(inner_pages, 1.0)
        params["io_ratio_pages_per_row"] = max(sort_pages, 1.0) / max(selectivity, 1e-9)
        params["io_ratio_run_vs_total_cost"] = _safe_float(params.get("io_pg_run_cost"), 0.0) / total_cost
        params["io_spill_density"] = spill_pages / max(n_page, 1.0)
        params["io_sort_spill_ratio"] = _safe_float(params.get("io_is_spill_sort"), 0.0) * npageaccesses / max(_safe_float(params.get("io_npages"), 1.0), 1.0)
        params["io_pages_per_node"] = n_page / max(_safe_float(params.get("io_tree_node_count"), 1.0), 1.0)
        params["io_cost_per_node"] = total_cost / max(_safe_float(params.get("io_tree_node_count"), 1.0), 1.0)
        params["io_index_selectivity"] = selectivity * n_index / max(n_page, 1.0)

        params["io_seq_pages_est"] = n_page
        params["io_index_pages_est"] = n_index
        params["io_bitmap_pages_est"] = pages_fetched
        total_access = max(1.0, n_page + n_index + pages_fetched)
        scan_total = max(
            1.0,
            _safe_float(params.get("io_oper_seq_scan"), 0.0)
            + _safe_float(params.get("io_oper_idx_scan"), 0.0)
            + _safe_float(params.get("io_oper_bm_heap"), 0.0),
        )
        params["io_seq_scan_share"] = _safe_float(params.get("io_oper_seq_scan"), 0.0) / scan_total
        params["io_bitmap_share"] = _safe_float(params.get("io_oper_bm_heap"), 0.0) / scan_total
        params["io_spill_pressure"] = spill_pages / total_pages
        params["io_join_pressure"] = (outer_pages + inner_pages) / total_pages
        params["io_index_pressure"] = n_index / total_pages
        params["io_outer_rows_factor"] = math.log1p(outer_rows)
        params["io_formula_seq_enhanced"] = n_page + params["io_I_bm_seq_part"] + params["io_I_sort_seq_part"] + params["io_I_spill_seq_part"] + 2.0 * params["io_I_hj_spill"]
        params["io_formula_rand_enhanced"] = params["io_I_idx_rand_part"] + params["io_I_idx_rand_pages"] + params["io_I_bm_rand_part"] + params["io_I_sort_rand_part"] + n_index * max(selectivity, 0.01)
        params["io_formula_total_enhanced"] = params["io_formula_seq_enhanced"] + 4.0 * params["io_formula_rand_enhanced"]
        params["io_nl_index_rand_pressure"] = _safe_float(params.get("io_oper_nl"), 0.0) * params["io_formula_rand_enhanced"]
        params["io_nl_index_mix_pressure"] = _safe_float(params.get("io_oper_nl"), 0.0) * (params["io_formula_rand_enhanced"] + params["io_formula_seq_enhanced"])
        params["io_nl_bitmap_rand_pressure"] = _safe_float(params.get("io_oper_nl"), 0.0) * params["io_I_bm_rand_part"]
        params["io_nl_bitmap_seq_pressure"] = _safe_float(params.get("io_oper_nl"), 0.0) * params["io_I_bm_seq_part"]
        params["io_inner_sel_rand_mix"] = selectivity * params["io_I_idx_rand_part"]
        params["io_inner_sel_seq_mix"] = selectivity * params["io_I_idx_seq_part"]
        params["io_outer_sel_rand_mix"] = math.log1p(outer_rows) * params["io_I_idx_rand_part"]
        params["io_outer_sel_seq_mix"] = math.log1p(outer_rows) * params["io_I_idx_seq_part"]
        params["io_hj_build_bytes_mb"] = inner_pages * self._block_size() / (1024.0 ** 2)
        params["io_hj_probe_cold_mb"] = outer_pages * cache_miss * self._block_size() / (1024.0 ** 2)
        params["io_seq_scan_pages_est"] = n_page
        params["io_seq_cold_pages_est"] = n_page * cache_miss
        params["io_read_model_pressure"] = params["io_formula_total_enhanced"] * cache_miss
        params["interact"] = params["io_formula_total_enhanced"] * cache_miss
        params["pred_cache"] = pred_cache
        params["io_cache_miss"] = cache_miss
        params["io_has_heavy_hj_build"] = 1.0 if params["io_hj_build_bytes_mb"] > 32.0 else 0.0
        params["io_has_heavy_sort_spill"] = 1.0 if _safe_float(params.get("io_Spill_Pages"), 0.0) > 4096.0 else 0.0
        params["io_is_multi_large_scan"] = 1.0 if (_safe_float(params.get("io_oper_seq_scan"), 0.0) + _safe_float(params.get("io_oper_idx_scan"), 0.0)) > 1.0 else 0.0
        params["io_largest_table_bytes_mb"] = n_page * self._block_size() / (1024.0 ** 2)
        params["io_top2_table_access_share"] = min(1.0, (n_page + n_index) / total_access)
        return {name: float(value) for name, value in params.items()}

    def _estimate_node_io(
        self,
        *,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
        work_mem_bytes: float,
        hash_mem_bytes: float,
        statement_kind: str,
        cache_hit_rate: float,
    ) -> Dict[str, float]:
        if self._physical_model_enabled():
            return self._estimate_node_io_physical(
                node=node,
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
                cache_hit_rate=cache_hit_rate,
            )
        seq_page_cost = self._seq_page_cost()
        random_page_cost = self._random_page_cost()
        bitmap_page_cost = self._bitmap_page_cost()
        node_type = str(node.get("node_type") or "").lower()
        io_cost = max(0.0, _safe_float(node.get("self_cost"), 0.0))
        rows = max(1.0, _safe_float(node.get("rows"), 1.0))
        width = max(8.0, _safe_float(node.get("width"), 8.0))
        bytes_est = rows * width
        pages_est = float(_ceil_div(bytes_est, self._block_size()))
        logical_read_blocks = 0.0
        temp_read_blocks = 0.0
        temp_write_blocks = 0.0
        wal_write_blocks = 0.0

        if node_type == "seq scan":
            logical_read_blocks = self._seq_scan_effective_read_factor() * min(
                io_cost / max(seq_page_cost, 1e-9),
                self._seq_scan_page_cap_factor() * max(1.0, pages_est),
            )
        elif node_type == "index scan":
            logical_read_blocks = self._index_scan_effective_read_factor() * min(
                io_cost / max(random_page_cost, 1e-9),
                self._index_scan_page_cap_factor() * max(1.0, pages_est),
            )
        elif node_type == "index only scan":
            logical_read_blocks = self._index_only_effective_read_factor() * min(
                self._index_only_kappa() * io_cost / max(random_page_cost, 1e-9),
                self._index_only_page_cap_factor() * max(1.0, pages_est),
            )
        elif node_type == "bitmap index scan":
            logical_read_blocks = self._bitmap_index_effective_read_factor() * min(
                io_cost / max(random_page_cost, 1e-9),
                self._bitmap_index_page_cap_factor() * max(1.0, pages_est),
            )
        elif node_type == "bitmap heap scan":
            logical_read_blocks = self._bitmap_heap_effective_read_factor() * min(
                io_cost / max(bitmap_page_cost, 1e-9),
                self._bitmap_heap_page_cap_factor() * max(1.0, pages_est),
            )
        elif node_type in ("sort", "incremental sort"):
            if bytes_est > work_mem_bytes:
                temp_pages = pages_est
                temp_read_blocks = temp_pages
                temp_write_blocks = temp_pages
        elif node_type in ("hash", "hashaggregate", "hash aggregate"):
            if bytes_est > hash_mem_bytes:
                temp_pages = pages_est
                temp_read_blocks = temp_pages
                temp_write_blocks = temp_pages
        elif node_type == "hash join":
            build_child = self._pick_build_child(node, node_by_id)
            probe_child = self._pick_probe_child(node, node_by_id)
            build_bytes = max(0.0, _safe_float((build_child or {}).get("bytes"), 0.0))
            if build_bytes > hash_mem_bytes:
                build_pages = max(1.0, _safe_float((build_child or {}).get("pages"), 0.0))
                probe_pages = max(1.0, _safe_float((probe_child or {}).get("pages"), 0.0))
                temp_read_blocks = build_pages + probe_pages
                temp_write_blocks = build_pages + probe_pages
        elif node_type == "materialize":
            if bytes_est > work_mem_bytes:
                temp_write_blocks = pages_est

        data_read_blocks = self._node_data_read_blocks(
            node_type=node_type,
            logical_read_blocks=logical_read_blocks,
            cache_hit_rate=cache_hit_rate,
        )
        return {
            "logical_read_blocks": logical_read_blocks,
            "data_read_blocks": data_read_blocks,
            "temp_read_blocks": temp_read_blocks,
            "temp_write_blocks": temp_write_blocks,
            "wal_write_blocks": wal_write_blocks,
        }

    def _estimate_node_io_physical(
        self,
        *,
        node: PlanNodeFeature,
        work_mem_bytes: float,
        hash_mem_bytes: float,
        cache_hit_rate: float,
    ) -> Dict[str, float]:
        """Estimate this node's exclusive physical IO without planner cost units."""
        node_type = str(node.get("node_type") or "").lower()
        rows = max(1.0, _safe_float(node.get("rows"), 1.0))
        input_rows = max(rows, _safe_float(node.get("input_rows"), rows))
        width = max(8.0, _safe_float(node.get("width"), 8.0))
        loops = max(1.0, _safe_float(node.get("execution_multiplier"), 1.0))
        relation_stats = node.get("relation_stats") or {}
        index_stats = node.get("index_stats") or {}
        relation_pages = max(
            1.0,
            _safe_float(
                relation_stats.get("relpages"),
                _ceil_div(input_rows * width, self._block_size()),
            ),
        )
        relation_tuples = max(
            1.0,
            _safe_float(relation_stats.get("reltuples"), input_rows),
        )

        seq_blocks = 0.0
        random_blocks = 0.0
        temp_read_blocks = 0.0
        temp_write_blocks = 0.0
        supported = True
        if node_type in {
            "seq scan",
            "parallel seq scan",
            "index scan",
            "index only scan",
            "bitmap heap scan",
        } and not relation_stats:
            supported = False
        if node_type in {
            "index scan",
            "index only scan",
            "bitmap index scan",
        } and not index_stats:
            supported = False

        def distinct_heap_pages(selected_rows: float) -> float:
            # Uniform occupancy approximation.  It stays bounded by the heap
            # size and, unlike rows/tuples_per_page, also models scattered rows.
            selected = max(0.0, min(float(selected_rows), relation_tuples))
            if selected <= 0.0:
                return 0.0
            return min(
                relation_pages,
                relation_pages
                * (1.0 - math.exp(-selected / max(relation_pages, 1.0))),
            )

        if node_type in ("seq scan", "parallel seq scan"):
            seq_blocks = relation_pages * loops
        elif node_type in ("index scan", "index only scan"):
            heap_pages = distinct_heap_pages(rows)
            index_pages = max(3.0, _safe_float(index_stats.get("relpages"), 3.0))
            index_tuples = max(
                1.0,
                _safe_float(index_stats.get("reltuples"), relation_tuples),
            )
            index_pages_touched = min(
                index_pages,
                3.0 + index_pages * min(1.0, rows / index_tuples),
            )
            if node_type == "index only scan":
                visible_fraction = min(
                    1.0,
                    max(
                        0.0,
                        _safe_float(relation_stats.get("relallvisible"), 0.0)
                        / relation_pages,
                    ),
                )
                heap_pages *= 1.0 - visible_fraction
            random_blocks = (heap_pages + index_pages_touched) * loops
        elif node_type == "bitmap index scan":
            index_pages = max(3.0, _safe_float(index_stats.get("relpages"), 3.0))
            index_tuples = max(
                1.0,
                _safe_float(index_stats.get("reltuples"), relation_tuples),
            )
            random_blocks = min(
                index_pages,
                3.0 + index_pages * min(1.0, rows / index_tuples),
            ) * loops
        elif node_type == "bitmap heap scan":
            heap_pages = distinct_heap_pages(rows)
            coverage = min(1.0, heap_pages / relation_pages)
            # Bitmap heap access becomes increasingly sequential as coverage
            # grows because tuple locations are consumed in physical order.
            sequential_fraction = math.sqrt(coverage)
            seq_blocks = heap_pages * sequential_fraction * loops
            random_blocks = heap_pages * (1.0 - sequential_fraction) * loops
        elif node_type in ("sort", "incremental sort"):
            bytes_est = input_rows * width
            if bytes_est > work_mem_bytes:
                spill_pages = float(_ceil_div(bytes_est, self._block_size()))
                temp_read_blocks = spill_pages * loops
                temp_write_blocks = spill_pages * loops
        elif node_type == "hash" or (
            node_type == "aggregate"
            and str(node.get("strategy") or "").lower() == "hashed"
        ):
            bytes_est = input_rows * width * 2.5
            if bytes_est > hash_mem_bytes:
                spill_pages = float(_ceil_div(bytes_est, self._block_size()))
                temp_read_blocks = spill_pages * loops
                temp_write_blocks = spill_pages * loops
        elif node_type in {
            "nested loop",
            "hash join",
            "merge join",
            "aggregate",
            "gather",
            "gather merge",
            "append",
            "result",
            "limit",
            "materialize",
            "memoize",
            "windowagg",
            "unique",
            "setop",
            "modifytable",
            "lockrows",
            "subquery scan",
            "cte scan",
            "function scan",
            "values scan",
            "projectset",
            "recursive union",
        }:
            # These nodes consume child IO, which is accounted for separately.
            pass
        else:
            supported = False

        physical_cfg = self._physical_model_cfg()
        seq_miss = physical_cfg.get("seq_miss_rate")
        random_miss = physical_cfg.get("random_miss_rate")
        if seq_miss in (None, ""):
            seq_miss = self._cache_miss_factor("seq_scan", cache_hit_rate)
        if random_miss in (None, ""):
            random_miss = self._cache_miss_factor("index_scan", cache_hit_rate)
        seq_miss_value = min(1.0, max(0.0, _safe_float(seq_miss, 1.0)))
        random_miss_value = min(1.0, max(0.0, _safe_float(random_miss, 1.0)))
        data_read_blocks = (
            seq_blocks * seq_miss_value
            + random_blocks * random_miss_value
        )
        return {
            "logical_read_blocks": seq_blocks + random_blocks,
            "seq_read_blocks": seq_blocks,
            "random_read_blocks": random_blocks,
            "data_read_blocks": data_read_blocks,
            "temp_read_blocks": temp_read_blocks,
            "temp_write_blocks": temp_write_blocks,
            # WAL is a statement-level quantity and is added exactly once by
            # _estimate_query_resources.
            "wal_write_blocks": 0.0,
            "physical_direction_supported": 1.0 if supported else 0.0,
        }

    def _estimate_node_cpu(
        self,
        *,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
    ) -> Dict[str, float]:
        if self._physical_model_enabled():
            return self._estimate_node_cpu_physical(
                node=node,
                node_by_id=node_by_id,
            )
        params_cfg = (self.estimator_cfg.get("cpu") or {})
        cpu_tuple_cost = _safe_float(params_cfg.get("cpu_tuple_cost"), 0.01)
        cpu_index_tuple_cost = _safe_float(params_cfg.get("cpu_index_tuple_cost"), 0.005)
        cpu_operator_cost = _safe_float(params_cfg.get("cpu_operator_cost"), 0.0025)
        parallel_setup_cost = _safe_float(params_cfg.get("parallel_setup_cost"), 1000.0)
        parallel_tuple_cost = _safe_float(params_cfg.get("parallel_tuple_cost"), 0.1)
        fallback_cost_scale = _safe_float(params_cfg.get("fallback_cost_scale"), 1.0)

        rows = max(1.0, _safe_float(node.get("rows"), 1.0))
        node_type = str(node.get("node_type") or "").lower()

        if node_type == "seq scan":
            cpu_work = rows * cpu_tuple_cost
        elif node_type in ("index scan", "index only scan", "bitmap heap scan", "bitmap index scan"):
            cpu_work = rows * (cpu_index_tuple_cost + cpu_operator_cost)
        elif node_type in ("sort", "incremental sort"):
            cpu_work = rows * math.log2(max(rows, 2.0)) * cpu_operator_cost
        elif node_type in ("hash", "hashaggregate", "hash aggregate"):
            cpu_work = rows * cpu_operator_cost * 2.0
        elif node_type == "hash join":
            child_rows = sum(
                _safe_float(node_by_id.get(child_id, {}).get("rows"), 0.0)
                for child_id in (node.get("children") or [])
            )
            cpu_work = max(rows, child_rows) * cpu_operator_cost * 2.0
        elif node_type == "merge join":
            child_rows = sum(
                _safe_float(node_by_id.get(child_id, {}).get("rows"), 0.0)
                for child_id in (node.get("children") or [])
            )
            cpu_work = max(rows, child_rows) * cpu_operator_cost * 1.5
        elif node_type == "nested loop":
            outer_rows = 0.0
            children = node.get("children") or []
            if children:
                outer_rows = _safe_float(node_by_id.get(children[0], {}).get("rows"), 0.0)
            cpu_work = max(rows, outer_rows) * cpu_operator_cost
        elif "aggregate" in node_type:
            cpu_work = rows * cpu_operator_cost
        elif node_type in ("gather", "gather merge"):
            cpu_work = parallel_setup_cost + rows * parallel_tuple_cost
        elif node_type in ("append", "result", "limit", "materialize", "memoize"):
            cpu_work = rows * cpu_operator_cost * 0.5
        else:
            cpu_work = rows * cpu_operator_cost * 0.25 * fallback_cost_scale

        if node.get("below_gather"):
            cpu_work *= max(1, _safe_int(node.get("query_parallel_factor"), 1))
        return {"cpu_work": cpu_work}

    def _estimate_node_cpu_physical(
        self,
        *,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
    ) -> Dict[str, float]:
        """Estimate exclusive executor work using rows and node semantics."""
        del node_by_id  # Child work is accounted for by each child's own record.
        node_type = str(node.get("node_type") or "").lower()
        rows = max(1.0, _safe_float(node.get("rows"), 1.0))
        input_rows = max(rows, _safe_float(node.get("input_rows"), rows))
        loops = max(1.0, _safe_float(node.get("execution_multiplier"), 1.0))
        relation_stats = node.get("relation_stats") or {}
        expression_count = sum(
            self._count_expression_ops(node.get(name))
            for name in (
                "filter",
                "index_cond",
                "recheck_cond",
                "join_filter",
                "hash_cond",
                "merge_cond",
            )
        )
        supported = True
        if node_type in ("seq scan", "parallel seq scan") and not relation_stats:
            supported = False

        # Weights describe executor operations, not PostgreSQL planner costs.
        weights = {
            "seq_scan": 1.0,
            "index_scan": 2.0,
            "bitmap_index": 0.4,
            "bitmap_heap": 1.5,
            "nested_loop": 0.5,
            "hash_build": 1.5,
            "hash_probe": 1.0,
            "merge_join": 0.8,
            "sort": 0.3,
            "aggregate": 1.3,
            "gather": 0.1,
            "projection": 0.25,
            "expression": 0.15,
        }
        configured_weights = self._physical_model_cfg().get("cpu_node_weights")
        if isinstance(configured_weights, dict):
            for name, value in configured_weights.items():
                parsed = _safe_float(value, float("nan"))
                if math.isfinite(parsed) and parsed >= 0.0:
                    weights[str(name)] = parsed
        fixed_work = {
            "seq scan": 2.0,
            "parallel seq scan": 2.0,
            "index scan": 1.0,
            "index only scan": 1.0,
            "bitmap index scan": 10.0,
            "bitmap heap scan": 10.0,
            "nested loop": 2.0,
            "hash": 20.0,
            "hash join": 5.0,
            "merge join": 5.0,
            "sort": 10.0,
            "incremental sort": 10.0,
            "aggregate": 5.0,
            "gather": 20.0,
            "gather merge": 20.0,
        }
        configured_fixed_work = self._physical_model_cfg().get(
            "cpu_node_fixed_work"
        )
        if isinstance(configured_fixed_work, dict):
            for name, value in configured_fixed_work.items():
                parsed = _safe_float(value, float("nan"))
                if math.isfinite(parsed) and parsed >= 0.0:
                    fixed_work[str(name).strip().lower()] = parsed

        if node_type in ("seq scan", "parallel seq scan"):
            scanned_rows = max(
                input_rows,
                _safe_float(relation_stats.get("reltuples"), input_rows),
            )
            self_work = scanned_rows * weights["seq_scan"]
        elif node_type in ("index scan", "index only scan"):
            self_work = rows * weights["index_scan"]
        elif node_type == "bitmap index scan":
            self_work = rows * weights["bitmap_index"]
        elif node_type == "bitmap heap scan":
            self_work = rows * weights["bitmap_heap"]
        elif node_type == "nested loop":
            self_work = input_rows * weights["nested_loop"]
        elif node_type == "hash":
            self_work = input_rows * weights["hash_build"]
        elif node_type == "hash join":
            # Hash construction belongs to the Hash child; this node only
            # accounts for probes and join qualification.
            self_work = input_rows * weights["hash_probe"]
        elif node_type == "merge join":
            self_work = input_rows * weights["merge_join"]
        elif node_type in ("sort", "incremental sort"):
            self_work = (
                input_rows
                * math.log2(max(input_rows, 2.0))
                * weights["sort"]
            )
        elif node_type == "aggregate":
            strategy = str(node.get("strategy") or "").lower()
            factor = weights["aggregate"] * (1.15 if strategy == "hashed" else 1.0)
            self_work = input_rows * factor
        elif node_type in ("gather", "gather merge"):
            self_work = rows * weights["gather"]
        elif node_type in {
            "append",
            "result",
            "limit",
            "materialize",
            "memoize",
            "windowagg",
            "unique",
            "setop",
            "modifytable",
            "lockrows",
            "subquery scan",
            "cte scan",
            "function scan",
            "values scan",
            "projectset",
            "recursive union",
        }:
            self_work = input_rows * weights["projection"]
        else:
            self_work = input_rows * weights["projection"]
            supported = False

        self_work += fixed_work.get(node_type, 0.0)
        self_work += input_rows * expression_count * weights["expression"]
        # Plan Rows below Gather is normally per worker.  Relation-wide Seq Scan
        # work already uses total reltuples and must not be multiplied again.
        parallel_factor = 1
        if (
            node.get("below_gather")
            and node_type not in ("seq scan", "parallel seq scan")
        ):
            parallel_factor = max(
                1,
                _safe_int(node.get("query_parallel_factor"), 1),
            )
        cpu_work = self_work * loops * parallel_factor
        return {
            "cpu_work": cpu_work,
            "physical_direction_supported": 1.0 if supported else 0.0,
        }

    def _estimate_node_memory(
        self,
        *,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
        work_mem_bytes: float,
        hash_mem_bytes: float,
    ) -> float:
        if self._physical_model_enabled():
            return self._estimate_node_memory_physical(
                node=node,
                node_by_id=node_by_id,
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
            )
        memory_cfg = self.estimator_cfg.get("memory") or {}
        beta_sort = _safe_float(memory_cfg.get("beta_sort"), 1.0)
        beta_hash = _safe_float(memory_cfg.get("beta_hash"), 1.0)
        beta_hashagg = _safe_float(memory_cfg.get("beta_hashagg"), 1.0)
        beta_materialize = _safe_float(memory_cfg.get("beta_materialize"), 1.0)
        beta_memoize = _safe_float(memory_cfg.get("beta_memoize"), 0.0)

        node_type = str(node.get("node_type") or "").lower()
        bytes_est = max(0.0, _safe_float(node.get("bytes"), 0.0))

        if node_type in ("sort", "incremental sort"):
            return min(work_mem_bytes, beta_sort * bytes_est)
        if node_type in ("hash",):
            return min(hash_mem_bytes, beta_hash * bytes_est)
        if node_type == "hash join":
            build_child = self._pick_build_child(node, node_by_id)
            build_bytes = max(0.0, _safe_float((build_child or {}).get("bytes"), 0.0))
            return min(hash_mem_bytes, beta_hash * build_bytes)
        if node_type in ("hashaggregate", "hash aggregate"):
            return min(hash_mem_bytes, beta_hashagg * bytes_est)
        if node_type == "materialize":
            return min(work_mem_bytes, beta_materialize * bytes_est)
        if node_type == "memoize":
            return min(work_mem_bytes, beta_memoize * bytes_est)
        return 0.0

    def _estimate_node_memory_physical(
        self,
        *,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
        work_mem_bytes: float,
        hash_mem_bytes: float,
    ) -> float:
        """Return the memory retained by this operator at its peak phase."""
        node_type = str(node.get("node_type") or "").lower()
        rows = max(1.0, _safe_float(node.get("input_rows"), node.get("rows")))
        width = max(8.0, _safe_float(node.get("width"), 8.0))
        raw_bytes = rows * width
        memory_cfg = self.estimator_cfg.get("memory") or {}
        if node_type in ("sort", "incremental sort"):
            return min(
                work_mem_bytes,
                max(0.0, _safe_float(memory_cfg.get("beta_sort"), 1.0))
                * raw_bytes,
            )
        if node_type == "hash":
            return min(
                hash_mem_bytes,
                max(0.0, _safe_float(memory_cfg.get("beta_hash"), 1.0))
                * raw_bytes
                * 2.5,
            )
        if node_type == "hash join":
            # A separate Hash child owns the build table.  Only fall back to a
            # join-level allocation for non-standard plans without that node.
            children = [
                node_by_id.get(child_id)
                for child_id in (node.get("children") or [])
            ]
            if any(
                str((child or {}).get("node_type") or "").lower() == "hash"
                for child in children
            ):
                return 0.0
            return min(hash_mem_bytes, raw_bytes * 2.5)
        if (
            node_type == "aggregate"
            and str(node.get("strategy") or "").lower() == "hashed"
        ):
            return min(
                hash_mem_bytes,
                max(0.0, _safe_float(memory_cfg.get("beta_hashagg"), 1.0))
                * raw_bytes
                * 2.0,
            )
        if node_type == "materialize":
            return min(
                work_mem_bytes,
                max(
                    0.0,
                    _safe_float(memory_cfg.get("beta_materialize"), 1.0),
                )
                * raw_bytes,
            )
        if node_type == "memoize":
            return min(
                work_mem_bytes,
                max(0.0, _safe_float(memory_cfg.get("beta_memoize"), 1.0))
                * raw_bytes,
            )
        if node_type == "windowagg":
            return min(work_mem_bytes, raw_bytes * 0.3)
        return 0.0

    def _estimate_pipeline_memory_bytes(
        self,
        *,
        node_features: Sequence[PlanNodeFeature],
        work_mem_bytes: float,
        hash_mem_bytes: float,
        include_parallel_workers: bool,
    ) -> float:
        """
        Estimate executor peak memory with blocking-operator lifetimes.

        Hash build memory survives while the probe side runs.  Sort-like nodes
        are phase-separated from their input, so their peaks use max rather than
        unconditional summation.
        """
        node_by_id = {
            str(node.get("node_id")): node
            for node in node_features
            if node.get("node_id") is not None
        }
        own_memory = {
            node_id: self._estimate_node_memory_physical(
                node=node,
                node_by_id=node_by_id,
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
            )
            for node_id, node in node_by_id.items()
        }

        def peak(node_id: str) -> float:
            node = node_by_id[node_id]
            children = [
                str(child_id)
                for child_id in (node.get("children") or [])
                if str(child_id) in node_by_id
            ]
            self_memory = own_memory[node_id]
            if not children:
                value = self_memory
            else:
                child_peaks = {child_id: peak(child_id) for child_id in children}
                node_type = str(node.get("node_type") or "").lower()
                if node_type == "hash join":
                    hash_children = [
                        child_id
                        for child_id in children
                        if str(node_by_id[child_id].get("node_type") or "").lower()
                        == "hash"
                    ]
                    other_children = [
                        child_id for child_id in children if child_id not in hash_children
                    ]
                    retained_hash = sum(child_peaks[item] for item in hash_children)
                    probe_peak = max(
                        (child_peaks[item] for item in other_children),
                        default=0.0,
                    )
                    value = self_memory + retained_hash + probe_peak
                elif node_type == "hash":
                    value = self_memory + max(child_peaks.values(), default=0.0)
                elif node_type in {
                    "sort",
                    "incremental sort",
                    "aggregate",
                    "materialize",
                    "memoize",
                    "windowagg",
                }:
                    value = max(
                        self_memory,
                        max(child_peaks.values(), default=0.0),
                    )
                else:
                    value = self_memory + max(
                        child_peaks.values(),
                        default=0.0,
                    )
            if (
                include_parallel_workers
                and str(node.get("node_type") or "").lower()
                in ("gather", "gather merge")
                and children
            ):
                parallel_factor = max(
                    1,
                    _safe_int(node.get("query_parallel_factor"), 1),
                    max(
                        (
                            _safe_int(
                                node_by_id[child_id].get("query_parallel_factor"),
                                1,
                            )
                            for child_id in children
                        ),
                        default=1,
                    ),
                )
                value *= parallel_factor
            return value

        return max(
            (
                peak(str(node["node_id"]))
                for node in node_features
                if node.get("parent_id") is None
                and node.get("node_id") is not None
            ),
            default=0.0,
        )

    def _estimate_path_memory_mb(self, node_features: Sequence[PlanNodeFeature]) -> float:
        relation_names = {
            str(node.get("relation_name"))
            for node in node_features
            if node.get("relation_name")
        }
        if relation_names:
            t = len(relation_names)
        else:
            t = sum(
                1
                for node in node_features
                if str(node.get("node_type") or "").lower()
                in ("seq scan", "index scan", "index only scan", "bitmap heap scan")
            )
        d = max((_safe_int(node.get("depth"), 0) for node in node_features), default=0)
        if t == 0:
            return 0.0
        if t < 12:
            y = 0.0972 * math.exp(0.4862 * t) - 0.8118 * d - 1.1793
        else:
            y = 5.5415 * t - 0.8744 * d - 16.1513
        return max(0.0, y)

    def _estimate_node_derived_memory_mb(
        self,
        *,
        node: PlanNodeFeature,
        work_mem_kb: float,
    ) -> float:
        node_type = str(node.get("node_type") or "")
        op_name = node_type.lower()
        plan_rows = max(0.0, _safe_float(node.get("rows"), 0.0))
        plan_width = max(0.0, _safe_float(node.get("width"), 0.0))
        work_mem = max(0.0, work_mem_kb)

        if op_name == "hash":
            hash_batches = _safe_int(
                node.get("original_hash_batches", node.get("hash_batches", 1)),
                1,
            )
            if hash_batches < 1:
                hash_batches = 1
            hash_buckets = _safe_int(
                node.get("original_hash_buckets", node.get("hash_buckets", 1)),
                1,
            )
            bucket_bytes = max(hash_buckets, 1024) * 8.0
            data_bytes = (plan_rows / hash_batches) * (plan_width + 24.0)
            estimated_kb = min((bucket_bytes + data_bytes) / 1024.0, work_mem)
            return max(0.0, estimated_kb) / 1024.0

        if op_name == "sort":
            estimated_kb = (
                7.131913e-4 * plan_rows * plan_width
                + 2.924917e-3 * plan_rows * math.log1p(plan_rows)
                + 2.5
            )
            return max(0.0, min(estimated_kb, work_mem)) / 1024.0

        return 0.0

    def _estimate_node_io_cost_proxy(
        self,
        *,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
    ) -> float:
        seq_page_cost = self._seq_page_cost()
        random_page_cost = self._random_page_cost()
        bitmap_page_cost = self._bitmap_page_cost()
        node_type = str(node.get("node_type") or "").lower()
        self_cost = max(0.0, _safe_float(node.get("self_cost"), 0.0))
        bytes_est = max(0.0, _safe_float(node.get("bytes"), 0.0))
        pages_est = float(_ceil_div(bytes_est, self._block_size()))

        if node_type == "seq scan":
            return pages_est * seq_page_cost
        if node_type in ("index scan", "bitmap index scan"):
            return pages_est * random_page_cost
        if node_type == "index only scan":
            return pages_est * random_page_cost
        if node_type == "bitmap heap scan":
            return pages_est * bitmap_page_cost
        if node_type in ("sort", "incremental sort"):
            if bytes_est > self._work_mem_bytes():
                return 2.0 * pages_est * seq_page_cost
            return 0.0
        if node_type in ("hash", "hashaggregate", "hash aggregate"):
            if bytes_est > self._hash_mem_bytes():
                return 2.0 * pages_est * seq_page_cost
            return 0.0
        if node_type == "hash join":
            build_child = self._pick_build_child(node, node_by_id)
            probe_child = self._pick_probe_child(node, node_by_id)
            build_bytes = max(0.0, _safe_float((build_child or {}).get("bytes"), 0.0))
            if build_bytes > self._hash_mem_bytes():
                build_pages = max(1.0, _safe_float((build_child or {}).get("pages"), 0.0))
                probe_pages = max(1.0, _safe_float((probe_child or {}).get("pages"), 0.0))
                return (build_pages + probe_pages) * seq_page_cost
            return 0.0
        if node_type == "materialize":
            if bytes_est > self._work_mem_bytes():
                return pages_est * seq_page_cost
            return 0.0
        return min(self_cost, pages_est * seq_page_cost)

    def _pick_build_child(
        self,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
    ) -> Optional[PlanNodeFeature]:
        children = [node_by_id.get(child_id) for child_id in (node.get("children") or [])]
        children = [item for item in children if item]
        if not children:
            return None
        return min(children, key=lambda item: _safe_float(item.get("rows"), 0.0))

    def _pick_probe_child(
        self,
        node: PlanNodeFeature,
        node_by_id: Dict[str, PlanNodeFeature],
    ) -> Optional[PlanNodeFeature]:
        children = [node_by_id.get(child_id) for child_id in (node.get("children") or [])]
        children = [item for item in children if item]
        if not children:
            return None
        return max(children, key=lambda item: _safe_float(item.get("rows"), 0.0))

    def _workload_window_seconds(self, workload_context: Dict[str, Any]) -> float:
        return max(1e-6, _safe_float(workload_context.get("elapsed_seconds"), 1.0))

    def _feature_window_seconds(self, workload_context: Dict[str, Any]) -> float:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        return max(
            1e-6,
            _safe_float(
                workload_context.get("feature_window_seconds")
                or shared_cfg.get("feature_window_seconds")
                or shared_cfg.get("model_feature_window_seconds"),
                5.0,
            ),
        )

    def _model_feature_window_scale(self, workload_context: Dict[str, Any]) -> float:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        if not bool(shared_cfg.get("normalize_model_features_to_window", True)):
            return 1.0
        family = self._infer_workload_family(workload_context)
        if family == "H" and not bool(shared_cfg.get("normalize_serial_model_features_to_window", False)):
            return 1.0
        return self._feature_window_seconds(workload_context) / self._workload_window_seconds(workload_context)

    def _explicit_active_sql_count(self, workload_context: Dict[str, Any]) -> float:
        for key in (
            "model_active_sql_count",
            "active_sql_count_5s",
            "feature_active_sql_count",
            "active_sql_count",
        ):
            value = _safe_float(workload_context.get(key), 0.0)
            if value > 0.0:
                return value
        return 0.0

    def _cache_hit_rate(self, workload_context: Dict[str, Any]) -> float:
        return min(1.0, max(0.0, _safe_float(workload_context.get("cache_hit_rate"), 0.0)))

    def _cache_miss_factor(self, category: str, cache_hit_rate: float) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        miss_cfg = io_cfg.get("cache_miss_factors") or {}
        miss_global = max(0.0, 1.0 - cache_hit_rate)
        alpha_defaults = {
            "seq_scan": 1.5,
            "index_scan": 0.5,
            "index_only": 0.3,
            "bitmap_index": 0.4,
            "bitmap_heap": 1.0,
        }
        alpha = _safe_float(miss_cfg.get(category), alpha_defaults.get(category, 1.0))
        return min(1.0, max(0.0, miss_global * max(0.0, alpha)))

    def _node_data_read_blocks(
        self,
        *,
        node_type: str,
        logical_read_blocks: float,
        cache_hit_rate: float,
    ) -> float:
        normalized = str(node_type or "").lower()
        if normalized == "seq scan":
            miss_factor = self._cache_miss_factor("seq_scan", cache_hit_rate)
        elif normalized == "index scan":
            miss_factor = self._cache_miss_factor("index_scan", cache_hit_rate)
        elif normalized == "index only scan":
            miss_factor = self._cache_miss_factor("index_only", cache_hit_rate)
        elif normalized == "bitmap index scan":
            miss_factor = self._cache_miss_factor("bitmap_index", cache_hit_rate)
        elif normalized == "bitmap heap scan":
            miss_factor = self._cache_miss_factor("bitmap_heap", cache_hit_rate)
        else:
            miss_factor = 1.0
        return max(0.0, logical_read_blocks * miss_factor)

    def _concurrency_value(self, workload_context: Dict[str, Any]) -> float:
        concurrency = workload_context.get("concurrency") or {}
        if isinstance(concurrency, dict):
            candidates = [
                concurrency.get("avg"),
                concurrency.get("sampled_avg"),
                concurrency.get("configured"),
                concurrency.get("avg_estimated"),
                concurrency.get("max"),
            ]
            for item in candidates:
                value = _safe_float(item, 0.0)
                if value > 0:
                    return value
        return 1.0

    def _resident_memory_baseline_bytes(self, workload_context: Dict[str, Any]) -> float:
        memory_cfg = self.estimator_cfg.get("memory") or {}
        baseline_bytes = _safe_float(
            workload_context.get("baseline_runtime_memory_bytes"),
            0.0,
        )
        if baseline_bytes <= 0:
            runtime_resource_raw = workload_context.get("runtime_resource_raw") or {}
            if isinstance(runtime_resource_raw, dict):
                baseline_bytes = _safe_float(runtime_resource_raw.get("memory"), 0.0)
        if baseline_bytes <= 0:
            baseline_bytes = _safe_float(memory_cfg.get("resident_memory_bytes"), 0.0)
        resident_scale = _safe_float(memory_cfg.get("resident_memory_scale"), 1.0)
        return max(0.0, baseline_bytes * max(0.0, resident_scale))

    def _block_size(self) -> int:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        return max(1024, _safe_int(shared_cfg.get("block_size"), 8192))

    def _seq_page_cost(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(1e-9, _safe_float(io_cfg.get("seq_page_cost"), 1.0))

    def _random_page_cost(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(1e-9, _safe_float(io_cfg.get("random_page_cost"), 4.0))

    def _bitmap_page_cost(self) -> float:
        return (self._seq_page_cost() + self._random_page_cost()) / 2.0

    def _seq_scan_page_cap_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(0.1, _safe_float(io_cfg.get("seq_scan_page_cap_factor"), 1.0))

    def _seq_scan_effective_read_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return min(1.0, max(0.001, _safe_float(io_cfg.get("seq_scan_effective_read_factor"), 0.1)))

    def _index_scan_page_cap_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(0.1, _safe_float(io_cfg.get("index_scan_page_cap_factor"), 4.0))

    def _index_scan_effective_read_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return min(1.0, max(0.001, _safe_float(io_cfg.get("index_scan_effective_read_factor"), 0.2)))

    def _index_only_page_cap_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(0.1, _safe_float(io_cfg.get("index_only_page_cap_factor"), 2.0))

    def _index_only_effective_read_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return min(1.0, max(0.001, _safe_float(io_cfg.get("index_only_effective_read_factor"), 0.1)))

    def _bitmap_index_page_cap_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(0.1, _safe_float(io_cfg.get("bitmap_index_page_cap_factor"), 2.0))

    def _bitmap_index_effective_read_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return min(1.0, max(0.001, _safe_float(io_cfg.get("bitmap_index_effective_read_factor"), 0.1)))

    def _bitmap_heap_page_cap_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(0.1, _safe_float(io_cfg.get("bitmap_heap_page_cap_factor"), 1.0))

    def _bitmap_heap_effective_read_factor(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return min(1.0, max(0.001, _safe_float(io_cfg.get("bitmap_heap_effective_read_factor"), 0.15)))

    def _work_mem_bytes(self) -> float:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        return max(
            float(self._block_size()),
            _safe_float(shared_cfg.get("work_mem_bytes"), 4.0 * 1024.0 * 1024.0),
        )

    def _hash_mem_bytes(self) -> float:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        return self._work_mem_bytes() * max(
            1.0,
            _safe_float(shared_cfg.get("hash_mem_multiplier"), 2.0),
        )

    def _index_only_kappa(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        allvisfrac = io_cfg.get("allvisfrac")
        if allvisfrac not in (None, ""):
            return 1.0 + max(0.0, 1.0 - min(1.0, _safe_float(allvisfrac, 0.0)))
        alpha = _safe_float(io_cfg.get("index_only_heap_back_alpha"), 0.3)
        return 1.0 + max(0.0, alpha)

    def _wal_blocks_per_write_statement(self) -> float:
        io_cfg = self.estimator_cfg.get("io") or {}
        return max(0.0, _safe_float(io_cfg.get("wal_blocks_per_write_statement"), 1.0))

    def _psql_args(self, sql: str) -> List[str]:
        return [
            "psql",
            "-h",
            self.pg_host,
            "-p",
            str(self.pg_port),
            "-U",
            self.pg_user,
            "-d",
            self.pg_dbname,
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-F",
            "\t",
            "-c",
            sql,
        ]

    def _psql_query(self, sql: str) -> str:
        if self.psql_query_func is not None:
            return self.psql_query_func(sql)
        proc = subprocess.run(
            self._psql_args(sql),
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            raise RuntimeError(f"psql query failed: {detail}")
        return (proc.stdout or "").strip()

    def _quote_sql_literal(self, value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        text_value = str(value)
        if text_value.upper() == "NULL":
            return "NULL"
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text_value):
            return text_value
        if text_value.startswith(("'", "DATE '", "TIMESTAMP '", "INTERVAL '")):
            return text_value
        return "'" + text_value.replace("'", "''") + "'"

    def _guess_custom_plan_param_values(
        self,
        queryid: str,
        query_text: str,
    ) -> Dict[int, str]:
        configured = self.custom_plan_params.get(queryid or "")
        placeholder_indices = sorted(
            {
                int(match.group(1))
                for match in re.finditer(r"\$(\d+)\b", query_text)
            }
        )
        if not configured:
            raise RuntimeError(
                "unsafe_custom_plan_binding_missing: no sealed explicit values "
                f"for queryid={queryid!r}"
            )
        expected_indices = (
            list(range(1, placeholder_indices[-1] + 1))
            if placeholder_indices
            else []
        )
        if (
            placeholder_indices != expected_indices
            or len(configured) != len(expected_indices)
        ):
            raise RuntimeError(
                "unsafe_custom_plan_binding_incomplete: placeholder indices "
                f"{placeholder_indices}, configured value count={len(configured)}"
            )
        return {
            idx + 1: self._quote_sql_literal(value)
            for idx, value in enumerate(configured)
        }

    def _materialize_query_for_custom_plan(self, query_text: str, queryid: Any) -> str:
        values = self._guess_custom_plan_param_values(str(queryid or ""), query_text)
        materialized = query_text
        for idx in sorted(values.keys(), reverse=True):
            materialized = re.sub(rf"\${idx}\b", values[idx], materialized)
        return materialized

    def _normalize_query_for_explain(self, query_text: str) -> str:
        rewritten, _audit = rewrite_pgss_query_for_generic_explain(query_text)
        return rewritten

    def _build_explain_sql(
        self,
        query_text: str,
        *,
        mode: str,
        queryid: Any = None,
    ) -> Tuple[str, str]:
        has_params = bool(re.search(r"\$\d+\b", query_text))
        explain_mode = str(mode or "generic").strip().lower()
        if explain_mode == "generic":
            if not has_params:
                return (
                    "EXPLAIN (FORMAT JSON, COSTS true, VERBOSE false, BUFFERS false) "
                    + query_text,
                    "generic",
                )
            return (
                "EXPLAIN (FORMAT JSON, GENERIC_PLAN true, COSTS true, VERBOSE false, BUFFERS false) "
                + query_text,
                "generic",
            )
        custom_query = (
            self._materialize_query_for_custom_plan(query_text, queryid)
            if has_params
            else query_text
        )
        return (
            "EXPLAIN (FORMAT JSON, COSTS true, VERBOSE false, BUFFERS false) "
            + custom_query,
            explain_mode,
        )

    def _build_explain_attempts(
        self,
        query_text: str,
        *,
        mode: str,
        queryid: Any = None,
    ) -> List[Tuple[str, str]]:
        normalized_query = self._normalize_query_for_explain(query_text)
        has_params = bool(re.search(r"\$\d+\b", query_text))
        attempts: List[Tuple[str, str]] = []
        seen: set[Tuple[str, str]] = set()

        if normalized_query != query_text:
            query_attempts = (
                (normalized_query, "_rewritten"),
                (query_text, ""),
            )
        else:
            query_attempts = ((query_text, ""),)

        for candidate_query, mode_suffix in query_attempts:
            explain_sql, explain_mode = self._build_explain_sql(
                candidate_query,
                mode=mode,
                queryid=queryid,
            )
            item = (explain_sql, explain_mode + mode_suffix)
            if item not in seen:
                attempts.append(item)
                seen.add(item)

        configured_custom_binding = bool(
            self.custom_plan_params.get(str(queryid or ""))
        )
        if has_params and configured_custom_binding:
            explain_sql, explain_mode = self._build_explain_sql(
                normalized_query,
                mode="custom",
                queryid=queryid,
            )
            item = (explain_sql, explain_mode + "_fallback")
            if item not in seen:
                attempts.append(item)
                seen.add(item)
        return attempts

    def _candidate_param_set_sql(self, params: Optional[Dict[str, float]]) -> List[str]:
        if not isinstance(params, dict):
            return []
        statements: List[str] = []
        for name in sorted(COST_PARAM_SET):
            if name not in params:
                continue
            value = float(params[name])
            if not math.isfinite(value):
                continue
            statements.append(f"SET LOCAL {name} = {value:.17g}")
        return statements

    def _wrap_explain_sql_with_params(
        self,
        explain_sql: str,
        params: Optional[Dict[str, float]],
    ) -> str:
        set_statements = self._candidate_param_set_sql(params)
        if not set_statements:
            return explain_sql
        # Keep EXPLAIN as the final command. Some psql call paths only return
        # the last command tag when ROLLBACK follows, which hides the JSON plan.
        statements = ["BEGIN", *set_statements, explain_sql]
        return ";\n".join(statements) + ";"

    def _extract_explain_json(self, raw: str) -> Any:
        text = str(raw or "").strip()
        if not text:
            raise RuntimeError("empty EXPLAIN output")
        starts = [idx for idx in (text.find("["), text.find("{")) if idx >= 0]
        if not starts:
            raise RuntimeError(
                "EXPLAIN output did not contain JSON; "
                f"output_prefix={text[:240]!r}"
            )
        start = min(starts)
        if text[start] == "[":
            end = text.rfind("]")
        else:
            end = text.rfind("}")
        if end < start:
            raise RuntimeError(
                "EXPLAIN JSON output was incomplete; "
                f"output_prefix={text[:240]!r}"
            )
        return json.loads(text[start : end + 1])

    def _is_retryable_explain_error(self, exc: Exception) -> bool:
        message = str(exc)
        return (
            "empty EXPLAIN output" in message
            or "EXPLAIN output did not contain JSON" in message
            or "EXPLAIN JSON output was incomplete" in message
        )

    def _explain_query_json(
        self,
        query_text: str,
        *,
        mode: str,
        queryid: Any = None,
        params: Optional[Dict[str, float]] = None,
    ) -> Tuple[Dict[str, Any], str]:
        errors: List[str] = []
        for explain_sql, explain_mode in self._build_explain_attempts(
            query_text,
            mode=mode,
            queryid=queryid,
        ):
            wrapped_sql = self._wrap_explain_sql_with_params(explain_sql, params)
            attempt_errors: List[str] = []
            for retry_idx, delay_s in enumerate((0.0, 1.0, 3.0), start=1):
                if delay_s > 0.0:
                    time.sleep(delay_s)
                try:
                    raw = self._psql_query(wrapped_sql)
                    plan_json = self._extract_explain_json(raw)
                    plan_doc = plan_json[0] if isinstance(plan_json, list) and plan_json else plan_json
                    if not isinstance(plan_doc, dict):
                        raise RuntimeError("unexpected plan document")
                    return plan_doc, explain_mode
                except Exception as exc:
                    attempt_errors.append(f"attempt_{retry_idx}: {exc}")
                    if not self._is_retryable_explain_error(exc):
                        break
            errors.append(f"{explain_mode}: {' | '.join(attempt_errors)}")
        raise RuntimeError(" ; ".join(errors))


def estimate_plan_resources(
    *,
    queries: Sequence[Dict[str, Any]],
    pg_host: str,
    pg_port: str,
    pg_user: str,
    pg_dbname: str,
    estimator_cfg: Optional[Dict[str, Any]] = None,
    plan_capture_modes: Optional[Sequence[str]] = None,
    custom_plan_params: Optional[Dict[str, List[Any]]] = None,
    psql_query_func: Optional[Callable[[str], str]] = None,
    params: Optional[Dict[str, float]] = None,
    workload_context: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]], Dict[str, Any]]:
    estimator = PlanResourceEstimator(
        pg_host=pg_host,
        pg_port=pg_port,
        pg_user=pg_user,
        pg_dbname=pg_dbname,
        plan_capture_modes=plan_capture_modes,
        custom_plan_params=custom_plan_params,
        estimator_cfg=estimator_cfg,
        psql_query_func=psql_query_func,
    )
    return estimator.estimate_resources_for_queries(
        queries,
        params=params,
        workload_context=workload_context,
    )
