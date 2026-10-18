"""MySQL EXPLAIN feature extraction and trained CPU/memory/IO prediction.

The estimator applies one candidate cost vector, captures fresh MySQL JSON
plans, builds the same feature families used during model training, and routes
the resulting candidate through all three persisted model artifacts.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mysql_cost_adapter import MySQLCostAdapter, SQLClient
from mysql_plan_clustering import build_canonical_plan_bundle


CPU_PLAN_FEATURES = (
    "F_scan_tuple", "F_nl_tuple", "F_join_tuple", "F_agg_tuple",
    "F_sort_tuple", "F_materialize_tuple", "F_proj_tuple", "F_index_tuple",
    "F_hash_build", "F_hash_probe", "F_scan_filter", "F_index_cond",
    "F_join_filter", "F_hash_cond", "F_merge_cond", "F_agg_trans",
    "F_agg_final", "F_sort_cmp", "F_materialize_oper",
)
DEFAULT_MODEL_ROOT = Path("/home/anonymous/program/resource_adaptive/mysql_plan_models")
DEFAULT_ARTIFACTS = {
    "cpu": DEFAULT_MODEL_ROOT / "cpu/artifacts/mysql_cpu_plan_model.joblib",
    "memory": DEFAULT_MODEL_ROOT / "memory/artifacts/global_memory_model.joblib",
    "io": DEFAULT_MODEL_ROOT / "io/output/mysql_total_io_plan_model.joblib",
}
DEFAULT_HIT_RATE = 0.95
INNODB_PAGE_SIZE = 16384.0


def build_mysql_optimistic_pruning_input(
    *,
    predicted_resources: Mapping[str, Any],
    normalization: Mapping[str, Any],
    error_bound_artifact_path: Any,
) -> Optional[Dict[str, Any]]:
    """Build the formal pruning payload from a pinned MySQL error artifact.

    Model errors are stored in each model's native output unit and converted by
    the same divisors used for the online prediction.  Any missing field,
    mismatched unit, bad digest, or absent simultaneous-coverage statement
    returns ``None`` so the outer loop keeps the candidate for real replay.
    QPS is deliberately not accepted or emitted here.
    """

    try:
        path = Path(str(error_bound_artifact_path))
        raw = path.read_bytes()
        artifact = json.loads(raw.decode("utf-8"))
        if not isinstance(artifact, Mapping):
            return None
        artifact_sha256 = hashlib.sha256(raw).hexdigest()
        if (
            artifact.get("schema_version") != "mysql_resource_error_bounds_v1"
            or artifact.get("immutable") is not True
            or artifact.get("simultaneous_coverage_guaranteed") is not True
            or "qps" in json.dumps(artifact, sort_keys=True).lower()
        ):
            return None
        resources = artifact.get("resources")
        bindings = artifact.get("bindings")
        if not isinstance(resources, Mapping) or set(resources) != {"cpu", "memory", "io"}:
            return None
        if not isinstance(bindings, Mapping):
            return None
        expected_units = {
            "cpu": ("cpu_seconds_per_window", "cpu_divisor"),
            "memory": ("memory_mb", "memory_mb_per_unit"),
            "io": ("io_mb_per_window", "io_divisor"),
        }
        errors: Dict[str, Dict[str, Any]] = {}
        predictions: Dict[str, float] = {}
        for name, (expected_unit, divisor_name) in expected_units.items():
            row = resources[name]
            if not isinstance(row, Mapping) or row.get("unit") != expected_unit:
                return None
            predicted = float(predicted_resources[name])
            divisor = float(normalization[divisor_name])
            heldout = float(row["heldout_abs_error_max"])
            applied = row["baseline_applied_error_bound"]
            if isinstance(applied, Mapping):
                applied_lower = float(applied["lower"])
                applied_upper = float(applied["upper"])
            else:
                applied_lower = applied_upper = float(applied)
            values = (predicted, divisor, heldout, applied_lower, applied_upper)
            if (
                not all(math.isfinite(value) for value in values)
                or predicted < 0.0
                or divisor <= 0.0
                or min(heldout, applied_lower, applied_upper) < 0.0
            ):
                return None
            predictions[name] = predicted
            errors[name] = {
                "lower": max(heldout, applied_lower) / divisor,
                "upper": max(heldout, applied_upper) / divisor,
                "valid": True,
                "scope_matches": True,
                "source_unit": expected_unit,
                "conversion_divisor": divisor,
                "envelope": "max(heldout_abs_error_max,baseline_applied_error_bound)",
            }
        required_binding_keys = {
            "freeze_id", "benchmark_id", "scenario_id",
            "resource_envelope_sha256", "domain_id",
        }
        if set(bindings) != required_binding_keys:
            return None
        return {
            "predicted_resources": predictions,
            "asymmetric_resource_errors": errors,
            "calibration_ready": True,
            "calibration_scope_matches": True,
            "simultaneous_coverage_guaranteed": True,
            "error_bound_provenance": {
                "schema_version": "resource_error_bound_provenance_v1",
                "immutable": True,
                "simultaneous_coverage_guaranteed": True,
                "resources": ["cpu", "memory", "io"],
                "artifact_sha256": artifact_sha256,
                "bindings": copy.deepcopy(dict(bindings)),
            },
        }
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _data_bytes(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?)B?", text)
    if match is None:
        return 0.0
    scales = {"": 1.0, "K": 1024.0, "M": 1024.0 ** 2, "G": 1024.0 ** 3, "T": 1024.0 ** 4}
    return float(match.group(1)) * scales[match.group(2)]


def _count_ops(value: Any) -> int:
    if not value:
        return 0
    return len(re.findall(r"=|>=|<=|<>|!=|>|<|\bAND\b|\bOR\b|\bLIKE\b|\bIN\b", str(value), re.I))


def _decode_plan(raw: str) -> Mapping[str, Any]:
    value: Any = json.loads(raw.strip())
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("EXPLAIN FORMAT=JSON did not return a JSON object")
    return value


def _canonical_json_value(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def is_read_only_model_query(sql: str) -> bool:
    """Recognize frozen read-only feature SQL, including parenthesized UNIONs.

    MySQL permits a query expression such as ``(SELECT ...) UNION (SELECT ...)``.
    The workload authority contains that exact form, so requiring SELECT to be
    the first non-whitespace byte incorrectly rejects a valid read-only query.
    Only leading parentheses are skipped; a write verb remains rejected.
    """

    candidate = str(sql).lstrip()
    while candidate.startswith("("):
        candidate = candidate[1:].lstrip()
    return re.match(r"(?is)^(?:SELECT|WITH)\b", candidate) is not None


def _mysql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if type(value) is bool:
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("dynamic binding float must be finite")
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise ValueError(f"unsupported dynamic binding type: {type(value).__name__}")


def _bind_positional_sql(sql: str, values: Any) -> str:
    if not isinstance(values, list):
        raise ValueError("dynamic MySQL binding values must be a positional list")
    if sql.count("?") != len(values):
        raise ValueError("dynamic SQL placeholder/binding count mismatch")
    pieces = sql.split("?")
    rendered = pieces[0]
    for value, suffix in zip(values, pieces[1:]):
        rendered += _mysql_literal(value) + suffix
    return rendered


class MySQLPlanFeatureExtractor:
    """Extract online-safe CPU formula and MySQL IO formula features."""

    def __init__(self, *, buffer_hit_rate: float = DEFAULT_HIT_RATE) -> None:
        self.hit_rate = min(max(float(buffer_hit_rate), 0.0), 1.0)

    def extract(self, plan: Mapping[str, Any]) -> Tuple[Dict[str, float], Dict[str, float]]:
        cpu = {name: 0.0 for name in CPU_PLAN_FEATURES}
        io: Dict[str, float] = {}
        self._walk(plan, cpu, io, multiplier=1.0)
        self._derive_io(io)
        return cpu, io

    @staticmethod
    def _add(values: Dict[str, float], name: str, amount: float) -> None:
        values[name] = values.get(name, 0.0) + float(amount)

    def _walk(
        self,
        node: Any,
        cpu: Dict[str, float],
        io: Dict[str, float],
        *,
        multiplier: float,
    ) -> float:
        if isinstance(node, list):
            output = 0.0
            for child in node:
                output += self._walk(child, cpu, io, multiplier=multiplier)
            return output
        if not isinstance(node, Mapping):
            return 0.0

        if isinstance(node.get("table"), Mapping):
            return self._table(node["table"], cpu, io, multiplier=multiplier)

        nested = node.get("nested_loop")
        if isinstance(nested, list):
            self._add(io, "mysql_has_nested_loop", 1.0)
            self._add(io, "mysql_n_tables_in_plan", float(len(nested)))
            total_output = 0.0
            for index, child in enumerate(nested):
                # MySQL's rows_produced_per_join and *_per_join cost fields
                # already describe the cumulative join prefix.  Multiplying
                # every child by the previous child's cumulative output counts
                # the same outer rows repeatedly and grows exponentially for
                # long JOB plans.  Keep only a multiplier inherited from an
                # enclosing execution context.
                child_output = self._walk(child, cpu, io, multiplier=multiplier)
                if index:
                    self._add(cpu, "F_nl_tuple", child_output)
                    self._add(cpu, "F_join_filter", child_output * _count_ops(child))
                total_output = child_output
            return total_output

        ordering = node.get("ordering_operation")
        if isinstance(ordering, Mapping):
            self._add(io, "mysql_has_orderby", 1.0)
            if ordering.get("using_filesort"):
                self._add(io, "mysql_n_filesort", 1.0)
            rows = self._walk(ordering, cpu, io, multiplier=multiplier)
            self._add(cpu, "F_sort_tuple", rows)
            if rows > 1.0:
                self._add(cpu, "F_sort_cmp", rows * math.log2(rows))
            return rows

        grouping = node.get("grouping_operation")
        if isinstance(grouping, Mapping):
            self._add(io, "mysql_has_groupby", 1.0)
            rows = self._walk(grouping, cpu, io, multiplier=multiplier)
            self._add(cpu, "F_agg_tuple", rows)
            self._add(cpu, "F_agg_trans", rows)
            self._add(cpu, "F_agg_final", rows)
            return rows

        materialized = node.get("materialized_from_subquery")
        if isinstance(materialized, Mapping):
            rows = self._walk(materialized, cpu, io, multiplier=multiplier)
            self._add(cpu, "F_materialize_tuple", rows)
            self._add(cpu, "F_materialize_oper", 2.0 * rows)
            return rows

        output = 0.0
        for key, child in node.items():
            if key in {"cost_info", "used_columns", "attached_condition", "index_condition"}:
                continue
            if isinstance(child, (Mapping, list)):
                output += self._walk(child, cpu, io, multiplier=multiplier)
        return output

    def _table(
        self,
        table: Mapping[str, Any],
        cpu: Dict[str, float],
        io: Dict[str, float],
        *,
        multiplier: float,
    ) -> float:
        access = str(table.get("access_type") or "ALL")
        rows = max(0.0, _number(table.get("rows_examined_per_scan")))
        rows_out = max(0.0, _number(table.get("rows_produced_per_join"), rows))
        total_rows = rows * max(multiplier, 1.0)
        total_output = rows_out * max(multiplier, 1.0)
        cost = table.get("cost_info") if isinstance(table.get("cost_info"), Mapping) else {}
        read_cost = _number(cost.get("read_cost"))
        eval_cost = _number(cost.get("eval_cost"))
        prefix_cost = _number(cost.get("prefix_cost"))
        data_bytes = _data_bytes(cost.get("data_read_per_join"))
        pages = math.ceil(data_bytes / INNODB_PAGE_SIZE) if data_bytes > 0.0 else 0.0
        weighted_pages = pages * max(multiplier, 1.0)
        disk_pages = weighted_pages * (1.0 - self.hit_rate)
        memory_pages = weighted_pages * self.hit_rate

        for name, value in (
            ("mysql_N_rows_examined", total_rows), ("mysql_N_rows_output", total_output),
            ("mysql_N_data_pages", weighted_pages), ("mysql_N_data_bytes", data_bytes * max(multiplier, 1.0)),
            ("mysql_read_cost", read_cost * max(multiplier, 1.0)),
            ("mysql_eval_cost", eval_cost * max(multiplier, 1.0)),
            ("mysql_theta_io_table_scan", disk_pages),
            ("mysql_theta_mem_table_scan", memory_pages),
        ):
            self._add(io, name, value)
        io["mysql_prefix_cost"] = max(io.get("mysql_prefix_cost", 0.0), prefix_cost)
        io["mysql_max_data_bytes"] = max(io.get("mysql_max_data_bytes", 0.0), data_bytes)
        io["mysql_max_rows_per_scan"] = max(io.get("mysql_max_rows_per_scan", 0.0), rows)
        if read_cost > 0.0:
            self._add(io, "mysql_cost_ratio_io_vs_eval", read_cost / max(eval_cost, 0.001))

        covering = bool(table.get("using_index")) and bool(table.get("key"))
        heavy = access in {"ALL", "range", "index"}
        light = access in {"const", "system", "ref", "eq_ref"}
        prefix = "mysql_heavy" if heavy else "mysql_light" if light else ""
        if prefix:
            self._add(io, f"{prefix}_theta_io", disk_pages)
            self._add(io, f"{prefix}_theta_mem", memory_pages)
            self._add(io, f"{prefix}_rows", total_rows)
            self._add(io, f"{prefix}_pages", weighted_pages)
            self._add(io, f"{prefix}_count", 1.0)

        access_key = {
            "ALL": "all", "index": "index", "range": "range", "ref": "ref",
            "eq_ref": "eq_ref", "const": "const", "system": "const",
        }.get(access)
        if access_key:
            self._add(io, f"mysql_n_{access_key}_scans", 1.0)
        page_key = {"ALL": "all", "index": "index", "range": "range", "ref": "ref", "eq_ref": "ref"}.get(access)
        if page_key:
            self._add(io, f"mysql_N_{page_key}_pages", weighted_pages)
        if covering:
            self._add(io, "mysql_n_covering_index", 1.0)

        if access == "ALL":
            self._add(cpu, "F_scan_tuple", total_rows)
        else:
            self._add(cpu, "F_index_tuple", total_rows)
            self._add(cpu, "F_index_cond", total_rows * _count_ops(table.get("index_condition")))
            if not covering:
                fetch_pages = rows * 0.5 if access in {"index", "range"} else 1.0
                self._add(io, "mysql_theta_io_row_fetch", fetch_pages * (1.0 - self.hit_rate) * max(multiplier, 1.0))
                self._add(io, "mysql_theta_mem_row_fetch", fetch_pages * self.hit_rate * max(multiplier, 1.0))
        self._add(cpu, "F_scan_filter", total_rows * _count_ops(table.get("attached_condition")))

        if table.get("using_temporary_table"):
            self._add(cpu, "F_materialize_tuple", total_output)
            self._add(cpu, "F_materialize_oper", 2.0 * total_output)
        if table.get("using_filesort"):
            self._add(io, "mysql_n_filesort", 1.0)
        for key, child in table.items():
            if key in {"cost_info", "used_columns", "attached_condition", "index_condition", "materialized_from_subquery"}:
                continue
            if isinstance(child, (Mapping, list)):
                self._walk(child, cpu, io, multiplier=multiplier)
        if isinstance(table.get("materialized_from_subquery"), Mapping):
            self._walk(table["materialized_from_subquery"], cpu, io, multiplier=multiplier)
        return total_output

    def _derive_io(self, values: Dict[str, float]) -> None:
        rows = values.get("mysql_N_rows_examined", 0.0)
        pages = values.get("mysql_N_data_pages", 0.0)
        data_bytes = values.get("mysql_N_data_bytes", 0.0)
        read = values.get("mysql_read_cost", 0.0)
        evaluate = values.get("mysql_eval_cost", 0.0)
        total = read + evaluate
        scans = sum(values.get(f"mysql_n_{name}_scans", 0.0) for name in ("all", "index", "range", "ref", "eq_ref", "const"))
        tables = max(values.get("mysql_n_tables_in_plan", 0.0), scans, 1.0)
        values.setdefault("mysql_n_tables_in_plan", tables)
        values["mysql_data_pages_estimated"] = pages
        values["mysql_rows_per_page"] = rows / max(pages, 1.0)
        values["mysql_read_cost_per_row"] = read / max(rows, 1.0)
        values["mysql_eval_cost_per_row"] = evaluate / max(rows, 1.0)
        values["mysql_tables_log"] = math.log1p(tables)
        values["mysql_covering_index_ratio"] = values.get("mysql_n_covering_index", 0.0) / max(scans, 1.0)
        theta_io = values.get("mysql_theta_io_table_scan", 0.0)
        theta_mem = values.get("mysql_theta_mem_table_scan", 0.0)
        values["mysql_io_page_ratio_disk"] = theta_io / max(theta_io + theta_mem, 1.0)
        values["mysql_pages_per_cost"] = pages / max(total, 0.001)
        values["mysql_pages_per_read_cost"] = pages / max(read, 0.001)
        values["mysql_pages_per_row"] = pages / max(rows, 1.0)
        values["mysql_bytes_per_row"] = data_bytes / max(rows, 1.0)
        values["mysql_cost_per_page"] = total / max(pages, 1.0)
        values["mysql_cost_ratio_io_vs_total"] = read / max(total, 0.001)
        heavy_count = values.get("mysql_heavy_count", 0.0)
        light_count = values.get("mysql_light_count", 0.0)
        values["mysql_heavy_scan_ratio"] = heavy_count / max(heavy_count + light_count, 1.0)
        heavy_pages = values.get("mysql_heavy_theta_io", 0.0) + values.get("mysql_heavy_theta_mem", 0.0)
        light_pages = values.get("mysql_light_theta_io", 0.0) + values.get("mysql_light_theta_mem", 0.0)
        values["mysql_heavy_io_ratio"] = heavy_pages / max(heavy_pages + light_pages, 1.0)
        for short in ("all", "ref", "range", "const"):
            values[f"mysql_{short}_scan_ratio"] = values.get(f"mysql_n_{short}_scans", 0.0) / max(scans, 1.0)
        values["mysql_pages_per_table"] = pages / tables
        values["mysql_theta_io"] = theta_io + values.get("mysql_theta_io_row_fetch", 0.0)
        values["mysql_theta_mem"] = theta_mem + values.get("mysql_theta_mem_row_fetch", 0.0)
        rows_out = values.get("mysql_N_rows_output", 0.0)
        temporary = values.get("mysql_n_filesort", 0.0) > 0.0 or values.get("mysql_has_groupby", 0.0) > 0.0
        disk_temp = temporary and rows_out > 10000.0
        values["mysql_theta_dc"] = float(disk_temp)
        values["mysql_theta_dr"] = rows_out if disk_temp else 0.0
        values["mysql_theta_mc"] = float(temporary and not disk_temp)
        values["mysql_theta_mr"] = rows_out if temporary and not disk_temp else 0.0
        values["mysql_optimizer_io_cost"] = read
        values["mysql_optimizer_total_cost"] = total
        values.setdefault("mysql_selectivity", min(1.0, values.get("mysql_N_rows_output", 0.0) / max(rows, 1.0)))
        values.setdefault("mysql_table_total_pages", pages)


@dataclass(frozen=True)
class WorkloadQuery:
    sql: str
    weight: float = 1.0
    query_id: str = ""
    dynamic: bool = False
    representative_bindings: tuple[Mapping[str, Any], ...] = ()


class MySQLExplainFeatureProvider:
    """Capture fresh plans from a configured, read-only workload sample."""

    def __init__(self, client: SQLClient, config: Mapping[str, Any]) -> None:
        self.client = client
        self.config = dict(config)
        self.extractor = MySQLPlanFeatureExtractor(
            buffer_hit_rate=float(config.get("buffer_hit_rate", DEFAULT_HIT_RATE)),
        )
        self.queries = self._load_queries(config)
        if not self.queries:
            raise ValueError("trained_models.feature_source requires queries or query_files")
        configured_registry = [str(value) for value in config.get("registry_query_ids") or []]
        loaded_registry = [query.query_id for query in self.queries]
        if configured_registry and configured_registry != loaded_registry:
            raise ValueError(
                "feature_source queries must exactly cover registry_query_ids in frozen order"
            )
        self.registry_query_ids = configured_registry or loaded_registry
        self.registry_sha256 = str(config.get("workload_registry_sha256") or "").lower()
        self.registry_authority_verified = False
        authority_path = config.get("workload_registry_path")
        if authority_path:
            self._verify_authoritative_registry(Path(str(authority_path)))
        elif config.get("require_strict_plan_registry") is True:
            raise ValueError(
                "strict plan registry requires workload_registry_path and verified file SHA"
            )

    def _verify_authoritative_registry(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("feature_source.workload_registry_path must be absolute")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"authoritative workload registry is unreadable: {path}") from exc
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != self.registry_sha256:
            raise ValueError(
                "authoritative workload registry SHA mismatch: "
                f"expected={self.registry_sha256} actual={actual_sha}"
            )
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("authoritative workload registry is not JSON") from exc
        if not isinstance(document, Mapping) or document.get("schema_version") != "mysql_plan_workload_registry_v1":
            raise ValueError("authoritative workload registry schema mismatch")
        expected_freeze = self.config.get("workload_freeze_id")
        expected_benchmark = self.config.get("benchmark_id")
        if (
            document.get("immutable") is not True
            or not isinstance(expected_freeze, str)
            or not expected_freeze
            or document.get("freeze_id") != expected_freeze
            or not isinstance(expected_benchmark, str)
            or not expected_benchmark
            or document.get("benchmark") != expected_benchmark
        ):
            raise ValueError(
                "authoritative workload registry freeze/benchmark binding mismatch"
            )
        rows = document.get("queries")
        if not isinstance(rows, list) or not rows:
            raise ValueError("authoritative workload registry queries are absent")
        if len(rows) != len(self.queries):
            raise ValueError("feature queries do not cover the authoritative registry")
        for order, (raw, query) in enumerate(zip(rows, self.queries), 1):
            if not isinstance(raw, Mapping):
                raise ValueError(f"registry query {order} is not an object")
            expected_sha = hashlib.sha256(query.sql.encode("utf-8")).hexdigest()
            if (
                raw.get("order") != order
                or raw.get("query_id") != query.query_id
                or str(raw.get("sql_sha256") or "").lower() != expected_sha
                or not isinstance(raw.get("weight"), (int, float))
                or isinstance(raw.get("weight"), bool)
                or float(raw["weight"]).hex() != float(query.weight).hex()
                or raw.get("dynamic") is not query.dynamic
            ):
                raise ValueError(
                    f"feature query does not match authoritative registry at order {order}"
                )
            authority_bindings = raw.get("representative_bindings") or []
            if _canonical_json_value(authority_bindings) != _canonical_json_value(
                list(query.representative_bindings)
            ):
                raise ValueError(
                    f"representative bindings differ from authoritative registry: {query.query_id}"
                )
            required_modes = raw.get("required_plan_modes") or []
            if query.dynamic and required_modes != ["generic", "custom"]:
                raise ValueError(
                    f"dynamic registry query must require generic/custom: {query.query_id}"
                )
            if not query.dynamic and required_modes not in ([], ["custom"]):
                raise ValueError(
                    f"static registry query has invalid plan modes: {query.query_id}"
                )
        self.registry_query_ids = [query.query_id for query in self.queries]
        self.registry_authority_verified = True

    @staticmethod
    def _load_queries(config: Mapping[str, Any]) -> List[WorkloadQuery]:
        rows: List[WorkloadQuery] = []
        for ordinal, item in enumerate(config.get("queries") or [], 1):
            if isinstance(item, Mapping):
                sql = str(item.get("sql") or "")
                weight = float(item.get("weight", 1.0))
                query_id = str(item.get("query_id") or f"query_{ordinal:04d}")
                dynamic = item.get("dynamic") is True
                bindings_raw = item.get("representative_bindings") or []
                if not isinstance(bindings_raw, list) or any(not isinstance(value, Mapping) for value in bindings_raw):
                    raise ValueError(f"representative_bindings must be object list: {query_id}")
                representative_bindings = tuple(copy.deepcopy(dict(value)) for value in bindings_raw)
            else:
                sql, weight = str(item), 1.0
                query_id = f"query_{ordinal:04d}"
                dynamic = False
                representative_bindings = ()
            sql = sql.strip().rstrip(";")
            if sql:
                rows.append(WorkloadQuery(sql, weight, query_id, dynamic, representative_bindings))
        paths: List[Path] = [Path(str(value)) for value in config.get("query_files") or []]
        directory = config.get("query_directory")
        if directory:
            paths.extend(sorted(Path(str(directory)).glob(str(config.get("query_glob", "*.sql")))))
        default_weight = float(config.get("query_file_weight", 1.0))
        for path_ordinal, path in enumerate(paths, len(rows) + 1):
            if not path.is_file():
                raise FileNotFoundError(f"model workload query not found: {path}")
            sql = path.read_text(encoding="utf-8").strip().rstrip(";")
            if sql:
                rows.append(WorkloadQuery(sql, default_weight, f"query_{path_ordinal:04d}__{path.stem}"))
        query_ids = [row.query_id for row in rows]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("model feature query_id values must be unique")
        for row in rows:
            if not math.isfinite(row.weight) or row.weight <= 0.0:
                raise ValueError("model workload query weights must be finite and positive")
            if not is_read_only_model_query(row.sql):
                raise ValueError("model feature queries must be read-only SELECT/WITH statements")
            binding_ids = [str(value.get("binding_id") or "") for value in row.representative_bindings]
            if row.dynamic:
                if (
                    not binding_ids
                    or any(not value for value in binding_ids)
                    or len(binding_ids) != len(set(binding_ids))
                    or any(value.get("values") is None for value in row.representative_bindings)
                ):
                    raise ValueError(
                        f"dynamic query requires unique representative binding_id/values: {row.query_id}"
                    )
            elif row.representative_bindings:
                raise ValueError(f"static query cannot declare representative bindings: {row.query_id}")
        return rows

    def collect(self) -> Dict[str, Any]:
        cpu = {name: 0.0 for name in CPU_PLAN_FEATURES}
        io: Dict[str, float] = {}
        plan_rows: List[Dict[str, float]] = []
        plan_captures: List[Dict[str, Any]] = []
        for query_order, query in enumerate(self.queries, 1):
            if query.dynamic:
                if not query.representative_bindings:
                    raise ValueError(
                        f"dynamic query lacks frozen representative bindings: {query.query_id}"
                    )
                binding_weights = [float(binding.get("weight", 1.0)) for binding in query.representative_bindings]
                if any(not math.isfinite(value) or value <= 0.0 for value in binding_weights):
                    raise ValueError(f"invalid representative binding weight: {query.query_id}")
                weight_sum = sum(binding_weights)
                required_ids = [str(binding["binding_id"]) for binding in query.representative_bindings]
                captures = [
                    (
                        binding,
                        _bind_positional_sql(query.sql, binding.get("values")),
                        query.weight * binding_weight / weight_sum,
                    )
                    for binding, binding_weight in zip(query.representative_bindings, binding_weights)
                ]
            else:
                required_ids = []
                captures = [({"binding_id": "static", "values": None}, query.sql, query.weight)]
            for binding, executable_sql, feature_weight in captures:
                plan = _decode_plan(self.client.execute("EXPLAIN FORMAT=JSON " + executable_sql))
                current_cpu, current_io = self.extractor.extract(plan)
                plan_rows.append(current_io)
                for name, value in current_cpu.items():
                    cpu[name] += value * feature_weight
                for name, value in current_io.items():
                    io[name] = io.get(name, 0.0) + value * feature_weight
                plan_captures.append(
                    {
                        "query_id": query.query_id,
                        "query_sql_sha256": hashlib.sha256(query.sql.encode("utf-8")).hexdigest(),
                        "query_order": query_order,
                        "query_weight": query.weight,
                        "effective_weight": feature_weight,
                        "dynamic": query.dynamic,
                        "binding_id": str(binding["binding_id"]),
                        "binding_values": copy.deepcopy(binding.get("values")),
                        "plan_mode": "custom",
                        "generic_plan_semantics_available": False if query.dynamic else None,
                        "required_binding_ids": required_ids,
                        "required_plan_modes": ["generic", "custom"] if query.dynamic else [],
                        "raw_explain": copy.deepcopy(dict(plan)),
                    }
                )
        active = float(self.config.get("active_sql_count", sum(item.weight for item in self.queries)))
        io["active_sql_count"] = active
        bundle = build_canonical_plan_bundle(
            plan_captures,
            registry_query_ids=self.registry_query_ids,
            registry_sha256=self.registry_sha256,
            registry_authority_verified=self.registry_authority_verified,
        )
        return {
            "cpu": cpu,
            "io": io,
            "plan_rows": plan_rows,
            "active_sql_count": active,
            "canonical_plan_bundle": bundle,
            "plan_captures": plan_captures,
        }


class TrainedModelResourceEstimator:
    """Estimator callable compatible with all existing MySQL search algorithms."""

    def __init__(self, adapter: MySQLCostAdapter, config: Mapping[str, Any]) -> None:
        try:
            import joblib  # type: ignore
            import numpy as np  # type: ignore
            import pandas as pd  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "trained_models requires joblib, numpy, pandas and scikit-learn; "
                "install requirements.txt in the model runtime"
            ) from exc
        self.np = np
        self.pd = pd
        self.adapter = adapter
        self.config = dict(config)
        paths = dict(DEFAULT_ARTIFACTS)
        paths.update({name: Path(str(value)) for name, value in (config.get("artifacts") or {}).items()})
        missing = [str(path) for path in paths.values() if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError(f"trained model artifacts are missing: {missing}")
        self.artifact_paths = {name: str(Path(path).resolve()) for name, path in paths.items()}
        self.cpu_artifact = joblib.load(paths["cpu"])
        self.memory_artifact = joblib.load(paths["memory"])
        self.io_artifact = joblib.load(paths["io"])
        source_cfg = config.get("feature_source")
        if not isinstance(source_cfg, Mapping):
            raise ValueError("trained_models.feature_source must be an object")
        self.provider = MySQLExplainFeatureProvider(adapter.client, source_cfg)
        self.measurements: List[Dict[str, Any]] = []

    def _benchmark(self) -> Tuple[str, str]:
        raw = str(self.config.get("benchmark", "CH")).upper()
        aliases = {
            "H": ("H", "H_TPCH"), "H_TPCH": ("H", "H_TPCH"), "TPCH": ("H", "H_TPCH"),
            "C": ("C", "C_TPCC"), "C_TPCC": ("C", "C_TPCC"), "TPCC": ("C", "C_TPCC"),
            "CH": ("CH", "CH_HYBRID"), "CH_HYBRID": ("CH", "CH_HYBRID"), "HYBRID": ("CH", "CH_HYBRID"),
        }
        if raw not in aliases:
            raise ValueError(f"unsupported trained model benchmark: {raw}")
        return aliases[raw]

    def _predict_cpu(self, features: Mapping[str, Any], family: str) -> float:
        active = float(features["active_sql_count"])
        row = {name: math.log1p(max(0.0, float(features["cpu"].get(name, 0.0)))) for name in CPU_PLAN_FEATURES}
        row["active_sql_count"] = math.log1p(max(0.0, active))
        for name in ("H_TPCH", "C_TPCC", "CH_HYBRID"):
            row[f"benchmark_{name}"] = float(family == name)
        case_number = float(self.config.get("case_number", 0.0))
        row["case_number"] = case_number
        row["tpch_scale_index"] = case_number * float(family == "H_TPCH")
        columns = list(self.cpu_artifact["features"])
        matrix = self.pd.DataFrame([[row.get(name, 0.0) for name in columns]], columns=columns)
        return max(0.0, float(self.cpu_artifact["model"].predict(matrix)[0]))

    def _memory_base(self) -> Tuple[float, List[float]]:
        context = self.config.get("memory_context") or {}
        recent = [float(value) for value in context.get("recent_memory_mb", [])]
        if recent:
            return recent[-1], recent
        if context.get("observed_base_memory_mb") is not None:
            base = float(context["observed_base_memory_mb"])
            return base, [base]
        if str(context.get("source", "performance_schema")) == "performance_schema":
            raw = self.adapter.client.execute(
                "SELECT COALESCE(SUM(CURRENT_NUMBER_OF_BYTES_USED),0)/1048576 "
                "FROM performance_schema.memory_summary_global_by_event_name"
            ).strip().splitlines()
            base = float(raw[-1].split("\t")[0]) if raw else 0.0
            if not math.isfinite(base) or base <= 0.0:
                raise RuntimeError(
                    "performance_schema returned no usable MySQL memory baseline; "
                    "enable memory instrumentation or set memory_context.observed_base_memory_mb"
                )
            return base, [base]
        raise ValueError("trained_models.memory_context needs recent/base memory or performance_schema source")

    def _predict_memory(self, features: Mapping[str, Any], benchmark: str) -> float:
        base, recent = self._memory_base()
        differences = self.np.diff(self.np.asarray(recent[-7:], dtype=float))
        drift = max(float(self.np.median(differences)), 0.0) if len(differences) else 0.0
        observed_base = base + 4.0 * drift
        row: Dict[str, float] = {}
        # One online search candidate is one aggregate workload window.  The
        # training candidate then took mean/max across windows, so for a single
        # online window both statistics equal its aggregate feature values.
        plan_rows = [features["io"]]
        source_names = {name.rsplit("__", 1)[0] for name in self.memory_artifact["feature_columns"] if "__" in name}
        for name in source_names:
            values = [float(item.get(name, 0.0)) for item in plan_rows]
            row[f"{name}__mean"] = float(sum(values) / len(values)) if values else 0.0
            row[f"{name}__max"] = max(values) if values else 0.0
        row.update({
            "active_sql_count__mean": float(features["active_sql_count"]),
            "active_sql_count__max": float(features["active_sql_count"]),
            "context_last_memory_mb": base,
            "context_positive_drift_mb_per_window": drift,
            "context_block_index": float(self.config.get("block_index", 0.0)),
            "context_candidate_progress": float(self.config.get("candidate_progress", 0.0)),
        })
        for name in ("H", "C", "CH"):
            row[f"benchmark__{name}"] = float(benchmark == name)
        columns = list(self.memory_artifact["feature_columns"])
        matrix = self.np.asarray([[row.get(name, 0.0) for name in columns]], dtype=float)
        dynamic = max(0.0, float(self.memory_artifact["model"].predict(matrix)[0]))
        shrinkage = float((self.memory_artifact.get("dynamic_shrinkage_by_benchmark") or {}).get(benchmark, 1.0))
        return observed_base + shrinkage * dynamic

    def _predict_io(self, features: Mapping[str, Any], benchmark: str, params: Mapping[str, float]) -> float:
        row = dict(features["io"])
        row.update({
            "context_case_number": float(self.config.get("case_number", 0.0)),
            "context_is_repeat": float(str(self.config.get("run_label", "primary")) != "primary"),
            "context_tpch_terminals": float(self.config.get("tpch_terminals", 1 if benchmark == "H" else 0)),
        })
        row["mysql_formula_io_cost"] = self._candidate_formula(row, params)
        columns = list(self.io_artifact["features"])
        matrix = self.pd.DataFrame([[row.get(name, 0.0) for name in columns]], columns=columns)
        run_label = str(self.config.get("run_label", "primary"))
        route = "H_SF23" if benchmark == "H" and run_label.startswith("sf") else "H_FIXED" if benchmark == "H" else benchmark
        bundle = self.io_artifact["models"][route]
        prediction = 0.0
        for model in bundle["models"].values():
            current = float(model.predict(matrix)[0])
            if bundle["properties"]["log_target"]:
                current = float(self.np.expm1(current))
            prediction += max(0.0, current)
        return prediction

    @staticmethod
    def _candidate_formula(row: Mapping[str, Any], params: Mapping[str, float]) -> float:
        return (
            row.get("mysql_theta_io", 0.0) * float(params.get("io_block_read_cost", 1.0))
            + row.get("mysql_theta_mem", 0.0) * float(params.get("memory_block_read_cost", 0.5))
            + row.get("mysql_theta_dc", 0.0) * float(params.get("disk_temptable_create_cost", 20.0))
            + row.get("mysql_theta_dr", 0.0) * float(params.get("disk_temptable_row_cost", 0.5))
            + row.get("mysql_theta_mc", 0.0) * float(params.get("memory_temptable_create_cost", 1.0))
            + row.get("mysql_theta_mr", 0.0) * float(params.get("memory_temptable_row_cost", 0.1))
        )

    def __call__(self, sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        _ = sql_set
        self.adapter.apply(params)
        features = self.provider.collect()
        formula = self._candidate_formula(features["io"], params)
        features["io"]["mysql_formula_io_cost"] = formula
        features["io"]["mysql_formula_x_1mcache"] = formula * (
            1.0 - self.provider.extractor.hit_rate
        )
        benchmark, family = self._benchmark()
        cpu_raw = self._predict_cpu(features, family)
        memory_raw_mb = self._predict_memory(features, benchmark)
        io_raw_mb = self._predict_io(features, benchmark, params)
        normal = self.config.get("normalization") or {}
        window = float(normal.get("window_seconds", self.config.get("window_seconds", 5.0)))
        if window <= 0.0:
            raise ValueError("trained_models normalization.window_seconds must be positive")
        resource = {
            "cpu": cpu_raw / float(normal.get("cpu_divisor", window)),
            "memory": memory_raw_mb / float(normal.get("memory_mb_per_unit", 1024.0)),
            "io": io_raw_mb / float(normal.get("io_divisor", window)),
        }
        pruning_input = build_mysql_optimistic_pruning_input(
            predicted_resources=resource,
            normalization={
                "cpu_divisor": float(normal.get("cpu_divisor", window)),
                "memory_mb_per_unit": float(normal.get("memory_mb_per_unit", 1024.0)),
                "io_divisor": float(normal.get("io_divisor", window)),
            },
            error_bound_artifact_path=(
                (self.config.get("optimistic_pruning") or {}).get(
                    "error_bound_artifact_path"
                )
                if isinstance(self.config.get("optimistic_pruning"), Mapping)
                else None
            ),
        )
        measurement = {
            "resource": resource,
            "raw_model_prediction": {"cpu_seconds": cpu_raw, "memory_mb": memory_raw_mb, "io_mb": io_raw_mb},
            "benchmark": benchmark,
            "feature_query_count": len(self.provider.queries),
            "active_sql_count": features["active_sql_count"],
            "artifacts": self.artifact_paths,
            "params": dict(params),
            "canonical_plan_bundle": copy.deepcopy(features["canonical_plan_bundle"]),
            "optimistic_pruning_error_status": (
                "valid_mysql_native_unit_envelope" if pruning_input is not None
                else "unavailable_or_invalid_fail_open"
            ),
        }
        if pruning_input is not None:
            measurement["optimistic_replay_pruning_input"] = pruning_input
        self.measurements.append(measurement)
        return {**resource, "_metadata": measurement}


def build_trained_model_estimator(
    adapter: MySQLCostAdapter,
    config: Mapping[str, Any],
) -> TrainedModelResourceEstimator:
    return TrainedModelResourceEstimator(adapter, config)
