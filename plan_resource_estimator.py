"""MySQL executor-physics resource estimator.

This module deliberately does not use MySQL's ``read_cost``, ``eval_cost`` or
``prefix_cost`` as physical work.  Those values are planner preferences and
change when the searched cost constants change.  Physical work is derived from
the selected access path, estimated rows, table/index sizes and executor buffer
limits, then anchored to one measured baseline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import math
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from mysql_cost_adapter import MySQLCostAdapter, SQLClient


INNODB_PAGE_SIZE = 16 * 1024
DEFAULT_ROW_WIDTH = 100.0
DEFAULT_CACHE_PROFILE = (0.85, 0.30)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _positive(value: Any, default: float = 0.0) -> float:
    return max(0.0, _number(value, default))


def _data_bytes(value: Any) -> float:
    if isinstance(value, (int, float)):
        return _positive(value)
    text = str(value or "").strip().upper()
    if not text:
        return 0.0
    suffixes = {
        "B": 1.0,
        "KB": 1024.0,
        "MB": 1024.0**2,
        "GB": 1024.0**3,
        "TB": 1024.0**4,
        "K": 1024.0,
        "M": 1024.0**2,
        "G": 1024.0**3,
        "T": 1024.0**4,
    }
    for suffix in sorted(suffixes, key=len, reverse=True):
        if text.endswith(suffix):
            return _positive(text[: -len(suffix)].strip()) * suffixes[suffix]
    return _positive(text)


def _decode_plan(plan: Any) -> Mapping[str, Any]:
    value = plan
    if isinstance(value, str):
        value = json.loads(value.strip())
        if isinstance(value, str):
            value = json.loads(value)
    if not isinstance(value, Mapping):
        raise ValueError("MySQL plan must be an EXPLAIN FORMAT=JSON object")
    return value


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class CacheProfile:
    sequential_hit_rate: float = DEFAULT_CACHE_PROFILE[0]
    random_hit_rate: float = DEFAULT_CACHE_PROFILE[1]

    def __post_init__(self) -> None:
        values = (self.sequential_hit_rate, self.random_hit_rate)
        if not all(math.isfinite(value) and 0.0 <= value < 1.0 for value in values):
            raise ValueError("cache hit rates must be finite and in [0, 1)")
        if self.sequential_hit_rate <= self.random_hit_rate:
            raise ValueError("sequential_hit_rate must exceed random_hit_rate")


@dataclass(frozen=True)
class IOWork:
    sequential_pages: float = 0.0
    random_pages: float = 0.0

    def __add__(self, other: "IOWork") -> "IOWork":
        return IOWork(
            self.sequential_pages + other.sequential_pages,
            self.random_pages + other.random_pages,
        )

    def scaled(self, multiplier: float) -> "IOWork":
        return IOWork(
            self.sequential_pages * multiplier,
            self.random_pages * multiplier,
        )


@dataclass(frozen=True)
class QueryWork:
    cpu: float = 0.0
    memory_bytes: float = 0.0
    io: IOWork = IOWork()
    output_rows: float = 0.0
    memory_demand_bytes: float = 0.0


@dataclass(frozen=True)
class ResourceCalibration:
    cpu_scale: float
    io_scale: float
    cache_profile: CacheProfile
    baseline_cpu_work: float
    baseline_memory_work: float
    baseline_effective_io_bytes: float
    baseline_real: Mapping[str, float]
    memory_resident: float
    memory_operator_scale: float = 1.0
    memory_bytes_per_unit: float = 1.0


def _merge_parallel(children: Iterable[QueryWork], *, output_rows: float = 0.0) -> QueryWork:
    values = list(children)
    return QueryWork(
        cpu=sum(item.cpu for item in values),
        memory_bytes=max((item.memory_bytes for item in values), default=0.0),
        io=IOWork(
            sum(item.io.sequential_pages for item in values),
            sum(item.io.random_pages for item in values),
        ),
        output_rows=output_rows or sum(item.output_rows for item in values),
        memory_demand_bytes=max(
            (item.memory_demand_bytes for item in values), default=0.0
        ),
    )


class MySQLPlanPhysics:
    """Translate MySQL JSON plan structure into CPU, peak-memory and page work."""

    def __init__(
        self,
        relation_stats: Mapping[str, Mapping[str, Any]],
        *,
        executor_parameters: Optional[Mapping[str, Any]] = None,
        page_size: int = INNODB_PAGE_SIZE,
    ) -> None:
        self.relation_stats = relation_stats
        self.params = dict(executor_parameters or {})
        self.page_size = max(1024, int(page_size))

    def work(self, plan: Any) -> QueryWork:
        return self._walk(_decode_plan(plan), loop_multiplier=1.0)

    def _stats(self, table: str, schema: str = "", index: str = "") -> Mapping[str, Any]:
        candidates = []
        if schema and table and index:
            candidates.append(f"{schema}.{table}.{index}")
        if table and index:
            candidates.append(f"{table}.{index}")
        if schema and table:
            candidates.append(f"{schema}.{table}")
        if table:
            candidates.append(table)
        for key in candidates:
            value = self.relation_stats.get(key)
            if isinstance(value, Mapping):
                return value
        return {}

    def _row_width(self, table: Mapping[str, Any], stats: Mapping[str, Any]) -> float:
        rows = max(_positive(table.get("rows_examined_per_scan")), 1.0)
        cost_info = table.get("cost_info")
        data_bytes = (
            _data_bytes(cost_info.get("data_read_per_join"))
            if isinstance(cost_info, Mapping)
            else 0.0
        )
        # data_read_per_join is a cardinality/width estimate, not a cost weight.
        from_plan = data_bytes / rows if data_bytes > 0.0 else 0.0
        return max(
            1.0,
            _positive(stats.get("avg_row_length")),
            _positive(stats.get("row_width")),
            from_plan,
            DEFAULT_ROW_WIDTH if not stats and from_plan <= 0.0 else 1.0,
        )

    @staticmethod
    def _join_buffer_kind(value: Any) -> str:
        text = str(value or "").strip().lower()
        if "hash" in text:
            return "hash"
        if text:
            return "block_nested_loop"
        return "nested_loop"

    @staticmethod
    def _table_node(value: Any) -> Optional[Mapping[str, Any]]:
        if isinstance(value, Mapping) and isinstance(value.get("table"), Mapping):
            return value["table"]
        return value if isinstance(value, Mapping) and "table_name" in value else None

    def _walk(self, node: Any, *, loop_multiplier: float) -> QueryWork:
        if isinstance(node, list):
            return _merge_parallel(
                self._walk(child, loop_multiplier=loop_multiplier) for child in node
            )
        if not isinstance(node, Mapping):
            return QueryWork()

        table = self._table_node(node)
        if table is not None:
            return self._table_work(table, loop_multiplier=loop_multiplier)

        nested = node.get("nested_loop")
        if isinstance(nested, list):
            return self._nested_loop_work(nested, loop_multiplier=loop_multiplier)

        hash_join = node.get("hash_join")
        if isinstance(hash_join, (Mapping, list)):
            return self._explicit_join_work(
                hash_join, loop_multiplier=loop_multiplier, kind="hash"
            )
        merge_join = node.get("merge_join")
        if isinstance(merge_join, (Mapping, list)):
            return self._explicit_join_work(
                merge_join, loop_multiplier=loop_multiplier, kind="merge"
            )

        for wrapper, kind in (
            ("ordering_operation", "sort"),
            ("grouping_operation", "aggregate"),
            ("duplicates_removal", "deduplicate"),
            ("windowing", "window"),
            ("materialized_from_subquery", "materialize"),
            ("buffer_result", "materialize"),
        ):
            child = node.get(wrapper)
            if isinstance(child, (Mapping, list)):
                return self._unary_work(
                    child, loop_multiplier=loop_multiplier, kind=kind, wrapper=node
                )

        children = [
            value
            for key, value in node.items()
            if key not in {"cost_info", "used_columns", "attached_condition", "index_condition"}
            and isinstance(value, (Mapping, list))
        ]
        if not children:
            return QueryWork(output_rows=_positive(node.get("rows_produced_per_join")))
        return _merge_parallel(
            self._walk(child, loop_multiplier=loop_multiplier) for child in children
        )

    def _table_work(self, table: Mapping[str, Any], *, loop_multiplier: float) -> QueryWork:
        name = str(table.get("table_name") or "")
        schema = str(table.get("schema_name") or table.get("database") or "")
        index = str(table.get("key") or "")
        stats = self._stats(name, schema, index)
        table_stats = self._stats(name, schema)
        access = str(table.get("access_type") or "ALL").upper()
        estimated_scan_rows = _positive(table.get("rows_examined_per_scan"))
        table_rows = max(
            _positive(table_stats.get("table_rows")),
            _positive(table_stats.get("reltuples")),
            estimated_scan_rows,
            1.0,
        )
        width = self._row_width(table, table_stats)
        table_pages = max(
            _positive(table_stats.get("data_pages")),
            _positive(table_stats.get("relpages")),
            math.ceil(
                max(_positive(table_stats.get("data_length")), table_rows * width)
                / self.page_size
            ),
            1.0,
        )
        filtered = min(1.0, max(0.0, _number(table.get("filtered"), 100.0) / 100.0))
        rows_per_scan = table_rows if access == "ALL" else max(estimated_scan_rows, 1.0)
        input_rows = rows_per_scan * max(loop_multiplier, 1.0)
        explicit_output = _positive(table.get("rows_produced_per_join"))
        output_rows = explicit_output or input_rows * filtered

        covering = bool(table.get("using_index")) and bool(index)
        if access == "ALL":
            cpu = input_rows * 1.0
            io = IOWork(sequential_pages=table_pages * max(loop_multiplier, 1.0))
        elif access == "INDEX":
            cpu = input_rows * (1.25 if covering else 2.0)
            index_pages = self._index_pages(stats, table_stats)
            if covering:
                io = IOWork(sequential_pages=index_pages * max(loop_multiplier, 1.0))
            else:
                heap_pages = self._distinct_pages(input_rows, table_pages, table_rows)
                io = IOWork(
                    sequential_pages=index_pages * max(loop_multiplier, 1.0),
                    random_pages=heap_pages,
                )
        else:
            cpu_weight = 1.4 if covering else 2.0
            cpu = input_rows * cpu_weight
            index_pages = self._index_pages(stats, table_stats)
            depth = max(1.0, math.ceil(math.log(max(index_pages, 2.0), 128.0)))
            leaf_pages = min(index_pages, max(1.0, rows_per_scan * width / self.page_size))
            lookup_count = max(loop_multiplier, 1.0)
            index_random = min(index_pages, lookup_count * depth)
            index_sequential = leaf_pages * lookup_count if access == "RANGE" else 0.0
            heap_random = (
                0.0
                if covering
                else self._distinct_pages(input_rows, table_pages, table_rows)
            )
            io = IOWork(
                sequential_pages=index_sequential,
                random_pages=index_random + heap_random,
            )

        memory = 0.0
        memory_demand = 0.0
        if table.get("using_temporary_table"):
            materialized = output_rows * width
            limit = self._temp_table_limit()
            memory = min(materialized, limit)
            memory_demand += materialized
            if materialized > limit:
                io += IOWork(sequential_pages=2.0 * materialized / self.page_size)
        if table.get("using_filesort"):
            sort_bytes = output_rows * width
            sort_limit = self._buffer_limit("sort_buffer_size", 256 * 1024)
            memory += min(sort_bytes, sort_limit)
            memory_demand += sort_bytes
            if sort_bytes > sort_limit:
                io += IOWork(sequential_pages=2.0 * sort_bytes / self.page_size)

        materialized = table.get("materialized_from_subquery")
        if isinstance(materialized, Mapping):
            child = self._walk(materialized, loop_multiplier=loop_multiplier)
            memory += child.memory_bytes
            memory_demand += child.memory_demand_bytes
            cpu += child.cpu + child.output_rows
            io += child.io
        return QueryWork(
            cpu=cpu,
            memory_bytes=memory,
            io=io,
            output_rows=output_rows,
            memory_demand_bytes=memory_demand,
        )

    def _nested_loop_work(self, children: Sequence[Any], *, loop_multiplier: float) -> QueryWork:
        cpu = 0.0
        io = IOWork()
        memory_peak = 0.0
        memory_demand_peak = 0.0
        previous_rows = 0.0
        output_rows = 0.0
        for index, child_node in enumerate(children):
            table = self._table_node(child_node)
            join_kind = (
                self._join_buffer_kind(table.get("using_join_buffer"))
                if table is not None and index > 0
                else "nested_loop"
            )
            child_loops = loop_multiplier
            if index > 0 and join_kind == "nested_loop":
                child_loops *= max(previous_rows, 1.0)
            child = self._walk(child_node, loop_multiplier=child_loops)
            cpu += child.cpu
            io += child.io
            memory_peak = max(memory_peak, child.memory_bytes)
            memory_demand_peak = max(
                memory_demand_peak, child.memory_demand_bytes
            )
            if index > 0:
                if join_kind == "hash":
                    build_rows = max(previous_rows, 1.0)
                    probe_rows = max(
                        _positive(table.get("rows_examined_per_scan")) if table else 0.0,
                        child.output_rows,
                        1.0,
                    )
                    width = self._row_width(table or {}, self._stats(str((table or {}).get("table_name") or "")))
                    hash_bytes = build_rows * width * 2.5
                    join_limit = self._buffer_limit("join_buffer_size", 256 * 1024)
                    resident_hash = min(hash_bytes, join_limit)
                    memory_peak = max(memory_peak, child.memory_bytes + resident_hash)
                    memory_demand_peak = max(
                        memory_demand_peak,
                        child.memory_demand_bytes + hash_bytes,
                    )
                    cpu += build_rows * 1.5 + probe_rows
                    if hash_bytes > join_limit:
                        io += IOWork(sequential_pages=2.0 * hash_bytes / self.page_size)
                elif join_kind == "block_nested_loop":
                    cpu += previous_rows * 0.7
                    memory_peak = max(
                        memory_peak,
                        child.memory_bytes
                        + self._buffer_limit("join_buffer_size", 256 * 1024),
                    )
                    memory_demand_peak = max(
                        memory_demand_peak,
                        child.memory_demand_bytes
                        + self._buffer_limit("join_buffer_size", 256 * 1024),
                    )
                else:
                    cpu += previous_rows * 0.5
            previous_rows = max(child.output_rows, 1.0)
            output_rows = child.output_rows
        return QueryWork(
            cpu=cpu,
            memory_bytes=memory_peak,
            io=io,
            output_rows=output_rows,
            memory_demand_bytes=memory_demand_peak,
        )

    def _explicit_join_work(
        self, child: Any, *, loop_multiplier: float, kind: str
    ) -> QueryWork:
        values = child if isinstance(child, list) else [
            value for value in child.values() if isinstance(value, (Mapping, list))
        ]
        works = [self._walk(value, loop_multiplier=loop_multiplier) for value in values]
        combined = _merge_parallel(works)
        rows = [max(value.output_rows, 1.0) for value in works]
        if kind == "hash":
            build = min(rows, default=1.0)
            probe = max(rows, default=1.0)
            width = DEFAULT_ROW_WIDTH
            memory = build * width * 2.5 + combined.memory_bytes
            memory_demand = build * width * 2.5 + combined.memory_demand_bytes
            cpu = combined.cpu + build * 1.5 + probe
        else:
            memory = combined.memory_bytes
            memory_demand = combined.memory_demand_bytes
            cpu = combined.cpu + sum(rows) * 0.8
        return QueryWork(
            cpu, memory, combined.io, max(rows, default=0.0), memory_demand
        )

    def _unary_work(
        self,
        child_node: Any,
        *,
        loop_multiplier: float,
        kind: str,
        wrapper: Mapping[str, Any],
    ) -> QueryWork:
        child = self._walk(child_node, loop_multiplier=loop_multiplier)
        rows = max(
            child.output_rows,
            _positive(wrapper.get("rows_produced_per_join")),
            1.0,
        )
        width = DEFAULT_ROW_WIDTH
        cpu = child.cpu
        memory = child.memory_bytes
        memory_demand = child.memory_demand_bytes
        io = child.io
        if kind == "sort":
            cpu += rows * math.log2(max(rows, 2.0)) * 0.3
            required = rows * width
            limit = self._buffer_limit("sort_buffer_size", 256 * 1024)
            memory += min(required, limit)
            memory_demand += required
            if required > limit:
                io += IOWork(sequential_pages=2.0 * required / self.page_size)
        elif kind in {"aggregate", "deduplicate"}:
            cpu += rows * 1.3
            required = rows * width
            limit = self._temp_table_limit()
            memory += min(required, limit)
            memory_demand += required
            if required > limit:
                io += IOWork(sequential_pages=2.0 * required / self.page_size)
        elif kind == "window":
            cpu += rows
            memory += min(rows * width * 0.3, self._temp_table_limit())
            memory_demand += rows * width * 0.3
        else:
            cpu += rows
            required = rows * width
            limit = self._temp_table_limit()
            memory += min(required, limit)
            memory_demand += required
            if required > limit:
                io += IOWork(sequential_pages=2.0 * required / self.page_size)
        return QueryWork(cpu, memory, io, rows, memory_demand)

    def _buffer_limit(self, name: str, default: float) -> float:
        return max(1.0, _positive(self.params.get(name), default))

    def _temp_table_limit(self) -> float:
        tmp = self._buffer_limit("tmp_table_size", 16 * 1024 * 1024)
        heap = self._buffer_limit("max_heap_table_size", tmp)
        return min(tmp, heap)

    def _index_pages(
        self, index_stats: Mapping[str, Any], table_stats: Mapping[str, Any]
    ) -> float:
        return max(
            1.0,
            _positive(index_stats.get("index_pages")),
            _positive(index_stats.get("relpages")),
            math.ceil(
                _positive(index_stats.get("index_length")) / self.page_size
            ),
            math.ceil(
                _positive(table_stats.get("index_length")) / self.page_size
            ),
        )

    @staticmethod
    def _distinct_pages(rows: float, pages: float, tuples: float) -> float:
        """Occupancy approximation for random clustered-page fetches."""
        rows = max(0.0, rows)
        pages = max(1.0, pages)
        tuples = max(1.0, tuples)
        if rows >= tuples:
            return pages
        return min(pages, pages * (1.0 - math.exp(-rows / pages)))


def node_cpu_work(
    plan: Any,
    relation_stats: Mapping[str, Mapping[str, Any]],
    executor_parameters: Optional[Mapping[str, Any]] = None,
) -> float:
    return MySQLPlanPhysics(
        relation_stats, executor_parameters=executor_parameters
    ).work(plan).cpu


def query_mem_work(
    plan: Any,
    relation_stats: Mapping[str, Mapping[str, Any]],
    executor_parameters: Optional[Mapping[str, Any]] = None,
) -> float:
    return MySQLPlanPhysics(
        relation_stats, executor_parameters=executor_parameters
    ).work(plan).memory_bytes


def node_io_work(
    plan: Any,
    relation_stats: Mapping[str, Mapping[str, Any]],
    cache_profile: Optional[CacheProfile] = None,
    executor_parameters: Optional[Mapping[str, Any]] = None,
) -> Tuple[float, float]:
    del cache_profile  # Cache is applied after sequential/random work is separated.
    io = MySQLPlanPhysics(
        relation_stats, executor_parameters=executor_parameters
    ).work(plan).io
    return io.sequential_pages, io.random_pages


def effective_io(
    plan: Any,
    relation_stats: Mapping[str, Mapping[str, Any]],
    sequential_hit_rate: float,
    random_hit_rate: float,
    *,
    executor_parameters: Optional[Mapping[str, Any]] = None,
    page_size: int = INNODB_PAGE_SIZE,
) -> float:
    profile = CacheProfile(sequential_hit_rate, random_hit_rate)
    sequential, random = node_io_work(
        plan, relation_stats, profile, executor_parameters
    )
    return (
        sequential * (1.0 - profile.sequential_hit_rate)
        + random * (1.0 - profile.random_hit_rate)
    ) * page_size


def calibrate_io(
    baseline_plans: Mapping[str, Any] | Sequence[Any],
    relation_stats: Mapping[str, Mapping[str, Any]],
    real_io: float,
    workload_weights: Mapping[str, float] | Sequence[float],
    *,
    executor_parameters: Optional[Mapping[str, Any]] = None,
    prior: CacheProfile = CacheProfile(),
    page_size: int = INNODB_PAGE_SIZE,
) -> CacheProfile:
    """Return the frozen cache profile used by baseline and candidates.

    One aggregate baseline supplies only one equation, while hit rates and the
    byte/time scale are three unknowns.  Therefore a single baseline cannot
    identify both hit rates.  We validate that the baseline has physical IO and
    retain an explicit prior; the separately calibrated ``io_scale`` supplies
    the missing unit conversion.
    """
    plans = (
        list(baseline_plans.values())
        if isinstance(baseline_plans, Mapping)
        else list(baseline_plans)
    )
    if isinstance(workload_weights, Mapping):
        if not isinstance(baseline_plans, Mapping):
            raise ValueError("mapping workload_weights require mapping baseline_plans")
        weights = [float(workload_weights[name]) for name in baseline_plans]
    else:
        weights = [float(value) for value in workload_weights]
    if len(plans) != len(weights):
        raise ValueError("baseline plan and workload weight counts differ")
    raw_pages = 0.0
    for weight, plan in zip(weights, plans):
        sequential, random = node_io_work(
            plan, relation_stats, prior, executor_parameters
        )
        raw_pages += weight * (sequential + random)
    if not math.isfinite(float(real_io)) or float(real_io) < 0.0:
        raise ValueError("real_io must be finite and non-negative")
    if float(real_io) > 0.0 and raw_pages * page_size <= 0.0:
        raise ValueError("positive baseline IO has zero physical plan IO work")
    return prior


def _weighted_work(
    plans: Mapping[str, Any],
    weights: Mapping[str, float],
    relation_stats: Mapping[str, Mapping[str, Any]],
    executor_parameters: Optional[Mapping[str, Any]],
) -> Tuple[float, float, IOWork, Dict[str, QueryWork]]:
    if set(plans) != set(weights):
        raise ValueError("plans and workload_weights must have identical query ids")
    physics = MySQLPlanPhysics(
        relation_stats, executor_parameters=executor_parameters
    )
    per_query: Dict[str, QueryWork] = {}
    cpu = 0.0
    io = IOWork()
    memory = 0.0
    for query_id, plan in plans.items():
        weight = float(weights[query_id])
        if not math.isfinite(weight) or weight <= 0.0:
            raise ValueError(f"invalid workload weight for {query_id}: {weight!r}")
        work = physics.work(plan)
        per_query[query_id] = work
        cpu += weight * work.cpu
        io += work.io.scaled(weight)
        memory = max(memory, work.memory_bytes)
    return cpu, memory, io, per_query


def _peak_memory_demand(per_query: Mapping[str, QueryWork]) -> float:
    """Return structural operator demand, independent of resident buffer pools."""
    return max(
        (work.memory_demand_bytes for work in per_query.values()),
        default=0.0,
    )


def calibrate_resources(
    baseline_real: Mapping[str, float],
    baseline_plans: Mapping[str, Any],
    workload_weights: Mapping[str, float],
    relation_stats: Mapping[str, Mapping[str, Any]],
    *,
    concurrency: int = 1,
    executor_parameters: Optional[Mapping[str, Any]] = None,
    cache_profile: CacheProfile = CacheProfile(),
    page_size: int = INNODB_PAGE_SIZE,
    memory_operator_scale: float = 1.0,
    memory_bytes_per_unit: float = 1.0,
) -> ResourceCalibration:
    for resource in ("cpu", "memory", "io"):
        value = _number(baseline_real.get(resource), math.nan)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"baseline_real[{resource!r}] must be finite and non-negative")
    base_cpu, base_mem_one, base_io, _ = _weighted_work(
        baseline_plans,
        workload_weights,
        relation_stats,
        executor_parameters,
    )
    memory_unit = _number(memory_bytes_per_unit, 0.0)
    if memory_unit <= 0.0:
        raise ValueError("memory_bytes_per_unit must be positive")
    base_mem = base_mem_one * max(1, int(concurrency)) / memory_unit
    profile = calibrate_io(
        baseline_plans,
        relation_stats,
        baseline_real["io"],
        workload_weights,
        executor_parameters=executor_parameters,
        prior=cache_profile,
        page_size=page_size,
    )
    effective_bytes = (
        base_io.sequential_pages * (1.0 - profile.sequential_hit_rate)
        + base_io.random_pages * (1.0 - profile.random_hit_rate)
    ) * page_size
    cpu_scale = float(baseline_real["cpu"]) / max(base_cpu, 1e-12)
    io_scale = (
        float(baseline_real["io"]) / effective_bytes
        if effective_bytes > 0.0
        else 0.0
    )
    operator_scale = _positive(memory_operator_scale, 1.0)
    if base_mem > 0.0 and operator_scale * base_mem > float(baseline_real["memory"]):
        operator_scale = float(baseline_real["memory"]) / base_mem
    resident = max(
        0.0,
        float(baseline_real["memory"]) - operator_scale * base_mem,
    )
    return ResourceCalibration(
        cpu_scale=cpu_scale,
        io_scale=io_scale,
        cache_profile=profile,
        baseline_cpu_work=base_cpu,
        baseline_memory_work=base_mem,
        baseline_effective_io_bytes=effective_bytes,
        baseline_real={name: float(baseline_real[name]) for name in ("cpu", "memory", "io")},
        memory_resident=resident,
        memory_operator_scale=operator_scale,
        memory_bytes_per_unit=memory_unit,
    )


def predict_resources(
    baseline_real: Mapping[str, float],
    baseline_plans: Mapping[str, Any],
    candidate_plans: Mapping[str, Any],
    workload_weights: Mapping[str, float],
    relation_stats: Mapping[str, Mapping[str, Any]],
    concurrency: int,
    capability: Optional[Mapping[str, float]] = None,
    *,
    executor_parameters: Optional[Mapping[str, Any]] = None,
    cache_profile: CacheProfile = CacheProfile(),
    page_size: int = INNODB_PAGE_SIZE,
    memory_operator_scale: float = 1.0,
    memory_bytes_per_unit: float = 1.0,
) -> Dict[str, float]:
    calibration = calibrate_resources(
        baseline_real,
        baseline_plans,
        workload_weights,
        relation_stats,
        concurrency=concurrency,
        executor_parameters=executor_parameters,
        cache_profile=cache_profile,
        page_size=page_size,
        memory_operator_scale=memory_operator_scale,
        memory_bytes_per_unit=memory_bytes_per_unit,
    )
    cpu, memory_one, io, _ = _weighted_work(
        candidate_plans,
        workload_weights,
        relation_stats,
        executor_parameters,
    )
    effective_bytes = (
        io.sequential_pages * (1.0 - cache_profile.sequential_hit_rate)
        + io.random_pages * (1.0 - cache_profile.random_hit_rate)
    ) * page_size
    predicted = {
        "cpu": calibration.cpu_scale * cpu,
        "memory": calibration.memory_resident
        + calibration.memory_operator_scale
        * memory_one
        * max(1, int(concurrency))
        / calibration.memory_bytes_per_unit,
        "io": calibration.io_scale * effective_bytes,
    }
    if capability is None:
        return predicted
    utilization: Dict[str, float] = {}
    for name, value in predicted.items():
        capacity = _number(capability.get(name), 0.0)
        if capacity <= 0.0:
            raise ValueError(f"capability[{name!r}] must be positive")
        utilization[name] = value / capacity
    return utilization


class MySQLRelationStatsCollector:
    """Cache planner-independent InnoDB table and index size statistics."""

    TABLE_SQL = (
        "SELECT TABLE_SCHEMA,TABLE_NAME,COALESCE(TABLE_ROWS,0),"
        "COALESCE(DATA_LENGTH,0),COALESCE(INDEX_LENGTH,0),"
        "COALESCE(AVG_ROW_LENGTH,0) FROM information_schema.TABLES "
        "WHERE ENGINE='InnoDB' AND TABLE_SCHEMA NOT IN "
        "('mysql','information_schema','performance_schema','sys') "
        "ORDER BY TABLE_SCHEMA,TABLE_NAME"
    )
    INDEX_SQL = (
        "SELECT database_name,table_name,index_name,stat_value "
        "FROM mysql.innodb_index_stats WHERE stat_name='size' "
        "ORDER BY database_name,table_name,index_name"
    )

    def __init__(self, client: SQLClient, *, page_size: int = INNODB_PAGE_SIZE) -> None:
        self.client = client
        self.page_size = max(1024, int(page_size))
        self._cache: Optional[Dict[str, Dict[str, float]]] = None

    def collect(self) -> Dict[str, Dict[str, float]]:
        if self._cache is not None:
            return copy.deepcopy(self._cache)
        result: Dict[str, Dict[str, float]] = {}
        short_names: Dict[str, list[Dict[str, float]]] = {}
        for line in self.client.execute(self.TABLE_SQL).splitlines():
            fields = line.split("\t")
            if len(fields) != 6:
                continue
            schema, table = fields[:2]
            row = {
                "table_rows": _positive(fields[2]),
                "data_length": _positive(fields[3]),
                "index_length": _positive(fields[4]),
                "avg_row_length": _positive(fields[5]),
                "data_pages": math.ceil(_positive(fields[3]) / self.page_size),
            }
            result[f"{schema}.{table}"] = row
            short_names.setdefault(table, []).append(row)
        for table, rows in short_names.items():
            if len(rows) == 1:
                result[table] = rows[0]
        try:
            index_output = self.client.execute(self.INDEX_SQL)
        except Exception:
            index_output = ""
        for line in index_output.splitlines():
            fields = line.split("\t")
            if len(fields) != 4:
                continue
            schema, table, index = fields[:3]
            row = {"index_pages": _positive(fields[3])}
            result[f"{schema}.{table}.{index}"] = row
            if table in short_names and len(short_names[table]) == 1:
                result[f"{table}.{index}"] = row
        self._cache = result
        return copy.deepcopy(result)


class MySQLPlanResourceEstimator:
    """Estimator adapter compatible with the existing MySQL search call shape."""

    def __init__(self, adapter: MySQLCostAdapter, config: Mapping[str, Any]) -> None:
        from trained_model_estimator import MySQLExplainFeatureProvider

        self.adapter = adapter
        self.config = dict(config)
        self.mode = str(self.config.get("type", "plan_physics"))
        if self.mode not in {"plan_physics", "ratio_scale"}:
            raise ValueError(f"unsupported MySQL plan estimator mode: {self.mode}")
        source = self.config.get("feature_source")
        if not isinstance(source, Mapping):
            raise ValueError(f"{self.mode}.feature_source must be an object")
        baseline_real = self.config.get("baseline_real")
        if not isinstance(baseline_real, Mapping):
            raise ValueError(f"{self.mode}.baseline_real must contain cpu/memory/io")
        self.baseline_real = {
            name: _number(baseline_real.get(name), math.nan)
            for name in ("cpu", "memory", "io")
        }
        if any(not math.isfinite(value) or value < 0.0 for value in self.baseline_real.values()):
            raise ValueError(f"{self.mode}.baseline_real must be finite and non-negative")
        configured_baseline = self.config.get("baseline_params")
        self.baseline_params = (
            copy.deepcopy(dict(configured_baseline))
            if isinstance(configured_baseline, Mapping)
            else None
        )
        self.ratio_min = _number(self.config.get("ratio_min"), 0.05)
        self.ratio_max = _number(self.config.get("ratio_max"), 20.0)
        if self.ratio_min <= 0.0 or self.ratio_max < self.ratio_min:
            raise ValueError("ratio_min/ratio_max must satisfy 0 < min <= max")
        self.uncalibrated_transition_log_step = _number(
            self.config.get("uncalibrated_transition_log_step"), 0.05
        )
        if not 0.0 < self.uncalibrated_transition_log_step <= 0.20:
            raise ValueError(
                "uncalibrated_transition_log_step must be within (0, 0.20]"
            )
        self.memory_feature_floor_bytes = _positive(
            self.config.get("memory_feature_floor_bytes"), 1.0
        )
        if self.memory_feature_floor_bytes <= 0.0:
            raise ValueError("memory_feature_floor_bytes must be positive")
        self.provider = MySQLExplainFeatureProvider(adapter.client, source)
        self.stats_collector = MySQLRelationStatsCollector(
            adapter.client,
            page_size=int(self.config.get("page_size", INNODB_PAGE_SIZE)),
        )
        self._calibration: Optional[ResourceCalibration] = None
        self._baseline_plans: Optional[Dict[str, Any]] = None
        self._baseline_weights: Optional[Dict[str, float]] = None
        self._baseline_params_used: Optional[Dict[str, Any]] = None
        self._baseline_features: Optional[Dict[str, float]] = None
        self._baseline_plan_work_sha256: Optional[str] = None
        self._baseline_capture: Optional[Tuple[Any, ...]] = None
        self._baseline_resident_memory_bytes: Optional[float] = None
        self.measurements: list[Dict[str, Any]] = []
        self.last_metadata: Dict[str, Any] = {}

    def _plans(self, features: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, float]]:
        captures = features.get("plan_captures")
        if not isinstance(captures, list) or not captures:
            raise RuntimeError("feature provider returned no raw plan captures")
        plans: Dict[str, Any] = {}
        weights: Dict[str, float] = {}
        for ordinal, capture in enumerate(captures, 1):
            if not isinstance(capture, Mapping):
                continue
            query_id = str(capture.get("query_id") or f"query_{ordinal:04d}")
            binding_id = str(capture.get("binding_id") or "static")
            identity = f"{query_id}::{binding_id}"
            raw = capture.get("raw_explain")
            if not isinstance(raw, Mapping):
                raise RuntimeError(f"missing raw EXPLAIN for {identity}")
            plans[identity] = raw
            weights[identity] = float(
                capture.get("effective_weight", capture.get("query_weight", 1.0))
            )
        return plans, weights

    def _capture(
        self, params: Mapping[str, Any]
    ) -> Tuple[
        Dict[str, Any], Dict[str, Any], Dict[str, float], Dict[str, Dict[str, float]],
        Dict[str, Any], CacheProfile, int, int, float, float,
    ]:
        self.adapter.apply(params)
        features = self.provider.collect()
        plans, weights = self._plans(features)
        relation_stats_cfg = self.config.get("relation_stats")
        relation_stats = (
            copy.deepcopy(dict(relation_stats_cfg))
            if isinstance(relation_stats_cfg, Mapping)
            else self.stats_collector.collect()
        )
        executor = dict(self.config.get("executor_parameters") or {})
        executor.update(
            {
                name: value
                for name, value in params.items()
                if name
                in {
                    "innodb_buffer_pool_size",
                    "join_buffer_size",
                    "sort_buffer_size",
                    "tmp_table_size",
                    "max_heap_table_size",
                }
            }
        )
        cache_cfg = self.config.get("cache_profile") or {}
        cache = CacheProfile(
            _number(cache_cfg.get("sequential_hit_rate"), DEFAULT_CACHE_PROFILE[0]),
            _number(cache_cfg.get("random_hit_rate"), DEFAULT_CACHE_PROFILE[1]),
        )
        concurrency = max(1, int(self.config.get("concurrency", 1)))
        page_size = int(self.config.get("page_size", INNODB_PAGE_SIZE))
        memory_scale = _positive(self.config.get("memory_operator_scale"), 1.0)
        memory_bytes_per_unit = _positive(
            self.config.get("memory_bytes_per_unit"), 1024.0**3
        )
        return (
            features, plans, weights, relation_stats, executor, cache,
            concurrency, page_size, memory_scale, memory_bytes_per_unit,
        )

    def _resident_memory_bytes(self, executor: Mapping[str, Any]) -> float:
        configured = _positive(executor.get("innodb_buffer_pool_size"))
        if configured > 0.0:
            return configured
        if self._baseline_resident_memory_bytes is not None:
            return self._baseline_resident_memory_bytes
        if self.mode != "ratio_scale":
            return 0.0
        raw = self.adapter.client.execute(
            "SELECT @@GLOBAL.innodb_buffer_pool_size"
        ).strip().splitlines()
        value = _positive(raw[-1].split("\t")[0]) if raw else 0.0
        if value <= 0.0:
            raise RuntimeError(
                "ratio_scale requires a positive innodb_buffer_pool_size memory feature"
            )
        return value

    @staticmethod
    def _work_sha256(per_query: Mapping[str, QueryWork]) -> str:
        return _canonical_sha256(
            {
                name: {
                    "cpu": work.cpu,
                    "memory": work.memory_bytes,
                    "memory_demand": work.memory_demand_bytes,
                    "seq": work.io.sequential_pages,
                    "rand": work.io.random_pages,
                }
                for name, work in per_query.items()
            }
        )

    def calibrate(
        self,
        sql_set: Any,
        baseline_params: Optional[Mapping[str, Any]] = None,
        runtime_resource_real: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Anchor structural plan features to a measured MySQL baseline."""
        del sql_set
        if runtime_resource_real is not None:
            values = {
                name: _number(runtime_resource_real.get(name), math.nan)
                for name in ("cpu", "memory", "io")
            }
            if any(not math.isfinite(value) or value < 0.0 for value in values.values()):
                raise ValueError("runtime_resource_real must contain finite non-negative cpu/memory/io")
            self.baseline_real = values
        selected = baseline_params if baseline_params is not None else self.baseline_params
        if not isinstance(selected, Mapping) or not selected:
            raise ValueError("ratio-scale calibration requires non-empty baseline_params")
        capture = self._capture(selected)
        (
            features, plans, weights, relation_stats, executor, cache,
            concurrency, page_size, memory_scale, memory_bytes_per_unit,
        ) = capture
        calibration = calibrate_resources(
            self.baseline_real,
            plans,
            weights,
            relation_stats,
            concurrency=concurrency,
            executor_parameters=executor,
            cache_profile=cache,
            page_size=page_size,
            memory_operator_scale=memory_scale,
            memory_bytes_per_unit=memory_bytes_per_unit,
        )
        _cpu, memory_one, _io, per_query = _weighted_work(
            plans, weights, relation_stats, executor
        )
        resident_memory_bytes = self._resident_memory_bytes(executor)
        memory_feature = (
            max(
                _peak_memory_demand(per_query) * concurrency,
                self.memory_feature_floor_bytes,
            )
            if self.mode == "ratio_scale"
            else calibration.memory_resident
            + calibration.memory_operator_scale * calibration.baseline_memory_work
        )
        self._calibration = calibration
        self._baseline_plans = copy.deepcopy(plans)
        self._baseline_weights = copy.deepcopy(weights)
        self._baseline_params_used = copy.deepcopy(dict(selected))
        self._baseline_features = {
            "cpu_total": calibration.baseline_cpu_work,
            "memory_total": memory_feature,
            "io_total": calibration.baseline_effective_io_bytes,
        }
        self._baseline_plan_work_sha256 = self._work_sha256(per_query)
        self._baseline_capture = copy.deepcopy(capture)
        self._baseline_resident_memory_bytes = resident_memory_bytes
        return {
            "baseline_real": copy.deepcopy(self.baseline_real),
            "baseline_features": copy.deepcopy(self._baseline_features),
            "baseline_params": copy.deepcopy(self._baseline_params_used),
            "baseline_plan_work_sha256": self._baseline_plan_work_sha256,
            "plan_bundle": copy.deepcopy(features.get("canonical_plan_bundle")),
        }

    def __call__(self, sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        calibrated_now = self._calibration is None
        if self._calibration is None:
            # Explicit configured incumbent wins. Falling back to the current
            # point preserves legacy plan_physics behavior for old configs.
            self.calibrate(sql_set, self.baseline_params or params)
        same_as_baseline = (
            calibrated_now
            and self._baseline_capture is not None
            and dict(params) == (self._baseline_params_used or {})
        )
        capture = (
            copy.deepcopy(self._baseline_capture)
            if same_as_baseline
            else self._capture(params)
        )
        (
            features, plans, weights, relation_stats, executor, _cache,
            concurrency, page_size, _memory_scale, _memory_bytes_per_unit,
        ) = capture
        if set(weights) != set(self._baseline_weights or {}):
            raise RuntimeError("candidate EXPLAIN coverage differs from baseline coverage")

        cpu, memory_one, io, per_query = _weighted_work(
            plans, weights, relation_stats, executor
        )
        calibration = self._calibration
        effective_bytes = (
            io.sequential_pages
            * (1.0 - calibration.cache_profile.sequential_hit_rate)
            + io.random_pages * (1.0 - calibration.cache_profile.random_hit_rate)
        ) * page_size
        unscaled_resource = {
            "cpu": calibration.cpu_scale * cpu,
            "memory": calibration.memory_resident
            + calibration.memory_operator_scale
            * memory_one
            * concurrency
            / calibration.memory_bytes_per_unit,
            "io": calibration.io_scale * effective_bytes,
        }
        current_features = {
            "cpu_total": cpu,
            "memory_total": (
                max(
                    _peak_memory_demand(per_query) * concurrency,
                    self.memory_feature_floor_bytes,
                )
                if self.mode == "ratio_scale"
                else unscaled_resource["memory"]
            ),
            "io_total": effective_bytes,
        }
        baseline_features = copy.deepcopy(self._baseline_features or {})
        plan_work_sha256 = self._work_sha256(per_query)
        calibrated_plan_work = plan_work_sha256 == self._baseline_plan_work_sha256
        ratios_unclipped: Dict[str, float] = {}
        ratios_clipped: Dict[str, float] = {}
        ratios: Dict[str, float] = {}
        resource: Dict[str, float] = {}
        for name in ("cpu", "memory", "io"):
            baseline_feature = _positive(baseline_features.get(f"{name}_total"))
            current_feature = _positive(current_features[f"{name}_total"])
            raw_ratio = current_feature / baseline_feature if baseline_feature > 1e-12 else 1.0
            if not math.isfinite(raw_ratio) or raw_ratio <= 0.0:
                raw_ratio = 1.0
            clipped_ratio = (
                min(self.ratio_max, max(self.ratio_min, raw_ratio))
                if self.mode == "ratio_scale"
                else raw_ratio
            )
            ratio = clipped_ratio
            if self.mode == "ratio_scale" and not calibrated_plan_work:
                if raw_ratio > 1.0 + 1e-12:
                    ratio = math.exp(self.uncalibrated_transition_log_step)
                elif raw_ratio < 1.0 - 1e-12:
                    ratio = math.exp(-self.uncalibrated_transition_log_step)
                else:
                    ratio = 1.0
            ratios_unclipped[name] = raw_ratio
            ratios_clipped[name] = clipped_ratio
            ratios[name] = ratio
            resource[name] = (
                self.baseline_real[name] * ratio
                if self.mode == "ratio_scale"
                else unscaled_resource[name]
            )
        metadata = {
            "estimator": (
                "mysql_ratio_scale_resource_estimator_v1"
                if self.mode == "ratio_scale"
                else "mysql_executor_physics_baseline_calibrated_v1"
            ),
            "resource_estimation_mode": self.mode,
            "cost_parameter_free_features": True,
            "planner_cost_fields_used_as_physical_work": False,
            "params": copy.deepcopy(params),
            "baseline_params": copy.deepcopy(self._baseline_params_used),
            "baseline_features": baseline_features,
            "current_features": current_features,
            "feature_ratios_unclipped": ratios_unclipped,
            "feature_ratios_clipped": ratios_clipped,
            "feature_ratios": ratios,
            "ratio_clip": [self.ratio_min, self.ratio_max],
            "calibrated_plan_work": calibrated_plan_work,
            "magnitude_trust": (
                "calibrated_plan_work"
                if calibrated_plan_work
                else "direction_only_uncalibrated_plan_transition"
            ),
            "uncalibrated_transition_log_step": (
                self.uncalibrated_transition_log_step
            ),
            "memory_feature_semantics": (
                "peak_operator_demand_bytes_without_buffer_pool"
                if self.mode == "ratio_scale"
                else "calibrated_resident_plus_executor_memory"
            ),
            "memory_feature_floor_bytes": self.memory_feature_floor_bytes,
            "calibration": {
                **asdict(calibration),
                "cache_profile": asdict(calibration.cache_profile),
            },
            "candidate_work": {
                "cpu": cpu,
                "memory_bytes_one_session_peak": memory_one,
                "sequential_pages": io.sequential_pages,
                "random_pages": io.random_pages,
                "effective_io_bytes": effective_bytes,
            },
            "query_work": {
                name: {
                    "cpu": work.cpu,
                    "memory_bytes": work.memory_bytes,
                    "memory_demand_bytes": work.memory_demand_bytes,
                    "sequential_pages": work.io.sequential_pages,
                    "random_pages": work.io.random_pages,
                }
                for name, work in per_query.items()
            },
            "plan_bundle": copy.deepcopy(features.get("canonical_plan_bundle")),
            "optimistic_pruning_error_status": (
                "unavailable_fail_open_until_plan_physics_error_bounds_are_calibrated"
            ),
            "baseline_plan_work_sha256": self._baseline_plan_work_sha256,
            "plan_work_sha256": plan_work_sha256,
        }
        self.last_metadata = metadata
        self.measurements.append(
            {"resource": copy.deepcopy(resource), "_metadata": copy.deepcopy(metadata)}
        )
        return {**resource, "_metadata": metadata}


def build_plan_resource_estimator(
    adapter: MySQLCostAdapter, config: Mapping[str, Any]
) -> MySQLPlanResourceEstimator:
    return MySQLPlanResourceEstimator(adapter, config)


def build_ratio_scale_resource_estimator(
    adapter: MySQLCostAdapter, config: Mapping[str, Any]
) -> MySQLPlanResourceEstimator:
    ratio_config = copy.deepcopy(dict(config))
    ratio_config["type"] = "ratio_scale"
    return MySQLPlanResourceEstimator(adapter, ratio_config)
