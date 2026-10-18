"""Plan-based resource estimator wrapper for PostgreSQL parameter optimizer.

This provides a drop-in `resource_estimation_func` (callable that accepts (sql_set, params_dict) -> resource_dict)
compatible with `GradientDescentParameterOptimizer` in `gradient_descent_optimizer.py`.

It internally uses `PlanResourceEstimator` (from `plan_resource_estimator.py`) to perform direct resource estimation
from PostgreSQL EXPLAIN plan trees, replacing the previous experience-table-based estimation which performed poorly.
The gradient descent step-size guidance (via `sensitivity_table` in `_calculate_direction`) remains unchanged.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from plan_resource_estimator import (
    estimate_plan_resources,
    PlanResourceEstimator,
    _safe_float,
)


class PlanBasedResourceEstimator:
    """Wrapper that matches the expected (sql_set, params) -> resource_dict interface."""

    def __init__(
        self,
        pg_host: str,
        pg_port: str,
        pg_user: str,
        pg_dbname: str,
        estimator_cfg: Optional[Dict[str, Any]] = None,
        plan_capture_modes: Optional[Sequence[str]] = None,
        custom_plan_params: Optional[Dict[str, List[Any]]] = None,
        psql_query_func: Optional[Any] = None,
        workload_context: Optional[Dict[str, Any]] = None,
        verbose: bool = True,
    ) -> None:
        self.pg_host = pg_host
        self.pg_port = pg_port
        self.pg_user = pg_user
        self.pg_dbname = pg_dbname
        self.estimator_cfg = estimator_cfg or {}
        self.plan_capture_modes = plan_capture_modes or ["generic"]
        self.custom_plan_params = custom_plan_params or {}
        self.psql_query_func = psql_query_func
        self.workload_context = workload_context or {}
        self.verbose = verbose

        # Create internal estimator (lazily, so connection is only when __call__ is invoked)
        self._internal_estimator: Optional[PlanResourceEstimator] = None

    def __call__(
        self, sql_set: Any, params: Dict[str, float]
    ) -> Dict[str, float]:
        """Resource estimation using plan trees.

        Args:
            sql_set: Workload object (typically sequence of query dicts or object with .queries)
            params: Parameter values dict for the current candidate (used for custom_plan_params override)

        Returns:
            resource_avg: Dict of estimated resource values (e.g. {'cpu': 1.2, 'memory': 4.5, 'io': 0.3})
        """
        # Build queries from sql_set if needed
        if hasattr(sql_set, "queries"):
            queries = sql_set.queries
        elif isinstance(sql_set, (list, tuple)):
            queries = sql_set
        else:
            # Fallback: try to extract or use as-is (may fail if not compatible)
            queries = sql_set  # type: ignore

        # Prepare params for override
        plan_params_override = {}
        for p_name in params:
            try:
                plan_params_override[p_name] = float(params[p_name])
            except (TypeError, ValueError):
                pass

        try:
            # Use the estimate_plan_resources helper which internally creates PlanResourceEstimator
            resource_avg, _, _ = estimate_plan_resources(
                queries=queries,
                pg_host=self.pg_host,
                pg_port=self.pg_port,
                pg_user=self.pg_user,
                pg_dbname=self.pg_dbname,
                estimator_cfg=self.estimator_cfg,
                plan_capture_modes=self.plan_capture_modes,
                custom_plan_params={
                    **(self.custom_plan_params or {}),
                    **plan_params_override,
                },
                psql_query_func=self.psql_query_func,
                params=params,
                workload_context=self.workload_context,
            )
            return resource_avg
        except Exception as exc:
            if self.verbose:
                import logging
                logging.getLogger(__name__).error(
                    f"Plan-based resource estimation failed: {exc}. "
                    "Falling back to zero/empty resource dict or partial estimate."
                )
            # Graceful fallback
            return {k: 0.0 for k in self.workload_context.get("resource_types", ["memory", "cpu", "io"]) if k in params}

    def close(self) -> None:
        """Close any resources held by the internal estimator."""
        if self._internal_estimator is not None:
            try:
                # internal estimator may not have close, but if it does
                pass
            except Exception:
                pass
