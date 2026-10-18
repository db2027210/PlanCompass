#!/usr/bin/env python3
"""Run PostgreSQL benchmark/bottleneck coverage matrix."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shlex
import signal
import socket
import subprocess
import sys
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree as ET

from prepare_bonect_reference import write_reference


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common_outer_loop import cap_real_replay_top_k

DEFAULT_BENCHMARKS = ["tpcc", "tpch", "chbench", "hybench_ap", "hybench_xp", "job"]
DEFAULT_BOTTLENECKS = ["cpu", "memory", "io"]
DEFAULT_BONECT_SCENARIOS = [
    ("cpu", "hybench_xp"),
    ("memory", "hybench_ap"),
    ("io", "hybench_xp"),
    ("none", "hybench_xp"),
]
DEFAULT_BONECT_CPU_XP_TP_CLIENTS = 2
DEFAULT_BONECT_CPU_XP_AP_CLIENTS = 8
DEFAULT_BONECT_MEMORY_AP_CLIENTS = 8
DEFAULT_BONECT_IO_XP_TP_CLIENTS = 16
DEFAULT_BONECT_IO_XP_AP_CLIENTS = 4
COST_PARAM_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
COST_PARAM_SET = set(COST_PARAM_KEYS)
BONECT_REJECTED_NEIGHBORHOODS_PATH = (
    PROJECT_ROOT / "report" / "bonect" / "phase2_known_rejected_neighborhoods.json"
)
DEFAULT_PG_COST_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
BONECT_COST_PARAM_FLOORS = {
    "seq_page_cost": 0.05,
    "random_page_cost": 0.05,
    "cpu_tuple_cost": 1e-6,
    "cpu_index_tuple_cost": 1e-6,
    "cpu_operator_cost": 1e-6,
    "parallel_setup_cost": 1.0,
    "parallel_tuple_cost": 1e-5,
}
BONECT_SCENARIO_COST_PARAM_FLOORS = {
    ("cpu", "hybench_xp"): {
        # Real phase-2 CPU/XP validation rejected lower tuple/operator regions:
        # v22 low tuple, v23 low operator neighborhood, and v29 low operator.
        # Keep the legal search in the seven cost params, but do not generate
        # real-validation candidates below the empirically safe side.
        "cpu_tuple_cost": 0.0012,
        "cpu_operator_cost": 0.0010,
    },
}
BONECT_ANCHOR_PATH_STEPS = [0.10, 0.20, 0.35, 0.50, 0.75, 1.0, 1.25, 1.50]
BONECT_PAIR_PROBE_VALUES_PER_PARAM = 5
BONECT_PAIR_PROBE_MAX_COMBINATION_SIZE = len(COST_PARAM_KEYS)
BONECT_PAIR_PROBE_FULL_FACTORIAL_GRID = True
BONECT_PAIR_PROBE_MAX_CANDIDATES = BONECT_PAIR_PROBE_VALUES_PER_PARAM ** len(COST_PARAM_KEYS)
BONECT_INITIAL_PARAMS = {
    ("cpu", "hybench_xp"): dict(DEFAULT_PG_COST_PARAMS),
    ("memory", "hybench_ap"): dict(DEFAULT_PG_COST_PARAMS),
    ("io", "hybench_xp"): dict(DEFAULT_PG_COST_PARAMS),
    ("none", "hybench_xp"): dict(DEFAULT_PG_COST_PARAMS),
}
BONECT_ROUGH_ANCHORS = {
    ("cpu", "hybench_xp"): [
        {
            "name": "phase1_cpu_primary_accepted_default_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "phase-1 accepted CPU default-anchor candidate: DB QPS +5.94%, "
                "resource direction accepted; replay in phase-2 before trusting "
                "current estimator rejection"
            ),
            "phase2_known_real_db_qps_delta_pct": 5.94,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 0.8,
                "random_page_cost": 3.626666666666666,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.003000000000000002,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        },
        {
            "name": "cpu_memory_lift_parallel_conservative",
            "params": {
                "seq_page_cost": 1.35,
                "random_page_cost": 4.0,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1350.0,
                "parallel_tuple_cost": 0.14,
            },
        },
        {
            "name": "cpu_memory_lift_parallel_strong",
            "params": {
                "seq_page_cost": 1.75,
                "random_page_cost": 4.0,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1800.0,
                "parallel_tuple_cost": 0.18,
            },
        },
        {
            "name": "cpu_memory_lift_parallel_io_shift",
            "params": {
                "seq_page_cost": 1.9,
                "random_page_cost": 4.35,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1700.0,
                "parallel_tuple_cost": 0.16,
            },
        },
        {
            "name": "phase2_v59_cpu_resource_ok_db_down_tradeoff",
            "params": {
                "seq_page_cost": 1.0139269500000003,
                "random_page_cost": 4.028493375000003,
                "cpu_tuple_cost": 0.010200000000000004,
                "cpu_index_tuple_cost": 0.0041,
                "cpu_operator_cost": 0.0018750000000000008,
                "parallel_setup_cost": 1025.0,
                "parallel_tuple_cost": 0.099617075,
            },
        },
        {
            "name": "phase2_v59_cpu_weak_primary_accepted_tradeoff",
            "params": {
                "seq_page_cost": 1.0139269500000003,
                "random_page_cost": 4.028493375000003,
                "cpu_tuple_cost": 0.010200000000000004,
                "cpu_index_tuple_cost": 0.0039000000000000007,
                "cpu_operator_cost": 0.0018750000000000008,
                "parallel_setup_cost": 1025.0,
                "parallel_tuple_cost": 0.099617075,
            },
        },
        {
            "name": "phase2_v44_cpu_dbqps_amp_goal_hit",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "phase-2 v44 CPU best-so-far: DB QPS +60.47%, "
                "CPU -7.32pp, DB/resource targets met; use as the "
                "high-DB-QPS anchor alongside the v80 high-resource-drop point"
            ),
            "phase2_known_real_db_qps_delta_pct": 60.468492785503834,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 1.713926950000001,
                "random_page_cost": 3.9284933750000026,
                "cpu_tuple_cost": 0.009700000000000004,
                "cpu_index_tuple_cost": 0.0039000000000000007,
                "cpu_operator_cost": 0.0018750000000000008,
                "parallel_setup_cost": 1025.0,
                "parallel_tuple_cost": 0.109617075,
            },
        },
        {
            "name": "phase2_v80_cpu_dbqps_amp_goal_hit",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "phase-2 v80 feedback round 3 best-so-far: DB QPS +14.82%, "
                "CPU -30.49pp, initial-baseline resource direction accepted, "
                "plan_change_count=5"
            ),
            "phase2_known_real_db_qps_delta_pct": 14.8152,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 2.4749999999999988,
                "random_page_cost": 1.7929100416666675,
                "cpu_tuple_cost": 0.0012,
                "cpu_index_tuple_cost": 0.004799999999999999,
                "cpu_operator_cost": 0.0016593749999999977,
                "parallel_setup_cost": 1040.0,
                "parallel_tuple_cost": 0.11901396812499998,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r01_c118_high_dbqps_weak_relief_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r01_c118_RRRLRLR: DB QPS +11.93%, CPU -0.05pp, "
                "XP QPS -4.64%, XP TPS +12.06%; keep as a high-DB-QPS weak-relief "
                "anchor so phase-2 can learn the DB/resource tradeoff, but do not "
                "treat it as a final resource-improvement hit"
            ),
            "phase2_known_real_db_qps_delta_pct": 11.925316794822777,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 75.0,
                "random_page_cost": 15.0,
                "cpu_tuple_cost": 0.75,
                "cpu_index_tuple_cost": 0.250001,
                "cpu_operator_cost": 0.75,
                "parallel_setup_cost": 2500.0,
                "parallel_tuple_cost": 7.5,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r02_c024_high_dbqps_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r02_c024_LLRLRRR: DB QPS +15.92%, CPU -1.62pp, "
                "XP QPS +5.96%, XP TPS +18.75%; keep as a phase-2 CPU "
                "anchor/replay seed, with Bonect real replay still the final arbiter"
            ),
            "phase2_known_real_db_qps_delta_pct": 15.91886447474003,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 62.5,
                "random_page_cost": 12.5,
                "cpu_tuple_cost": 0.875,
                "cpu_index_tuple_cost": 0.125001,
                "cpu_operator_cost": 0.875,
                "parallel_setup_cost": 3750.0,
                "parallel_tuple_cost": 8.75,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r02_c079_resource_drop_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r02_c079_RLLRRRL: DB QPS +2.22%, CPU -8.28pp, "
                "XP QPS +3.31%, XP TPS +17.27%; keep as a resource-drop anchor "
                "to preserve the lower-CPU region, while Bonect real replay remains "
                "the final DB-QPS arbiter"
            ),
            "phase2_known_real_db_qps_delta_pct": 2.2176568888517396,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 87.5,
                "random_page_cost": 12.5,
                "cpu_tuple_cost": 0.625,
                "cpu_index_tuple_cost": 0.375001,
                "cpu_operator_cost": 0.875,
                "parallel_setup_cost": 3750.0,
                "parallel_tuple_cost": 6.25,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r02_c080_balanced_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r02_c080_RLLRRRR: DB QPS +12.63%, CPU -2.72pp, "
                "XP QPS +4.64%, XP TPS +16.81%; use as the current external "
                "balanced DB/resource anchor for CPU phase-2 search"
            ),
            "phase2_known_real_db_qps_delta_pct": 12.62702558706848,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 87.5,
                "random_page_cost": 12.5,
                "cpu_tuple_cost": 0.625,
                "cpu_index_tuple_cost": 0.375001,
                "cpu_operator_cost": 0.875,
                "parallel_setup_cost": 3750.0,
                "parallel_tuple_cost": 8.75,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r03_c112_dbqps_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r03_c112_RRLRRRR: DB QPS +10.50%, CPU -1.16pp, "
                "XP QPS -0.66%, XP TPS +12.77%; keep as a high-DB-QPS CPU "
                "anchor, but do not treat it as a final resource-improvement hit "
                "because CPU relief is below the phase-2 secondary 2pp target"
            ),
            "phase2_known_real_db_qps_delta_pct": 10.504164946104495,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 93.75,
                "random_page_cost": 18.75,
                "cpu_tuple_cost": 0.8125,
                "cpu_index_tuple_cost": 0.437501,
                "cpu_operator_cost": 0.6875,
                "parallel_setup_cost": 4375.0,
                "parallel_tuple_cost": 6.875,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r03_c120_balanced_near_target_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r03_c120_RRRLRRR: DB QPS +9.89%, CPU -2.12pp, "
                "XP QPS +4.64%, XP TPS +12.98%; keep as a balanced near-target "
                "CPU phase-2 anchor because it satisfies the secondary resource "
                "relief target and is just below the preferred +10% DB-QPS target"
            ),
            "phase2_known_real_db_qps_delta_pct": 9.889732470696707,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 93.75,
                "random_page_cost": 18.75,
                "cpu_tuple_cost": 0.9375,
                "cpu_index_tuple_cost": 0.312501,
                "cpu_operator_cost": 0.6875,
                "parallel_setup_cost": 4375.0,
                "parallel_tuple_cost": 6.875,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r02_c123_resource_drop_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r02_c123_RRRRLRL: DB QPS +3.99%, CPU -18.08pp, "
                "XP QPS +8.61%, XP TPS +22.79%; use as a CPU resource-drop "
                "anchor so binary/path search keeps the strong bottleneck relief region"
            ),
            "phase2_known_real_db_qps_delta_pct": 3.9911733225941033,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 87.5,
                "random_page_cost": 17.5,
                "cpu_tuple_cost": 0.875,
                "cpu_index_tuple_cost": 0.375001,
                "cpu_operator_cost": 0.625,
                "parallel_setup_cost": 3750.0,
                "parallel_tuple_cost": 6.25,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r04_c025_high_dbqps_weak_relief_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r04_c025_LLRRLLL: DB QPS +14.80%, CPU -0.09pp, "
                "XP QPS +3.97%, XP TPS +17.12%; keep as a high-DB-QPS "
                "weak-relief boundary anchor for binary/path search, while "
                "Bonect real replay remains the final arbiter"
            ),
            "phase2_known_real_db_qps_delta_pct": 14.797722918442185,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 78.125,
                "random_page_cost": 15.625,
                "cpu_tuple_cost": 0.84375,
                "cpu_index_tuple_cost": 0.343751,
                "cpu_operator_cost": 0.53125,
                "parallel_setup_cost": 2812.5,
                "parallel_tuple_cost": 5.3125,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r04_c026_high_dbqps_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r04_c026_LLRRLLR: DB QPS +14.07%, CPU -0.85pp, "
                "XP QPS -5.30%, XP TPS +14.41%; keep as a high-DB-QPS "
                "CPU-drop anchor near the round-4 boundary"
            ),
            "phase2_known_real_db_qps_delta_pct": 14.073285392619331,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 78.125,
                "random_page_cost": 15.625,
                "cpu_tuple_cost": 0.84375,
                "cpu_index_tuple_cost": 0.343751,
                "cpu_operator_cost": 0.53125,
                "parallel_setup_cost": 2812.5,
                "parallel_tuple_cost": 5.9375,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r04_c027_current_best_dbqps_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r04_c027_LLRRLRL: DB QPS +15.77%, CPU -0.88pp, "
                "XP QPS +3.97%, XP TPS +17.12%; current round-4 highest "
                "accepted DB-QPS CPU-drop point and a priority replay/anchor seed"
            ),
            "phase2_known_real_db_qps_delta_pct": 15.769646374547621,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 78.125,
                "random_page_cost": 15.625,
                "cpu_tuple_cost": 0.84375,
                "cpu_index_tuple_cost": 0.343751,
                "cpu_operator_cost": 0.53125,
                "parallel_setup_cost": 3437.5,
                "parallel_tuple_cost": 5.3125,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r04_c029_balanced_boundary_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r04_c029_LLRRRLL: DB QPS +14.09%, CPU -1.49pp, "
                "XP QPS +1.99%, XP TPS +16.10%; keep as a balanced boundary "
                "anchor, close to the secondary 2pp CPU-relief target"
            ),
            "phase2_known_real_db_qps_delta_pct": 14.093678603132275,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 78.125,
                "random_page_cost": 15.625,
                "cpu_tuple_cost": 0.84375,
                "cpu_index_tuple_cost": 0.343751,
                "cpu_operator_cost": 0.59375,
                "parallel_setup_cost": 2812.5,
                "parallel_tuple_cost": 5.3125,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r04_c037_weak_relief_boundary_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r04_c037_LRLLRLL: DB QPS +8.66%, CPU -0.08pp, "
                "XP QPS -5.30%, XP TPS +9.25%; keep as a weak-relief "
                "boundary anchor so the binary/path search preserves the "
                "transition between high-DB-QPS CPU-up rejects and CPU-down "
                "accepted points"
            ),
            "phase2_known_real_db_qps_delta_pct": 8.658449954582057,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 78.125,
                "random_page_cost": 16.875,
                "cpu_tuple_cost": 0.78125,
                "cpu_index_tuple_cost": 0.281251,
                "cpu_operator_cost": 0.59375,
                "parallel_setup_cost": 2812.5,
                "parallel_tuple_cost": 5.3125,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r04_c043_weak_relief_boundary_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r04_c043_LRLRLRL: DB QPS +9.15%, CPU -0.12pp, "
                "XP QPS -3.97%, XP TPS +9.81%; keep as a weak-relief "
                "boundary anchor near the preferred +10% DB-QPS target so "
                "binary/path search can interpolate toward stronger CPU relief"
            ),
            "phase2_known_real_db_qps_delta_pct": 9.15094866237552,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 78.125,
                "random_page_cost": 16.875,
                "cpu_tuple_cost": 0.78125,
                "cpu_index_tuple_cost": 0.343751,
                "cpu_operator_cost": 0.53125,
                "parallel_setup_cost": 3437.5,
                "parallel_tuple_cost": 5.3125,
            },
        },
        {
            "name": "external_binary_hyxp_cpu_r04_c120_high_dbqps_weak_relief_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "cpu",
            "phase2_known_real_source": (
                "external binary-plan HyBenchXP CPU accepted rough-validation "
                "candidate cpu_r04_c120_RRRLRRR: DB QPS +12.09%, CPU -0.16pp, "
                "XP QPS +1.32%, XP TPS +12.06%; keep as a high-DB-QPS "
                "weak-relief anchor on the round-4 RRRLRRR branch for "
                "binary/path search, while Bonect real replay remains the "
                "final arbiter"
            ),
            "phase2_known_real_db_qps_delta_pct": 12.087158866277314,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 84.375,
                "random_page_cost": 16.875,
                "cpu_tuple_cost": 0.84375,
                "cpu_index_tuple_cost": 0.281251,
                "cpu_operator_cost": 0.59375,
                "parallel_setup_cost": 3437.5,
                "parallel_tuple_cost": 5.9375,
            },
        },
        {
            "name": "result_bonect_cpu_iter11_safe_operator_projection",
            "params": {
                "seq_page_cost": 1.880974,
                "random_page_cost": 1.986345,
                "cpu_tuple_cost": 0.00182,
                "cpu_index_tuple_cost": 0.004245,
                # Phase-2 real feedback found CPU/XP candidates below 0.0005
                # repeatedly lose DB QPS or raise CPU. Keep the rough anchor's
                # page/parallel direction but project operator cost to the safe
                # floor so anchor/path generation does not spend top-k budget in
                # the rejected low-operator region.
                "cpu_operator_cost": 0.0010,
                "parallel_setup_cost": 1314.633528,
                "parallel_tuple_cost": 0.238439,
            },
        }
    ],
    ("memory", "hybench_ap"): [
        {
            "name": "phase1_memory_primary_accepted_default_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "memory",
            "phase2_known_real_source": (
                "phase-1 accepted Memory default-anchor candidate: DB QPS +3.88%, "
                "Memory decreased but phase-2 improvement was weak; keep only as "
                "a replay-admission seed and do not treat it as phase-2 success"
            ),
            "phase2_known_real_db_qps_delta_pct": 3.88,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 0.8,
                "random_page_cost": 3.2,
                "cpu_tuple_cost": 0.008,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.003000000000000002,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        },
        {
            "name": "phase2_v105_memory_dbqps_amp_goal_hit",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "memory",
            "phase2_known_real_source": (
                "phase-2 v105 memory best-so-far: DB QPS +96.16%, "
                "Memory -6.22pp, IO +0.12pp, plan_change_count=6; "
                "use as replay-admission seed when the resource estimator "
                "rounds the non-target IO uplift to zero"
            ),
            "phase2_known_real_db_qps_delta_pct": 96.16313044937645,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 0.82,
                "random_page_cost": 2.968741846428572,
                "cpu_tuple_cost": 0.007490885714285716,
                "cpu_index_tuple_cost": 0.005028571428571429,
                "cpu_operator_cost": 0.0031501000000000003,
                "parallel_setup_cost": 1007.8478979000001,
                "parallel_tuple_cost": 0.10200000000000002,
            },
        },
        {
            "name": "phase2_v111_memory_dbqps_amp_goal_hit",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "memory",
            "phase2_known_real_source": (
                "phase-2 v111 memory best-so-far before launcher failure: "
                "DB QPS +112.72%, Memory -6.34pp, resource target met; "
                "use as a replay-admission seed and anchor/path expansion point"
            ),
            "phase2_known_real_db_qps_delta_pct": 112.71992416483778,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 1.0,
                "random_page_cost": 2.6444215434210525,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.00367495,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        },
        {
            "name": "phase2_v109_memory_resource_stronger_goal_hit",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "memory",
            "phase2_known_real_source": (
                "phase-2 v109 memory best-so-far: DB QPS +112.34%, "
                "Memory -7.49pp, DB/resource targets met; use as a "
                "resource-strong peer to the v111 max-DB-QPS anchor"
            ),
            "phase2_known_real_db_qps_delta_pct": 112.33680262460624,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 1.0,
                "random_page_cost": 2.548599635714286,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0036749500000000006,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        },
        {
            "name": "phase2_v123_memory_cpu_up_weak_primary_accepted",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "memory",
            "phase2_known_real_other_resource_up": ["cpu"],
            "phase2_known_real_source": (
                "phase-2 v123 Memory/AP weak primary accepted: DB QPS +0.138%, "
                "Memory -0.56pp, CPU +1.66pp, IO 0. Use as a CPU-up replay "
                "calibration seed because Memory/AP IO is flat and must not be "
                "counted as the other-resource-up signal."
            ),
            "phase2_known_real_db_qps_delta_pct": 0.13843766032846094,
            "phase2_known_real_prefix_goal_passed": True,
            "phase2_known_real_target_delta_pp": -0.5592716379576473,
            "phase2_known_real_other_delta_pp": 1.6617750052635758,
            "params": {
                "seq_page_cost": 1.0,
                "random_page_cost": 4.0,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0037689968421052646,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        },
        {
            "name": "result_bonect_mem_iter13_strict_good",
            "params": {
                "seq_page_cost": 0.84803,
                "random_page_cost": 2.811719,
                "cpu_tuple_cost": 0.003556,
                "cpu_index_tuple_cost": 0.005423,
                "cpu_operator_cost": 0.000001,
                "parallel_setup_cost": 1112.589292,
                "parallel_tuple_cost": 0.068187,
            },
        }
    ],
    ("io", "hybench_xp"): [
        {
            "name": "phase1_io_primary_accepted_default_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "io",
            "phase2_known_real_source": (
                "phase-1 accepted IO default-anchor candidate: DB QPS +1.33%, "
                "IO decreased but remained high; keep only as a replay-admission "
                "seed because phase-2 still needs stronger DB QPS/resource gains"
            ),
            "phase2_known_real_db_qps_delta_pct": 1.33,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 1.0,
                "random_page_cost": 4.053333333333332,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        },
        {
            "name": "result_bonect_io_iter16_strict_good",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "io",
            "phase2_known_real_source": (
                "result/bonect/io.md coarse iteration 16: DB QPS +16.1% "
                "versus the coarse default row, IO total fell by about 3.66pp "
                "while CPU and Memory rose; old score rejected it, but phase-2 "
                "DBQPS/resource semantics make it a valid replay-admission seed"
            ),
            "phase2_known_real_db_qps_delta_pct": 16.08,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 2.0,
                "random_page_cost": 16.378542,
                "cpu_tuple_cost": 0.003285,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.000874,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.08,
            },
        },
        {
            "name": "result_bonect_io_finetune_iter04_dbqps_io_goal_hit",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "io",
            "phase2_known_real_source": (
                "result/bonect/io.md fine-tune iteration 4: DB QPS +31.91% "
                "versus the fine-tune baseline, IO fell by about 2.16pp, and "
                "CPU/Memory rose; use as a stronger IO phase-2 anchor/path seed"
            ),
            "phase2_known_real_db_qps_delta_pct": 31.91,
            "phase2_known_real_prefix_goal_passed": True,
            "params": {
                "seq_page_cost": 2.301714,
                "random_page_cost": 13.744121,
                "cpu_tuple_cost": 0.003771,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.001035,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.094227,
            },
        }
    ],
    ("none", "hybench_xp"): [
        {
            "name": "phase1_none_primary_accepted_default_anchor",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "none",
            "phase2_known_real_source": (
                "phase-1 accepted None default-anchor candidate: DB QPS +9.76%, "
                "CPU/Memory/IO rose overall, with XP QPS risk; keep only as a "
                "replay-admission seed for phase-2 None amplification"
            ),
            "phase2_known_real_db_qps_delta_pct": 9.76,
            "phase2_known_real_prefix_goal_passed": False,
            "params": {
                "seq_page_cost": 0.8,
                "random_page_cost": 3.6363636363636367,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0030000000000000005,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        },
        {
            "name": "phase2_none_v59_cand24_dbqps_fallback_20260710_055902",
            "phase2_known_real_direction_seed": True,
            "phase2_known_real_target_resource": "none",
            "phase2_known_real_source": (
                "phase-2 v59 cand24 paired fixed-snapshot replay: DB QPS "
                "+6.014%, CPU +1.31%, Memory +1.04%, IO +6.74%, with six "
                "plan changes and IO utilization 30.037%. Preserve it as a "
                "forced explainable fallback/plan-divergence anchor; it does "
                "not meet the +15% long-term DB-QPS target."
            ),
            "phase2_known_real_db_qps_delta_pct": 6.01432,
            "phase2_known_real_prefix_goal_passed": False,
            "params": {
                "seq_page_cost": 1.5856250000000003,
                "random_page_cost": 4.0,
                "cpu_tuple_cost": 0.01000075,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0039377125,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.00001,
            },
        },
        {
            "name": "robust_v5_none_low_random_extra_label_rank1",
            "params": {
                "seq_page_cost": 0.1,
                "random_page_cost": 0.1,
                "cpu_tuple_cost": 0.00562341,
                "cpu_index_tuple_cost": 0.05,
                "cpu_operator_cost": 0.0000149535,
                "parallel_setup_cost": 10.0,
                "parallel_tuple_cost": 0.0001,
            },
        },
        {
            "name": "robust_v5_none_estimator_top1",
            "params": {
                "seq_page_cost": 1.0,
                "random_page_cost": 3.16228,
                "cpu_tuple_cost": 0.1,
                "cpu_index_tuple_cost": 0.05,
                "cpu_operator_cost": 0.000223607,
                "parallel_setup_cost": 10.0,
                "parallel_tuple_cost": 0.0001,
            },
        },
        {
            "name": "robust_v5_none_estimator_top2_path_anchor",
            "params": {
                "seq_page_cost": 0.316228,
                "random_page_cost": 3.16228,
                "cpu_tuple_cost": 0.1,
                "cpu_index_tuple_cost": 0.05,
                "cpu_operator_cost": 0.000223607,
                "parallel_setup_cost": 10.0,
                "parallel_tuple_cost": 0.0001,
            },
        },
    ],
}
BONECT_FORCE_REPLAY_ANCHORS = {
    ("cpu", "hybench_xp"): [
        {
            "name": "cpu_active_grid_cand3_explainable_best_20260704_0340",
            "source": "cpu_active_grid_cand3_explainable_best_20260704_0340",
            "accept_reason": "primary_db_qps_candidate_accepted",
            "db_qps": 18.875502836911497,
            "db_qps_delta_pct": 24.99617878168483,
            "optimized_params": {
                "seq_page_cost": 0.05,
                "random_page_cost": 24.693541562993424,
                "cpu_tuple_cost": 0.004821052631578946,
                "cpu_index_tuple_cost": 0.009211263157894735,
                "cpu_operator_cost": 0.005342105263157894,
                "parallel_setup_cost": 4263.980263157895,
                "parallel_tuple_cost": 0.1,
            },
            "resource": {
                "cpu": 1.6459038058720694,
                "memory": 2.597131880502852,
                "io": 4.330346758704293,
            },
            "primary_target_status": {
                "baseline_db_qps": 15.100863899110847,
                "candidate_db_qps": 18.875502836911497,
                "db_qps_delta_pct": 24.99617878168483,
                "target_db_qps_delta_pct": 10.0,
                "db_qps_target_met": True,
                "bottleneck_resource": "cpu",
                "bottleneck_resource_delta_pp": -9.42127041865852,
                "bottleneck_resource_improvement_pp": 9.42127041865852,
                "other_resource_up": {
                    "memory": False,
                    "io": True,
                },
                "explainable_under_current_goal": True,
            },
        },
        {
            "name": "cpu_active_grid_cand13_explainable_dbqps_best_20260704_0504",
            "source": "cpu_active_grid_cand13_explainable_dbqps_best_20260704_0504",
            "accept_reason": "primary_db_qps_candidate_accepted",
            "db_qps": 22.91372926289244,
            "db_qps_delta_pct": 51.73787942726993,
            "optimized_params": {
                "seq_page_cost": 1.0,
                "random_page_cost": 0.05,
                "cpu_tuple_cost": 0.0555157894736842,
                "cpu_index_tuple_cost": 0.009211263157894735,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 4263.980263157895,
                "parallel_tuple_cost": 0.1,
            },
            "resource": {
                "cpu": 1.5995823868589765,
                "memory": 2.8993696413542094,
                "io": 0.6085450055757867,
            },
            "primary_target_status": {
                "baseline_db_qps": 15.100863899110847,
                "candidate_db_qps": 22.91372926289244,
                "db_qps_delta_pct": 51.73787942726993,
                "target_db_qps_delta_pct": 10.0,
                "db_qps_target_met": True,
                "bottleneck_resource": "cpu",
                "bottleneck_resource_delta_pp": -11.65116725428328,
                "bottleneck_resource_improvement_pp": 11.65116725428328,
                "other_resource_up": {
                    "memory": True,
                    "io": False,
                },
                "explainable_under_current_goal": True,
            },
        },
    ],
}


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable (override §577-579).

    ``bool(os.getenv(name, ""))`` would misjudge non-empty strings like ``"0"`` or
    ``"false"`` as ``True``. This helper uses the same truthy/falsy vocabulary as
    ``parameter_optimizer._to_bool`` so ``ADAPTIVE_REPLAY_CONTROLLER_ENABLED=0``
    resolves to ``False``.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return default


def _force_replay_anchor_param_signature(record: Dict[str, Any]) -> Tuple[Tuple[str, float], ...]:
    raw_params = record.get("optimized_params")
    if not isinstance(raw_params, dict):
        raw_params = record.get("params")
    if not isinstance(raw_params, dict):
        return tuple()
    signature: List[Tuple[str, float]] = []
    for name in COST_PARAM_KEYS:
        if name not in raw_params:
            return tuple()
        try:
            signature.append((name, round(float(raw_params[name]), 12)))
        except (TypeError, ValueError):
            return tuple()
    return tuple(signature)


def _normalize_forced_replay_anchor_record(
    record: Dict[str, Any],
    *,
    source_path: Path,
    index: int,
) -> Dict[str, Any]:
    raw_params = record.get("optimized_params")
    if not isinstance(raw_params, dict):
        raw_params = record.get("params")
    if not isinstance(raw_params, dict):
        raise RuntimeError(
            "BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON record "
            f"#{index} in {source_path!s} must contain optimized_params or params"
        )
    param_keys = set(str(name) for name in raw_params.keys())
    if param_keys != COST_PARAM_SET:
        extra = sorted(param_keys - COST_PARAM_SET)
        missing = sorted(COST_PARAM_SET - param_keys)
        raise RuntimeError(
            "BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON record "
            f"#{index} in {source_path!s} must contain exactly the 7 cost params; "
            f"missing={missing}, extra={extra}"
        )
    normalized_params: Dict[str, float] = {}
    for name in COST_PARAM_KEYS:
        try:
            normalized_params[name] = float(raw_params[name])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON record "
                f"#{index} in {source_path!s} has non-numeric {name!r}"
            ) from exc
    normalized = copy.deepcopy(record)
    normalized["optimized_params"] = normalized_params
    if "params" in normalized:
        normalized["params"] = copy.deepcopy(normalized_params)
    normalized.setdefault("name", f"external_forced_replay_anchor_{index}")
    normalized.setdefault("source", f"external_seed_json:{source_path!s}#{index}")
    normalized.setdefault("accept_reason", "external_forced_replay_anchor_seed")
    normalized["external_forced_replay_anchor_seed_path"] = str(source_path)
    normalized["external_forced_replay_anchor_seed_index"] = index
    return normalized


def _load_external_forced_replay_anchor_seed_records(
    bottleneck: str,
    benchmark: str,
) -> List[Dict[str, Any]]:
    raw_path = str(os.getenv("BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON") or "").strip()
    if not raw_path:
        return []
    seed_path = Path(raw_path).expanduser()
    if not seed_path.is_absolute():
        seed_path = PROJECT_ROOT / seed_path
    if not seed_path.is_file():
        raise RuntimeError(
            "BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON points to a missing file: "
            f"{seed_path!s}"
        )
    try:
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            "Failed to parse BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON "
            f"{seed_path!s}: {exc}"
        ) from exc

    records: Any
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        scenario_keys = (
            f"{bottleneck}/{benchmark}",
            f"{bottleneck}:{benchmark}",
            f"{bottleneck}_{benchmark}",
            bottleneck,
        )
        records = None
        for key in scenario_keys:
            value = payload.get(key)
            if isinstance(value, list):
                records = value
                break
        if records is None:
            for key in ("force_best_replay_anchor_seed_records", "anchors", "records"):
                value = payload.get(key)
                if isinstance(value, list):
                    records = value
                    break
        if records is None:
            raise RuntimeError(
                "BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON object must contain a list "
                "under force_best_replay_anchor_seed_records, anchors, records, "
                f"or a scenario key for {bottleneck}/{benchmark}"
            )
    else:
        raise RuntimeError(
            "BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON must be a JSON list or object"
        )

    normalized_records: List[Dict[str, Any]] = []
    for idx, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                "BONECT_FORCE_REPLAY_ANCHOR_SEED_JSON record "
                f"#{idx} in {seed_path!s} must be an object"
            )
        normalized_records.append(
            _normalize_forced_replay_anchor_record(
                item,
                source_path=seed_path,
                index=idx,
            )
        )
    return normalized_records


def bonect_forced_replay_anchors(
    bottleneck: str,
    benchmark: str,
) -> List[Dict[str, object]]:
    """Return explicit best anchors or derive them from known-real scenario seeds."""
    key = (bottleneck, benchmark)
    base_records: List[Dict[str, object]] = []
    explicit = BONECT_FORCE_REPLAY_ANCHORS.get(key)
    if explicit:
        base_records = copy.deepcopy(explicit)
    else:
        base_records = []
    derived: List[Dict[str, object]] = []
    target_db_qps_delta_pct = 15.0
    if not base_records:
        for anchor in BONECT_ROUGH_ANCHORS.get(key, []):
            if not anchor.get("phase2_known_real_direction_seed"):
                continue
            params = anchor.get("params")
            if not isinstance(params, dict) or set(params) != COST_PARAM_SET:
                continue
            name = str(anchor.get("name") or "known_real_anchor")
            db_qps_delta_pct = anchor.get("phase2_known_real_db_qps_delta_pct")
            derived.append(
                {
                    "name": name,
                    "source": f"known_real:{name}",
                    "accept_reason": "historical_known_real_replay_seed",
                    "db_qps_delta_pct": db_qps_delta_pct,
                    "optimized_params": copy.deepcopy(params),
                    "primary_target_status": {
                        "db_qps_delta_pct": db_qps_delta_pct,
                        "target_db_qps_delta_pct": target_db_qps_delta_pct,
                        "db_qps_target_met": (
                            float(db_qps_delta_pct) >= target_db_qps_delta_pct
                            if db_qps_delta_pct is not None
                            else None
                        ),
                        "bottleneck_resource": bottleneck,
                        "historical_known_real_direction_seed": True,
                    },
                }
            )
        base_records = derived

    external_records = _load_external_forced_replay_anchor_seed_records(
        bottleneck,
        benchmark,
    )
    merged: List[Dict[str, object]] = []
    seen: Set[Tuple[Tuple[str, float], ...]] = set()
    for record in list(external_records) + list(base_records):
        signature = _force_replay_anchor_param_signature(record)
        if not signature or signature in seen:
            continue
        seen.add(signature)
        merged.append(copy.deepcopy(record))
    return merged


DEFAULT_JOB_SQL_DIR = "/home/anonymous/dataset/pg_data/join-order-benchmark"
DEFAULT_JOB_DATA_DIR = "/home/anonymous/dataset/pg_data/jobdata"
DEFAULT_PYTHON_EXECUTABLE = "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
JOB_TABLES = [
    "aka_name",
    "aka_title",
    "cast_info",
    "char_name",
    "comp_cast_type",
    "company_name",
    "company_type",
    "complete_cast",
    "info_type",
    "keyword",
    "kind_type",
    "link_type",
    "movie_companies",
    "movie_info",
    "movie_info_idx",
    "movie_keyword",
    "movie_link",
    "name",
    "person_info",
    "role_type",
    "title",
]


@dataclass(frozen=True)
class PgConn:
    host: str
    port: int
    user: str
    password: str
    dbname: str

    def dsn(self, dbname: Optional[str] = None) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{dbname or self.dbname}"

    def jdbc_url(
        self,
        dbname: Optional[str] = None,
        *,
        statement_timeout_ms: int = 0,
        work_mem_mb: int = 0,
        max_parallel_workers_per_gather: int = -1,
    ) -> str:
        url = (
            f"jdbc:postgresql://{self.host}:{self.port}/{dbname or self.dbname}"
            "?sslmode=disable&ApplicationName=resource-adaptive-matrix&reWriteBatchedInserts=true"
        )
        session_options: List[str] = []
        if statement_timeout_ms > 0:
            session_options.append(
                f"-c%20statement_timeout%3D{int(statement_timeout_ms)}"
            )
        if work_mem_mb > 0:
            session_options.append(f"-c%20work_mem%3D{int(work_mem_mb)}MB")
        if max_parallel_workers_per_gather >= 0:
            session_options.append(
                "-c%20max_parallel_workers_per_gather%3D"
                f"{int(max_parallel_workers_per_gather)}"
            )
        if session_options:
            url += "&options=" + "%20".join(session_options)
        return url


@dataclass(frozen=True)
class CommandSpec:
    benchmark: str
    bottleneck: str
    command: List[str]
    cwd: Path
    env: Dict[str, str]
    log_path: Path
    pg_dbname: str
    runnable: bool = True
    reason: str = ""


@dataclass(frozen=True)
class PrepareSpec:
    name: str
    commands: List[List[str]]
    cwd: Path
    env: Dict[str, str]
    log_path: Path
    runnable: bool = True
    reason: str = ""


def parse_csv(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_bottlenecks(raw: str) -> List[str]:
    aliases = {
        "mem": "memory",
    }
    return [aliases.get(item, item) for item in parse_csv(raw)]


def run_quiet(command: Sequence[str], *, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def quote_cmd(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def shell_join(commands: Sequence[str]) -> str:
    return " && ".join(commands)


def natural_sort_key(path: Path) -> List[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name)]


def set_text(root: ET.Element, tag: str, value: str) -> None:
    elem = root.find(tag)
    if elem is None:
        elem = ET.SubElement(root, tag)
    elem.text = value


def update_benchbase_xml(
    *,
    template_path: Path,
    output_path: Path,
    benchmark: str,
    conn: PgConn,
    time_sec: int,
    terminals: int,
    scalefactor: float,
    work_mem_mb: int = 0,
    max_parallel_workers_per_gather: int = -1,
    weights: Optional[str] = None,
    serial: Optional[bool] = None,
) -> None:
    tree = ET.parse(template_path)
    root = tree.getroot()

    set_text(
        root,
        "url",
        conn.jdbc_url(
            work_mem_mb=work_mem_mb,
            max_parallel_workers_per_gather=max_parallel_workers_per_gather,
        ),
    )
    set_text(root, "username", conn.user)
    set_text(root, "password", conn.password)
    set_text(root, "type", "POSTGRES")
    set_text(root, "driver", "org.postgresql.Driver")
    set_text(root, "isolation", "TRANSACTION_READ_COMMITTED")
    set_text(root, "scalefactor", str(scalefactor))
    if weights:
        weights_elem = root.find(".//works/work/weights")
        if weights_elem is None:
            raise RuntimeError("BenchBase XML is missing works/work/weights")
        weights_elem.text = str(weights)
    if serial is not None:
        serial_elem = root.find(".//works/work/serial")
        if serial_elem is None:
            raise RuntimeError("BenchBase XML is missing works/work/serial")
        serial_elem.text = "true" if serial else "false"

    if benchmark == "chbenchmark":
        for elem in list(root.findall("terminals")):
            root.remove(elem)
        tpcc_terminals = ET.SubElement(root, "terminals")
        tpcc_terminals.set("bench", "tpcc")
        tpcc_terminals.text = str(terminals)
        ch_terminals = ET.SubElement(root, "terminals")
        ch_terminals.set("bench", "chbenchmark")
        ch_terminals.text = str(max(1, terminals // 4))
    else:
        set_text(root, "terminals", str(terminals))

    works = root.find("works")
    if works is not None:
        for work in works.findall("work"):
            time_tag = work.find("time")
            if serial is True and time_tag is not None:
                work.remove(time_tag)
                time_tag = None
            if time_tag is None and serial is False:
                time_tag = ET.SubElement(work, "time")
            if time_tag is not None:
                time_tag.text = str(time_sec)
            rate_tag = work.find("rate")
            if rate_tag is not None:
                rate_tag.text = "unlimited"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="UTF-8", xml_declaration=True)


def update_hybench_props(
    *,
    template_path: Path,
    output_path: Path,
    conn: PgConn,
    benchmark: str,
    run_mins: int,
    tp_clients: int,
    ap_clients: int,
    random_seed: int,
    ap_query_ids: Optional[str] = None,
    ap_query_weights: Optional[str] = None,
    ap_query_sequence: Optional[str] = None,
    ap_query_sequence_limit: Optional[str] = None,
    progress_windows: Optional[str] = None,
    statement_timeout_ms: int = 0,
    work_mem_mb: int = 0,
    max_parallel_workers_per_gather: int = -1,
    extra_props: Optional[Dict[str, str]] = None,
) -> None:
    lines = template_path.read_text(encoding="utf-8").splitlines()
    replacements = {
        "username": conn.user,
        "password": conn.password,
        "username_ap": conn.user,
        "password_ap": conn.password,
        "url": conn.jdbc_url(
            statement_timeout_ms=statement_timeout_ms,
            work_mem_mb=work_mem_mb,
            max_parallel_workers_per_gather=max_parallel_workers_per_gather,
        ),
        "url_tp": conn.jdbc_url(
            statement_timeout_ms=statement_timeout_ms,
            work_mem_mb=work_mem_mb,
            max_parallel_workers_per_gather=max_parallel_workers_per_gather,
        ),
        "url_ap": conn.jdbc_url(
            statement_timeout_ms=statement_timeout_ms,
            work_mem_mb=work_mem_mb,
            max_parallel_workers_per_gather=max_parallel_workers_per_gather,
        ),
        "random_seed": str(random_seed),
    }
    if benchmark == "hybench_ap":
        replacements.update({"apclient": str(ap_clients), "apRunMins": str(run_mins)})
    elif benchmark == "hybench_tp":
        replacements.update({"tpclient": str(tp_clients), "tpRunMins": str(run_mins)})
    else:
        replacements.update({"xtpclient": str(tp_clients), "xapclient": str(ap_clients), "xpRunMins": str(run_mins)})

    optional_ap_keys = {
        "progress_windows": progress_windows,
    }
    if benchmark == "hybench_ap":
        optional_ap_keys.update({
            "ap_query_ids": ap_query_ids,
            "ap_query_weights": ap_query_weights,
            "ap_query_sequence": ap_query_sequence,
            "ap_query_sequence_limit": ap_query_sequence_limit,
        })
    optional_ap_keys = {
        key: str(value).strip()
        for key, value in optional_ap_keys.items()
        if value is not None and str(value).strip()
    }
    explicit_extra_props = {
        str(key).strip(): str(value).strip()
        for key, value in (extra_props or {}).items()
        if str(key).strip() and str(value).strip()
    }
    forbidden_xp_ap_keys = {
        "ap_query_ids",
        "ap_query_weights",
        "ap_query_sequence",
        "ap_query_sequence_limit",
    }
    if benchmark == "hybench_xp" and forbidden_xp_ap_keys.intersection(explicit_extra_props):
        raise ValueError("native HyBench XP does not accept AP query-selection properties")

    seen = set()
    out_lines: List[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            out_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in replacements:
            out_lines.append(f"{key}={replacements[key]}")
            seen.add(key)
        elif benchmark == "hybench_xp" and key in forbidden_xp_ap_keys:
            continue
        elif key in optional_ap_keys:
            out_lines.append(f"{key}={optional_ap_keys[key]}")
            seen.add(key)
        elif key in explicit_extra_props:
            out_lines.append(f"{key}={explicit_extra_props[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in replacements.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    for key, value in optional_ap_keys.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    for key, value in explicit_extra_props.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def parse_hybench_statement_sql_file_overrides(
    raw_values: Sequence[str],
) -> Dict[str, Path]:
    """Parse repeatable SECTION=PATH HyBench statement overrides."""
    overrides: Dict[str, Path] = {}
    for raw in raw_values:
        section, sep, raw_path = str(raw).partition("=")
        section = section.strip()
        raw_path = raw_path.strip()
        if not sep or not section or not raw_path:
            raise ValueError(
                f"Invalid HyBench statement override {raw!r}; expected SECTION=PATH"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", section):
            raise ValueError(f"Invalid HyBench statement section {section!r}")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f"HyBench statement SQL file does not exist for {section}: {path}"
            )
        overrides[section] = path
    return overrides


def replace_hybench_statement_sql(toml_text: str, section: str, sql: str) -> str:
    """Replace one SQL field in the HyBench statement TOML."""
    if '"""' in sql:
        raise ValueError(f"HyBench SQL override for {section} contains triple quotes")
    section_pattern = re.compile(rf"(?m)^\[{re.escape(section)}\]\s*$")
    section_match = section_pattern.search(toml_text)
    if not section_match:
        raise ValueError(f"HyBench statement section not found: [{section}]")
    body_start = section_match.end()
    next_section = re.search(r"(?m)^\[[^\]]+\]\s*$", toml_text[body_start:])
    body_end = body_start + next_section.start() if next_section else len(toml_text)
    body = toml_text[body_start:body_end]
    sql_pattern = re.compile(
        r'(?ms)^sql\s*=\s*(?:""".*?"""|"(?:\\.|[^"\\])*"|\[.*?\])\s*'
    )
    replacement = f'sql = """{sql.strip()}"""\n'
    if sql_pattern.search(body):
        body = sql_pattern.sub(replacement, body, count=1)
    else:
        if body and not body.endswith("\n"):
            body += "\n"
        body += replacement
    return toml_text[:body_start] + body + toml_text[body_end:]


def build_hybench_statement_profile(
    *,
    base_path: Path,
    output_path: Path,
    raw_overrides: Sequence[str],
) -> Path:
    """Generate a run-local HyBench statement profile when SQL overrides exist."""
    overrides = parse_hybench_statement_sql_file_overrides(raw_overrides)
    if not overrides:
        return base_path
    text = base_path.read_text(encoding="utf-8")
    for section, path in overrides.items():
        text = replace_hybench_statement_sql(
            text,
            section,
            path.read_text(encoding="utf-8"),
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return output_path


def load_hybench_setup_sql(raw_paths: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Load setup SQL files and return SQL text plus resolved provenance paths."""
    sql_items: List[str] = []
    resolved_paths: List[str] = []
    for raw_path in raw_paths:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"HyBench setup SQL file does not exist: {path}")
        sql = path.read_text(encoding="utf-8").strip()
        if not sql:
            raise ValueError(f"HyBench setup SQL file is empty: {path}")
        sql_items.append(sql)
        resolved_paths.append(str(path))
    return sql_items, resolved_paths


def load_phase2_teacher_recovery_config(
    raw_path: str,
    bottleneck: str,
) -> Dict[str, Any]:
    """Load one scenario's answer-free teacher recovery audit configuration."""
    if not str(raw_path or "").strip():
        return {}
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Teacher recovery config does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Teacher recovery config must be a JSON object: {path}")
    scenarios = payload.get("scenarios")
    if isinstance(scenarios, dict):
        scenario = scenarios.get(str(bottleneck))
        if not isinstance(scenario, dict):
            raise ValueError(
                f"Teacher recovery config has no object for scenario {bottleneck!r}: {path}"
            )
        result = copy.deepcopy(scenario)
    else:
        result = copy.deepcopy(payload)
    result["config_path"] = str(path)
    return result


def bonect_hybench_clients(bottleneck: str, benchmark: str, args: argparse.Namespace) -> tuple[int, int]:
    if bottleneck == "cpu" and benchmark == "hybench_xp":
        return args.bonect_cpu_xp_tp_clients, args.bonect_cpu_xp_ap_clients
    if bottleneck == "memory" and benchmark == "hybench_ap":
        return 0, args.bonect_memory_ap_clients
    if bottleneck == "io" and benchmark == "hybench_xp":
        return args.bonect_io_xp_tp_clients, args.bonect_io_xp_ap_clients
    return args.hybench_tp_clients, args.hybench_ap_clients


def bonect_hybench_query_controls(
    bottleneck: str,
    benchmark: str,
    args: argparse.Namespace,
) -> Dict[str, Optional[str]]:
    if benchmark == "hybench_xp":
        return {
            "ap_query_ids": None,
            "ap_query_weights": None,
            "ap_query_sequence": None,
            "ap_query_sequence_limit": None,
            "progress_windows": args.hybench_progress_windows,
        }
    return {
        "ap_query_ids": args.hybench_ap_query_ids,
        "ap_query_weights": args.hybench_ap_query_weights,
        "ap_query_sequence": args.hybench_ap_query_sequence,
        "ap_query_sequence_limit": args.hybench_ap_query_sequence_limit,
        "progress_windows": args.hybench_progress_windows,
    }


def check_database(conn: PgConn, dbname: str) -> Optional[bool]:
    env = dict(os.environ)
    env.setdefault("PGCONNECT_TIMEOUT", "5")
    proc = run_quiet(
        ["psql", conn.dsn("postgres"), "-Atc", f"SELECT 1 FROM pg_database WHERE datname = '{dbname}' LIMIT 1;"],
        env=env,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() == "1"


def check_tcp(host: str, port: int, timeout_s: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def check_psql_ready(conn: PgConn, dbname: str = "postgres") -> bool:
    proc = run_quiet(
        [
            "psql",
            conn.dsn(dbname),
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT 1;",
        ]
    )
    return proc.returncode == 0


def ssh_command(args: argparse.Namespace, remote_command: str) -> List[str]:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(args.vm_runtime_ssh_port),
    ]
    if args.vm_runtime_ssh_identity_file:
        command.extend(["-i", args.vm_runtime_ssh_identity_file])
    command.extend([args.vm_runtime_ssh_target, f"bash -lc {shlex.quote(remote_command)}"])
    return command


def wait_for_ssh(args: argparse.Namespace) -> bool:
    deadline = time.time() + args.vm_ssh_ready_timeout_sec
    next_log = time.time()
    while time.time() < deadline:
        proc = run_quiet(ssh_command(args, "true"))
        if proc.returncode == 0:
            print(f"VM SSH ready: {args.vm_runtime_ssh_target}:{args.vm_runtime_ssh_port}", flush=True)
            return True
        if time.time() >= next_log:
            detail = (proc.stderr or proc.stdout or "").strip()
            print(f"Waiting for VM SSH {args.vm_runtime_ssh_target}:{args.vm_runtime_ssh_port}. {detail}", flush=True)
            next_log = time.time() + 30.0
        time.sleep(args.vm_ready_poll_sec)
    return False


def ensure_vm_running(args: argparse.Namespace) -> bool:
    proc = run_quiet(["sudo", "virsh", "domstate", args.vm_name], cwd=PROJECT_ROOT)
    state = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        print(f"FAIL vm-state/{args.vm_name}: {detail}", flush=True)
        return False
    if state == "running":
        return True
    if state in {"paused", "pmsuspended"}:
        print(f"VM {args.vm_name} state is {state}; forcing shut off before start", flush=True)
        destroy_proc = run_quiet(["sudo", "virsh", "destroy", args.vm_name], cwd=PROJECT_ROOT)
        if destroy_proc.returncode != 0:
            detail = (destroy_proc.stderr or destroy_proc.stdout or "").strip()
            print(f"FAIL vm-destroy/{args.vm_name}: {detail}", flush=True)
            return False
        time.sleep(2.0)
        state = "shut off"
    elif state != "shut off":
        print(f"VM {args.vm_name} state is {state}; forcing restart from current state", flush=True)
        destroy_proc = run_quiet(["sudo", "virsh", "destroy", args.vm_name], cwd=PROJECT_ROOT)
        if destroy_proc.returncode != 0:
            detail = (destroy_proc.stderr or destroy_proc.stdout or "").strip()
            print(f"FAIL vm-destroy/{args.vm_name}: {detail}", flush=True)
            return False
        time.sleep(2.0)
        state = "shut off"

    print(f"Starting VM {args.vm_name} before PostgreSQL readiness check", flush=True)
    start_proc = run_quiet(["sudo", "virsh", "start", args.vm_name], cwd=PROJECT_ROOT)
    if start_proc.returncode != 0:
        detail = (start_proc.stderr or start_proc.stdout or "").strip()
        print(f"FAIL vm-start/{args.vm_name}: {detail}", flush=True)
        return False
    return True


def wait_for_postgres(args: argparse.Namespace, conn: PgConn) -> bool:
    deadline = time.time() + args.pg_ready_timeout_sec
    next_log = time.time()
    while time.time() < deadline:
        if check_tcp(conn.host, conn.port, timeout_s=5.0) and check_psql_ready(conn, "postgres"):
            print(f"PostgreSQL ready: {conn.host}:{conn.port}", flush=True)
            return True
        if time.time() >= next_log:
            print(f"Waiting for PostgreSQL {conn.host}:{conn.port}", flush=True)
            next_log = time.time() + 30.0
        time.sleep(args.vm_ready_poll_sec)
    return False


def ensure_postgres_after_vm_profile(args: argparse.Namespace, conn: PgConn) -> bool:
    if not args.start_pg_after_vm_profile:
        return True
    if check_tcp(conn.host, conn.port, timeout_s=3.0) and check_psql_ready(conn, "postgres"):
        print(f"PostgreSQL already ready: {conn.host}:{conn.port}", flush=True)
        return True
    if not ensure_vm_running(args):
        return False
    if not wait_for_ssh(args):
        print(f"FAIL pg-start: VM SSH not ready after {args.vm_ssh_ready_timeout_sec}s", flush=True)
        return False

    print(f"Starting PostgreSQL via SSH: {args.vm_runtime_ssh_target}", flush=True)
    proc = run_quiet(
        ssh_command(args, args.pg_start_command),
        cwd=PROJECT_ROOT,
    )
    if proc.returncode != 0:
        print(f"FAIL pg-start: exit={proc.returncode}", flush=True)
        if proc.stdout.strip():
            print(proc.stdout.strip(), flush=True)
        if proc.stderr.strip():
            print(proc.stderr.strip(), flush=True)
        return False
    if proc.stdout.strip():
        print(proc.stdout.strip(), flush=True)
    return wait_for_postgres(args, conn)


def build_env(base: Dict[str, str], conn: PgConn) -> Dict[str, str]:
    env = dict(base)
    env.update(
        {
            "PGHOST": conn.host,
            "PGPORT": str(conn.port),
            "PGUSER": conn.user,
            "PGPASSWORD": conn.password,
            "PGDATABASE": conn.dbname,
        }
    )
    return env


def create_db_if_missing_command(conn: PgConn, dbname: str) -> List[str]:
    exists_sql = f"SELECT 1 FROM pg_database WHERE datname = '{dbname}' LIMIT 1;"
    create_sql = f"CREATE DATABASE {dbname} OWNER {conn.user};"
    check_cmd = (
        f"psql {shlex.quote(conn.dsn('postgres'))} -Atc "
        f"{shlex.quote(exists_sql)}"
    )
    create_cmd = (
        f"psql {shlex.quote(conn.dsn('postgres'))} -v ON_ERROR_STOP=1 -c "
        f"{shlex.quote(create_sql)}"
    )
    return ["bash", "-lc", f"set -euo pipefail; {check_cmd} | grep -qx 1 || {create_cmd}"]


def pg_stat_statements_extension_shell(conn: PgConn, dbname: str) -> str:
    sql = "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
    return (
        f"psql {shlex.quote(conn.dsn(dbname))} -v ON_ERROR_STOP=1 -c "
        f"{shlex.quote(sql)}"
    )


def db_exists_shell(conn: PgConn, dbname: str) -> str:
    exists_sql = f"SELECT 1 FROM pg_database WHERE datname = '{dbname}' LIMIT 1;"
    return (
        f"psql {shlex.quote(conn.dsn('postgres'))} -Atc "
        f"{shlex.quote(exists_sql)} | grep -qx 1"
    )


def terminate_db_shell(conn: PgConn, dbname: str) -> str:
    terminate_sql = (
        "SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        f"WHERE datname = '{dbname}' AND pid <> pg_backend_pid();"
    )
    return (
        f"psql {shlex.quote(conn.dsn('postgres'))} -v ON_ERROR_STOP=1 -c "
        f"{shlex.quote(terminate_sql)}"
    )


def drop_db_shell(conn: PgConn, dbname: str) -> str:
    return (
        f"psql {shlex.quote(conn.dsn('postgres'))} -v ON_ERROR_STOP=1 -c "
        f"{shlex.quote(f'DROP DATABASE IF EXISTS {dbname};')}"
    )


def create_db_shell(conn: PgConn, dbname: str) -> str:
    return (
        f"psql {shlex.quote(conn.dsn('postgres'))} -v ON_ERROR_STOP=1 -c "
        f"{shlex.quote(f'CREATE DATABASE {dbname} OWNER {conn.user};')}"
    )


def load_template_dbname(dbname: str) -> str:
    return f"template_{dbname}"


def create_db_from_template_shell(conn: PgConn, dbname: str, template_dbname: str) -> str:
    sql = f"CREATE DATABASE {dbname} WITH TEMPLATE {template_dbname} OWNER {conn.user};"
    return (
        f"psql {shlex.quote(conn.dsn('postgres'))} -v ON_ERROR_STOP=1 -c "
        f"{shlex.quote(sql)}"
    )


def refresh_template_shell(conn: PgConn, dbname: str, template_dbname: str) -> str:
    demote_sql = f"ALTER DATABASE {template_dbname} WITH IS_TEMPLATE false ALLOW_CONNECTIONS true;"
    promote_sql = f"ALTER DATABASE {template_dbname} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;"
    return shell_join([
        f"({terminate_db_shell(conn, template_dbname)} || true)",
        f"({drop_db_shell(conn, template_dbname)} || true)",
        terminate_db_shell(conn, dbname),
        create_db_from_template_shell(conn, template_dbname, dbname),
        (
            f"psql {shlex.quote(conn.dsn('postgres'))} -v ON_ERROR_STOP=1 -c "
            f"{shlex.quote(demote_sql)}"
        ),
        (
            f"psql {shlex.quote(conn.dsn('postgres'))} -v ON_ERROR_STOP=1 -c "
            f"{shlex.quote(promote_sql)}"
        ),
    ])


def database_missing_shell(conn: PgConn, dbname: str) -> str:
    exists_sql = f"SELECT 1 FROM pg_database WHERE datname = '{dbname}' LIMIT 1;"
    return (
        f"! psql {shlex.quote(conn.dsn('postgres'))} -Atc "
        f"{shlex.quote(exists_sql)} | grep -qx 1"
    )


def benchbase_load_command(benchbase_root: Path, benchmark: str, xml_path: Path, args: argparse.Namespace) -> List[str]:
    return [
        "java",
        f"-Xmx{args.java_xmx}",
        "-jar",
        "benchbase.jar",
        "-b",
        benchmark,
        "-c",
        str(xml_path),
        "--create=true",
        "--load=true",
    ]


def job_load_command(conn: PgConn, job_sql_dir: Path, job_data_dir: Path) -> List[str]:
    copy_commands = []
    for table in JOB_TABLES:
        csv_path = job_data_dir / f"{table}.csv"
        copy_commands.append(f"\\copy {table} FROM '{csv_path}' WITH (FORMAT csv, ESCAPE '\\')")
    load_sql = shell_join(
        [
            f"psql {shlex.quote(conn.dsn())} -v ON_ERROR_STOP=1 -f {shlex.quote(str(job_sql_dir / 'schema.sql'))}",
            *(
                f"psql {shlex.quote(conn.dsn())} -v ON_ERROR_STOP=1 -c {shlex.quote(copy_sql)}"
                for copy_sql in copy_commands
            ),
            f"psql {shlex.quote(conn.dsn())} -v ON_ERROR_STOP=1 -f {shlex.quote(str(job_sql_dir / 'fkindexes.sql'))}",
            f"psql {shlex.quote(conn.dsn())} -v ON_ERROR_STOP=1 -c 'ANALYZE;'",
        ]
    )
    return ["bash", "-lc", f"set -euo pipefail; {load_sql}"]


def conditional_load_command(
    *,
    conn: PgConn,
    dbname: str,
    load_commands: Sequence[str],
    force: bool,
    rebuild: bool = False,
    use_template: bool = True,
    refresh_template: bool = False,
) -> List[str]:
    template_dbname = load_template_dbname(dbname)
    create_cmd = create_db_shell(conn, dbname)
    terminate_cmd = terminate_db_shell(conn, dbname)
    drop_cmd = drop_db_shell(conn, dbname)
    ensure_extension_cmd = pg_stat_statements_extension_shell(conn, dbname)
    load_and_refresh_cmds = [
        create_cmd,
        ensure_extension_cmd,
        *load_commands,
        ensure_extension_cmd,
    ]
    if use_template:
        load_and_refresh_cmds.append(
            f"echo 'refreshing load template {template_dbname} from {dbname}'"
        )
        load_and_refresh_cmds.append(refresh_template_shell(conn, dbname, template_dbname))
    load_and_refresh_body = shell_join(load_and_refresh_cmds)
    restore_from_template_body = shell_join([
        f"echo 'restoring {dbname} from load template {template_dbname}'",
        terminate_cmd,
        drop_cmd,
        create_db_from_template_shell(conn, dbname, template_dbname),
        ensure_extension_cmd,
    ])
    if rebuild:
        reload_body = shell_join([terminate_cmd, drop_cmd, load_and_refresh_body])
        if use_template and not refresh_template:
            body = (
                f"if {db_exists_shell(conn, template_dbname)}; then "
                f"{restore_from_template_body}; "
                f"else {reload_body}; fi"
            )
        else:
            body = reload_body
    elif force:
        body = shell_join([create_db_if_missing_shell(conn, dbname), load_and_refresh_body])
    else:
        if use_template:
            existing_body = (
                f"echo 'database {dbname} exists; skip load'; "
                f"{ensure_extension_cmd}; "
                f"if {database_missing_shell(conn, template_dbname)}; then "
                f"echo 'creating missing load template {template_dbname} from existing {dbname}'; "
                f"{refresh_template_shell(conn, dbname, template_dbname)}; "
                f"else echo 'load template {template_dbname} exists'; fi"
            )
            missing_body = (
                f"if {db_exists_shell(conn, template_dbname)}; then "
                f"{restore_from_template_body}; "
                f"else {load_and_refresh_body}; fi"
            )
        else:
            existing_body = f"echo 'database {dbname} exists; skip load'; {ensure_extension_cmd}"
            missing_body = load_and_refresh_body
        body = f"if {database_missing_shell(conn, dbname)}; then {missing_body}; else {existing_body}; fi"
    return ["bash", "-lc", f"set -euo pipefail; {body}"]


def create_db_if_missing_shell(conn: PgConn, dbname: str) -> str:
    return quote_cmd(create_db_if_missing_command(conn, dbname))


def vm_profile_command(profile: str, args: argparse.Namespace) -> Optional[List[str]]:
    profiles = {
        "cpu": (args.vm_cpu_cpu, args.vm_cpu_mem, args.vm_cpu_read_mb, args.vm_cpu_write_mb),
        "memory": (args.vm_memory_cpu, args.vm_memory_mem, args.vm_memory_read_mb, args.vm_memory_write_mb),
        "io": (args.vm_io_cpu, args.vm_io_mem, args.vm_io_read_mb, args.vm_io_write_mb),
        "none": (args.vm_none_cpu, args.vm_none_mem, args.vm_none_read_mb, args.vm_none_write_mb),
    }
    if profile not in profiles:
        return None
    cpu_num, mem_size, read_mb, write_mb = profiles[profile]
    return [
        str(PROJECT_ROOT / "shell/set_vm_resource.sh"),
        args.vm_name,
        str(cpu_num),
        str(mem_size),
        str(read_mb),
        str(write_mb),
        args.vm_disk_dev,
    ]


def vm_profile_command_from_config(
    config_path: Optional[Path],
    args: argparse.Namespace,
) -> Optional[List[str]]:
    if config_path is None or not config_path.exists():
        return None
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    vm_resource = cfg.get("vm_resource")
    if not isinstance(vm_resource, dict):
        return None
    try:
        cpu_num = int(float(vm_resource["cpu"]))
        memory_gb = float(vm_resource["memory_gb"])
        read_mb = int(float(vm_resource["read_mb"]))
        write_mb = int(float(vm_resource["write_mb"]))
    except (KeyError, TypeError, ValueError):
        return None
    memory_arg = f"{int(memory_gb)}G" if memory_gb.is_integer() else f"{memory_gb:g}G"
    return [
        str(PROJECT_ROOT / "shell/set_vm_resource.sh"),
        str(vm_resource.get("vm_name") or args.vm_name),
        str(cpu_num),
        memory_arg,
        str(read_mb),
        str(write_mb),
        str(vm_resource.get("disk_dev") or args.vm_disk_dev),
    ]


def config_requires_vm_profile_reapply(config_path: Optional[Path]) -> bool:
    if config_path is None or not config_path.exists():
        return False
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    vm_resource = cfg.get("vm_resource")
    if not isinstance(vm_resource, dict):
        return False
    adaptive_source = vm_resource.get("adaptive_source")
    return bool(
        isinstance(adaptive_source, dict)
        and adaptive_source.get("requires_vm_profile_reapply")
    )


def parse_memory_gb(raw: str) -> float:
    text = str(raw).strip().lower()
    if not text:
        return 0.0
    multiplier = 1.0
    if text.endswith("g"):
        text = text[:-1]
    elif text.endswith("gb"):
        text = text[:-2]
    elif text.endswith("m"):
        text = text[:-1]
        multiplier = 1.0 / 1024.0
    elif text.endswith("mb"):
        text = text[:-2]
        multiplier = 1.0 / 1024.0
    return max(0.0, float(text) * multiplier)


def vm_profile_values(profile: str, args: argparse.Namespace) -> Dict[str, float]:
    profiles = {
        "cpu": (args.vm_cpu_cpu, args.vm_cpu_mem, args.vm_cpu_read_mb, args.vm_cpu_write_mb),
        "memory": (args.vm_memory_cpu, args.vm_memory_mem, args.vm_memory_read_mb, args.vm_memory_write_mb),
        "io": (args.vm_io_cpu, args.vm_io_mem, args.vm_io_read_mb, args.vm_io_write_mb),
        "none": (args.vm_none_cpu, args.vm_none_mem, args.vm_none_read_mb, args.vm_none_write_mb),
    }
    if profile not in profiles:
        raise ValueError(f"unknown bottleneck profile: {profile}")
    cpu_num, mem_size, read_mb, write_mb = profiles[profile]
    return {
        "cpu": float(cpu_num),
        "memory_gb": parse_memory_gb(str(mem_size)),
        "read_mb": float(read_mb),
        "write_mb": float(write_mb),
        "io": float(read_mb + write_mb),
    }


def build_bottleneck_profile(profile: str, args: argparse.Namespace) -> Dict[str, object]:
    values = vm_profile_values(profile, args)
    capability = {
        "cpu": max(0.1, values["cpu"] * args.capability_headroom),
        "memory": max(0.1, values["memory_gb"] * args.capability_headroom),
        "io": max(0.1, values["io"] * args.capability_headroom),
    }
    capability.update(parse_capability_overrides(getattr(args, "bonect_capability", [])))
    weights = {"cpu": 1.0, "memory": 1.0, "io": 1.0}
    reward_weights = {"cpu": 1.0, "memory": 1.0, "io": args.io_reward_weight}
    penalty_weights = {"cpu": 1.0, "memory": 1.0, "io": args.io_penalty_weight}
    if profile in {"cpu", "memory"}:
        weights[profile] = args.target_resource_weight
        reward_weights[profile] = args.target_resource_weight
        penalty_weights[profile] = args.target_resource_weight
    elif profile == "io":
        weights["io"] = args.target_resource_weight
        penalty_weights["io"] = args.target_resource_weight
    vm_resource = {
        "vm_name": args.vm_name,
        "cpu": int(values["cpu"]),
        "memory_gb": int(values["memory_gb"]) if values["memory_gb"].is_integer() else values["memory_gb"],
        "read_mb": int(values["read_mb"]),
        "write_mb": int(values["write_mb"]),
        "disk_dev": args.vm_disk_dev,
    }
    return {
        "target_resource": profile,
        "capability": capability,
        "resource_weights": weights,
        "resource_reward_weights": reward_weights,
        "resource_penalty_weights": penalty_weights,
        "vm_resource": vm_resource,
    }


def optimizer_workload_command(spec: CommandSpec) -> str:
    return f"cd {shlex.quote(str(spec.cwd))} && {quote_cmd(spec.command)}"


def bonect_initial_params(bottleneck: str, benchmark: str) -> Optional[Dict[str, float]]:
    params = BONECT_INITIAL_PARAMS.get((bottleneck, benchmark))
    return dict(params) if params else None


def bonect_feedback_rejected_neighborhoods(
    bottleneck: str,
    benchmark: str,
) -> List[Dict[str, object]]:
    path = BONECT_REJECTED_NEIGHBORHOODS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    records: object
    if isinstance(data, dict):
        records = data.get(f"{bottleneck}/{benchmark}", [])
    elif isinstance(data, list):
        records = data
    else:
        return []
    if not isinstance(records, list):
        return []
    filtered: List[Dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("bottleneck", bottleneck)) != bottleneck:
            continue
        if str(record.get("benchmark", benchmark)) != benchmark:
            continue
        params = record.get("params")
        if not isinstance(params, dict):
            continue
        active_params = {
            name: value
            for name, value in params.items()
            if name in COST_PARAM_SET
        }
        if len(active_params) != len(COST_PARAM_KEYS):
            continue
        item = dict(record)
        item["params"] = active_params
        filtered.append(item)
    return filtered


def bonect_near_hit_anchor_probe_config(
    bottleneck: str,
    benchmark: str,
) -> Dict[str, object]:
    if (bottleneck, benchmark) != ("memory", "hybench_ap"):
        return {
            "enabled": False,
            "anchors": [],
            "boundary_anchors": [],
        }
    return {
        "enabled": True,
        "anchors": [
            {
                "name": "v124_cand12_dbqps_up_memory_down_no_other_up",
                "db_qps_delta_pct": 12.58,
                "target_resource_delta_pp": -0.0458,
                "best_other_resource_delta_pp": 0.0,
                "params": {
                    "seq_page_cost": 1.12,
                    "random_page_cost": 4.0,
                    "cpu_tuple_cost": 0.01,
                    "cpu_index_tuple_cost": 0.005,
                    "cpu_operator_cost": 0.0025,
                    "parallel_setup_cost": 1000.0,
                    "parallel_tuple_cost": 0.1,
                },
            },
            {
                "name": "v124_cand18_dbqps_up_memory_down_no_other_up",
                "db_qps_delta_pct": 9.78,
                "target_resource_delta_pp": -0.0218,
                "best_other_resource_delta_pp": 0.0,
                "params": {
                    "seq_page_cost": 0.7749999999999999,
                    "random_page_cost": 2.710927308035715,
                    "cpu_tuple_cost": 0.006863607142857144,
                    "cpu_index_tuple_cost": 0.005035714285714287,
                    "cpu_operator_cost": 0.0033126250000000005,
                    "parallel_setup_cost": 1000.0,
                    "parallel_tuple_cost": 0.1,
                },
            },
        ],
        "boundary_anchors": [
            {
                "name": "v124_cand20_dbqps_up_cpu_up_memory_near_boundary",
                "db_qps_delta_pct": 4.72,
                "target_resource_delta_pp": 0.0138,
                "best_other_resource_delta_pp": 0.0358,
                "params": {
                    "seq_page_cost": 0.9,
                    "random_page_cost": 3.6,
                    "cpu_tuple_cost": 0.009000000000000001,
                    "cpu_index_tuple_cost": 0.005,
                    "cpu_operator_cost": 0.0027500000000000007,
                    "parallel_setup_cost": 1000.0,
                    "parallel_tuple_cost": 0.1,
                },
            },
            {
                "name": "v124_cand4_dbqps_flat_cpu_up_memory_up_boundary",
                "db_qps_delta_pct": 0.13,
                "target_resource_delta_pp": 0.0975,
                "best_other_resource_delta_pp": 0.2977,
                "params": {
                    "seq_page_cost": 0.7263157894736842,
                    "random_page_cost": 2.9744710583928575,
                    "cpu_tuple_cost": 0.006899578947368422,
                    "cpu_index_tuple_cost": 0.005,
                    "cpu_operator_cost": 0.0025,
                    "parallel_setup_cost": 1000.0,
                    "parallel_tuple_cost": 0.1,
                },
            },
            {
                "name": "v124_cand14_cpu_up_memory_up_boundary",
                "db_qps_delta_pct": -0.52,
                "target_resource_delta_pp": 0.0735,
                "best_other_resource_delta_pp": 0.2634,
                "params": {
                    "seq_page_cost": 0.8875,
                    "random_page_cost": 3.55,
                    "cpu_tuple_cost": 0.008875,
                    "cpu_index_tuple_cost": 0.005,
                    "cpu_operator_cost": 0.00278125,
                    "parallel_setup_cost": 1000.0,
                    "parallel_tuple_cost": 0.1,
                },
            },
            {
                "name": "v124_cand16_cpu_up_memory_high_boundary",
                "db_qps_delta_pct": -3.21,
                "target_resource_delta_pp": 0.3247,
                "best_other_resource_delta_pp": 0.6625,
                "params": {
                    "seq_page_cost": 0.7031578947368422,
                    "random_page_cost": 4.0,
                    "cpu_tuple_cost": 0.01,
                    "cpu_index_tuple_cost": 0.005,
                    "cpu_operator_cost": 0.0025,
                    "parallel_setup_cost": 1000.0,
                    "parallel_tuple_cost": 0.1,
                },
            },
        ],
        "interpolation_steps": [
            0.0,
            0.10,
            0.20,
            0.35,
            0.50,
            0.65,
            0.80,
            0.90,
            0.95,
            0.98,
        ],
        "max_candidates": 96,
        "min_topk": 6,
        "description": (
            "Memory/AP v124 repair probe: use real replay near-hits with DB QPS up "
            "and Memory down, then interpolate toward real CPU-up boundary points "
            "to search for Memory down plus CPU/IO up. These are search hints only; "
            "final acceptance still requires real DB QPS up and explainable resources."
        ),
    }


def bonect_active_params(bottleneck: str, benchmark: str) -> Optional[List[str]]:
    all_cost_params = [
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost",
    ]
    if bottleneck == "cpu" and benchmark == "hybench_xp":
        # 2026-06-22 control action after pg_bonect_bf_cpu_local_20260622_050829:
        # the six-param local search produced two flat top-k rounds with no plan
        # changes. Re-enable seq_page_cost, but only inside a tight rough-reference
        # band so it can cross planner thresholds without revisiting the old
        # seq_page_cost=3.5 / XP-QPS-collapse region.
        return list(all_cost_params)
    if bottleneck == "io" and benchmark == "hybench_xp":
        return list(all_cost_params)
    if bottleneck == "memory" and benchmark == "hybench_ap":
        return list(all_cost_params)
    return None


def _bonect_cost_param_floor(
    name: str,
    bottleneck: Optional[str] = None,
    benchmark: Optional[str] = None,
) -> float:
    floor = float(BONECT_COST_PARAM_FLOORS.get(name, 1e-9))
    scenario_floor = BONECT_SCENARIO_COST_PARAM_FLOORS.get(
        (str(bottleneck), str(benchmark)),
        {},
    ).get(name)
    if scenario_floor is not None:
        try:
            floor = max(floor, float(scenario_floor))
        except (TypeError, ValueError):
            pass
    return floor


def _bonect_normalize_cost_params(
    params: Dict[str, float],
    bottleneck: Optional[str] = None,
    benchmark: Optional[str] = None,
) -> Dict[str, float]:
    normalized: Dict[str, float] = {}
    for name in COST_PARAM_KEYS:
        raw_value = params.get(name, DEFAULT_PG_COST_PARAMS[name])
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = float(DEFAULT_PG_COST_PARAMS[name])
        floor = _bonect_cost_param_floor(name, bottleneck, benchmark)
        if not math.isfinite(value) or value <= 0.0:
            value = floor
        normalized[name] = max(floor, value)
    return normalized


def _bonect_interpolate_cost_params(
    start: Dict[str, float],
    end: Dict[str, float],
    step: float,
    bottleneck: Optional[str] = None,
    benchmark: Optional[str] = None,
) -> Dict[str, float]:
    start_params = _bonect_normalize_cost_params(start, bottleneck, benchmark)
    end_params = _bonect_normalize_cost_params(end, bottleneck, benchmark)
    interpolated: Dict[str, float] = {}
    for name in COST_PARAM_KEYS:
        floor = _bonect_cost_param_floor(name, bottleneck, benchmark)
        value = start_params[name] + (end_params[name] - start_params[name]) * float(step)
        interpolated[name] = max(floor, float(value))
    return interpolated


def bonect_full_factorial_probe_candidate_cap(
    *,
    param_count: int,
    values_per_param: int,
) -> int:
    """Upper-bound full active-parameter grid candidates as values_per_param^param_count."""
    return int(values_per_param) ** max(0, int(param_count))


def bonect_reference_path_anchors(bottleneck: str, benchmark: str) -> List[Dict[str, object]]:
    """Return phase-2 anchor/path points that the legal search range must cover."""
    anchors: List[Dict[str, object]] = []
    seen = set()

    def add_anchor(
        name: str,
        params: Dict[str, float],
        metadata: Optional[Dict[str, object]] = None,
    ) -> Dict[str, float]:
        normalized = _bonect_normalize_cost_params(params, bottleneck, benchmark)
        key = tuple(round(normalized[item], 12) for item in COST_PARAM_KEYS)
        if key not in seen:
            anchor_item: Dict[str, object] = {"name": name, "params": normalized}
            if isinstance(metadata, dict):
                for meta_key in (
                    "phase2_known_real_direction_seed",
                    "phase2_known_real_target_resource",
                    "phase2_known_real_source",
                    "phase2_known_real_db_qps_delta_pct",
                    "phase2_known_real_prefix_goal_passed",
                    "phase2_known_real_other_resource_up",
                    "phase2_known_real_other_resources_up",
                    "phase2_known_real_target_delta_pp",
                    "phase2_known_real_other_delta_pp",
                ):
                    if meta_key in metadata:
                        anchor_item[meta_key] = metadata[meta_key]
            anchors.append(anchor_item)
            seen.add(key)
        return normalized

    default_params = add_anchor("postgres_default", DEFAULT_PG_COST_PARAMS)
    initial = bonect_initial_params(bottleneck, benchmark)
    if initial:
        add_anchor("bonect_initial", initial)

    raw_anchors = BONECT_ROUGH_ANCHORS.get((bottleneck, benchmark), [])
    normalized_anchors: List[Tuple[str, Dict[str, float]]] = []
    for idx, raw_anchor in enumerate(raw_anchors, start=1):
        if not isinstance(raw_anchor, dict):
            continue
        anchor_name = str(raw_anchor.get("name") or f"anchor_{idx}")
        anchor_params_raw = raw_anchor.get("params")
        if not isinstance(anchor_params_raw, dict):
            anchor_params_raw = raw_anchor
        anchor_params = add_anchor(
            anchor_name,
            anchor_params_raw,  # type: ignore[arg-type]
            raw_anchor,
        )
        normalized_anchors.append((anchor_name, anchor_params))

    for anchor_name, anchor_params in normalized_anchors:
        for step in BONECT_ANCHOR_PATH_STEPS:
            add_anchor(
                f"default_to_{anchor_name}_step_{step:g}",
                _bonect_interpolate_cost_params(
                    default_params,
                    anchor_params,
                    step,
                    bottleneck,
                    benchmark,
                ),
            )

    if normalized_anchors:
        phase1_name, phase1_params = normalized_anchors[0]
        for rough_name, rough_params in normalized_anchors[1:]:
            for step in BONECT_ANCHOR_PATH_STEPS:
                add_anchor(
                    f"{phase1_name}_to_{rough_name}_step_{step:g}",
                    _bonect_interpolate_cost_params(
                        phase1_params,
                        rough_params,
                        step,
                        bottleneck,
                        benchmark,
                    ),
                )
    return anchors


def expand_bonect_param_ranges_with_references(
    bottleneck: str,
    benchmark: str,
    ranges: Optional[Dict[str, Dict[str, float]]],
) -> Optional[Dict[str, Dict[str, float]]]:
    if not isinstance(ranges, dict):
        return ranges
    references = bonect_reference_path_anchors(bottleneck, benchmark)
    expanded: Dict[str, Dict[str, float]] = {}
    for name, raw_range in ranges.items():
        item = dict(raw_range)
        floor = _bonect_cost_param_floor(name, bottleneck, benchmark)
        try:
            min_value = float(item.get("min_value", DEFAULT_PG_COST_PARAMS[name]))
            max_value = float(item.get("max_value", DEFAULT_PG_COST_PARAMS[name]))
        except (TypeError, ValueError):
            min_value = max_value = float(DEFAULT_PG_COST_PARAMS[name])
        ref_values: List[float] = []
        for ref in references:
            params = ref.get("params") if isinstance(ref, dict) else None
            if isinstance(params, dict) and name in params:
                try:
                    value = float(params[name])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value > 0.0:
                    ref_values.append(value)
        if ref_values:
            min_value = min(min_value, min(ref_values))
            max_value = max(max_value, max(ref_values))
        if max_value < min_value:
            min_value, max_value = max_value, min_value
        span = max(max_value - min_value, abs(max_value) * 0.01, floor)
        item["min_value"] = max(floor, min_value - span * 0.05)
        item["max_value"] = max(item["min_value"] * 1.000001, max_value + span * 0.05)
        expanded[name] = item
    return expanded


def validate_cost_param_scope(cfg: Dict[str, object]) -> None:
    """Fail generated configs that try to tune non-cost PostgreSQL parameters."""
    violations: List[str] = []

    def check_keys(path: str, value: object) -> None:
        if isinstance(value, dict):
            keys = value.keys()
        elif isinstance(value, list):
            keys = [str(item) for item in value]
        else:
            return
        invalid = sorted(str(key) for key in keys if str(key) not in COST_PARAM_SET)
        if invalid:
            violations.append(f"{path}: {invalid}")

    check_keys("initial_params", cfg.get("initial_params"))
    check_keys("p_real", cfg.get("p_real"))
    check_keys("p_candidate", cfg.get("p_candidate"))
    check_keys("param_ranges", cfg.get("param_ranges"))
    check_keys("param_step_scale", cfg.get("param_step_scale"))
    check_keys("sensitivity_table", cfg.get("sensitivity_table"))
    check_keys("fixed_params", cfg.get("fixed_params"))

    optimizer = cfg.get("optimizer")
    if isinstance(optimizer, dict):
        check_keys("optimizer.active_params", optimizer.get("active_params"))
        check_keys("optimizer.param_step_scale", optimizer.get("param_step_scale"))
        check_keys("optimizer.sensitivity_table", optimizer.get("sensitivity_table"))
        gradient = optimizer.get("gradient")
        if isinstance(gradient, dict):
            check_keys("optimizer.gradient.active_params", gradient.get("active_params"))
            boosted = gradient.get("boosted_params")
            if boosted is not None:
                check_keys("optimizer.gradient.boosted_params", boosted)
            trial = gradient.get("aggressive_trial")
            if isinstance(trial, dict):
                check_keys("optimizer.gradient.aggressive_trial.params", trial.get("params"))
        bruteforce = optimizer.get("bruteforce")
        if isinstance(bruteforce, dict):
            check_keys("optimizer.bruteforce.active_params", bruteforce.get("active_params"))

    if violations:
        raise ValueError(
            "Bonect tuning is restricted to these 7 cost parameters only: "
            f"{COST_PARAM_KEYS}. Non-cost tuning fields: {'; '.join(violations)}"
        )


def bonect_param_ranges(bottleneck: str, benchmark: str) -> Optional[Dict[str, Dict[str, float]]]:
    if bottleneck == "io" and benchmark == "hybench_xp":
        return {
            # Continuation range from pg_bonect_bf_io_20260621_160000 round 2.
            # Keep the rough-tuning direction available: higher seq_page_cost,
            # lower random_page_cost, higher CPU costs, higher parallel_tuple_cost.
            "seq_page_cost": {"min_value": 1.152, "max_value": 2.56, "priority": 1},
            "random_page_cost": {"min_value": 15.84, "max_value": 33.76, "priority": 2},
            "parallel_tuple_cost": {"min_value": 0.008, "max_value": 0.09248, "priority": 3},
            "cpu_tuple_cost": {"min_value": 0.0003, "max_value": 0.003948, "priority": 4},
            "cpu_operator_cost": {"min_value": 0.00008, "max_value": 0.0011808, "priority": 5},
            "cpu_index_tuple_cost": {"min_value": 0.0025, "max_value": 0.0100, "priority": 6},
            "parallel_setup_cost": {"min_value": 500.0, "max_value": 1500.0, "priority": 7},
        }
    if bottleneck == "cpu" and benchmark == "hybench_xp":
        return {
            # CPU rough-reference retry after pg_bonect_bf_cpu_local_20260622_050829:
            # the safer six-param local band kept performance healthy but produced
            # two flat top-k rounds and no plan changes. Move toward the partial
            # CPU good region from result/bonect/cpu.md while preserving the new
            # strict performance/resource guards and avoiding the old extreme
            # page-cost collapse.
            "seq_page_cost": {
                "min_value": 1.50,
                "max_value": 2.10,
                "step": 0.05,
                "discrete_count": 41,
                "priority": 1,
            },
            "random_page_cost": {
                "min_value": 1.80,
                "max_value": 3.20,
                "step": 0.10,
                "discrete_count": 41,
                "priority": 2,
            },
            "cpu_tuple_cost": {
                # Keep the lower bound above the low-tuple real-feedback risk
                # after reference-range padding. v22/v31 showed <0.001 should
                # not be validated for CPU/XP even when estimated CPU falls.
                "min_value": 0.00200,
                "max_value": 0.01200,
                "step": 0.00050,
                "discrete_count": 41,
                "priority": 3,
            },
            "cpu_index_tuple_cost": {
                "min_value": 0.00300,
                "max_value": 0.00500,
                "step": 0.00020,
                "discrete_count": 41,
                "priority": 4,
            },
            "cpu_operator_cost": {
                # v23/v29/v31/v62 real feedback makes <0.001 a DB-QPS
                # and/or CPU-direction risk. Generate CPU/XP candidates from
                # the safe side of that boundary while still covering default
                # and phase-1 anchors.
                "min_value": 0.00100,
                "max_value": 0.00325,
                "step": 0.000125,
                "discrete_count": 41,
                "priority": 5,
            },
            "parallel_setup_cost": {
                "min_value": 1000.0,
                "max_value": 1500.0,
                "step": 50.0,
                "discrete_count": 41,
                "priority": 6,
            },
            "parallel_tuple_cost": {
                "min_value": 0.18000,
                "max_value": 0.28000,
                "step": 0.01000,
                "discrete_count": 41,
                "priority": 7,
            },
        }
    if bottleneck == "memory" and benchmark == "hybench_ap":
        return {
            # Memory AP refinement after pg_bonect_bf_mem_refine2_20260622_141219:
            # two top-k rounds repeated the same candidate while the range
            # excluded the rough strict-good low cpu_* cost region.
            "seq_page_cost": {"min_value": 0.72, "max_value": 1.05, "priority": 1},
            "random_page_cost": {"min_value": 2.60, "max_value": 3.90, "priority": 2},
            "cpu_tuple_cost": {"min_value": 0.0025, "max_value": 0.0125, "priority": 3},
            "cpu_index_tuple_cost": {"min_value": 0.0045, "max_value": 0.0065, "priority": 4},
            "cpu_operator_cost": {"min_value": 0.000001, "max_value": 0.0035, "priority": 5},
            "parallel_setup_cost": {"min_value": 950.0, "max_value": 1140.0, "priority": 6},
            "parallel_tuple_cost": {"min_value": 0.045, "max_value": 0.115, "priority": 7},
        }
    if bottleneck == "none" and benchmark == "hybench_xp":
        return {
            # No-bottleneck resource-iteration search: stay near PostgreSQL
            # defaults but allow all seven planner cost params to move enough
            # to increase DB QPS and use more available CPU/Memory/IO.
            "seq_page_cost": {"min_value": 0.50, "max_value": 2.00, "discrete_count": 21},
            "random_page_cost": {"min_value": 2.00, "max_value": 8.00, "discrete_count": 21},
            "cpu_tuple_cost": {"min_value": 0.0050, "max_value": 0.0200, "discrete_count": 21},
            "cpu_index_tuple_cost": {"min_value": 0.0025, "max_value": 0.0100, "discrete_count": 21},
            "cpu_operator_cost": {"min_value": 0.0010, "max_value": 0.0050, "discrete_count": 21},
            "parallel_setup_cost": {"min_value": 500.0, "max_value": 1500.0, "discrete_count": 21},
            "parallel_tuple_cost": {"min_value": 0.050, "max_value": 0.200, "discrete_count": 21},
        }
    return None


def bonect_blind_recovery_param_ranges() -> Dict[str, Dict[str, float]]:
    """Return answer-independent broad ranges used for Phase-2 blind recovery."""
    return {
        "seq_page_cost": {
            "min_value": 0.0001,
            "max_value": 100.0,
            "discrete_count": 5,
            "priority": 1,
        },
        "random_page_cost": {
            "min_value": 0.0001,
            "max_value": 20.0,
            "discrete_count": 5,
            "priority": 2,
        },
        "cpu_tuple_cost": {
            "min_value": 0.000001,
            "max_value": 1.0,
            "discrete_count": 5,
            "priority": 3,
        },
        "cpu_index_tuple_cost": {
            "min_value": 0.000001,
            "max_value": 1.0,
            "discrete_count": 5,
            "priority": 4,
        },
        "cpu_operator_cost": {
            "min_value": 0.000001,
            "max_value": 1.0,
            "discrete_count": 5,
            "priority": 5,
        },
        "parallel_setup_cost": {
            "min_value": 0.0,
            "max_value": 10000.0,
            "discrete_count": 5,
            "priority": 6,
        },
        "parallel_tuple_cost": {
            "min_value": 0.000001,
            "max_value": 10.0,
            "discrete_count": 5,
            "priority": 7,
        },
    }


def parse_param_step_overrides(values: Optional[List[str]]) -> Dict[str, float]:
    """Parse repeated param=step CLI overrides for bruteforce grid spacing."""
    parsed: Dict[str, float] = {}
    for raw in values or []:
        if "=" not in str(raw):
            raise ValueError(f"Invalid --bruteforce-param-step value {raw!r}; expected param=step")
        name, value = str(raw).split("=", 1)
        name = name.strip()
        if name not in COST_PARAM_SET:
            raise ValueError(
                f"Invalid --bruteforce-param-step parameter {name!r}; allowed: {COST_PARAM_KEYS}"
            )
        try:
            step = float(value)
        except ValueError as exc:
            raise ValueError(f"Invalid step for {name}: {value!r}") from exc
        if step <= 0.0:
            raise ValueError(f"Invalid non-positive step for {name}: {step}")
        parsed[name] = step
    return parsed


def parse_param_values_overrides(values: Optional[List[str]]) -> Dict[str, List[float]]:
    """Parse repeated param=v1,v2,... CLI overrides for explicit bruteforce grid points."""
    parsed: Dict[str, List[float]] = {}
    for raw in values or []:
        if "=" not in str(raw):
            raise ValueError(f"Invalid --bruteforce-param-values value {raw!r}; expected param=v1,v2,...")
        name, value = str(raw).split("=", 1)
        name = name.strip()
        if name not in COST_PARAM_SET:
            raise ValueError(
                f"Invalid --bruteforce-param-values parameter {name!r}; allowed: {COST_PARAM_KEYS}"
            )
        points: List[float] = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                point = float(item)
            except ValueError as exc:
                raise ValueError(f"Invalid grid value for {name}: {item!r}") from exc
            if point <= 0.0:
                raise ValueError(f"Invalid non-positive grid value for {name}: {point}")
            points.append(point)
        points = sorted(set(points))
        if len(points) < 2:
            raise ValueError(f"Need at least two grid values for {name}: {value!r}")
        parsed[name] = points
    return parsed


def parse_capability_overrides(values: Optional[List[str]]) -> Dict[str, float]:
    """Parse repeated resource=value CLI overrides for generated resource capability."""
    allowed = {"cpu", "memory", "io"}
    parsed: Dict[str, float] = {}
    for raw in values or []:
        if "=" not in str(raw):
            raise ValueError(f"Invalid --bonect-capability value {raw!r}; expected resource=value")
        name, value = str(raw).split("=", 1)
        name = name.strip()
        if name not in allowed:
            raise ValueError(
                f"Invalid --bonect-capability resource {name!r}; allowed: {sorted(allowed)}"
            )
        try:
            capability_value = float(value)
        except ValueError as exc:
            raise ValueError(f"Invalid capability for {name}: {value!r}") from exc
        if capability_value <= 0.0:
            raise ValueError(f"Invalid non-positive capability for {name}: {capability_value}")
        parsed[name] = capability_value
    return parsed


def parse_param_range_overrides(values: Optional[List[str]]) -> Dict[str, Tuple[float, float]]:
    """Parse repeated param=min:max CLI overrides for bruteforce ranges."""
    parsed: Dict[str, Tuple[float, float]] = {}
    for raw in values or []:
        if "=" not in str(raw):
            raise ValueError(f"Invalid --bonect-param-range value {raw!r}; expected param=min:max")
        name, value = str(raw).split("=", 1)
        name = name.strip()
        if name not in COST_PARAM_SET:
            raise ValueError(
                f"Invalid --bonect-param-range parameter {name!r}; allowed: {COST_PARAM_KEYS}"
            )
        if ":" not in value:
            raise ValueError(f"Invalid --bonect-param-range value for {name}: {value!r}; expected min:max")
        raw_min, raw_max = value.split(":", 1)
        try:
            min_value = float(raw_min)
            max_value = float(raw_max)
        except ValueError as exc:
            raise ValueError(f"Invalid range for {name}: {value!r}") from exc
        if min_value <= 0.0 or max_value <= 0.0:
            raise ValueError(f"Invalid non-positive range for {name}: {value!r}")
        if min_value >= max_value:
            raise ValueError(f"Invalid range for {name}: min must be < max, got {value!r}")
        parsed[name] = (min_value, max_value)
    return parsed


def parse_param_value_overrides(values: Optional[List[str]], *, option_name: str) -> Dict[str, float]:
    """Parse repeated param=value CLI overrides constrained to PostgreSQL cost params."""
    parsed: Dict[str, float] = {}
    for raw in values or []:
        if "=" not in str(raw):
            raise ValueError(f"Invalid {option_name} value {raw!r}; expected param=value")
        name, value = str(raw).split("=", 1)
        name = name.strip()
        if name not in COST_PARAM_SET:
            raise ValueError(
                f"Invalid {option_name} parameter {name!r}; allowed: {COST_PARAM_KEYS}"
            )
        try:
            param_value = float(value)
        except ValueError as exc:
            raise ValueError(f"Invalid value for {name}: {value!r}") from exc
        if param_value <= 0.0:
            raise ValueError(f"Invalid non-positive value for {name}: {param_value}")
        parsed[name] = param_value
    return parsed


def parse_active_param_override(raw_value: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated active parameter override constrained to cost params."""
    if raw_value is None:
        return None
    raw_text = str(raw_value).strip()
    if not raw_text:
        return None
    parsed: List[str] = []
    for item in raw_text.split(","):
        name = item.strip()
        if not name:
            continue
        if name not in COST_PARAM_SET:
            raise ValueError(
                f"Invalid --bonect-active-params parameter {name!r}; allowed: {COST_PARAM_KEYS}"
            )
        if name not in parsed:
            parsed.append(name)
    if not parsed:
        return None
    return parsed


def apply_initial_param_overrides(
    params: Optional[Dict[str, float]],
    args: argparse.Namespace,
) -> Optional[Dict[str, float]]:
    initial_overrides = parse_param_value_overrides(
        getattr(args, "bonect_initial_param", []),
        option_name="--bonect-initial-param",
    )
    if not initial_overrides:
        return params
    updated = dict(params or DEFAULT_PG_COST_PARAMS)
    updated.update(initial_overrides)
    return updated


def apply_bruteforce_grid_overrides(
    ranges: Optional[Dict[str, Dict[str, float]]],
    args: argparse.Namespace,
) -> Optional[Dict[str, Dict[str, float]]]:
    if not isinstance(ranges, dict):
        return ranges
    discrete_count = int(getattr(args, "bruteforce_param_discrete_count", 0) or 0)
    step_overrides = parse_param_step_overrides(getattr(args, "bruteforce_param_step", []))
    values_overrides = parse_param_values_overrides(getattr(args, "bruteforce_param_values", []))
    range_overrides = parse_param_range_overrides(getattr(args, "bonect_param_range", []))
    updated: Dict[str, Dict[str, float]] = {}
    for name, value in ranges.items():
        item = dict(value)
        if name in range_overrides:
            min_value, max_value = range_overrides[name]
            item["min_value"] = min_value
            item["max_value"] = max_value
        if discrete_count >= 2:
            item["discrete_count"] = discrete_count
            item.pop("step", None)
        if name in step_overrides:
            item["step"] = step_overrides[name]
        if name in values_overrides:
            points = values_overrides[name]
            item["values"] = points
            item["min_value"] = min(points)
            item["max_value"] = max(points)
            item["discrete_count"] = len(points)
            item.pop("step", None)
        updated[name] = item
    return updated


def bonect_sensitivity_table(bottleneck: str, benchmark: str) -> Optional[Dict[str, Dict[str, float]]]:
    if bottleneck == "io" and benchmark == "hybench_xp":
        return {
            # Match result/bonect/io.md: reduce random-page preference gradually and
            # put more pressure on CPU-cost and parallel-tuple costs.
            "seq_page_cost": {"io": -0.10},
            "random_page_cost": {"io": 0.08},
            "cpu_tuple_cost": {"io": -0.25},
            "cpu_index_tuple_cost": {"io": 0.00},
            "cpu_operator_cost": {"io": -0.25},
            "parallel_setup_cost": {"io": 0.00},
            "parallel_tuple_cost": {"io": -0.25},
        }
    if bottleneck == "cpu" and benchmark == "hybench_xp":
        return {
            # Phase-2 real replay update: repeatedly lowering cpu_tuple_cost /
            # cpu_operator_cost looked good to the estimator but failed real
            # CPU direction or DB QPS. Keep the CPU pressure on page/parallel
            # plan-shape changes and stop using low CPU-cost values as the
            # default CPU-relief prior.
            "seq_page_cost": {"cpu": -0.10, "memory": +0.04, "io": -0.10},
            "random_page_cost": {"cpu": +0.10, "memory": +0.02, "io": +0.10},
            "cpu_tuple_cost": {"cpu": -0.05, "memory": -0.05, "io": -0.05},
            "cpu_index_tuple_cost": {"cpu": 0.00, "memory": -0.02, "io": 0.00},
            "cpu_operator_cost": {"cpu": -0.08, "memory": -0.05, "io": -0.05},
            "parallel_setup_cost": {"cpu": -0.10, "memory": +0.02, "io": 0.00},
            "parallel_tuple_cost": {"cpu": -0.20, "memory": +0.05, "io": -0.10},
        }
    if bottleneck == "memory" and benchmark == "hybench_ap":
        return {
            # L2 ESCALATION: Inverted signs to match result/bonect/mem.md direction.
            # Bonect reference: seq↓(1.0→0.848), cpu_tuple↑(0.01→0.0117),
            # cpu_op↑(0.0025→0.0031), par_tuple↓(0.1→0.0546).
            # Inverted: seq_page_cost, parallel_tuple_cost.
            "seq_page_cost": {"memory": +0.15, "cpu": +0.10, "io": -0.15},
            "random_page_cost": {"memory": +0.10, "cpu": -0.10, "io": +0.15},
            "cpu_tuple_cost": {"memory": -0.20, "cpu": -0.25, "io": -0.10},
            "cpu_index_tuple_cost": {"memory": -0.05, "cpu": -0.10, "io": -0.05},
            "cpu_operator_cost": {"memory": -0.25, "cpu": -0.25, "io": -0.10},
            "parallel_setup_cost": {"memory": -0.15, "cpu": -0.10, "io": -0.05},
            "parallel_tuple_cost": {"memory": +0.20, "cpu": -0.15, "io": -0.10},
        }
    if bottleneck == "none" and benchmark == "hybench_xp":
        return {
            # No-bottleneck mode does not favor lowering a single resource.
            # The real feedback guard requires at least one resource to move
            # upward into its sweet range, so this remains a balanced prior.
            "seq_page_cost": {"cpu": -0.05, "memory": +0.02, "io": -0.05},
            "random_page_cost": {"cpu": +0.05, "memory": +0.02, "io": +0.05},
            "cpu_tuple_cost": {"cpu": +0.10, "memory": -0.02, "io": -0.05},
            "cpu_index_tuple_cost": {"cpu": +0.05, "memory": -0.01, "io": 0.00},
            "cpu_operator_cost": {"cpu": +0.10, "memory": -0.02, "io": -0.05},
            "parallel_setup_cost": {"cpu": -0.05, "memory": +0.01, "io": 0.00},
            "parallel_tuple_cost": {"cpu": -0.10, "memory": +0.02, "io": -0.05},
        }
    return None


def bonect_phase2_max_param_delta_rel_pct(target_resource: str, args: argparse.Namespace) -> float:
    requested = float(args.gradient_max_param_delta_rel_pct)
    if str(target_resource) in {"cpu", "memory", "io"}:
        return max(requested, 600.0)
    return max(requested, 300.0)


def build_optimizer_config(spec: CommandSpec, args: argparse.Namespace, run_id: str) -> Path:
    profile = build_bottleneck_profile(spec.bottleneck, args)
    teacher_recovery_cfg = load_phase2_teacher_recovery_config(
        args.phase2_teacher_recovery_config,
        spec.bottleneck,
    )
    teacher_blind_holdout = bool(
        teacher_recovery_cfg.get("enabled")
        and teacher_recovery_cfg.get("blind_holdout", True)
    )
    answer_blind_recovery_mode = bool(
        getattr(args, "phase2_blind_recovery", False)
    )
    blind_recovery_mode = bool(answer_blind_recovery_mode or teacher_blind_holdout)
    broad_blind_search_mode = blind_recovery_mode
    if answer_blind_recovery_mode and teacher_recovery_cfg:
        raise RuntimeError(
            "--phase2-blind-recovery cannot be combined with "
            "--phase2-teacher-recovery-config; the final blind run must not load "
            "teacher coordinates, even for in-process auditing"
        )
    blind_coordinate_overrides = {
        "--bonect-initial-param": getattr(args, "bonect_initial_param", []),
        "--bonect-param-range": getattr(args, "bonect_param_range", []),
        "--bruteforce-param-values": getattr(args, "bruteforce_param_values", []),
        "--bruteforce-param-step": getattr(args, "bruteforce_param_step", []),
    }
    if broad_blind_search_mode:
        supplied_coordinate_overrides = sorted(
            name for name, value in blind_coordinate_overrides.items() if value
        )
        if supplied_coordinate_overrides:
            raise RuntimeError(
                "blind/teacher-holdout recovery must start from PostgreSQL defaults and "
                "answer-independent broad ranges; remove coordinate-bearing overrides: "
                + ", ".join(supplied_coordinate_overrides)
            )
    hybench_setup_sql, hybench_setup_sql_paths = (
        load_hybench_setup_sql(args.hybench_setup_sql_file)
        if spec.benchmark in {"hybench_ap", "hybench_xp"}
        else ([], [])
    )
    output_root = PROJECT_ROOT / args.output_dir / run_id
    config_path = PROJECT_ROOT / args.generated_config_dir / run_id / spec.bottleneck / f"{spec.benchmark}.json"
    history_path = output_root / spec.bottleneck / f"{spec.benchmark}_optimizer_history.jsonl"
    plan_capture_dir = output_root / spec.bottleneck / f"{spec.benchmark}_plan_captures"
    resource_model_output_dir = (
        PROJECT_ROOT
        / args.generated_config_dir
        / run_id
        / spec.bottleneck
        / f"{spec.benchmark}_resource_models"
    )
    requested_active_params = parse_active_param_override(
        getattr(args, "bonect_active_params", None)
    )
    if (
        broad_blind_search_mode
        and requested_active_params
        and set(requested_active_params) != COST_PARAM_SET
    ):
        raise RuntimeError(
            "--phase2-blind-recovery requires all seven cost parameters to remain active"
        )
    active_params = (
        list(COST_PARAM_KEYS)
        if broad_blind_search_mode
        else requested_active_params
        or bonect_active_params(spec.bottleneck, spec.benchmark)
        or list(COST_PARAM_KEYS)
    )
    resource_target_ranges = {
        "cpu": {"min_pct": 65.0, "max_pct": 80.0, "target_pct": 72.5},
        "memory": {"min_pct": 70.0, "max_pct": 80.0, "target_pct": 75.0},
        "io": {"min_pct": 50.0, "max_pct": 75.0, "target_pct": 62.5},
    }
    hard_overload_util_pct = 90.0
    target_guard_resource = str(profile["target_resource"])
    target_guard_upper_pct = (
        resource_target_ranges.get(target_guard_resource, {}).get("max_pct")
        if target_guard_resource != "none"
        else None
    )
    if target_guard_upper_pct is None:
        target_guard_upper_pct = hard_overload_util_pct
    resource_direction_policy = (
        "none_any_sweet_up"
        if profile["target_resource"] == "none"
        else "bottleneck_down_any_other_up"
    )
    phase2_strong_min_db_qps_delta_pct = 15.0
    phase2_preferred_db_qps_delta_pct = 20.0
    latency_max_ratio = 1.0 + 10000.0 / 100.0
    is_memory_hybench_ap = (spec.bottleneck, spec.benchmark) == ("memory", "hybench_ap")
    configured_workload_timeout_sec = float(args.measured_workload_timeout_sec)
    workload_timeout_sec = (
        configured_workload_timeout_sec
        if configured_workload_timeout_sec > 0
        else max(float(args.benchbase_time_sec), float(args.hybench_run_mins) * 60.0) + 120.0
    )

    phase2_score_min_target_down_util_pct = (
        2.0
        if profile["target_resource"] in {"cpu", "memory", "io"}
        else 0.0
    )
    phase2_prefix_min_target_down_util_pct = (
        0.50
        if profile["target_resource"] in {"cpu", "memory", "io"}
        else 0.0
    )
    phase2_score_min_other_up_util_pct = (
        0.20
        if profile["target_resource"] in {"cpu", "memory", "io"}
        else 0.0
    )
    phase2_gate_min_other_up_util_pct = (
        0.01
        if profile["target_resource"] in {"cpu", "memory", "io"}
        else 0.0
    )
    phase2_prefix_min_other_up_util_pct = phase2_gate_min_other_up_util_pct
    phase2_target_error_margin_util_pct = 5.0 if is_memory_hybench_ap else 0.0
    resource_model_quality_required_resources = parse_csv(
        args.resource_model_quality_required_resources
    )
    resource_model_quality_optional_resources: List[str] = []
    if not resource_model_quality_required_resources:
        resource_model_quality_required_resources = ["cpu", "io", "memory"]
    pair_probe_wall_clock_seconds = max(
        0.0,
        float(os.getenv("BONECT_PAIR_PROBE_WALL_CLOCK_SECONDS", "0") or "0"),
    )
    pair_probe_min_generated_before_time_limit = max(
        0,
        int(os.getenv("BONECT_PAIR_PROBE_MIN_GENERATED_BEFORE_TIME_LIMIT", "0") or "0"),
    )
    pair_probe_progress_interval = max(
        1,
        int(os.getenv("BONECT_PAIR_PROBE_PROGRESS_INTERVAL", "250") or "250"),
    )
    requested_candidate_top_k = max(1, int(args.bruteforce_candidate_top_k))
    requested_candidate_replay_top_k = max(1, int(args.bruteforce_candidate_replay_top_k))
    adaptive_replay_controller_enabled = bool(args.adaptive_replay_controller_enabled)
    adaptive_replay_controller_max_budget = cap_real_replay_top_k(
        max(1, int(args.adaptive_replay_controller_max_budget))
    )
    adaptive_replay_controller_min_evidence = max(
        0, int(args.adaptive_replay_controller_min_evidence)
    )
    adaptive_replay_controller_batch_size = max(
        1, int(args.adaptive_replay_controller_batch_size)
    )
    adaptive_replay_controller_max_expansions = max(
        0, int(args.adaptive_replay_controller_max_expansions)
    )
    # iter107: Memory replay-calibration plumbing (override §2026-07-06 iter100
    # §8 #1 / §2026-07-06 23:55 iter102 §8 #2 / §2026-07-07 03:12 iter106 §715-
    # 806 next_iter PRIORITY #1). When --memory-replay-calibration-enabled is
    # set, the generated config gets a memory_replay_calibration block under
    # metrics.plan_estimator.model_retraining.quality_gate; the optimizer's
    # validate_resource_model_quality_gate reads that block and fails the run
    # when predicted-down vs actual-down evidence shows the Memory model is
    # unreliable for direction ranking (v6 root cause: 21 predicted-down but
    # only 3 actual-down → predicted-down-actual-up ratio ~85%).
    memory_replay_calibration_enabled = bool(args.memory_replay_calibration_enabled)
    memory_replay_calibration_max_predicted_down_actual_up_ratio = float(
        args.memory_replay_calibration_max_predicted_down_actual_up_ratio
    )
    memory_replay_calibration_min_predicted_down_count = max(
        0, int(args.memory_replay_calibration_min_predicted_down_count)
    )
    memory_replay_calibration_evidence_path_str = str(
        args.memory_replay_calibration_evidence_path or ""
    )
    memory_replay_calibration_evidence: Dict[str, object] = {}
    if memory_replay_calibration_evidence_path_str:
        evidence_file = Path(memory_replay_calibration_evidence_path_str)
        if not evidence_file.is_file():
            raise RuntimeError(
                "memory_replay_calibration provenance hard-fail: "
                f"--memory-replay-calibration-evidence-path "
                f"{evidence_file!s} is not a file"
            )
        try:
            with open(evidence_file, "r", encoding="utf-8") as ef:
                memory_replay_calibration_evidence = json.load(ef)
        except Exception as exc:
            raise RuntimeError(
                "memory_replay_calibration provenance hard-fail: "
                f"failed to parse evidence JSON {evidence_file!s}: {exc}"
            ) from exc
        if not isinstance(memory_replay_calibration_evidence, dict):
            raise RuntimeError(
                "memory_replay_calibration provenance hard-fail: "
                f"evidence JSON {evidence_file!s} must be a JSON object"
            )
    if profile["target_resource"] == "cpu":
        candidate_top_k = max(60, requested_candidate_top_k)
        candidate_replay_top_k = cap_real_replay_top_k(
            requested_candidate_replay_top_k,
            available_count=candidate_top_k,
        )
    else:
        candidate_top_k = requested_candidate_top_k
        candidate_replay_top_k = cap_real_replay_top_k(
            requested_candidate_replay_top_k,
            available_count=candidate_top_k,
        )
    if broad_blind_search_mode:
        candidate_top_k = max(60, requested_candidate_top_k)
        candidate_replay_top_k = cap_real_replay_top_k(
            requested_candidate_replay_top_k,
            available_count=candidate_top_k,
        )
    # Adaptive control may choose where to stop within the ranked prefix, but
    # it cannot increase the global five-replay budget. Candidate estimation
    # remains independent and may retain a much larger read-only pool.
    if adaptive_replay_controller_enabled:
        candidate_top_k = max(
            candidate_top_k, adaptive_replay_controller_max_budget
        )
        candidate_replay_top_k = adaptive_replay_controller_max_budget
        small_loop_min_validated_candidate_count = (
            adaptive_replay_controller_min_evidence
        )
        small_loop_abort_on_prefix_insufficient = True
    else:
        small_loop_min_validated_candidate_count = candidate_replay_top_k
        small_loop_abort_on_prefix_insufficient = False
    candidate_replay_top_k = cap_real_replay_top_k(
        candidate_replay_top_k,
        available_count=candidate_top_k,
    )
    small_loop_min_validated_candidate_count = min(
        small_loop_min_validated_candidate_count,
        candidate_replay_top_k,
    )
    reference_path_anchors = (
        []
        if broad_blind_search_mode
        else bonect_reference_path_anchors(spec.bottleneck, spec.benchmark)
    )
    pair_probe_values_per_param = BONECT_PAIR_PROBE_VALUES_PER_PARAM
    pair_probe_full_factorial_grid = args.bonect_pair_probe_mode == "full-factorial"
    pair_probe_max_combination_size = min(
        len(COST_PARAM_KEYS),
        max(1, int(args.bonect_pair_probe_max_combination_size)),
    )
    requested_pair_probe_max_candidates = max(
        0,
        int(args.bonect_pair_probe_max_candidates),
    )
    if requested_pair_probe_max_candidates > 0:
        pair_probe_max_candidates = requested_pair_probe_max_candidates
    elif pair_probe_full_factorial_grid:
        pair_probe_max_candidates = bonect_full_factorial_probe_candidate_cap(
            param_count=len(COST_PARAM_KEYS),
            values_per_param=pair_probe_values_per_param,
        )
    else:
        pair_probe_max_candidates = sum(
            math.comb(len(COST_PARAM_KEYS), combination_size)
            * pair_probe_values_per_param ** combination_size
            for combination_size in range(1, pair_probe_max_combination_size + 1)
        )
    real_delta_artifact_arg = str(
        args.phase2_real_delta_calibration_artifact or ""
    ).strip()
    real_delta_artifact_path = ""
    if real_delta_artifact_arg:
        resolved_real_delta_artifact = Path(real_delta_artifact_arg).expanduser()
        if not resolved_real_delta_artifact.is_absolute():
            resolved_real_delta_artifact = PROJECT_ROOT / resolved_real_delta_artifact
        resolved_real_delta_artifact = resolved_real_delta_artifact.resolve()
        if not resolved_real_delta_artifact.is_file():
            raise RuntimeError(
                "phase2 real-delta calibration artifact is not a file: "
                f"{resolved_real_delta_artifact}"
            )
        real_delta_artifact_path = str(resolved_real_delta_artifact)
    real_delta_min_plan_feature_coverage = min(
        1.0,
        max(0.0, float(args.phase2_real_delta_min_plan_feature_coverage)),
    )
    near_hit_anchor_probe_cfg = bonect_near_hit_anchor_probe_config(
        spec.bottleneck,
        spec.benchmark,
    )
    near_hit_anchor_probe_cfg["enabled"] = False
    near_hit_anchor_probe_cfg["max_candidates"] = 0
    near_hit_anchor_probe_cfg["min_topk"] = 0
    if broad_blind_search_mode:
        near_hit_anchor_probe_cfg["anchors"] = []
        near_hit_anchor_probe_cfg["boundary_anchors"] = []
        near_hit_anchor_probe_cfg["interpolation_steps"] = []

    hybench_snapshot_restore_enabled = bool(
        args.hybench_snapshot_restore_enabled
        and args.scenario_set == "bonect"
        and spec.benchmark in {"hybench_ap", "hybench_xp"}
    )
    hybench_snapshot_restore_command = " ".join(
        [
            f"OPTIMIZER_CONFIG_PATH={shlex.quote(str(config_path))}",
            f"DB_HOST={shlex.quote(str(args.pg_host))}",
            f"DB_PORT={shlex.quote(str(args.pg_port))}",
            f"DB_USER={shlex.quote(str(args.pg_user))}",
            f"DB_PASSWORD={shlex.quote(str(args.pg_password))}",
            f"DB_NAME={shlex.quote(str(spec.pg_dbname))}",
            f"TEMPLATE_DB={shlex.quote(str(args.hybench_snapshot_template_db))}",
            f"VM_RESOURCE_MAX_CPU={int(args.hybench_snapshot_boost_cpu)}",
            f"VM_RESOURCE_MAX_MEMORY_GB={int(args.hybench_snapshot_boost_memory_gb)}",
            "VM_RESOURCE_MAX_READ_MB=0",
            "VM_RESOURCE_MAX_WRITE_MB=0",
            "QUIET=1",
            shlex.quote(str(args.hybench_snapshot_restore_command)),
        ]
    )

    cfg = {
        "capability": profile["capability"],
        "scoring": {
            "overload_threshold": args.overload_threshold,
            "score_mode": args.score_mode,
            "resource_weights": profile["resource_weights"],
            "resource_reward_weights": profile["resource_reward_weights"],
            "resource_penalty_weights": profile["resource_penalty_weights"],
            "resource_target_ranges": resource_target_ranges,
        },
        "vm_resource": profile["vm_resource"],
        "initial_params": (
            dict(DEFAULT_PG_COST_PARAMS)
            if broad_blind_search_mode
            else apply_initial_param_overrides(
                bonect_initial_params(spec.bottleneck, spec.benchmark),
                args,
            )
        ),
        "param_ranges": (
            bonect_blind_recovery_param_ranges()
            if broad_blind_search_mode
            else apply_bruteforce_grid_overrides(
                expand_bonect_param_ranges_with_references(
                    spec.bottleneck,
                    spec.benchmark,
                    bonect_param_ranges(spec.bottleneck, spec.benchmark),
                ),
                args,
            )
        ),
        "sensitivity_table": bonect_sensitivity_table(spec.bottleneck, spec.benchmark),
        "bottleneck_calibration": {
            "enabled": bool(args.adaptive_bottleneck_calibration and profile["target_resource"] != "none"),
            "resources": profile["target_resource"],
            "min_util_pct": args.adaptive_bottleneck_min_util_pct,
            "target_util_pct": args.adaptive_bottleneck_target_util_pct,
            "expand_overloaded_non_targets": True,
            "non_target_overload_util_pct": args.final_feedback_overload_util_pct,
            "non_target_target_util_pct": args.final_feedback_target_util_pct,
            "write_back": True,
            "backup": True,
            "method": "baseline_actual_runtime_capability",
            "stop_on_vm_profile_reapply_required": True,
        },
        "optimizer": {
            "mode": args.optimizer_mode,
            "gradient": {
                "eta": args.gradient_eta,
                "max_eta": args.gradient_eta,
                "eta_growth_factor": 1.0,
                "max_param_delta_ratio": args.gradient_max_param_delta_ratio,
                "min_param_delta_rel_pct": args.gradient_min_param_delta_rel_pct,
                "max_param_delta_rel_pct": bonect_phase2_max_param_delta_rel_pct(
                    str(profile["target_resource"]),
                    args,
                ),
                "repeat_candidate_jump_enabled": True,
                "repeat_candidate_jump_multiplier": 1.5,
                "active_bottleneck_underuse_scale": 0.0,
                "max_iter": args.optimizer_max_iter,
                "patience": args.optimizer_patience,
                "convergence_target_util": args.final_feedback_target_util_pct / 100.0,
                "convergence_tolerance": args.final_feedback_tolerance_pct / 100.0,
                "convergence_overload_util": min(
                    args.final_feedback_overload_util_pct,
                    hard_overload_util_pct,
                ) / 100.0,
                "post_convergence_explore_iter": args.post_convergence_explore_iter,
                "target_accept_guard_enabled": True,
                "target_accept_guard_upper_util": float(target_guard_upper_pct) / 100.0,
                "target_accept_guard_tolerance": 0.0,
                "target_accept_guard_min_improvement_util": 1e-4,
                "target_bottleneck_resources": (
                    []
                    if profile["target_resource"] == "none"
                    else [str(profile["target_resource"])]
                ),
                "adaptive_rejection_enabled": True,
                "adaptive_rejection_shrink_first": 0.5,
                "adaptive_rejection_shrink_first_after": 1,
                "adaptive_rejection_shrink_second": 0.25,
                "adaptive_rejection_shrink_second_after": 2,
                "adaptive_rejection_focus_after": 2,
                "adaptive_rejection_focus_top_n": 7,
                "adaptive_rejection_stop_after": 10,
                "active_params": active_params,
            },
            "param_step_scale": {
                "seq_page_cost": 0.2,
                "random_page_cost": 0.2,
                "cpu_tuple_cost": 0.0005,
                "cpu_index_tuple_cost": 0.0005,
                "cpu_operator_cost": 0.0002,
                "parallel_setup_cost": 20.0,
                "parallel_tuple_cost": 0.01,
            },
            "bruteforce": {
                "blind_recovery": {
                    "enabled": broad_blind_search_mode,
                    "answer_blind": answer_blind_recovery_mode,
                    "teacher_holdout_diagnostic": teacher_blind_holdout,
                    "start": "postgresql_defaults",
                    "range_source": "answer_independent_optimizer_defaults",
                    "teacher_coordinates_loaded": bool(teacher_recovery_cfg),
                    "teacher_coordinates_affect_selection": False,
                    "coordinate_overrides_allowed": False,
                },
                "max_iter": max(
                    24 if broad_blind_search_mode else 1,
                    int(args.bruteforce_max_iter),
                ),
                "shrink_factor": 0.5,
                "early_stop_threshold": max(1, int(args.bruteforce_early_stop_threshold)),
                "candidate_top_k": candidate_top_k,
                "candidate_replay_top_k": candidate_replay_top_k,
                "force_best_replay_anchor": True,
                "force_best_replay_anchor_seed_records": (
                    []
                    if blind_recovery_mode
                    else bonect_forced_replay_anchors(
                        spec.bottleneck,
                        spec.benchmark,
                    )
                ),
                "teacher_recovery_audit": teacher_recovery_cfg,
                "small_loop_min_validated_candidate_count": small_loop_min_validated_candidate_count,
                "small_loop_require_first_stage_ranking_gate": True,
                "small_loop_allow_goal_rich_prefix_despite_pairwise_inversion": (
                    profile["target_resource"] in {"cpu", "memory", "io"}
                ),
                "small_loop_abort_on_prefix_insufficient": small_loop_abort_on_prefix_insufficient,
                "small_loop_pairwise_inversion_threshold": 0.10,
                "small_loop_min_long_term_goal_hits": 1,
                "adaptive_replay_controller": {
                    "enabled": adaptive_replay_controller_enabled,
                    "max_budget": adaptive_replay_controller_max_budget,
                    "min_evidence": adaptive_replay_controller_min_evidence,
                    "batch_size": adaptive_replay_controller_batch_size,
                    "max_expansions": adaptive_replay_controller_max_expansions,
                    "lane_quotas": {
                        "explainable": 7,
                        "fallback": 5,
                        "diagnostic": 5,
                        "mandatory_anchor": 2,
                        "reserve": 5,
                    },
                },
                "range_shrink_factor": float(args.bruteforce_range_shrink_factor),
                "range_update_strategy": (
                    "binary_interval"
                    if broad_blind_search_mode
                    else str(args.bruteforce_range_update_strategy)
                ),
                "range_min_width_pct": max(0.0, float(args.bruteforce_range_min_width_pct)),
                "range_min_width": max(0.0, float(args.bruteforce_range_min_width)),
                "max_param_delta_rel_pct": (
                    0.0
                    if broad_blind_search_mode
                    else bonect_phase2_max_param_delta_rel_pct(
                        str(profile["target_resource"]),
                        args,
                    )
                ),
                "active_params": active_params,
                "candidate_gate": {
                    "enabled": profile["target_resource"] != "none" and not args.disable_bonect_candidate_gate,
                    "target_resource": profile["target_resource"],
                    "adaptive_replay_controller_enabled": (
                        adaptive_replay_controller_enabled
                    ),
                    "real_delta_calibration": {
                        "enabled": bool(real_delta_artifact_path),
                        "artifact_path": real_delta_artifact_path,
                        "require_training_gate": True,
                        "require_prediction": True,
                        "require_support": True,
                        "unsupported_policy": "keep_uncertain",
                        "uncertain_replay_min_count": 3,
                        "min_plan_feature_coverage": real_delta_min_plan_feature_coverage,
                        "description": (
                            "Use paired restored-default real DB-QPS/CPU/Memory/IO delta labels "
                            "to correct candidate ranking. Refuse predictions outside the "
                            "validated plan-feature support domain."
                        ),
                    },
                    "require_predicted_bottleneck_decrease": not args.disable_bonect_candidate_gate,
                    "require_passed_candidate": False,
                    "require_explainable_direction": profile["target_resource"] != "none",
                    "allow_known_real_direction_seed_gate_bypass": (
                        not blind_recovery_mode
                        and profile["target_resource"] in {"cpu", "memory", "io"}
                    ),
                    "known_real_seed_min_interpolation_step": 0.5,
                    "preflight_param_feature_filter": {
                        "enabled": True,
                        "min_changed_endpoint_count": 1,
                        "min_active_params": 1,
                        "fallback_to_resource_values": False,
                        "drop_if_feature_unavailable": False,
                        "interaction_probe": {
                            "enabled": True,
                            "max_partner_params": (
                                len(COST_PARAM_KEYS) - 1
                                if broad_blind_search_mode
                                else 3
                            ),
                            "min_changed_pair_count": 1,
                        },
                        "description": (
                            "Before candidate generation, probe each active cost parameter at "
                            "its current min/max bounds. If neither endpoint changes the "
                            "resource-estimator input feature signature relative to baseline, "
                            "test bounded two-parameter interactions before removing it from "
                            "the candidate-generation active set."
                        ),
                    },
                    "min_delta_util_pct": (
                        0.50
                        if profile["target_resource"] in {"cpu", "memory", "io"}
                        else 0.0
                    ),
                    "min_delta_abs": 0.0,
                    "target_error_margin_util_pct": phase2_target_error_margin_util_pct,
                    "target_error_margin_reason": (
                        "Memory/AP post-fix replay observed three consecutive candidates "
                        "where predicted Memory down became actual Memory up; use a 5pp "
                        "conservative target-down margin for ranking/gate/prefix replay. "
                        "This changes only seven-cost-parameter candidate selection, not "
                        "the final acceptance rule."
                        if is_memory_hybench_ap
                        else ""
                    ),
                    "min_other_up_util_pct": phase2_gate_min_other_up_util_pct,
                    "other_up_ignored_resources": [],
                    "other_up_ignored_resources_reason": (
                        "2026-07-03 user semantics: for every bottleneck, any one "
                        "non-target resource may explain the resource shift. Memory/AP "
                        "therefore allows IO as an other-up signal in small-loop ranking; "
                        "final acceptance still requires real replay evidence."
                        if (spec.bottleneck, spec.benchmark) == ("memory", "hybench_ap")
                        else ""
                    ),
                    "score_min_target_down_util_pct": phase2_score_min_target_down_util_pct,
                    "score_min_other_up_util_pct": phase2_score_min_other_up_util_pct,
                    "score_target_goal_weight": 180.0,
                    "score_other_goal_weight": 120.0,
                    "score_target_shortfall_weight": 140.0,
                    "score_other_shortfall_weight": 420.0,
                    "require_score_goal_prefix": profile["target_resource"] != "none",
                    "score_goal_prefix_min_count": (
                        3 if profile["target_resource"] in {"cpu", "memory", "io"} else 1
                    ),
                    "score_goal_prefix_min_target_down_util_pct": phase2_prefix_min_target_down_util_pct,
                    "score_goal_prefix_min_other_up_util_pct": phase2_prefix_min_other_up_util_pct,
                    "score_goal_prefix_fill_min_topk": candidate_replay_top_k,
                    "score_goal_prefix_near_miss_admission": {
                        "enabled": profile["target_resource"] in {"cpu", "memory", "io"},
                        "min_count": (
                            3 if profile["target_resource"] in {"cpu", "memory", "io"} else 0
                        ),
                        "min_target_down_util_pct": phase2_prefix_min_target_down_util_pct,
                        "min_other_up_util_pct": phase2_gate_min_other_up_util_pct,
                        "allow_zero_other_up": False,
                        "allow_target_down_only": (
                            profile["target_resource"] == "cpu"
                            or is_memory_hybench_ap
                        ),
                        "max_non_target_down_util_pct": (
                            8.00
                            if profile["target_resource"] == "cpu" or is_memory_hybench_ap
                            else 5.00
                        ),
                        "count_known_real_seed": False,
                        "description": (
                            "Replay-admission only: if the estimator predicts enough target "
                            "bottleneck decrease but misses the stronger score margin, keep "
                            "a small diagnostic prefix only when at least one non-target "
                            "resource is still predicted to increase. Zero non-target uplift "
                            "does not satisfy the phase-2 resource-direction semantics. "
                            "For CPU and Memory/AP, target-down-only diagnostic admission is "
                            "allowed when strict other-up prediction is empty or noisy, "
                            "bounded by the non-target down guard, and final acceptance still "
                            "requires real bottleneck-down plus a real non-target resource increase."
                        ),
                    },
                    "require_all_non_target_up": False,
                    "max_non_target_down_util_pct": 5.00,
                    "topk_early_return": {
                        "enabled": True,
                        "min_iterations": (
                            6
                            if profile["target_resource"] == "cpu"
                            else 5
                            if profile["target_resource"] in {"memory", "io"}
                            else 4
                        ),
                        "min_topk": candidate_top_k,
                        "min_gate_passed": (
                            3
                            if profile["target_resource"] in {"cpu", "memory", "io"}
                            else 0
                        ),
                        "min_unique_resource_value_count": (
                            8
                            if profile["target_resource"] == "cpu"
                            else 6
                            if profile["target_resource"] in {"memory", "io"}
                            else 3
                        ),
                        "min_plan_change_topk": (
                            4
                            if profile["target_resource"] == "cpu"
                            else 3
                            if profile["target_resource"] in {"memory", "io"}
                            else 0
                        ),
                        "skip_post_loop_probes_when_ready": False,
                        "description": (
                            "Return from the coordinate scan once enough non-baseline, score-ranked, "
                            "plan-changing, resource-diverse, safety-filtered, and direction-goal "
                            "candidates exist for real-validation ranking. Resource direction is "
                            "a scoring objective and prefix readiness gate, not a hard candidate "
                            "filter before replay. Even when the replay prefix is ready, still run "
                            "the post-loop surrogate/backprop and pair/risk probes so phase-2 "
                            "candidate generation contains the required robust-ranking plus "
                            "surrogate proposal evidence before real replay."
                        ),
                    },
                    "topk_count_fill": {
                        "enabled": True,
                        "respect_safety": True,
                        "respect_source_quota": True,
                        "description": (
                            "If resource-signature diversity or plateau controls stop top-k "
                            "selection before top20, keep filling with parameter-distinct, "
                            "safety-passed candidates. This preserves the required top20 "
                            "small-loop ranking calibration prefix while still sorting strict "
                            "direction candidates first."
                        ),
                    },
                    "known_real_seed_prefix_reserve": {
                        "enabled": (
                            not blind_recovery_mode
                            and profile["target_resource"] in {"cpu", "memory", "io"}
                        ),
                        "min_count": (
                            3 if profile["target_resource"] == "cpu" else
                            5 if profile["target_resource"] == "memory" else
                            3 if profile["target_resource"] == "io" else 0
                        ),
                        "preferred_other_resources": (
                            ["memory", "io"]
                            if profile["target_resource"] == "cpu"
                            else ["cpu", "io"]
                            if profile["target_resource"] == "memory"
                            else ["cpu", "memory"]
                            if profile["target_resource"] == "io"
                            else []
                        ),
                        "require_preferred_other_up": (
                            profile["target_resource"] in {"cpu", "memory", "io"}
                        ),
                        "respect_safety": True,
                        "respect_source_quota": False,
                        "allow_feedback_rejected_neighborhood": False,
                        "description": (
                            "Reserve a small top20 replay-calibration prefix for known-real "
                            "phase-2 direction seeds. Bottleneck scenarios keep recent "
                            "VM-explainable DB-QPS anchors in the replay prefix to verify "
                            "reproducibility before trusting a noisy resource estimator; final "
                            "acceptance still uses real replay evidence."
                        ),
                    },
                    "db_qps_proxy_score_enabled": True,
                    "db_qps_unknown_penalty": 8.0,
                    "db_qps_distance_penalty_weight": 6.0,
                    "db_qps_boundary_penalty_weight": 6.0,
                    "db_qps_negative_hard_penalty": 50.0,
                    "db_qps_gain_score_weight": 3.0,
                    "db_qps_loss_score_weight": 4.0,
                    "db_qps_known_risk_penalty_enabled": not blind_recovery_mode,
                    "db_qps_proxy_apply_to_scan_score": True,
                    "known_param_risk_blocks_scan_update": True,
                    "db_qps_high_risk_boundary_hits": 2.0,
                    "db_qps_high_risk_distance": 1.50,
                    "db_qps_high_risk_sort_status": -0.25,
                    "exclude_high_risk_unknown_from_topk": True,
                    "exclude_high_risk_known_negative_from_topk": True,
                    "exclude_known_param_risk_from_topk": True,
                    "exclude_negative_db_qps_sort_status_from_topk": True,
                    "cpu_low_tuple_cost_risk_enabled": (
                        not blind_recovery_mode
                        and profile["target_resource"] == "cpu"
                    ),
                    "cpu_low_tuple_cost_threshold": 0.001,
                    "cpu_low_tuple_cost_penalty": 35.0,
                    "cpu_low_tuple_single_param_extra_penalty": 20.0,
                    "cpu_low_tuple_cost_sort_status": -0.75,
                    "cpu_low_operator_cost_risk_enabled": (
                        not blind_recovery_mode
                        and profile["target_resource"] == "cpu"
                    ),
                    "cpu_low_operator_cost_threshold": 0.001,
                    "cpu_low_operator_cost_penalty": 45.0,
                    "cpu_low_operator_single_param_extra_penalty": 20.0,
                    "cpu_low_operator_cost_sort_status": -0.75,
                    "cpu_low_seq_high_parallel_risk_enabled": (
                        not blind_recovery_mode
                        and profile["target_resource"] == "cpu"
                    ),
                    "cpu_low_seq_high_parallel_seq_threshold": 0.75,
                    "cpu_low_seq_high_parallel_tuple_threshold": 0.20,
                    "cpu_low_seq_high_parallel_penalty": 90.0,
                    "cpu_low_seq_high_parallel_sort_status": -0.90,
                    "feedback_rejected_neighborhood_penalty": 60.0,
                    "feedback_rejected_neighborhood_radius": 0.35,
                    "feedback_rejected_neighborhood_high_risk_radius": 0.18,
                    "feedback_rejected_neighborhood_soft_radius": 0.50,
                    "feedback_rejected_neighborhood_sort_status": -1.0,
                    "feedback_rejected_neighborhood_limit": 16,
                    "feedback_rejected_neighborhoods": (
                        []
                        if blind_recovery_mode
                        else bonect_feedback_rejected_neighborhoods(
                            spec.bottleneck,
                            spec.benchmark,
                        )
                    ),
                    "risk_boundary_probe": {
                        "enabled": False,
                        "max_candidates": 24,
                        "min_target_delta_util_pct": 0.50,
                        "min_other_up_util_pct": phase2_gate_min_other_up_util_pct,
                        "description": (
                            "If strict CPU direction near-misses are blocked by known "
                            "low-cost risk rules, evaluate clipped variants on the risk "
                            "boundary so top-k does not become empty before real replay."
                        ),
                    },
                    "soft_directional_fallback": {
                        "enabled": profile["target_resource"] != "none",
                        "min_topk": (
                            6
                            if profile["target_resource"] == "cpu"
                            else 4
                            if profile["target_resource"] in {"memory", "io"}
                            else 3
                        ),
                        "min_target_delta_util_pct": 0.50,
                        "min_other_up_util_pct": phase2_gate_min_other_up_util_pct,
                        "require_all_non_target_up": False,
                        "max_non_target_down_util_pct": 5.00,
                        "description": (
                            "Fallback only when the 0.5pp semantic direction gate plus DB-QPS/risk "
                            "top-k safety returns no real-validation candidates. It still "
                            "requires bottleneck down and at least one non-target resource up."
                        ),
                    },
                    "robust_ranking": {
                        "enabled": True,
                        "variant": "pairwise_rank_surrogate_v5",
                        "source_report": (
                            "report/bonect/latest_robust_ranking_validation_plan/"
                            "final_robust_ranking_evaluation.md"
                        ),
                        "validation_manifest": (
                            "report/bonect/latest_robust_ranking_validation_plan/"
                            "final_robust_ranking_evaluation_validation_manifest.md"
                        ),
                        "next_round_recommendation": (
                            "report/bonect/latest_robust_ranking_validation_plan/"
                            "final_robust_ranking_evaluation_next_round_recommendation.md"
                        ),
                        "score_formula": (
                            "point_score - scenario_target_uncertainty_weight * target_uncertainty "
                            "- scenario_uncertainty_weight * total_uncertainty - 0.10 * overload "
                            "+ pairwise_surrogate_v5_plateau_adjustment + target_down_first_resource_direction_objective "
                            "+ 1e-9 * plateau_tiebreak"
                        ),
                        "error_bound_inputs": [
                            "train_test_residual_error_bound",
                            "baseline_residual_or_anchor_calibration_bound",
                            "per_resource_uncertainty",
                        ],
                        "tie_break": "plan_confidence, lower uncertainty_penalty, plateau_tiebreak",
                    },
                    "plateau_control": {
                        "enabled": True,
                        "max_same_predicted_resource_topk": max(
                            1,
                            int(args.bonect_plateau_max_same_resource_topk),
                        ),
                        "max_same_predicted_resource_value_topk": max(
                            1,
                            int(args.bonect_plateau_max_same_resource_value_topk),
                        ),
                        "score_epsilon": 1e-6,
                        "source_quota": {
                            "candidate_anchor_probe": 0,
                            "candidate_near_hit_anchor_probe": 0,
                            "candidate_surrogate_probe": 0,
                            "parameter_scan": 4,
                            "candidate_pair_probe": candidate_top_k,
                        },
                        "description": (
                            "When robust-ranking top-k collapses to the same predicted resource "
                            "values, or the same predicted values plus near-identical robust "
                            "score, cap that plateau before real validation and force search "
                            "into distinct estimated resource regions."
                        ),
                    },
                    "directional_rescue": {
                        "enabled": True,
                        "min_topk": (
                            min(candidate_replay_top_k, 24)
                            if profile["target_resource"] == "cpu"
                            else 4
                            if profile["target_resource"] in {"memory", "io"}
                            else 3
                        ),
                        "min_target_delta_util_pct": 0.50,
                        "min_other_up_util_pct": phase2_gate_min_other_up_util_pct,
                        "require_all_non_target_up": False,
                        "max_non_target_down_util_pct": 5.00,
                        "prefer_directional_in_ranking": True,
                        "description": (
                            "Phase-2 stop-and-diagnose rescue: reserve real-validation top-k "
                            "slots for candidates whose estimated resource deltas satisfy the "
                            "explainable target direction. Within the reserved lane and ordinary "
                            "ranking, order by DB-QPS proxy first and resource strength second."
                        ),
                    },
                    "description": (
                        "Use pairwise_rank_surrogate_v5 robust ranking for top-k. In strict mode, "
                        "do not send a brute-force candidate to real validation unless the estimator "
                        "predicts a measurable decrease in the target bottleneck resource; in relaxed "
                        "mode, keep this as logged diagnostics and let real validation arbitrate."
                    ),
                },
                "candidate_pair_probe": {
                    "enabled": True,
                    "only_if_no_gate_candidate": False,
                    "values_per_param": pair_probe_values_per_param,
                    "full_factorial_grid": pair_probe_full_factorial_grid,
                    "max_combination_size": pair_probe_max_combination_size,
                    "max_candidates": pair_probe_max_candidates,
                    "wall_clock_seconds": pair_probe_wall_clock_seconds,
                    "min_generated_before_time_limit": (
                        pair_probe_min_generated_before_time_limit
                    ),
                    "progress_interval": pair_probe_progress_interval,
                    "anchor_probe": {
                        "enabled": False,
                        "anchors": reference_path_anchors,
                        "interpolation_steps": BONECT_ANCHOR_PATH_STEPS,
                        "max_candidates": 0,
                        "min_topk": 0,
                        "description": (
                            "Disabled when the phase-2 inner loop uses the full active-param 5^k grid; "
                            "forced best replay anchors are handled separately before real replay."
                        ),
                    },
                    "near_hit_anchor_probe": near_hit_anchor_probe_cfg,
                    "surrogate_probe": {
                        "enabled": False,
                        "backend": "torch_or_scipy",
                        "max_candidates": 0,
                        "optimization_steps": 240 if is_memory_hybench_ap else 180,
                        "seed_top_k": 80 if is_memory_hybench_ap else 48,
                        "rbf_bandwidth": 1.25 if is_memory_hybench_ap else 1.10,
                        "snap_to_grid": True,
                        "db_gain_weight": 1.0,
                        "direction_reward_weight": (
                            1.20
                            if profile["target_resource"] == "cpu"
                            else 1.20
                            if is_memory_hybench_ap
                            else 0.55
                            if profile["target_resource"] in {"memory", "io"}
                            else 0.45
                        ),
                        "uncertainty_penalty_weight": (
                            0.55 if profile["target_resource"] == "cpu" else 0.40
                        ),
                        "direction_miss_penalty_weight": (
                            2.00
                            if profile["target_resource"] == "cpu"
                            else 4.00
                            if is_memory_hybench_ap
                            else 2.00
                            if profile["target_resource"] in {"memory", "io"}
                            else 0.20
                        ),
                        "catastrophic_risk_penalty_weight": (
                            0.20 if profile["target_resource"] == "cpu" else 0.10
                        ),
                        "distance_penalty_weight": 0.08,
                        "boundary_penalty_weight": 0.20,
                        "description": (
                            "Phase-2 differentiable surrogate/backprop proposal in seven-cost-param "
                            "log-space. It uses only estimated resource/ranking/plan-confidence inputs "
                            "from the inner loop, snaps continuous optima back to the legal grid, and "
                            "leaves real benchmark validation as the final arbiter."
                        ),
                    },
                    "source_quota": {
                        "candidate_anchor_probe": 0,
                        "candidate_near_hit_anchor_probe": 0,
                        "candidate_surrogate_probe": 0,
                        "parameter_scan": 4,
                        "candidate_pair_probe": candidate_top_k,
                    },
                    "description": (
                        "Probe either the full active-cost-parameter grid or a bounded sparse "
                        "combination grid with five representative values per active parameter, "
                        "then let robust ranking choose the real replay prefix."
                    ),
                },
            },
        },
        "metrics": {
            "source": "plan-estimator",
            "plan_estimator": {
                "bootstrap_workload_once": True,
                "resource_types": ["memory", "cpu", "io"],
                "shared": {
                    "calibration_factor": 1.0,
                    "explain_workers": args.plan_explain_workers,
                    "block_size": 8192,
                    "work_mem_bytes": args.plan_work_mem_mb * 1024 * 1024,
                    "learned_cpu_vs_plan_formula_ratio_min": args.plan_learned_cpu_vs_formula_ratio_min,
                    "learned_cpu_vs_plan_formula_ratio_max": args.plan_learned_cpu_vs_formula_ratio_max,
                    "learned_cpu_vs_plan_formula_ratio_guard_mode": "baseline_sanity",
                    "learned_cpu_anchor_delta_scale_max": args.plan_learned_cpu_anchor_delta_scale_max,
                    "learned_io_plateau_use_baseline_scaled_plan_formula": True,
                    "learned_io_direction_disagreement_use_baseline_scaled_plan_formula": True,
                    "learned_memory_plateau_use_baseline_scaled_plan_formula": True,
                    "learned_memory_plateau_rel_tolerance": 0.01,
                    "learned_point_prediction_stage": (
                        "after_baseline_anchor_before_error_bound"
                    ),
                },
                "model_retraining": {
                    "enabled": True,
                    "run_id": f"{run_id}_{spec.bottleneck}_{spec.benchmark}",
                    "feature_cache_dir": "/home/anonymous/program/features_cache_M1",
                    "output_dir": str(resource_model_output_dir),
                    "apply_to_runtime": True,
                    "baseline_error_bound_enabled": True,
                    "strict_retrain_gate": True,
                    "fallback_on_error": args.bonect_allow_v5_retrain_fallback,
                    "fallback_model_version": "v5",
                    "fallback_policy": (
                        "For phase-2 top20/ranking validation, current-baseline CPU/IO/Memory "
                        "retraining is strict by default. Fallback to latest v5 robust ranking and "
                        "pre-retrain resource-model artifacts is allowed only when strict_retrain_gate "
                        "is disabled and fallback_on_error is explicitly enabled."
                    ),
                    "require_all_resources": True,
                    "seed": args.random_seed,
                    "quality_gate": {
                        "enabled": args.resource_model_quality_gate,
                        "required_resources": resource_model_quality_required_resources,
                        "optional_resources": resource_model_quality_optional_resources,
                        "min_test_samples": args.resource_model_quality_min_test_samples,
                        "max_test_relative_error_p95": None,
                        "max_test_relative_error_max": None,
                        "min_test_r2": args.resource_model_quality_min_test_r2,
                        "selected_model_only": True,
                        "workload_family": "H" if spec.benchmark == "hybench_ap" else "CH",
                        "io_hard_targets": ["total"],
                        "warning_test_relative_error_p95": args.resource_model_quality_max_test_relative_error_p95,
                        "warning_test_relative_error_max": args.resource_model_quality_max_test_relative_error_max,
                        "enforce_test_relative_error_p95": False,
                        "enforce_test_relative_error_max": False,
                        "enforce_test_r2": True,
                        "warning_policy": (
                            "Relative-error p95/max are logged as robust-ranking uncertainty risks because "
                            "near-zero CPU/IO denominators make tail relative errors unstable; hard gate checks "
                            "runtime-selected CPU model and IO total model sample coverage before top-k."
                        ),
                        "fail_on_missing_metrics": True,
                        "fail_on_optional_resource": False,
                        "baseline_sanity": {
                            "enabled": True,
                            "required_resources": ["cpu", "io", "memory"],
                            "max_relative_error": 1.0,
                            "max_relative_error_by_resource": {
                                "cpu": 1.0,
                                "io": 1.0,
                                "memory": 1.0,
                            },
                            "signal_floor": 1e-9,
                            "allow_plan_formula_fallback": True,
                            "hard_fail": True,
                            "prediction_stage": "after_baseline_anchor_before_error_bound",
                            "anchor_delta_scale_min_by_resource": {
                                "cpu": 0.05,
                                "io": 0.05,
                                "memory": 0.05,
                            },
                            "anchor_delta_scale_max_by_resource": {
                                "cpu": args.plan_learned_cpu_anchor_delta_scale_max,
                                "io": 10.0,
                                "memory": 10.0,
                            },
                        },
                        # iter107: memory_replay_calibration gate (override
                        # §2026-07-06 iter99 §7 #1 / §2026-07-06 23:55 iter103
                        # §8 #3 / §2026-07-07 03:12 iter106 §715-806 next_iter
                        # PRIORITY #1). validate_resource_model_quality_gate
                        # in parameter_optimizer.py reads this block. The
                        # evidence is a robust-ranking direction-risk signal by
                        # default; set hard_fail=true only for strict audits
                        # that intentionally stop before optimization.
                        "memory_replay_calibration": {
                            "enabled": memory_replay_calibration_enabled,
                            "hard_fail": False,
                            "max_predicted_down_actual_up_ratio":
                                memory_replay_calibration_max_predicted_down_actual_up_ratio,
                            "min_predicted_down_count":
                                memory_replay_calibration_min_predicted_down_count,
                            "evidence": memory_replay_calibration_evidence,
                            "evidence_path": memory_replay_calibration_evidence_path_str,
                        },
                    },
                },
                "memory": {
                    "enabled": True,
                    "learned_model": {
                        "enabled": True,
                        "path": "config/generated/memory_resource_models.json",
                        "model_set": "AP_only" if spec.benchmark == "hybench_ap" else "TP_Hybrid",
                        "required": True,
                    },
                    "overlap_factor": 0.6,
                    "concurrency_formula": "baseline_resident_plus_weighted_working",
                    "resident_memory_scale": 1.0,
                    "beta_sort": 1.0,
                    "beta_hash": 1.0,
                    "beta_hashagg": 1.0,
                    "beta_materialize": 1.0,
                    "beta_memoize": 0.0,
                },
                "cpu": {
                    "enabled": True,
                    "learned_model": {
                        "enabled": True,
                        "path": "config/generated/cpu_resource_models.json",
                        "model_set": "C_only" if spec.benchmark == "hybench_xp" else "auto",
                        "required": True,
                    },
                    "cpu_tuple_cost": 0.01,
                    "cpu_index_tuple_cost": 0.005,
                    "cpu_operator_cost": 0.0025,
                    "parallel_setup_cost": 1000.0,
                    "parallel_tuple_cost": 0.1,
                    "cpu_seconds_per_work": 1e-6,
                    "cpu_seconds_bias": 0.0,
                    "overlap_factor": 1.0,
                    "fallback_cost_scale": 1.0,
                },
                "io": {
                    "enabled": True,
                    "learned_model": {
                        "enabled": True,
                        "path": "config/generated/io_resource_models.json",
                        "model_set": "auto" if spec.benchmark == "hybench_xp" else "CH",
                        "auto_select_by_baseline_actual": spec.benchmark == "hybench_xp",
                        "auto_candidate_model_sets": ["C", "CH", "H"],
                        "required": True,
                    },
                    "seq_page_cost": 1.0,
                    "random_page_cost": 4.0,
                    "read_overlap_factor": 1.0,
                    "write_overlap_factor": 1.0,
                },
                "logging": {
                    "enabled": True,
                    "log_topn": 1,
                    "log_operator_breakdown": True,
                    "log_query_breakdown": True,
                    "candidate_probe_compact_logging": True,
                },
            },
            "docker_runtime": {
                "container": args.docker_runtime_container,
                "interval_sec": args.runtime_monitor_interval_sec,
                "warmup_sec": args.runtime_monitor_warmup_sec,
                "memory_mode": args.docker_runtime_memory_mode,
                "io_from_prometheus": False,
            },
            "vm_runtime": {
                "host": args.pg_host,
                "ssh_target": args.vm_runtime_ssh_target,
                "ssh_port": args.vm_runtime_ssh_port,
                "ssh_identity_file": args.vm_runtime_ssh_identity_file,
                "interval_sec": args.runtime_monitor_interval_sec,
                "warmup_sec": args.runtime_monitor_warmup_sec,
                "cpu_mode": args.vm_runtime_cpu_mode,
                "io_mode": args.vm_runtime_io_mode,
                "memory_mode": args.vm_runtime_memory_mode,
                "disk_devices": [args.vm_disk_dev],
            },
        },
        "benchmark": {
            "workload_cmd": optimizer_workload_command(spec),
            "workload_timeout_sec": workload_timeout_sec,
            "hybench_statement_timeout_ms": args.hybench_statement_timeout_ms,
            "baseline_only": args.baseline_only,
            "warmup_cmd": optimizer_workload_command(spec),
            "warmup_enabled": args.warmup_enabled,
            "warmup_timeout_sec": args.warmup_timeout_sec,
            "validation_warmup_cmd": optimizer_workload_command(spec),
            "validation_warmup_enabled": args.validation_warmup_enabled,
            "validation_warmup_required": False,
            "validation_warmup_timeout_sec": args.validation_warmup_timeout_sec,
            "validation_warmup_drain_timeout_sec": (
                args.validation_warmup_drain_timeout_sec
            ),
            "validation_warmup_drain_poll_sec": args.validation_warmup_drain_poll_sec,
            "validation_warmup_drain_max_app_connections": (
                args.validation_warmup_drain_max_app_connections
            ),
            "validation_warmup_drain_application_name": (
                args.validation_warmup_drain_application_name
            ),
            "hybench_statement_sql_files": list(args.hybench_statement_sql_file),
            "hybench_setup_sql_files": hybench_setup_sql_paths,
        },
        "logging": {
            "history_path": str(history_path),
        },
        "pre_optimizer_process_cleanup": {
            "enabled": not str(run_id).startswith("pg_bonect_phase2"),
            "disabled_reason": (
                "Phase-2 Bonect runs use explicit preflight process checks; "
                "global pgrep cleanup is disabled to avoid terminating peer "
                "top20 ranking/real-replay loops."
            ),
            "require_current_run_match": True,
            "patterns": ["hybench", "APClient", "parameter_optimizer.py"],
            "after_warmup_timeout": True,
            "current_run_id": run_id,
            "ownership_cmdline_substrings": [
                str(output_root),
                str(history_path),
                str(config_path.parent),
                str(spec.log_path),
            ],
            "skip_cmdline_substrings": [
                "pg_bonect_phase2",
                "history/pg_benchmark_bottleneck_matrix/pg_bonect_phase2",
                "bonect_supervisor.py --run-id pg_bonect_phase2",
                str(output_root),
            ],
        },
        "resource_iteration_reports": {
            "enabled": True,
            "markdown": True,
            "output_dir": str(output_root / spec.bottleneck / f"{spec.benchmark}_resource_reports"),
        },
        "postgres": {
            "host": args.pg_host,
            "port": args.pg_port,
            "user": args.pg_user,
            "dbname": spec.pg_dbname,
        },
        "plan_capture": {
            "enabled": True,
            "limit": args.plan_capture_limit,
            "output_dir": str(plan_capture_dir),
            "modes": ["generic"],
        },
        "pre_benchmark_recovery": {
            "enabled": True,
            "required": True,
            "target_db": spec.pg_dbname,
            "sql": [*hybench_setup_sql, "CHECKPOINT;"],
            "snapshot_restore": {
                "enabled": hybench_snapshot_restore_enabled,
                "required": hybench_snapshot_restore_enabled,
                "only_for_tp_workloads": False,
                "runner": "local",
                "command": hybench_snapshot_restore_command,
                "timeout_sec": args.hybench_snapshot_restore_timeout_sec,
                "tags": ["baseline", "final", "feedback"],
                "template_db": args.hybench_snapshot_template_db,
                "policy": (
                    "HyBench mutates database state. Bonect baseline/final/feedback real "
                    "measurements must restore the same immutable template before warmup "
                    "and measurement so resource-model calibration and replay ranking are "
                    "comparable."
                ),
            },
            "restart_db": {
                "enabled": True,
                "tags": ["baseline", "final", "feedback"],
            },
            "vm_drop_cache": {
                "enabled": True,
                "required": True,
                "ssh_target": args.vm_runtime_ssh_target,
                "ssh_port": args.vm_runtime_ssh_port,
                "ssh_identity_file": args.vm_runtime_ssh_identity_file,
                "command": args.vm_drop_cache_command,
                "tags": ["baseline", "final", "feedback"],
            },
        },
        "post_benchmark_cleanup": {
            "enabled": True,
            "sql": "DISCARD ALL;",
            "terminate_idle": False,
            "target_user": args.pg_user,
            "target_db": spec.pg_dbname,
            "restart_db": False,
            "restart_required": True,
            "vm_restart_cmd": args.pg_start_command,
            "vm_restart_ssh_target": args.vm_runtime_ssh_target,
            "vm_restart_ssh_port": args.vm_runtime_ssh_port,
            "vm_restart_ssh_identity_file": args.vm_runtime_ssh_identity_file,
            "ready_db": spec.pg_dbname,
            "ready_host": args.pg_host,
            "ready_port": str(args.pg_port),
            "ready_sql": "SELECT 1;",
            "restart_wait_timeout_sec": args.pg_ready_timeout_sec,
            "restart_poll_interval_sec": args.vm_ready_poll_sec,
        },
        "noise_guard": {
            "enabled": False,
        },
        "auto_explain": {
            "enabled": False,
            "required": False,
            "output_dir": str(output_root / spec.bottleneck / f"{spec.benchmark}_auto_explain"),
        },
        "final_evaluation": {
            "run_validation": True if args.use_resource_estimator else args.final_validation,
            "actual_score_required": True if args.use_resource_estimator else False,
            "runtime_source": "vm-runtime",
            "degradation_check_enabled": False,
            "feedback_loop": {
                "enabled": bool(args.final_feedback_loop),
                "max_rounds": max(int(args.final_feedback_max_rounds), 24),
                "post_convergence_rounds": args.post_convergence_explore_iter,
                "target_util_pct": args.final_feedback_target_util_pct,
                "tolerance_pct": args.final_feedback_tolerance_pct,
                "overload_util_pct": args.final_feedback_overload_util_pct,
                "hard_overload_util_pct": hard_overload_util_pct,
                "resource_target_ranges": resource_target_ranges,
                "target_resources": (
                    []
                    if profile["target_resource"] == "none"
                    else [str(profile["target_resource"])]
                ),
                "single_bottleneck_protection": profile["target_resource"] in {"cpu", "memory", "io"},
                "require_all_resources_converged": profile["target_resource"] == "none",
                "non_target_overload_util_pct": args.final_feedback_overload_util_pct,
                "non_target_target_util_pct": args.final_feedback_target_util_pct,
                "retarget_enabled": False,
                "retarget_once": False,
                "accept_by_actual_score": True,
                "actual_score_tolerance": 0.0,
                "adaptive_rejection_enabled": True,
                "reject_shrink_first_after": 1,
                "reject_shrink_second_after": 2,
                "reject_focus_after": 2,
                "reject_focus_top_n": 7,
                "reject_stop_after": 10,
                "skip_rejected_resource_signatures": False,
                "online_estimator_correction_enabled": True,
                "online_estimator_correction_learning_rate": 0.5,
                "performance_guard_enabled": True,
                "replay_candidate_pruning": {
                    "enabled": True,
                    "apply_to_initial_topk": True,
                    "apply_to_feedback_topk": True,
                    "keep_known_real_seed": True,
                    "known_real_seed_min_interpolation_step": 0.5,
                    "resource_improvement_margin_pp": 0.0,
                    "fallback_bound_util_pp": 1.0,
                    "log_all_checks": True,
                    "db_qps_proxy_pruning_enabled": True,
                    "db_qps_proxy_uncertainty_multiplier": 1.0,
                    "db_qps_proxy_missing_uncertainty_pct": 5.0,
                    "db_qps_proxy_margin_pct": 0.0,
                    "description": (
                        "The inner loop still returns top20 ranked candidates. Before each "
                        "real replay, prune only candidates whose optimistic resource "
                        "bounds still cannot beat the current best-so-far resource "
                        "improvement, whose optimistic DB QPS proxy cannot beat the "
                        "current best-so-far DB QPS, or whose bounds cannot satisfy the "
                        "explainable resource direction."
                    ),
                },
                "min_workload_qps_ratio": 0.0,
                "min_workload_tps_ratio": 0.0,
                "min_db_qps_ratio": 1.0,
                "min_db_tps_ratio": 0.0,
                "max_p90_ratio": latency_max_ratio,
                "max_p95_ratio": latency_max_ratio,
                "max_p99_ratio": latency_max_ratio,
                "max_top_sql_degradation_pct": 10000.0,
                "resource_iteration_goal": {
                    "enabled": True,
                    "db_qps_must_increase": True,
                    "db_tps_must_increase": False,
                    "stop_after_primary_acceptance": False,
                    "continue_after_primary_acceptance_rounds": 5,
                    "select_best_primary_by_db_qps": True,
                    "target_db_qps_delta_pct": phase2_strong_min_db_qps_delta_pct,
                    # Any real bottleneck decrease is explainable under the phase-2
                    # contract. Keep a positive epsilon because the optimizer uses
                    # this field when counting long-term-goal hits.
                    "target_resource_delta_pp": (
                        0.0 if profile["target_resource"] == "none" else 1e-6
                    ),
                    "latency_max_degradation_pct": 10000.0,
                    "resource_direction_policy": resource_direction_policy,
                    "resource_direction_min_delta_pct": 0.0,
                    "bottleneck_resource": profile["target_resource"],
                    "other_up_ignored_resources": [],
                    "fallback_candidate_enabled": True,
                    "fallback_acceptance_enabled": False,
                    "fallback_bottleneck_max_increase_pct": 2.0,
                    "strong_constraints": {
                        "enabled": True,
                        "min_db_qps_delta_pct": phase2_strong_min_db_qps_delta_pct,
                        "preferred_db_qps_delta_pct": phase2_preferred_db_qps_delta_pct,
                        "none_initial_util_min_pct": 20.0,
                        "none_initial_util_max_pct": 30.0,
                        "none_initial_resource_ranges": {
                            "cpu": [20.0, 35.0],
                            "memory": [15.0, 50.0],
                            "io": [12.0, 30.0],
                        },
                        "none_require_all_initial_resources_in_range": True,
                        "none_require_all_resources_up": False,
                        "none_require_any_final_resource_in_good_range": True,
                        "none_require_any_resource_lifted_into_good_range": True,
                        "none_min_sweet_lift": {
                            "cpu": 10.0,
                            "memory": 10.0,
                            "io": 10.0,
                        },
                        "none_require_all_final_resources_in_good_range": False,
                        "io_good_range_uses_normalized_mib_s": True,
                        "bottleneck_require_single_initial_bottleneck": True,
                        "bottleneck_initial_target_min_util_pct": 80.0,
                        "bottleneck_initial_non_target_max_util_pct": 79.999,
                        "bottleneck_require_target_down": True,
                        "bottleneck_require_target_not_bottleneck": True,
                        "bottleneck_require_target_in_good_range": False,
                        "bottleneck_require_any_other_up": True,
                        "bottleneck_require_any_other_in_good_range": True,
                        "bottleneck_prefer_any_other_in_good_range": True,
                        "target_min_decrease_pp": 5.0,
                        "preferred_target_decrease_pp": 10.0,
                        "other_up_min_delta_pp": 0.0,
                        "diagnose_unmet_goal": True,
                        "description": (
                            "Strong Phase-2 success requires DB QPS +15% (preferred +20%). "
                            "For bottleneck scenarios the baseline must contain exactly one target "
                            "bottleneck, the target must decrease by at least 5 utilization points "
                            "from >=80% to <80%; a decrease of at least 10 points is preferred. "
                            "At least one "
                            "non-target resource must increase, and at least "
                            "one non-target resource must finish in its good range. For no-bottleneck, "
                            "the baseline must satisfy the per-resource baseline ranges; the candidate "
                            "must lift at least one resource by the configured minimum into its good "
                            "range. Raising more resources "
                            "or all resources into good ranges is preferred, not the minimum hard "
                            "gate. If no candidate satisfies "
                            "these constraints, keep the global best DB-QPS fallback but classify "
                            "the failure as baseline/workload, search, ranking, estimator, or "
                            "resource-scoring related."
                        ),
                    },
                    "description": (
                        "Phase-2 DB-QPS amplification: do not stop at the first primary acceptance. "
                        "Keep validating robust top-k/surrogate/path candidates, select best-so-far by "
                        "DB QPS delta among candidates that satisfy the strong Phase-2 constraints. "
                        "Fallback candidates are only diagnostic until the main agent proves workload "
                        "construction cannot produce a stronger explainable point."
                    ),
                },
                "actual_score": {
                    "mode": "composite",
                    "resource_weight": 1.0,
                    "metric_weights": {
                        "workload_qps": 0.0,
                        "workload_tps": 0.0,
                        "db_qps": 1.0,
                        "db_xact_tps": 0.0,
                        "p95": 0.0,
                        "p99": 0.0,
                    },
                },
                "select_best_actual": True,
                "write_back": False,
            },
        },
        "tuning_constraints": {
            "strict_cost_params_only": True,
            "allowed_params": list(COST_PARAM_KEYS),
            "description": "Only these seven PostgreSQL planner cost parameters may be tuned.",
        },
        "_generated_from": {
            "script": str(Path(__file__).resolve()),
            "run_id": run_id,
            "phase": "phase2_dbqps_amplification",
            "benchmark": spec.benchmark,
            "bottleneck": spec.bottleneck,
            "target_resource": profile["target_resource"],
            "workload_log_path": str(spec.log_path),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    # RC#1 config-side activation (iter92, override directive 2026-07-06
    # §356-376 root cause #1): for memory target, hard-enforce R2 /
    # relative-error-p95 / relative-error-max quality gates so a Memory
    # AP_only model with negative R2 or runaway relative error cannot pass
    # (v6 round-0 root cause #1). CPU/IO targets keep warning-only behavior
    # unchanged so existing CPU/IO run-configs are byte-identical and don't
    # take a regression risk before v7 launch.
    if spec.bottleneck == "memory":
        _qg = cfg["metrics"]["plan_estimator"]["model_retraining"]["quality_gate"]
        _qg["enforce_test_r2"] = True
        _qg["enforce_test_relative_error_p95"] = True
        _qg["enforce_test_relative_error_max"] = True
        _qg["min_test_r2"] = max(float(_qg.get("min_test_r2", 0.0)), 0.0)
        # iter107: memory_replay_calibration provenance hard-fail (override
        # §2026-07-06 iter100 §8 #3 / §2026-07-07 03:12 iter106 §715-806
        # next_iter PRIORITY #3). When --memory-replay-calibration-enabled is
        # set for a memory target run, the caller MUST also pass
        # --memory-replay-calibration-evidence-path pointing at a valid JSON
        # object produced by shell/build_memory_replay_calibration_evidence.py.
        # Without provable evidence, the gate is meaningless; refuse to write
        # the generated config rather than emit a silent no-op gate that v7
        # would later misinterpret as a successful pass.
        _mrc = _qg.setdefault("memory_replay_calibration", {})
        _mrc_enabled = bool(_mrc.get("enabled", False))
        _mrc_evidence = _mrc.get("evidence", {})
        if _mrc_enabled and not (
            isinstance(_mrc_evidence, dict) and _mrc_evidence
        ):
            raise RuntimeError(
                "memory_replay_calibration provenance hard-fail: "
                "--memory-replay-calibration-enabled is set for memory target "
                "but no evidence JSON was loaded via "
                "--memory-replay-calibration-evidence-path. Refusing to emit "
                "a silent no-op gate. Provide a valid evidence JSON produced "
                "by shell/build_memory_replay_calibration_evidence.py."
            )
    validate_cost_param_scope(cfg)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def optimizer_command(spec: CommandSpec, args: argparse.Namespace, run_id: str) -> tuple[List[str], Dict[str, str], Path]:
    config_path = build_optimizer_config(spec, args, run_id)
    env = dict(spec.env)
    env.update(
        {
            "OPTIMIZER_CONFIG": str(config_path),
            "METRICS_SOURCE": "plan-estimator",
            "PGHOST": args.pg_host,
            "PGPORT": str(args.pg_port),
            "PGUSER": args.pg_user,
            "PGPASSWORD": args.pg_password,
            "PG_DBNAME": spec.pg_dbname,
        }
    )
    command = [args.python_executable, str(PROJECT_ROOT / "parameter_optimizer.py")]
    return command, env, config_path


def benchbase_dbname(args: argparse.Namespace, benchmark: str) -> str:
    suffix = {
        "tpcc": "tpcc",
        "tpch": "tpch",
        "chbench": "chbench",
    }.get(benchmark)
    if suffix is None:
        raise ValueError(f"unknown BenchBase benchmark: {benchmark}")
    return f"{args.benchbase_db}_{suffix}"


def benchbase_scalefactor_for(args: argparse.Namespace, benchmark: str) -> float:
    scalefactors = {
        "tpcc": args.tpcc_scalefactor,
        "tpch": args.tpch_scalefactor,
        "chbench": args.chbench_scalefactor,
    }
    value = scalefactors.get(benchmark)
    if value is not None:
        return value
    return args.benchbase_scalefactor


def benchbase_spec(
    *,
    benchmark: str,
    bottleneck: str,
    args: argparse.Namespace,
    conn: PgConn,
    run_id: str,
) -> CommandSpec:
    benchbase_root = Path(args.benchbase_root)
    mapping = {
        "tpcc": ("tpcc", "sample_tpcc_config.xml"),
        "tpch": ("tpch", "sample_tpch_config.xml"),
        "chbench": ("chbenchmark", "sample_chbenchmark_config.xml"),
    }
    benchbase_name, template_name = mapping[benchmark]
    template_path = benchbase_root / "config/postgres" / template_name
    log_path = Path(args.output_dir) / run_id / bottleneck / f"{benchmark}.log"
    xml_path = (PROJECT_ROOT / args.output_dir / run_id / "configs" / bottleneck / f"{benchmark}.xml").resolve()

    if not (benchbase_root / "benchbase.jar").is_file():
        return CommandSpec(benchmark, bottleneck, [], benchbase_root, {}, log_path, conn.dbname, False, "benchbase.jar not found")
    if not template_path.is_file():
        return CommandSpec(
            benchmark, bottleneck, [], benchbase_root, {}, log_path, conn.dbname, False, f"template not found: {template_path}"
        )
    db_exists = check_database(conn, conn.dbname)
    if db_exists is False and not (args.benchbase_create_load or args.auto_load_missing or args.force_load):
        return CommandSpec(
            benchmark,
            bottleneck,
            [],
            benchbase_root,
            {},
            log_path,
            conn.dbname,
            False,
            f"database '{conn.dbname}' does not exist; rerun with --benchbase-create-load or load it first",
        )

    update_benchbase_xml(
        template_path=template_path,
        output_path=xml_path,
        benchmark=benchbase_name,
        conn=conn,
        time_sec=args.benchbase_time_sec,
        terminals=1 if benchmark == "tpch" else args.benchbase_terminals,
        scalefactor=benchbase_scalefactor_for(args, benchmark),
        # The native BenchBase serial phase executes its complete registered
        # TPC-H workload (Q1-Q22) exactly once. JOB runs as the second parallel
        # benchmark process; query concurrency is not fabricated inside TPC-H.
        serial=True if benchmark == "tpch" else None,
    )

    command = [
        args.python_executable,
        str(PROJECT_ROOT / "shell/run_benchmark_capture.py"),
        "benchbase",
        "--root",
        str(benchbase_root),
        "--benchmark",
        benchbase_name,
        "--config",
        str(xml_path),
        "--java-xmx",
        args.java_xmx,
    ]
    if args.benchbase_create_load:
        command.append("--create-load")
    return CommandSpec(benchmark, bottleneck, command, benchbase_root, {}, log_path, conn.dbname)


def hybench_spec(
    *,
    benchmark: str,
    bottleneck: str,
    args: argparse.Namespace,
    conn: PgConn,
    run_id: str,
) -> CommandSpec:
    hybench_root = Path(args.hybench_root)
    template_path = hybench_root / "conf/pg.props"
    base_stmt_path = hybench_root / "conf/stmt_postgres.toml"
    log_path = Path(args.output_dir) / run_id / bottleneck / f"{benchmark}.log"
    props_path = (PROJECT_ROOT / args.output_dir / run_id / "configs" / bottleneck / f"{benchmark}.props").resolve()
    stmt_profile_path = (
        PROJECT_ROOT
        / args.output_dir
        / run_id
        / "configs"
        / bottleneck
        / f"{benchmark}_statements.toml"
    ).resolve()

    if not (hybench_root / "hybench").is_file():
        return CommandSpec(benchmark, bottleneck, [], hybench_root, {}, log_path, conn.dbname, False, "hybench script not found")
    if not template_path.is_file() or not base_stmt_path.is_file():
        return CommandSpec(
            benchmark, bottleneck, [], hybench_root, {}, log_path, conn.dbname, False, "HyBench pg.props or stmt_postgres.toml not found"
        )
    db_exists = check_database(conn, conn.dbname)
    if db_exists is False:
        return CommandSpec(
            benchmark, bottleneck, [], hybench_root, {}, log_path, conn.dbname, False, f"database '{conn.dbname}' does not exist"
        )

    tp_clients, ap_clients = bonect_hybench_clients(bottleneck, benchmark, args)
    if benchmark == "hybench_xp" and any(
        str(value or "").strip()
        for value in (
            args.hybench_ap_query_ids,
            args.hybench_ap_query_weights,
            args.hybench_ap_query_sequence,
            args.hybench_ap_query_sequence_limit,
        )
    ):
        return CommandSpec(
            benchmark,
            bottleneck,
            [],
            hybench_root,
            {},
            log_path,
            conn.dbname,
            False,
            "native HyBench XP forbids AP query ids, weights, and fixed sequences; "
            "configure only TP/AP client concurrency and the native TP transaction mix",
        )
    xp_tp_mix_props: Dict[str, str] = {}
    raw_xp_tp_mix = str(args.hybench_xp_tp_percent_mix or "").strip()
    if raw_xp_tp_mix:
        if benchmark != "hybench_xp":
            return CommandSpec(
                benchmark,
                bottleneck,
                [],
                hybench_root,
                {},
                log_path,
                conn.dbname,
                False,
                "--hybench-xp-tp-percent-mix is valid only for hybench_xp",
            )
        try:
            xp_tp_mix = [int(item.strip()) for item in raw_xp_tp_mix.split(",")]
        except ValueError:
            xp_tp_mix = []
        if len(xp_tp_mix) != 6 or any(value < 0 for value in xp_tp_mix) or sum(xp_tp_mix) != 100:
            return CommandSpec(
                benchmark,
                bottleneck,
                [],
                hybench_root,
                {},
                log_path,
                conn.dbname,
                False,
                "--hybench-xp-tp-percent-mix requires 6 non-negative integers whose sum is 100",
            )
        xp_tp_mix_props = {
            f"at{index}_percent": str(value)
            for index, value in enumerate(xp_tp_mix, start=1)
        }
    query_controls = bonect_hybench_query_controls(bottleneck, benchmark, args)
    update_hybench_props(
        template_path=template_path,
        output_path=props_path,
        conn=conn,
        benchmark=benchmark,
        run_mins=args.hybench_run_mins,
        tp_clients=tp_clients,
        ap_clients=ap_clients,
        random_seed=args.random_seed,
        ap_query_ids=query_controls["ap_query_ids"],
        ap_query_weights=query_controls["ap_query_weights"],
        ap_query_sequence=query_controls["ap_query_sequence"],
        ap_query_sequence_limit=query_controls["ap_query_sequence_limit"],
        progress_windows=query_controls["progress_windows"],
        statement_timeout_ms=args.hybench_statement_timeout_ms,
        extra_props=xp_tp_mix_props,
    )
    stmt_path = build_hybench_statement_profile(
        base_path=base_stmt_path,
        output_path=stmt_profile_path,
        raw_overrides=args.hybench_statement_sql_file,
    )
    mode = {
        "hybench_ap": "runap",
        "hybench_tp": "runtp",
        "hybench_xp": "runxp",
    }[benchmark]
    command = ["bash", "hybench", "-t", mode, "-c", str(props_path), "-f", str(stmt_path)]
    return CommandSpec(benchmark, bottleneck, command, hybench_root, {}, log_path, conn.dbname)


def job_spec(
    *,
    benchmark: str,
    bottleneck: str,
    args: argparse.Namespace,
    conn: PgConn,
    run_id: str,
) -> CommandSpec:
    job_sql_dir = Path(args.job_sql_dir) if args.job_sql_dir else None
    log_path = Path(args.output_dir) / run_id / bottleneck / f"{benchmark}.log"
    if not job_sql_dir or not job_sql_dir.is_dir():
        return CommandSpec(
            benchmark, bottleneck, [], PROJECT_ROOT, {}, log_path, conn.dbname, False, "JOB SQL directory not configured; pass --job-sql-dir"
        )

    sql_files = sorted(
        (
            path for path in job_sql_dir.glob("*.sql")
            if re.fullmatch(r"[0-9]+[a-z]\.sql", path.name)
        ),
        key=natural_sort_key,
    )
    if not sql_files:
        return CommandSpec(
            benchmark, bottleneck, [], PROJECT_ROOT, {}, log_path, conn.dbname, False, f"no .sql files found in {job_sql_dir}"
        )
    db_exists = check_database(conn, conn.dbname)
    if db_exists is False and not (args.auto_load_missing or args.force_load):
        return CommandSpec(
            benchmark, bottleneck, [], PROJECT_ROOT, {}, log_path, conn.dbname, False, f"database '{conn.dbname}' does not exist"
        )

    command = [
        args.python_executable,
        str(PROJECT_ROOT / "shell/run_benchmark_capture.py"),
        "job",
        "--dsn",
        conn.dsn(),
        "--sql-dir",
        str(job_sql_dir),
        "--rounds",
        str(args.job_rounds),
        "--statement-timeout-ms",
        str(args.job_statement_timeout_ms),
        "--work-mem-mb",
        str(args.job_work_mem_mb),
        "--workers",
        str(args.job_workers),
        "--max-concurrent-long-queries",
        str(args.job_max_concurrent_long_queries),
        "--long-query-fraction",
        str(args.job_long_query_fraction),
    ]
    return CommandSpec(benchmark, bottleneck, command, PROJECT_ROOT, build_env(os.environ, conn), log_path, conn.dbname)


def build_specs(args: argparse.Namespace, run_id: str) -> List[CommandSpec]:
    benchmarks = parse_csv(args.benchmarks)
    bottlenecks = parse_bottlenecks(args.bottlenecks)
    hybench_conn = PgConn(args.pg_host, args.pg_port, args.pg_user, args.pg_password, args.hybench_db)
    job_conn = PgConn(args.pg_host, args.pg_port, args.pg_user, args.pg_password, args.job_db)

    specs: List[CommandSpec] = []
    if args.scenario_set == "bonect":
        bonect_bottlenecks = set(parse_bottlenecks(args.bonect_bottlenecks))
        for bottleneck, benchmark in DEFAULT_BONECT_SCENARIOS:
            if bonect_bottlenecks and bottleneck not in bonect_bottlenecks:
                continue
            if bottleneck == "memory" and args.bonect_memory_use_xp:
                benchmark = "hybench_xp"
            specs.append(
                hybench_spec(
                    benchmark=benchmark,
                    bottleneck=bottleneck,
                    args=args,
                    conn=hybench_conn,
                    run_id=run_id,
                )
            )
        return specs

    for bottleneck in bottlenecks:
        for benchmark in benchmarks:
            if benchmark in {"tpcc", "tpch", "chbench"}:
                benchbase_conn = PgConn(
                    args.pg_host,
                    args.pg_port,
                    args.pg_user,
                    args.pg_password,
                    benchbase_dbname(args, benchmark),
                )
                specs.append(benchbase_spec(benchmark=benchmark, bottleneck=bottleneck, args=args, conn=benchbase_conn, run_id=run_id))
            elif benchmark in {"hybench_ap", "hybench_xp"}:
                specs.append(hybench_spec(benchmark=benchmark, bottleneck=bottleneck, args=args, conn=hybench_conn, run_id=run_id))
            elif benchmark == "job":
                specs.append(job_spec(benchmark=benchmark, bottleneck=bottleneck, args=args, conn=job_conn, run_id=run_id))
            else:
                raise ValueError(f"unknown benchmark: {benchmark}")
    return specs


def build_prepare_specs(args: argparse.Namespace, run_id: str) -> List[PrepareSpec]:
    if not args.auto_load_missing and not args.force_load:
        return []

    benchmarks = parse_csv(args.benchmarks)
    prepare_specs: List[PrepareSpec] = []
    benchbase_root = Path(args.benchbase_root)
    output_root = PROJECT_ROOT / args.output_dir / run_id

    benchbase_benchmarks = [name for name in benchmarks if name in {"tpcc", "tpch", "chbench"}]
    if benchbase_benchmarks:
        mapping = {
            "tpcc": ("tpcc", "sample_tpcc_config.xml"),
            "tpch": ("tpch", "sample_tpch_config.xml"),
            "chbench": ("chbenchmark", "sample_chbenchmark_config.xml"),
        }
        for benchmark in benchbase_benchmarks:
            conn = PgConn(
                args.pg_host,
                args.pg_port,
                args.pg_user,
                args.pg_password,
                benchbase_dbname(args, benchmark),
            )
            benchbase_name, template_name = mapping[benchmark]
            template_path = benchbase_root / "config/postgres" / template_name
            xml_path = (output_root / "prepare" / f"{benchmark}_load.xml").resolve()
            if template_path.is_file():
                update_benchbase_xml(
                    template_path=template_path,
                    output_path=xml_path,
                    benchmark=benchbase_name,
                    conn=conn,
                    time_sec=args.benchbase_time_sec,
                    terminals=args.benchbase_terminals,
                    scalefactor=benchbase_scalefactor_for(args, benchmark),
                )
                prepare_specs.append(
                    PrepareSpec(
                        name=f"benchbase-{benchmark}-load",
                        commands=[
                            conditional_load_command(
                                conn=conn,
                                dbname=conn.dbname,
                                load_commands=[quote_cmd(benchbase_load_command(benchbase_root, benchbase_name, xml_path, args))],
                                force=args.force_load,
                                rebuild=args.rebuild_load_databases,
                                use_template=args.use_load_templates,
                                refresh_template=args.refresh_load_templates,
                            )
                        ],
                        cwd=benchbase_root,
                        env={},
                        log_path=output_root / "prepare" / f"{benchmark}_load.log",
                    )
                )

    if "job" in benchmarks:
        conn = PgConn(args.pg_host, args.pg_port, args.pg_user, args.pg_password, args.job_db)
        job_sql_dir = Path(args.job_sql_dir) if args.job_sql_dir else Path(DEFAULT_JOB_SQL_DIR)
        job_data_dir = Path(args.job_data_dir) if args.job_data_dir else Path(DEFAULT_JOB_DATA_DIR)
        missing_inputs = []
        for path in [job_sql_dir / "schema.sql", job_sql_dir / "fkindexes.sql"]:
            if not path.is_file():
                missing_inputs.append(str(path))
        missing_csv = [str(job_data_dir / f"{table}.csv") for table in JOB_TABLES if not (job_data_dir / f"{table}.csv").is_file()]
        if missing_csv:
            missing_inputs.extend(missing_csv[:5])
            if len(missing_csv) > 5:
                missing_inputs.append(f"... {len(missing_csv) - 5} more missing CSV files")
        if missing_inputs:
            prepare_specs.append(
                PrepareSpec(
                    name="job-load",
                    commands=[],
                    cwd=PROJECT_ROOT,
                    env={},
                    log_path=output_root / "prepare" / "job_load.log",
                    runnable=False,
                    reason="missing JOB load inputs: " + ", ".join(missing_inputs),
                )
            )
        else:
            prepare_specs.append(
                PrepareSpec(
                    name="job-load",
                    commands=[
                        conditional_load_command(
                            conn=conn,
                            dbname=args.job_db,
                            load_commands=[quote_cmd(job_load_command(conn, job_sql_dir, job_data_dir))],
                            force=args.force_load,
                            rebuild=args.rebuild_load_databases,
                            use_template=args.use_load_templates,
                            refresh_template=args.refresh_load_templates,
                        )
                    ],
                    cwd=PROJECT_ROOT,
                    env=build_env(os.environ, conn),
                    log_path=output_root / "prepare" / "job_load.log",
                )
            )
    return prepare_specs


def check_environment(args: argparse.Namespace, specs: Sequence[CommandSpec], prepare_specs: Sequence[PrepareSpec]) -> int:
    print("=== Environment check ===", flush=True)
    print(f"PG target: {args.pg_host}:{args.pg_port}", flush=True)
    mode = "optimizer/plan-estimator" if args.use_resource_estimator else "direct workload"
    print(f"run mode: {mode}", flush=True)
    print(
        "BenchBase scalefactors: "
        f"tpcc={args.tpcc_scalefactor}, tpch={args.tpch_scalefactor}, chbench={args.chbench_scalefactor}",
        flush=True,
    )
    print(f"PG TCP reachable: {'OK' if check_tcp(args.pg_host, args.pg_port) else 'FAILED'}", flush=True)
    benchbase_dbnames = {
        benchbase_dbname(args, benchmark)
        for benchmark in parse_csv(args.benchmarks)
        if benchmark in {"tpcc", "tpch", "chbench"}
    }
    for dbname in sorted({*benchbase_dbnames, args.hybench_db, args.job_db}):
        conn = PgConn(args.pg_host, args.pg_port, args.pg_user, args.pg_password, dbname)
        exists = check_database(conn, dbname)
        if exists is True:
            status = "OK"
        elif exists is False:
            status = "MISSING"
        else:
            status = "UNKNOWN (psql check failed from Python; command may still run)"
        print(f"database {dbname}: {status}", flush=True)
        if args.use_load_templates and dbname in {*benchbase_dbnames, args.job_db}:
            template_name = load_template_dbname(dbname)
            template_exists = check_database(conn, template_name)
            if template_exists is True:
                template_status = "OK"
            elif template_exists is False:
                template_status = "MISSING"
            else:
                template_status = "UNKNOWN"
            print(f"load template {template_name}: {template_status}", flush=True)

    benchbase_root = Path(args.benchbase_root)
    hybench_root = Path(args.hybench_root)
    print(f"BenchBase jar: {'OK' if (benchbase_root / 'benchbase.jar').is_file() else 'MISSING'}", flush=True)
    print(f"HyBench script: {'OK' if (hybench_root / 'hybench').is_file() else 'MISSING'}", flush=True)
    job_sql_dir = Path(args.job_sql_dir) if args.job_sql_dir else Path(DEFAULT_JOB_SQL_DIR)
    count = len(list(job_sql_dir.glob("*.sql"))) if job_sql_dir.is_dir() else 0
    print(f"JOB SQL dir: {job_sql_dir} ({count} sql files)", flush=True)
    job_data_dir = Path(args.job_data_dir) if args.job_data_dir else Path(DEFAULT_JOB_DATA_DIR)
    csv_count = len(list(job_data_dir.glob("*.csv"))) if job_data_dir.is_dir() else 0
    print(f"JOB data dir: {job_data_dir} ({csv_count} csv files)", flush=True)

    blocked = [spec for spec in specs if not spec.runnable]
    print(f"planned specs: {len(specs)}, runnable: {len(specs) - len(blocked)}, blocked: {len(blocked)}", flush=True)
    for spec in blocked:
        print(f"BLOCKED {spec.bottleneck}/{spec.benchmark}: {spec.reason}", flush=True)
    prepare_blocked = [spec for spec in prepare_specs if not spec.runnable]
    for spec in prepare_blocked:
        print(f"BLOCKED prepare/{spec.name}: {spec.reason}", flush=True)
    return 1 if (blocked or prepare_blocked) and args.strict_check else 0


def execute_prepare_specs(args: argparse.Namespace, prepare_specs: Sequence[PrepareSpec]) -> int:
    exit_code = 0
    for spec in prepare_specs:
        if not spec.runnable:
            print(f"SKIP prepare/{spec.name}: {spec.reason}", flush=True)
            if args.run:
                exit_code = exit_code or 1
                if not args.continue_on_failure:
                    return exit_code
            continue
        if not spec.commands:
            continue
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"PREPARE {spec.name}: log={spec.log_path}", flush=True)
        for command in spec.commands:
            print(f"  {quote_cmd(command)}", flush=True)
            if not args.run:
                continue
            env = dict(os.environ)
            env.update(spec.env)
            with spec.log_path.open("a", encoding="utf-8") as log_fp:
                log_fp.write(f"\n$ {quote_cmd(command)}\n")
                log_fp.flush()
                proc = subprocess.Popen(
                    command,
                    cwd=str(spec.cwd),
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    log_fp.write(line)
                    log_fp.flush()
                    print(line, end="", flush=True)
                proc.wait()
            if proc.returncode != 0:
                print(f"FAIL prepare/{spec.name}: exit={proc.returncode}, log={spec.log_path}", flush=True)
                exit_code = exit_code or proc.returncode
                if not args.continue_on_failure:
                    return exit_code
                break
    return exit_code


def execute_specs(args: argparse.Namespace, specs: Sequence[CommandSpec]) -> int:
    current_bottleneck = None
    failed_bottlenecks = set()
    exit_code = 0
    conn = PgConn(args.pg_host, args.pg_port, args.pg_user, args.pg_password, "postgres")

    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except Exception:
        pass

    active_child: Dict[str, object] = {}

    def write_runner_status(path: Path, payload: Dict[str, object]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    def runner_signal_handler(signum: int, _frame: object) -> None:
        status_path = active_child.get("status_path")
        if isinstance(status_path, Path):
            payload = {key: value for key, value in active_child.items() if key != "status_path"}
            write_runner_status(
                status_path,
                {
                    **payload,
                    "event": "runner_signal",
                    "signal_number": int(signum),
                    "signal_name": getattr(signal.Signals(signum), "name", str(signum)),
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "note": "optimizer child was started in its own session and is not killed by this handler",
                },
            )
        raise SystemExit(128 + int(signum))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, runner_signal_handler)
        except Exception:
            pass

    for spec in specs:
        if spec.bottleneck != current_bottleneck:
            current_bottleneck = spec.bottleneck
            vm_cmd = vm_profile_command(spec.bottleneck, args)
            if args.run and args.apply_vm_profile and vm_cmd:
                print(f"Applying VM profile {spec.bottleneck}: {quote_cmd(vm_cmd)}", flush=True)
                vm_env = dict(os.environ)
                if args.force_vm_destroy_on_shutdown_timeout:
                    vm_env["VM_RESOURCE_FORCE_DESTROY_ON_SHUTDOWN_TIMEOUT"] = "1"
                proc = subprocess.run(vm_cmd, cwd=str(PROJECT_ROOT), env=vm_env, check=False)
                if proc.returncode != 0:
                    print(f"FAIL vm-profile/{spec.bottleneck}: exit={proc.returncode}", flush=True)
                    exit_code = exit_code or proc.returncode
                    if not args.continue_on_failure:
                        return exit_code
                    failed_bottlenecks.add(spec.bottleneck)
                elif not ensure_postgres_after_vm_profile(args, conn):
                    exit_code = exit_code or 1
                    if not args.continue_on_failure:
                        return exit_code
                    failed_bottlenecks.add(spec.bottleneck)
            elif vm_cmd:
                print(f"[dry-vm] {spec.bottleneck}: {quote_cmd(vm_cmd)}", flush=True)

        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        if spec.bottleneck in failed_bottlenecks:
            print(f"SKIP {spec.bottleneck}/{spec.benchmark}: VM profile failed for this bottleneck", flush=True)
            continue
        if not spec.runnable:
            print(f"SKIP {spec.bottleneck}/{spec.benchmark}: {spec.reason}", flush=True)
            if args.run:
                exit_code = exit_code or 1
            if args.stop_on_skip and args.run:
                if not args.continue_on_failure:
                    return exit_code
            continue

        run_command = spec.command
        run_env = dict(spec.env)
        run_cwd = spec.cwd
        config_path: Optional[Path] = None
        if args.use_resource_estimator:
            run_command, run_env, config_path = optimizer_command(spec, args, args.run_id)
            run_cwd = PROJECT_ROOT
            printable = quote_cmd(run_command)
            print(
                f"OPTIMIZER {spec.bottleneck}/{spec.benchmark}: config={config_path} command={printable}",
                flush=True,
            )
        else:
            printable = quote_cmd(run_command)
            print(f"RUN {spec.bottleneck}/{spec.benchmark}: (cd {run_cwd} && {printable})", flush=True)
        if not args.run:
            continue

        env = dict(os.environ)
        env.update(run_env)
        attempt = 0
        max_attempts = 1 + max(0, int(args.vm_profile_reapply_max_retries))
        while True:
            attempt += 1
            log_mode = "w" if attempt == 1 else "a"
            with spec.log_path.open(log_mode, encoding="utf-8") as log_fp:
                if attempt > 1:
                    log_fp.write(
                        "\n"
                        f"=== RETRY {attempt}/{max_attempts} after adaptive VM profile reapply ===\n"
                    )
                    log_fp.flush()
                status_path = spec.log_path.with_name(f"{spec.log_path.name}.runner_status.json")
                status_payload: Dict[str, object] = {
                    "event": "optimizer_start",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "run_id": args.run_id,
                    "bottleneck": spec.bottleneck,
                    "benchmark": spec.benchmark,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "cwd": str(run_cwd),
                    "command": run_command,
                    "log_path": str(spec.log_path),
                    "config_path": str(config_path) if config_path is not None else None,
                    "runner_pid": os.getpid(),
                }
                write_runner_status(status_path, status_payload)
                active_child.clear()
                active_child.update(status_payload)
                active_child["status_path"] = status_path
                proc_obj = subprocess.Popen(
                    run_command,
                    cwd=str(run_cwd),
                    env=env,
                    text=True,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    child_pgid = os.getpgid(proc_obj.pid)
                except Exception:
                    child_pgid = None
                active_child["child_pid"] = proc_obj.pid
                active_child["child_pgid"] = child_pgid
                write_runner_status(
                    status_path,
                    {
                        **status_payload,
                        "event": "optimizer_running",
                        "child_pid": proc_obj.pid,
                        "child_pgid": child_pgid,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                returncode = proc_obj.wait()
                proc = subprocess.CompletedProcess(run_command, returncode)
                write_runner_status(
                    status_path,
                    {
                        **status_payload,
                        "event": "optimizer_exit",
                        "child_pid": proc_obj.pid,
                        "child_pgid": child_pgid,
                        "returncode": returncode,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                active_child.clear()
            if proc.returncode != 75:
                break
            if not args.apply_vm_profile:
                print(
                    f"FAIL {spec.bottleneck}/{spec.benchmark}: exit=75 requires VM profile reapply, "
                    "--apply-vm-profile is not enabled",
                    flush=True,
                )
                break
            if attempt >= max_attempts:
                print(
                    f"FAIL {spec.bottleneck}/{spec.benchmark}: exit=75 after "
                    f"{attempt} attempts, log={spec.log_path}",
                    flush=True,
                )
                break
            if not config_requires_vm_profile_reapply(config_path):
                print(
                    f"FAIL {spec.bottleneck}/{spec.benchmark}: exit=75 but config has no "
                    "vm_resource.adaptive_source.requires_vm_profile_reapply",
                    flush=True,
                )
                break
            reapply_cmd = vm_profile_command_from_config(config_path, args)
            if not reapply_cmd:
                print(
                    f"FAIL {spec.bottleneck}/{spec.benchmark}: exit=75 but adaptive "
                    "vm_resource command could not be built",
                    flush=True,
                )
                break
            print(
                f"Adaptive VM profile reapply {spec.bottleneck}/{spec.benchmark}: "
                f"{quote_cmd(reapply_cmd)}",
                flush=True,
            )
            vm_env = dict(os.environ)
            if args.force_vm_destroy_on_shutdown_timeout:
                vm_env["VM_RESOURCE_FORCE_DESTROY_ON_SHUTDOWN_TIMEOUT"] = "1"
            vm_proc = subprocess.run(reapply_cmd, cwd=str(PROJECT_ROOT), env=vm_env, check=False)
            if vm_proc.returncode != 0:
                print(
                    f"FAIL adaptive-vm-profile/{spec.bottleneck}: exit={vm_proc.returncode}",
                    flush=True,
                )
                proc = vm_proc
                break
            if not ensure_postgres_after_vm_profile(args, conn):
                proc = subprocess.CompletedProcess(reapply_cmd, 1)
                break
            print(
                f"RETRY {spec.bottleneck}/{spec.benchmark}: adaptive VM profile applied; "
                "rerun baseline before tuning",
                flush=True,
            )
        if proc.returncode != 0:
            print(f"FAIL {spec.bottleneck}/{spec.benchmark}: exit={proc.returncode}, log={spec.log_path}", flush=True)
            exit_code = exit_code or proc.returncode
            if not args.continue_on_failure:
                return exit_code
        else:
            print(f"OK {spec.bottleneck}/{spec.benchmark}: log={spec.log_path}", flush=True)
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PostgreSQL benchmark x bottleneck matrix runner.")
    parser.add_argument(
        "--scenario-set",
        choices=["bonect", "matrix"],
        default=os.getenv("SCENARIO_SET", "bonect"),
        help=(
            "bonect runs four resource-iteration scenarios: "
            "cpu/hybench_xp, memory/hybench_ap, io/hybench_xp, none/hybench_xp. "
            "matrix runs the cartesian product from --benchmarks and --bottlenecks."
        ),
    )
    parser.add_argument("--benchmarks", default=",".join(DEFAULT_BENCHMARKS))
    parser.add_argument("--bottlenecks", default=",".join(DEFAULT_BOTTLENECKS))
    parser.add_argument(
        "--bonect-bottlenecks",
        default=os.getenv("BONECT_BOTTLENECKS", ""),
        help=(
            "Comma-separated bottleneck filter for --scenario-set bonect. "
            "Use this to skip already concluded scenarios, e.g. memory,io."
        ),
    )
    parser.add_argument("--pg-host", default=os.getenv("PGHOST", "192.0.2.8"))
    parser.add_argument("--pg-port", type=int, default=int(os.getenv("PGPORT", "25432")))
    parser.add_argument("--pg-user", default=os.getenv("PGUSER", "postgres"))
    parser.add_argument("--pg-password", default=os.getenv("PGPASSWORD", "CHANGE_ME"))
    parser.add_argument("--benchbase-db", default=os.getenv("BENCHBASE_DB", "benchbase"))
    parser.add_argument("--hybench-db", default=os.getenv("HYBENCH_DB", "hybench_sf10x"))
    parser.add_argument("--job-db", default=os.getenv("JOB_DB", "imdbload"))
    parser.add_argument("--benchbase-root", default=os.getenv("BENCHBASE_ROOT", "/home/anonymous/benchbase-postgres"))
    parser.add_argument("--hybench-root", default=os.getenv("HYBENCH_ROOT", "/home/anonymous/HyBench-2024-master"))
    parser.add_argument("--job-sql-dir", default=os.getenv("JOB_SQL_DIR", DEFAULT_JOB_SQL_DIR))
    parser.add_argument("--job-data-dir", default=os.getenv("JOB_DATA_DIR", DEFAULT_JOB_DATA_DIR))
    parser.add_argument("--job-rounds", type=int, default=int(os.getenv("JOB_ROUNDS", "1")))
    parser.add_argument("--job-workers", type=int, default=int(os.getenv("JOB_WORKERS", "8")))
    parser.add_argument(
        "--job-max-concurrent-long-queries",
        type=int,
        default=int(os.getenv("JOB_MAX_CONCURRENT_LONG_QUERIES", "1")),
    )
    parser.add_argument(
        "--job-long-query-fraction",
        type=float,
        default=float(os.getenv("JOB_LONG_QUERY_FRACTION", "0.2")),
    )
    parser.add_argument(
        "--job-statement-timeout-ms",
        type=int,
        default=int(os.getenv("JOB_STATEMENT_TIMEOUT_MS", "300000")),
    )
    parser.add_argument("--job-work-mem-mb", type=int, default=int(os.getenv("JOB_WORK_MEM_MB", "0")))
    parser.add_argument("--output-dir", default="history/pg_benchmark_bottleneck_matrix")
    parser.add_argument("--generated-config-dir", default="config/generated/pg_benchmark_bottleneck_matrix")
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--benchbase-time-sec", type=int, default=int(os.getenv("BENCHBASE_TIME_SEC", "300")))
    parser.add_argument("--benchbase-terminals", type=int, default=int(os.getenv("BENCHBASE_TERMINALS", "16")))
    parser.add_argument("--benchbase-scalefactor", type=float, default=float(os.getenv("BENCHBASE_SCALEFACTOR", "1")))
    parser.add_argument(
        "--tpcc-scalefactor",
        type=float,
        default=float(os.getenv("TPCC_SCALEFACTOR", os.getenv("BENCHBASE_SCALEFACTOR", "1"))),
        help="BenchBase TPCC scale factor. Defaults to BENCHBASE_SCALEFACTOR or 1.",
    )
    parser.add_argument(
        "--tpch-scalefactor",
        type=float,
        default=float(os.getenv("TPCH_SCALEFACTOR", "3")),
        help="BenchBase TPCH scale factor. Default 3 targets roughly 10GB PostgreSQL data size.",
    )
    parser.add_argument(
        "--chbench-scalefactor",
        type=float,
        default=float(os.getenv("CHBENCH_SCALEFACTOR", os.getenv("TPCH_SCALEFACTOR", "3"))),
        help="BenchBase CHBench scale factor. Defaults to TPCH_SCALEFACTOR or 3.",
    )
    parser.add_argument("--benchbase-create-load", action="store_true")
    parser.add_argument(
        "--no-auto-load-missing",
        dest="auto_load_missing",
        action="store_false",
        help="Disable automatic load preparation when a target database is missing.",
    )
    parser.set_defaults(auto_load_missing=True)
    parser.add_argument("--force-load", action="store_true", help="Run load preparation even if the database exists.")
    parser.add_argument(
        "--rebuild-load-databases",
        action="store_true",
        help=(
            "Drop and recreate load databases during prepare. With load templates enabled, restore from "
            "template_<dbname> when present; otherwise reload data and create/update that template."
        ),
    )
    parser.add_argument(
        "--no-load-templates",
        dest="use_load_templates",
        action="store_false",
        help="Disable template_<dbname> persistence for loaded databases.",
    )
    parser.set_defaults(use_load_templates=True)
    parser.add_argument(
        "--refresh-load-templates",
        action="store_true",
        help="Force data reload and recreate template_<dbname> even if a load template already exists.",
    )
    parser.add_argument("--java-xmx", default=os.getenv("JAVA_XMX", "16g"))
    parser.add_argument("--hybench-run-mins", type=int, default=int(os.getenv("HYBENCH_RUN_MINS", "5")))
    parser.add_argument(
        "--hybench-statement-timeout-ms",
        type=int,
        default=int(os.getenv("HYBENCH_STATEMENT_TIMEOUT_MS", "300000")),
        help="PostgreSQL statement_timeout applied to generated HyBench JDBC URLs; <=0 disables it.",
    )
    parser.add_argument("--hybench-tp-clients", type=int, default=int(os.getenv("HYBENCH_TP_CLIENTS", "16")))
    parser.add_argument("--hybench-ap-clients", type=int, default=int(os.getenv("HYBENCH_AP_CLIENTS", "4")))
    parser.add_argument(
        "--hybench-xp-tp-percent-mix",
        default=os.getenv("HYBENCH_XP_TP_PERCENT_MIX", ""),
        help=(
            "Optional native HyBench XP AT1-AT6 percentage mix: six comma-separated "
            "non-negative integers summing to 100."
        ),
    )
    parser.add_argument(
        "--bonect-cpu-xp-tp-clients",
        type=int,
        default=int(os.getenv("BONECT_CPU_XP_TP_CLIENTS", str(DEFAULT_BONECT_CPU_XP_TP_CLIENTS))),
        help="HyBench XP TP clients for the bonect CPU scenario. Lowering this reduces short-transaction CPU pressure.",
    )
    parser.add_argument(
        "--bonect-cpu-xp-ap-clients",
        type=int,
        default=int(os.getenv("BONECT_CPU_XP_AP_CLIENTS", str(DEFAULT_BONECT_CPU_XP_AP_CLIENTS))),
        help="HyBench XP AP/IQ clients for the bonect CPU scenario. Raising this exposes scan/sort/join plans.",
    )
    parser.add_argument(
        "--bonect-memory-ap-clients",
        type=int,
        default=int(os.getenv("BONECT_MEMORY_AP_CLIENTS", str(DEFAULT_BONECT_MEMORY_AP_CLIENTS))),
        help="HyBench AP clients for the bonect memory scenario.",
    )
    parser.add_argument(
        "--bonect-memory-use-xp",
        action="store_true",
        default=os.getenv("BONECT_MEMORY_USE_XP", "0") == "1",
        help=(
            "Run the Bonect memory scenario with HyBench XP instead of the default AP-only mode. "
            "This is intended for scenario-specific teacher/reconstruction validation."
        ),
    )
    parser.add_argument(
        "--bonect-io-xp-tp-clients",
        type=int,
        default=int(os.getenv("BONECT_IO_XP_TP_CLIENTS", str(DEFAULT_BONECT_IO_XP_TP_CLIENTS))),
        help="HyBench XP TP clients for the bonect IO scenario.",
    )
    parser.add_argument(
        "--bonect-io-xp-ap-clients",
        type=int,
        default=int(os.getenv("BONECT_IO_XP_AP_CLIENTS", str(DEFAULT_BONECT_IO_XP_AP_CLIENTS))),
        help="HyBench XP AP/IQ clients for the bonect IO scenario.",
    )
    parser.add_argument("--random-seed", type=int, default=int(os.getenv("RANDOM_SEED", "4508333")))
    parser.add_argument(
        "--hybench-ap-query-ids",
        default=os.getenv("HYBENCH_AP_QUERY_IDS"),
        help="Optional comma-separated AP query ids for HyBench runap only; forbidden for native runxp.",
    )
    parser.add_argument(
        "--hybench-ap-query-weights",
        default=os.getenv("HYBENCH_AP_QUERY_WEIGHTS"),
        help="Optional runap weights matching --hybench-ap-query-ids; forbidden for native runxp.",
    )
    parser.add_argument(
        "--hybench-ap-query-sequence",
        default=os.getenv("HYBENCH_AP_QUERY_SEQUENCE"),
        help="Optional runap query sequence for deterministic subset reconstruction; forbidden for native runxp.",
    )
    parser.add_argument(
        "--hybench-ap-query-sequence-limit",
        default=os.getenv("HYBENCH_AP_QUERY_SEQUENCE_LIMIT"),
        help="Optional HyBench AP query sequence limit.",
    )
    parser.add_argument(
        "--hybench-progress-windows",
        default=os.getenv("HYBENCH_PROGRESS_WINDOWS"),
        help="Optional HyBench progress_windows override.",
    )
    parser.add_argument(
        "--hybench-statement-sql-file",
        action="append",
        default=[],
        metavar="SECTION=PATH",
        help=(
            "Repeatable HyBench statement override, for example "
            "--hybench-statement-sql-file AP-6=shell/custom_ap6.sql. "
            "A run-local TOML profile is generated; the source file is not modified."
        ),
    )
    parser.add_argument(
        "--hybench-setup-sql-file",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Repeatable setup SQL file executed after immutable-template restore and before each "
            "measured HyBench validation."
        ),
    )
    parser.add_argument("--run", action="store_true", help="Execute commands. Without this, only print the plan.")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only run load preparation/template materialization, then exit before benchmark experiments.",
    )
    parser.add_argument(
        "--use-resource-estimator",
        action="store_true",
        help="Run parameter_optimizer.py with metrics.source=plan-estimator for every benchmark/bottleneck spec.",
    )
    parser.add_argument(
        "--allow-direct-bonect-benchmark",
        action="store_true",
        default=os.getenv("ALLOW_DIRECT_BONECT_BENCHMARK", "0") == "1",
        help=(
            "Allow --scenario-set bonect --run without --use-resource-estimator. "
            "This is a diagnostic escape hatch; Phase2 tuning must not use it."
        ),
    )
    parser.add_argument(
        "--disable-resource-model-quality-gate",
        dest="resource_model_quality_gate",
        action="store_false",
        help=(
            "Disable the pre-optimization train/test quality gate for retrained CPU/IO/Memory resource models. "
            "Bonect long-loop runs should keep this enabled."
        ),
    )
    parser.set_defaults(resource_model_quality_gate=True)
    parser.add_argument(
        "--disable-bonect-candidate-gate",
        action="store_true",
        default=str(os.getenv("DISABLE_BONECT_CANDIDATE_GATE", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "Disable the Bonect brute-force candidate gate that requires predicted target-resource "
            "decrease. This is intended for offline/real-validation experiments where robust ranking "
            "should still choose top candidates even when the resource estimator is overly conservative."
        ),
    )
    parser.add_argument(
        "--bonect-relax-candidate-gate",
        action="store_true",
        default=str(os.getenv("BONECT_RELAX_CANDIDATE_GATE", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "Keep the Bonect candidate gate and robust ranking metadata enabled, but do not require "
            "predicted target-resource decrease before sending v5-ranked top-k candidates to real validation."
        ),
    )
    parser.add_argument(
        "--bonect-plateau-max-same-resource-topk",
        type=int,
        default=int(os.getenv("BONECT_PLATEAU_MAX_SAME_RESOURCE_TOPK", "1")),
        help="Maximum robust-ranking top-k entries allowed with the same resource+score plateau signature.",
    )
    parser.add_argument(
        "--bonect-plateau-max-same-resource-value-topk",
        type=int,
        default=int(os.getenv("BONECT_PLATEAU_MAX_SAME_RESOURCE_VALUE_TOPK", "1")),
        help="Maximum robust-ranking top-k entries allowed with identical predicted resource values.",
    )
    parser.add_argument(
        "--bonect-allow-v5-retrain-fallback",
        action="store_true",
        default=str(os.getenv("BONECT_ALLOW_V5_RETRAIN_FALLBACK", "0")).strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "Do baseline resource-model retraining first, but if retraining or its quality gate fails, "
            "continue the outer loop with latest v5 robust ranking and pre-retrain model artifacts."
        ),
    )
    parser.add_argument(
        "--resource-model-quality-required-resources",
        default=os.getenv("RESOURCE_MODEL_QUALITY_REQUIRED_RESOURCES", "cpu,io,memory"),
        help="Comma-separated resources whose retrained models must pass train/test quality before optimization.",
    )
    parser.add_argument(
        "--resource-model-quality-min-test-samples",
        type=int,
        default=int(os.getenv("RESOURCE_MODEL_QUALITY_MIN_TEST_SAMPLES", "5")),
        help="Minimum test samples required for each required resource model metric record.",
    )
    parser.add_argument(
        "--resource-model-quality-max-test-relative-error-p95",
        type=float,
        default=float(os.getenv("RESOURCE_MODEL_QUALITY_MAX_TEST_RELATIVE_ERROR_P95", "1.0")),
        help="Maximum allowed p95 relative error on the held-out test split for required resource models.",
    )
    parser.add_argument(
        "--resource-model-quality-max-test-relative-error-max",
        type=float,
        default=float(os.getenv("RESOURCE_MODEL_QUALITY_MAX_TEST_RELATIVE_ERROR_MAX", "5.0")),
        help="Maximum allowed worst-case relative error on the held-out test split for required resource models.",
    )
    parser.add_argument(
        "--resource-model-quality-min-test-r2",
        type=float,
        default=float(os.getenv("RESOURCE_MODEL_QUALITY_MIN_TEST_R2", "0.0")),
        help="Minimum allowed R2 on the held-out test split for required resource models.",
    )
    parser.add_argument("--python-executable", default=os.getenv("PYTHON_EXECUTABLE", DEFAULT_PYTHON_EXECUTABLE))
    parser.add_argument(
        "--optimizer-mode",
        choices=["gradient", "bruteforce"],
        default=os.getenv("OPTIMIZER_MODE", "bruteforce"),
    )
    parser.add_argument("--optimizer-max-iter", type=int, default=int(os.getenv("OPTIMIZER_MAX_ITER", "100")))
    parser.add_argument("--optimizer-patience", type=int, default=int(os.getenv("OPTIMIZER_PATIENCE", "100")))
    parser.add_argument(
        "--post-convergence-explore-iter",
        type=int,
        default=int(os.getenv("POST_CONVERGENCE_EXPLORE_ITER", "20")),
        help="Continue this many optimizer iterations after the first convergence point is found.",
    )
    parser.add_argument("--gradient-eta", type=float, default=float(os.getenv("GRADIENT_ETA", "0.2")))
    parser.add_argument(
        "--gradient-max-param-delta-ratio",
        type=float,
        default=float(os.getenv("GRADIENT_MAX_PARAM_DELTA_RATIO", "0.20")),
        help="Conservative max single-iteration parameter delta ratio for gradient optimizer.",
    )
    parser.add_argument(
        "--gradient-min-param-delta-rel-pct",
        type=float,
        default=float(os.getenv("GRADIENT_MIN_PARAM_DELTA_REL_PCT", "10")),
        help="Minimum relative delta percentage for non-zero gradient parameter updates.",
    )
    parser.add_argument(
        "--gradient-max-param-delta-rel-pct",
        type=float,
        default=float(os.getenv("GRADIENT_MAX_PARAM_DELTA_REL_PCT", "20")),
        help="Maximum relative delta percentage for each parameter in one optimizer step/feedback round.",
    )
    parser.add_argument(
        "--bruteforce-max-iter",
        type=int,
        default=int(os.getenv("BRUTEFORCE_MAX_ITER", "24")),
        help="Maximum estimator-only coordinate-scan iterations before validating the top-k real candidates.",
    )
    parser.add_argument(
        "--bruteforce-candidate-top-k",
        type=int,
        default=int(os.getenv("BRUTEFORCE_CANDIDATE_TOP_K", "20")),
        help="Number of resource-estimated bruteforce candidates to keep in the inner-loop ranking pool.",
    )
    parser.add_argument(
        "--bruteforce-candidate-replay-top-k",
        type=int,
        default=int(os.getenv("BRUTEFORCE_CANDIDATE_REPLAY_TOP_K", "20")),
        help="Number of ranked bruteforce candidates to replay with real validation in each feedback round.",
    )
    parser.add_argument(
        "--phase2-real-delta-calibration-artifact",
        default=os.getenv("PHASE2_REAL_DELTA_CALIBRATION_ARTIFACT", ""),
        help=(
            "Path to a passed paired-baseline Phase2 delta-calibration artifact. "
            "When set, generated configs require supported DB-QPS/resource-delta predictions."
        ),
    )
    parser.add_argument(
        "--phase2-teacher-recovery-config",
        default=os.getenv("PHASE2_TEACHER_RECOVERY_CONFIG", ""),
        help=(
            "JSON config for in-process teacher-coordinate auditing. This is diagnostic and "
            "must not be supplied to the final answer-blind recovery run."
        ),
    )
    parser.add_argument(
        "--phase2-blind-recovery",
        action="store_true",
        default=_env_bool("PHASE2_BLIND_RECOVERY", False),
        help=(
            "Run without historical forced seed anchors, known-real seed bypass/reserve, "
            "rejected-neighborhood memory, or scenario-specific risk priors. This flag "
            "contains no teacher coordinates; audit teacher recovery after the run."
        ),
    )
    parser.add_argument(
        "--phase2-real-delta-min-plan-feature-coverage",
        type=float,
        default=float(
            os.getenv("PHASE2_REAL_DELTA_MIN_PLAN_FEATURE_COVERAGE", "0.80")
        ),
        help="Minimum required calibrated plan-feature coverage for candidate ranking.",
    )
    parser.add_argument(
        "--adaptive-replay-controller-enabled",
        action="store_true",
        default=_env_bool("ADAPTIVE_REPLAY_CONTROLLER_ENABLED", False),
        help=(
            "Enable lane-aware adaptive replay controller (override §478-544). "
            "When set, candidate_replay_top_k is capped by --adaptive-replay-controller-max-budget, "
            "small_loop_min_validated_candidate_count is decoupled to --adaptive-replay-controller-min-evidence, "
            "small_loop_abort_on_prefix_insufficient becomes True, and lane quotas are enabled."
        ),
    )
    parser.add_argument(
        "--adaptive-replay-controller-max-budget",
        type=int,
        default=int(os.getenv("ADAPTIVE_REPLAY_CONTROLLER_MAX_BUDGET", "24")),
        help="Maximum replay budget when adaptive replay controller is enabled (override §450).",
    )
    parser.add_argument(
        "--adaptive-replay-controller-min-evidence",
        type=int,
        default=int(os.getenv("ADAPTIVE_REPLAY_CONTROLLER_MIN_EVIDENCE", "12")),
        help="Minimum evidence count when adaptive replay controller is enabled (override §450).",
    )
    parser.add_argument(
        "--adaptive-replay-controller-batch-size",
        type=int,
        default=int(os.getenv("ADAPTIVE_REPLAY_CONTROLLER_BATCH_SIZE", "6")),
        help="Batch size for adaptive replay controller (override §450).",
    )
    parser.add_argument(
        "--adaptive-replay-controller-max-expansions",
        type=int,
        default=int(os.getenv("ADAPTIVE_REPLAY_CONTROLLER_MAX_EXPANSIONS", "2")),
        help="Maximum expansions for adaptive replay controller (override §450).",
    )
    parser.add_argument(
        "--memory-replay-calibration-enabled",
        action="store_true",
        default=_env_bool("MEMORY_REPLAY_CALIBRATION_ENABLED", False),
        help=(
            "iter107: enable Memory replay-calibration gate inside the generated "
            "config's resource model quality_gate. When enabled, the gate reads "
            "predicted-down vs actual-down evidence and fails the optimizer if "
            "predicted-down-actual-up ratio exceeds the threshold "
            "(--memory-replay-calibration-max-predicted-down-actual-up-ratio, "
            "default 0.5) and predicted_down_count >= "
            "--memory-replay-calibration-min-predicted-down-count (default 5). "
            "v6 round-0 evidence: 21 predicted-down but only 3 actual-down "
            "(predicted-down-actual-up ratio ~85%%)."
        ),
    )
    parser.add_argument(
        "--memory-replay-calibration-max-predicted-down-actual-up-ratio",
        type=float,
        default=float(os.getenv("MEMORY_REPLAY_CALIBRATION_MAX_PREDICTED_DOWN_ACTUAL_UP_RATIO", "0.5")),
        help=(
            "iter107: maximum allowed predicted-down-actual-up ratio. "
            "When predicted_down_count >= --memory-replay-calibration-min-predicted-down-count "
            "and predicted_down_actual_up_count / predicted_down_count exceeds this ratio, "
            "the resource quality gate fails. Default 0.5 (override §2026-07-07 iter106 §715-806)."
        ),
    )
    parser.add_argument(
        "--memory-replay-calibration-min-predicted-down-count",
        type=int,
        default=int(os.getenv("MEMORY_REPLAY_CALIBRATION_MIN_PREDICTED_DOWN_COUNT", "5")),
        help=(
            "iter107: minimum predicted-down count for the predicted-down-actual-up ratio "
            "to apply. If predicted_down_count < this floor, the ratio is statistically "
            "unreliable and the calibration check is skipped. Default 5."
        ),
    )
    parser.add_argument(
        "--memory-replay-calibration-evidence-path",
        type=str,
        default=os.getenv("MEMORY_REPLAY_CALIBRATION_EVIDENCE_PATH", ""),
        help=(
            "iter107: path to a JSON file produced by "
            "shell/build_memory_replay_calibration_evidence.py containing "
            "predicted_count/actual_count/predicted_down_actual_up_count fields. "
            "When provided, the evidence is inlined into the generated config's "
            "memory_replay_calibration.evidence block so the optimizer gates can "
            "consume it without runtime file reads. When the file is missing or "
            "unparseable, build_optimizer_config raises a hard RuntimeError (provenance "
            "hard-fail) for memory-target runs with --memory-replay-calibration-enabled."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=_env_bool("DRY_RUN", False),
        help=(
            "iter107: build and write the generated optimizer configs only, then exit 0 "
            "without launching PG/HyBench/optimizer subprocesses. Enables verification "
            "of generated config blocks (memory_replay_calibration, adaptive_replay_controller, "
            "etc.) before v7 launch."
        ),
    )
    parser.add_argument(
        "--bruteforce-early-stop-threshold",
        type=int,
        default=int(os.getenv("BRUTEFORCE_EARLY_STOP_THRESHOLD", "4")),
        help="Stop estimator-only bruteforce after this many consecutive no-improvement iterations.",
    )
    parser.add_argument(
        "--bruteforce-range-shrink-factor",
        type=float,
        default=float(os.getenv("BRUTEFORCE_RANGE_SHRINK_FACTOR", "0.5")),
        help="Factor for centered bruteforce interval shrinking around the accepted real-validation candidate.",
    )
    parser.add_argument(
        "--bruteforce-range-update-strategy",
        choices=["centered", "binary_interval"],
        default=os.getenv("BRUTEFORCE_RANGE_UPDATE_STRATEGY", "binary_interval"),
        help=(
            "How outer-loop real-validation updates bruteforce parameter intervals. "
            "binary_interval keeps the half interval containing the real best point."
        ),
    )
    parser.add_argument(
        "--bruteforce-range-min-width-pct",
        type=float,
        default=float(os.getenv("BRUTEFORCE_RANGE_MIN_WIDTH_PCT", "1.0")),
        help="Stop bruteforce interval search when each active parameter range is below this percentage of its initial width.",
    )
    parser.add_argument(
        "--bruteforce-range-min-width",
        type=float,
        default=float(os.getenv("BRUTEFORCE_RANGE_MIN_WIDTH", "0.0")),
        help="Absolute minimum bruteforce range width. The effective threshold is max(this value, range_min_width_pct).",
    )
    parser.add_argument(
        "--bruteforce-param-discrete-count",
        type=int,
        default=int(os.getenv("BRUTEFORCE_PARAM_DISCRETE_COUNT", "20")),
        help="Override param_ranges.<param>.discrete_count for all active Bonect cost params when >=2.",
    )
    parser.add_argument(
        "--bruteforce-param-step",
        action="append",
        default=[],
        help=(
            "Override param_ranges.<param>.step for a single cost parameter as param=step. "
            "May be repeated; only the seven PostgreSQL cost parameters are allowed."
        ),
    )
    parser.add_argument(
        "--bruteforce-param-values",
        action="append",
        default=[],
        help=(
            "Override param_ranges.<param>.values for explicit bruteforce grid points as "
            "param=v1,v2,... May be repeated; only the seven PostgreSQL cost parameters are allowed."
        ),
    )
    parser.add_argument(
        "--bonect-param-range",
        action="append",
        default=[],
        help=(
            "Override param_ranges.<param>.min_value/max_value as param=min:max. "
            "May be repeated; only the seven PostgreSQL cost parameters are allowed."
        ),
    )
    parser.add_argument(
        "--bonect-active-params",
        default=os.getenv("BONECT_ACTIVE_PARAMS"),
        help=(
            "Comma-separated active PostgreSQL cost params for Bonect tuning. "
            "Only the seven cost parameters are allowed."
        ),
    )
    parser.add_argument(
        "--bonect-initial-param",
        action="append",
        default=[],
        help=(
            "Override initial_params.<param> for one PostgreSQL cost parameter as param=value. "
            "May be repeated; only the seven PostgreSQL cost parameters are allowed."
        ),
    )
    parser.add_argument("--score-mode", default=os.getenv("SCORE_MODE", "band_target"))
    parser.add_argument("--overload-threshold", type=float, default=float(os.getenv("OVERLOAD_THRESHOLD", "0.8")))
    parser.add_argument("--target-resource-weight", type=float, default=float(os.getenv("TARGET_RESOURCE_WEIGHT", "3.0")))
    parser.add_argument("--io-reward-weight", type=float, default=float(os.getenv("IO_REWARD_WEIGHT", "0.25")))
    parser.add_argument("--io-penalty-weight", type=float, default=float(os.getenv("IO_PENALTY_WEIGHT", "1.0")))
    parser.add_argument("--capability-headroom", type=float, default=float(os.getenv("CAPABILITY_HEADROOM", "0.9")))
    parser.add_argument(
        "--bonect-capability",
        action="append",
        default=[],
        help=(
            "Override generated capability.<resource> as resource=value for cpu, memory, or io. "
            "This controls environment/resource normalization only, not PostgreSQL tunable parameters."
        ),
    )
    parser.add_argument(
        "--no-adaptive-bottleneck-calibration",
        dest="adaptive_bottleneck_calibration",
        action="store_false",
        help=(
            "Disable baseline-runtime capability calibration. By default, resource-estimator runs lower the target "
            "resource capability after the first real baseline so the target bottleneck is actually represented in JSON."
        ),
    )
    parser.set_defaults(adaptive_bottleneck_calibration=True)
    parser.add_argument(
        "--adaptive-bottleneck-min-util-pct",
        type=float,
        default=float(os.getenv("ADAPTIVE_BOTTLENECK_MIN_UTIL_PCT", "80")),
        help="Only recalibrate target capability when baseline utilization is below this percentage.",
    )
    parser.add_argument(
        "--adaptive-bottleneck-target-util-pct",
        type=float,
        default=float(os.getenv("ADAPTIVE_BOTTLENECK_TARGET_UTIL_PCT", "90")),
        help="Target utilization after lowering target-resource capability from real baseline usage.",
    )
    parser.add_argument(
        "--no-final-feedback-loop",
        dest="final_feedback_loop",
        action="store_false",
        help=(
            "Disable post-final-validation feedback rounds. By default generated optimizer configs rerun "
            "optimization when final actual resources are overloaded or far from the target utilization."
        ),
    )
    parser.set_defaults(final_feedback_loop=True)
    parser.add_argument(
        "--final-feedback-max-rounds",
        type=int,
        default=int(os.getenv("FINAL_FEEDBACK_MAX_ROUNDS", "30")),
        help="Maximum extra optimize+final-validation rounds after the first final replay.",
    )
    parser.add_argument(
        "--final-feedback-target-util-pct",
        type=float,
        default=float(os.getenv("FINAL_FEEDBACK_TARGET_UTIL_PCT", "80")),
        help="Target actual utilization percentage for feedback-loop capability retargeting.",
    )
    parser.add_argument(
        "--final-feedback-tolerance-pct",
        type=float,
        default=float(os.getenv("FINAL_FEEDBACK_TOLERANCE_PCT", "5")),
        help="Allowed absolute utilization deviation from final-feedback target before rerunning optimization.",
    )
    parser.add_argument(
        "--final-feedback-overload-util-pct",
        type=float,
        default=float(os.getenv("FINAL_FEEDBACK_OVERLOAD_UTIL_PCT", "100")),
        help="Utilization percentage treated as resource overload in final feedback loop.",
    )
    parser.add_argument("--plan-explain-workers", type=int, default=int(os.getenv("PLAN_EXPLAIN_WORKERS", "8")))
    parser.add_argument("--plan-work-mem-mb", type=int, default=int(os.getenv("PLAN_WORK_MEM_MB", "32")))
    parser.add_argument("--plan-capture-limit", type=int, default=int(os.getenv("PLAN_CAPTURE_LIMIT", "100")))
    parser.add_argument(
        "--plan-learned-cpu-vs-formula-ratio-min",
        type=float,
        default=float(os.getenv("PLAN_LEARNED_CPU_VS_FORMULA_RATIO_MIN", "0.25")),
        help=(
            "Minimum allowed learned CPU / plan-formula CPU ratio before falling back to formula. "
            "Default preserves the estimator's historical guard."
        ),
    )
    parser.add_argument(
        "--plan-learned-cpu-vs-formula-ratio-max",
        type=float,
        default=float(os.getenv("PLAN_LEARNED_CPU_VS_FORMULA_RATIO_MAX", "4.0")),
        help=(
            "Maximum allowed learned CPU / plan-formula CPU ratio before falling back to formula. "
            "Default preserves the estimator's historical guard."
        ),
    )
    parser.add_argument(
        "--plan-learned-cpu-anchor-delta-scale-max",
        type=float,
        default=float(os.getenv("PLAN_LEARNED_CPU_ANCHOR_DELTA_SCALE_MAX", "10.0")),
        help=(
            "Maximum current-baseline CPU anchor delta scale. Values above this bound "
            "remain a hard resource-model sanity failure."
        ),
    )
    parser.add_argument(
        "--no-warmup",
        dest="warmup_enabled",
        action="store_false",
        help="Disable the pre-baseline workload warmup run inside each optimizer experiment.",
    )
    parser.set_defaults(warmup_enabled=True)
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        default=_env_bool("BONECT_BASELINE_ONLY", False),
        help=(
            "Run restored baseline warmup, measurement, resource monitoring, and strong gate, "
            "then exit before model retraining and candidate search."
        ),
    )
    parser.add_argument(
        "--warmup-timeout-sec",
        type=float,
        default=float(os.getenv("WARMUP_TIMEOUT_SEC", "180")),
        help="Maximum seconds for the pre-baseline workload warmup run. Timeout is treated as warmup completion.",
    )
    parser.add_argument(
        "--no-validation-warmup",
        dest="validation_warmup_enabled",
        action="store_false",
        help="Disable the unmonitored warmup run before each measured validation workload.",
    )
    parser.set_defaults(validation_warmup_enabled=True)
    parser.add_argument(
        "--validation-warmup-timeout-sec",
        type=float,
        default=float(os.getenv("VALIDATION_WARMUP_TIMEOUT_SEC", os.getenv("WARMUP_TIMEOUT_SEC", "180"))),
        help="Maximum seconds for the unmonitored warmup before baseline/final/feedback validation.",
    )
    parser.add_argument(
        "--validation-warmup-drain-timeout-sec",
        type=float,
        default=float(os.getenv("VALIDATION_WARMUP_DRAIN_TIMEOUT_SEC", "90")),
        help=(
            "Maximum seconds to wait for warmup JDBC sessions to disappear before "
            "starting the measured workload."
        ),
    )
    parser.add_argument(
        "--validation-warmup-drain-poll-sec",
        type=float,
        default=float(os.getenv("VALIDATION_WARMUP_DRAIN_POLL_SEC", "2")),
        help="Polling interval for the post-warmup JDBC connection drain gate.",
    )
    parser.add_argument(
        "--validation-warmup-drain-max-app-connections",
        type=int,
        default=int(os.getenv("VALIDATION_WARMUP_DRAIN_MAX_APP_CONNECTIONS", "0")),
        help="Maximum matching warmup application sessions allowed before measurement.",
    )
    parser.add_argument(
        "--validation-warmup-drain-application-name",
        default=os.getenv(
            "VALIDATION_WARMUP_DRAIN_APPLICATION_NAME",
            "resource-adaptive-matrix",
        ),
        help="pg_stat_activity application_name used by the warmup connection drain gate.",
    )
    parser.add_argument(
        "--no-hybench-snapshot-restore",
        dest="hybench_snapshot_restore_enabled",
        action="store_false",
        help=(
            "Disable immutable HyBench template restore before Bonect baseline/final/feedback "
            "measurements. Intended only for explicit diagnostics."
        ),
    )
    parser.set_defaults(hybench_snapshot_restore_enabled=True)
    parser.add_argument(
        "--hybench-snapshot-template-db",
        default=os.getenv("HYBENCH_SNAPSHOT_TEMPLATE_DB", "hybench_sf10x_base"),
        help="Immutable PostgreSQL template database used to restore HyBench real replays.",
    )
    parser.add_argument(
        "--hybench-snapshot-restore-command",
        default=os.getenv(
            "HYBENCH_SNAPSHOT_RESTORE_COMMAND",
            str(PROJECT_ROOT / "shell" / "restore_hybench_sf10x_from_template.sh"),
        ),
        help="Local command that restores the HyBench database from the immutable template.",
    )
    parser.add_argument(
        "--hybench-snapshot-restore-timeout-sec",
        type=float,
        default=float(os.getenv("HYBENCH_SNAPSHOT_RESTORE_TIMEOUT_SEC", "3600")),
        help="Hard timeout for each immutable HyBench template restore.",
    )
    parser.add_argument(
        "--hybench-snapshot-boost-cpu",
        type=int,
        default=int(os.getenv("HYBENCH_SNAPSHOT_BOOST_CPU", "96")),
        help="Temporary VM vCPU count used while cloning the immutable HyBench template.",
    )
    parser.add_argument(
        "--hybench-snapshot-boost-memory-gb",
        type=int,
        default=int(os.getenv("HYBENCH_SNAPSHOT_BOOST_MEMORY_GB", "64")),
        help="Temporary VM memory in GiB used while cloning the immutable HyBench template.",
    )
    parser.add_argument(
        "--runtime-monitor-warmup-sec",
        type=float,
        default=float(os.getenv("RUNTIME_MONITOR_WARMUP_SEC", "0")),
        help="Seconds to discard from runtime monitor samples during measured workload runs. Separate from workload warmup.",
    )
    parser.add_argument(
        "--runtime-monitor-interval-sec",
        type=float,
        default=float(os.getenv("RUNTIME_MONITOR_INTERVAL_SEC", "2")),
    )
    parser.add_argument(
        "--measured-workload-timeout-sec",
        type=float,
        default=float(os.getenv("MEASURED_WORKLOAD_TIMEOUT_SEC", "0")),
        help=(
            "Hard deadline for each measured workload. A value <=0 derives the deadline "
            "from the configured benchmark duration plus 120 seconds."
        ),
    )
    parser.add_argument("--docker-runtime-container", default=os.getenv("DOCKER_RUNTIME_CONTAINER", "pg-master"))
    parser.add_argument("--docker-runtime-memory-mode", default=os.getenv("DOCKER_RUNTIME_MEMORY_MODE", "pss"))
    parser.add_argument("--vm-runtime-ssh-target", default=os.getenv("VM_RUNTIME_SSH_TARGET", "anonymous@192.0.2.8"))
    parser.add_argument("--vm-runtime-ssh-port", type=int, default=int(os.getenv("VM_RUNTIME_SSH_PORT", "22")))
    parser.add_argument("--vm-runtime-ssh-identity-file", default=os.getenv("VM_RUNTIME_SSH_IDENTITY_FILE", ""))
    parser.add_argument("--vm-runtime-cpu-mode", default=os.getenv("VM_RUNTIME_CPU_MODE", "pg_proc"))
    parser.add_argument("--vm-runtime-io-mode", default=os.getenv("VM_RUNTIME_IO_MODE", "disk_device"))
    parser.add_argument("--vm-runtime-memory-mode", default=os.getenv("VM_RUNTIME_MEMORY_MODE", "vm_used"))
    parser.add_argument(
        "--vm-drop-cache-command",
        default=os.getenv("VM_DROP_CACHE_COMMAND", "sudo -n /home/anonymous/bin/drop_vm_cache.sh"),
        help="Remote command used via SSH to drop VM filesystem/page cache before each measured validation.",
    )
    parser.add_argument(
        "--final-validation",
        action="store_true",
        help="Also run parameter_optimizer.py final validation. Default is off for plan-estimator matrix runs.",
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--strict-check", action="store_true")
    parser.add_argument("--stop-on-skip", action="store_true")
    failure_group = parser.add_mutually_exclusive_group()
    failure_group.add_argument(
        "--continue-on-failure",
        dest="continue_on_failure",
        action="store_true",
        default=True,
        help="Continue running later prepare steps and experiment specs after a failure. This is the default.",
    )
    failure_group.add_argument(
        "--fail-fast",
        dest="continue_on_failure",
        action="store_false",
        help="Stop at the first prepare, VM-profile, skip, or benchmark failure.",
    )
    parser.add_argument("--apply-vm-profile", action="store_true", help="Actually call shell/set_vm_resource.sh before each bottleneck group.")
    parser.add_argument(
        "--vm-profile-reapply-max-retries",
        type=int,
        default=int(os.getenv("VM_PROFILE_REAPPLY_MAX_RETRIES", "2")),
        help=(
            "Maximum per-case retries after parameter_optimizer exits 75 because "
            "baseline bottleneck calibration wrote an adaptive vm_resource profile."
        ),
    )
    parser.add_argument(
        "--no-start-pg-after-vm-profile",
        dest="start_pg_after_vm_profile",
        action="store_false",
        help="Do not start/wait for PostgreSQL after each VM profile change.",
    )
    parser.set_defaults(start_pg_after_vm_profile=True)
    parser.add_argument(
        "--pg-start-command",
        default=os.getenv(
            "PG_START_COMMAND",
            "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
            "-D /home/anonymous/pg_master/pg_data status >/dev/null 2>&1 && "
            "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
            "-D /home/anonymous/pg_master/pg_data -w -t 600 -m fast restart "
            "</dev/null >/tmp/resource_adaptive_pg_start.log 2>&1 || "
            "sudo -n -u postgres /usr/lib/postgresql/17/bin/pg_ctl "
            "-D /home/anonymous/pg_master/pg_data -w -t 600 start "
            "</dev/null >/tmp/resource_adaptive_pg_start.log 2>&1; "
            "rc=$?; cat /tmp/resource_adaptive_pg_start.log; exit $rc",
        ),
        help="Remote command used via SSH to start/restart PostgreSQL after a VM profile change.",
    )
    parser.add_argument(
        "--pg-ready-timeout-sec",
        type=float,
        default=float(os.getenv("PG_READY_TIMEOUT_SEC", "900")),
        help="Seconds to wait for PostgreSQL after each VM profile change.",
    )
    parser.add_argument(
        "--vm-ssh-ready-timeout-sec",
        type=float,
        default=float(os.getenv("VM_SSH_READY_TIMEOUT_SEC", "300")),
        help="Seconds to wait for VM SSH after each VM profile change.",
    )
    parser.add_argument(
        "--vm-ready-poll-sec",
        type=float,
        default=float(os.getenv("VM_READY_POLL_SEC", "5")),
    )
    parser.add_argument(
        "--force-vm-destroy-on-shutdown-timeout",
        action="store_true",
        default=os.getenv("VM_RESOURCE_FORCE_DESTROY_ON_SHUTDOWN_TIMEOUT", "0") == "1",
        help="Allow set_vm_resource.sh to virsh destroy the VM if graceful shutdown times out.",
    )
    parser.add_argument(
        "--bonect-force-pair-probe",
        action="store_true",
        default=os.getenv("BONECT_FORCE_PAIR_PROBE", "0") == "1",
        help=(
            "Force bounded pair-probe candidates into Bonect bruteforce ranking even when relaxed "
            "candidate gate already has coordinate-scan candidates."
        ),
    )
    parser.add_argument(
        "--bonect-pair-probe-mode",
        choices=["full-factorial", "sparse"],
        default=os.getenv("BONECT_PAIR_PROBE_MODE", "full-factorial"),
        help=(
            "Use the full 5^active grid, or a sparse combination grid for "
            "pre-full-grid real-ranking calibration."
        ),
    )
    parser.add_argument(
        "--bonect-pair-probe-max-combination-size",
        type=int,
        default=int(
            os.getenv(
                "BONECT_PAIR_PROBE_MAX_COMBINATION_SIZE",
                str(BONECT_PAIR_PROBE_MAX_COMBINATION_SIZE),
            )
        ),
        help="Maximum active-parameter combination size in sparse pair-probe mode.",
    )
    parser.add_argument(
        "--bonect-pair-probe-max-candidates",
        type=int,
        default=int(os.getenv("BONECT_PAIR_PROBE_MAX_CANDIDATES", "0")),
        help=(
            "Maximum pair-probe candidates; 0 derives 5^7 for full mode or the "
            "selected sparse-combination upper bound."
        ),
    )
    parser.add_argument("--vm-name", default=os.getenv("VM_NAME", "artifact-ubuntu20"))
    parser.add_argument("--vm-disk-dev", default=os.getenv("VM_DISK_DEV", "vda"))
    parser.add_argument("--vm-cpu-cpu", type=int, default=15)
    parser.add_argument("--vm-cpu-mem", default="16G")
    parser.add_argument("--vm-cpu-read-mb", type=int, default=30)
    parser.add_argument("--vm-cpu-write-mb", type=int, default=30)
    parser.add_argument("--vm-memory-cpu", type=int, default=14)
    parser.add_argument("--vm-memory-mem", default="12G")
    parser.add_argument("--vm-memory-read-mb", type=int, default=800)
    parser.add_argument("--vm-memory-write-mb", type=int, default=800)
    parser.add_argument("--vm-io-cpu", type=int, default=8)
    parser.add_argument("--vm-io-mem", default="16G")
    parser.add_argument("--vm-io-read-mb", type=int, default=50)
    parser.add_argument("--vm-io-write-mb", type=int, default=20)
    parser.add_argument("--vm-none-cpu", type=int, default=32)
    parser.add_argument("--vm-none-mem", default="16G")
    parser.add_argument("--vm-none-read-mb", type=int, default=100)
    parser.add_argument("--vm-none-write-mb", type=int, default=100)
    parser.add_argument(
        "--allow-noncanonical-none-profile",
        action="store_true",
        default=os.getenv("BONECT_ALLOW_NONCANONICAL_NONE_PROFILE", "0") == "1",
        help=(
            "Allow explicit No-bottleneck VM/capability profile overrides. "
            "Use only after a measured baseline shows the canonical strong-none profile misses the 20-30%% resource gate."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (
        args.scenario_set == "bonect"
        and args.run
        and not args.use_resource_estimator
        and not args.allow_direct_bonect_benchmark
    ):
        print(
            "ERROR: refusing Bonect run without --use-resource-estimator; "
            "otherwise this would execute a plain benchmark and perform no parameter optimization. "
            "Pass --allow-direct-bonect-benchmark only for an intentional diagnostic run.",
            file=sys.stderr,
            flush=True,
        )
        return 77
    if args.scenario_set == "bonect":
        requested_bottlenecks = {
            item.strip().lower()
            for item in str(args.bonect_bottlenecks or "").split(",")
            if item.strip()
        }
        if requested_bottlenecks == {"none"}:
            capability_overrides = parse_capability_overrides(getattr(args, "bonect_capability", []))
            none_profile_ok = (
                int(args.vm_none_cpu) == 32
                and str(args.vm_none_mem).strip().upper() == "11G"
                and int(args.vm_none_read_mb) == 40
                and int(args.vm_none_write_mb) == 40
                and abs(float(capability_overrides.get("cpu", -1.0)) - 28.8) < 1e-6
                and abs(float(capability_overrides.get("memory", -1.0)) - 9.9) < 1e-6
                and abs(float(capability_overrides.get("io", -1.0)) - 72.0) < 1e-6
            )
            if not none_profile_ok and not args.allow_noncanonical_none_profile:
                print(
                    "ERROR: refusing none Bonect run that does not match the strong "
                    "baseline profile: vm=32CPU/11G/40MBps/40MBps and "
                    "capability=cpu:28.8,memory:9.9,io:72.0. "
                    "Pass --allow-noncanonical-none-profile only after a measured "
                    "baseline proves the canonical profile misses the strong none gate.",
                    file=sys.stderr,
                    flush=True,
                )
                return 76
    if args.run and args.start_pg_after_vm_profile:
        conn = PgConn(args.pg_host, args.pg_port, args.pg_user, args.pg_password, "postgres")
        if not ensure_postgres_after_vm_profile(args, conn):
            return 1
    try:
        specs = build_specs(args, args.run_id)
        prepare_specs = build_prepare_specs(args, args.run_id)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    if args.scenario_set == "bonect":
        try:
            ref = write_reference(args.run_id, PROJECT_ROOT / args.output_dir)
            print(f"BONECT_REFERENCE {ref['summary_md']}", flush=True)
        except Exception as exc:
            print(f"ERROR: failed to prepare result/bonect reference: {exc}", file=sys.stderr, flush=True)
            return 1

    # iter107: --dry-run mode (override §2026-07-06 iter100 §8 #2 /
    # §2026-07-06 23:55 iter103 §8 #1 / §2026-07-07 03:12 iter106 §715-806
    # next_iter PRIORITY #2 + #3): build and write all generated optimizer
    # config files, then exit 0 WITHOUT launching PG/HyBench/optimizer
    # subprocesses. Enables end-to-end verification of generated-config
    # blocks (adaptive_replay_controller, memory_replay_calibration, quality
    # gate / Min_test_r2 / required_resources) before v7 real launch.
    if args.dry_run:
        written_paths: List[Path] = []
        failures: List[str] = []
        for spec in specs:
            try:
                cfg_path = build_optimizer_config(spec, args, args.run_id)
                print(f"DRY_RUN_CFG_WRITTEN {cfg_path}", flush=True)
                written_paths.append(cfg_path)
            except Exception as exc:
                failures.append(f"{spec.bottleneck}/{spec.benchmark}: {exc}")
                print(f"ERROR: DRY_RUN cfg build failed for {spec.bottleneck}/{spec.benchmark}: {exc}", flush=True)
        print(
            f"DRY_RUN_DONE written={len(written_paths)} failures={len(failures)}",
            flush=True,
        )
        return 0 if not failures else 1

    check_rc = check_environment(args, specs, prepare_specs)
    if args.check_only or check_rc != 0:
        return check_rc
    prepare_rc = execute_prepare_specs(args, prepare_specs)
    if prepare_rc != 0 and not args.continue_on_failure:
        return prepare_rc
    if args.prepare_only:
        return prepare_rc
    specs_rc = execute_specs(args, specs)
    return prepare_rc or specs_rc


if __name__ == "__main__":
    raise SystemExit(main())
