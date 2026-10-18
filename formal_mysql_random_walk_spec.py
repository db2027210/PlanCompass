"""Frozen Session 5 target mapping and schedule validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SESSION_ID = "mysql_random_walk"
ENGINE = "mysql"
METHOD = "random_walk"

WORKSPACE = Path("/home/anonymous/program/resource_adaptive_mysql")
SHARED_FROZEN = (
    WORKSPACE
    / "history/unified_formal/six_session_20260721_v3/shared/frozen_config"
)
AUTHORITATIVE_FILES = {
    WORKSPACE / "preflight/mysql_pg_unified_four_benchmark_configuration_20260721.md": (
        "5dc09709391c830ab8742c01af1bd93cd8483196e25d8e00226abeffc7f64cce"
    ),
    WORKSPACE / "preflight/recost_unified_four_benchmark_formal_experiment_plan_20260721.md": (
        "57bca1c1db861ed6b6096c05ef39fb35e9afb942780144ac1d530bc4cb68e3c9"
    ),
    WORKSPACE / "preflight/mysql_three_algorithm_session_prompts_goals_20260721.md": (
        "44e98f7538fae34d2e167f03b7850a62aca3f8c85411cdf064e53a620320bfce"
    ),
}


@dataclass(frozen=True)
class SessionRunSpec:
    ordinal: int
    phase: str
    run_id: str
    repeat_id: int
    benchmark: str
    workload_variant: str
    scenario: str
    case_id: str
    container_key: str
    repeat_one_source: str | None

    @property
    def exp_id(self) -> str:
        return f"{self.run_id}_r{self.repeat_id}"

    @property
    def output_relative_dir(self) -> str:
        return f"random_walk/{self.exp_id}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


RUN_DEFINITIONS: dict[str, dict[str, Any]] = {
    "A13": {
        "benchmark": "hybench_ap",
        "workload_variant": "HyBench-AP10-sensitive",
        "scenario": "memory",
        "case_id": "mysql_hybench_ap_memory",
        "container_key": "hybench",
    },
    "A14": {
        "benchmark": "job",
        "workload_variant": "JOB-26a-sensitive",
        "scenario": "memory",
        "case_id": "mysql_job_memory",
        "container_key": "job",
    },
    "A15": {
        "benchmark": "tpch",
        "workload_variant": "TPC-H-Q18-sensitive",
        "scenario": "memory",
        "case_id": "mysql_tpch_memory",
        "container_key": "tpch",
    },
    "A16": {
        "benchmark": "hybench_xp",
        "workload_variant": "HyBench-XP-AP10-sensitive",
        "scenario": "io",
        "case_id": "mysql_hybench_xp_io",
        "container_key": "hybench",
    },
    "B16": {
        "benchmark": "hybench_ap",
        "workload_variant": "HyBench-AP10-sensitive",
        "scenario": "memory",
        "case_id": "mysql_hybench_ap_memory",
        "container_key": "hybench",
        "repeat_one_source": "A13",
    },
    "B17": {
        "benchmark": "job",
        "workload_variant": "JOB-26a-sensitive",
        "scenario": "memory",
        "case_id": "mysql_job_memory",
        "container_key": "job",
        "repeat_one_source": "A14",
    },
    "B18": {
        "benchmark": "hybench_xp",
        "workload_variant": "HyBench-XP-AP10-sensitive",
        "scenario": "io",
        "case_id": "mysql_hybench_xp_io",
        "container_key": "hybench",
        "repeat_one_source": "A16",
    },
    "E13": {
        "benchmark": "hybench_ap",
        "workload_variant": "HyBench-AP10-sensitive",
        "scenario": "none",
        "case_id": "mysql_hybench_ap_none",
        "container_key": "hybench",
    },
    "E14": {
        "benchmark": "job",
        "workload_variant": "JOB-26a-sensitive",
        "scenario": "none",
        "case_id": "mysql_job_none",
        "container_key": "job",
    },
    "E15": {
        "benchmark": "tpch",
        "workload_variant": "TPC-H-Q18-sensitive",
        "scenario": "none",
        "case_id": "mysql_tpch_none",
        "container_key": "tpch",
    },
    "E16": {
        "benchmark": "hybench_xp",
        "workload_variant": "HyBench-XP-AP10-sensitive",
        "scenario": "none",
        "case_id": "mysql_hybench_xp_none",
        "container_key": "hybench",
    },
}

EXPECTED_TARGETS = {
    ("A13", 1),
    ("A14", 1),
    ("A15", 1),
    ("A16", 1),
    ("B16", 2),
    ("B16", 3),
    ("B17", 2),
    ("B17", 3),
    ("B18", 2),
    ("B18", 3),
    ("E13", 1),
    ("E14", 1),
    ("E15", 1),
    ("E16", 1),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_authoritative_files() -> dict[str, str]:
    observed = {str(path): _sha256(path) for path in AUTHORITATIVE_FILES}
    mismatch = {
        str(path): {"expected": expected, "observed": observed[str(path)]}
        for path, expected in AUTHORITATIVE_FILES.items()
        if observed[str(path)] != expected
    }
    if mismatch:
        raise ValueError(f"authoritative plan/session file changed: {mismatch}")
    return observed


def load_session_schedule(schedule_path: Path | None = None) -> tuple[SessionRunSpec, ...]:
    path = schedule_path or SHARED_FROZEN / "point_schedule.json"
    schedule = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schedule, dict) or schedule.get("total_entries") != 84:
        raise ValueError("invalid frozen point schedule")
    raw_entries = schedule.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("point schedule entries are missing")
    selected = [entry for entry in raw_entries if entry.get("session_id") == SESSION_ID]
    if len(selected) != 14:
        raise ValueError(f"Session 5 must have exactly 14 entries, got {len(selected)}")
    targets = {(entry.get("run_id"), entry.get("repeat_id")) for entry in selected}
    if targets != EXPECTED_TARGETS:
        raise ValueError(
            f"Session 5 schedule target mismatch: missing={sorted(EXPECTED_TARGETS - targets)} "
            f"extra={sorted(targets - EXPECTED_TARGETS)}"
        )
    result: list[SessionRunSpec] = []
    for entry in selected:
        fixed = {
            "engine": ENGINE,
            "method": METHOD,
            "session_id": SESSION_ID,
        }
        for key, expected in fixed.items():
            if entry.get(key) != expected:
                raise ValueError(f"schedule entry {entry.get('ordinal')} has wrong {key}")
        run_id = str(entry["run_id"])
        definition = RUN_DEFINITIONS[run_id]
        phase = str(entry["phase"])
        if phase != run_id[0]:
            raise ValueError(f"schedule phase mismatch for {run_id}")
        repeat_id = int(entry["repeat_id"])
        if phase in {"A", "E"} and repeat_id != 1:
            raise ValueError(f"{run_id} must be repeat 1")
        if phase == "B" and repeat_id not in {2, 3}:
            raise ValueError(f"{run_id} must be repeat 2 or 3")
        result.append(
            SessionRunSpec(
                ordinal=int(entry["ordinal"]),
                phase=phase,
                run_id=run_id,
                repeat_id=repeat_id,
                benchmark=str(definition["benchmark"]),
                workload_variant=str(definition["workload_variant"]),
                scenario=str(definition["scenario"]),
                case_id=str(definition["case_id"]),
                container_key=str(definition["container_key"]),
                repeat_one_source=definition.get("repeat_one_source"),
            )
        )
    return tuple(result)


def validate_against_frozen_matrices(
    specs: Sequence[SessionRunSpec],
    *,
    experiment_path: Path | None = None,
    benchmark_path: Path | None = None,
) -> None:
    experiment = json.loads(
        (experiment_path or SHARED_FROZEN / "experiment_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    expected = experiment["sessions"][SESSION_ID]
    if expected.get("campaign_id") != "mysql3alg_20260721_v3":
        raise ValueError("Session 5 campaign id mismatch")
    if expected.get("method") != METHOD or expected.get("new_runs") != 14:
        raise ValueError("Session 5 method/run count mismatch")
    if set(expected["A"]) != {spec.run_id for spec in specs if spec.phase == "A"}:
        raise ValueError("Session 5 A mapping mismatch")
    if set(expected["E"]) != {spec.run_id for spec in specs if spec.phase == "E"}:
        raise ValueError("Session 5 E mapping mismatch")
    b_pairs = {
        (run_id, repeat_id)
        for run_id, repeats in expected["B_new_repeats"].items()
        for repeat_id in repeats
    }
    if b_pairs != {(spec.run_id, spec.repeat_id) for spec in specs if spec.phase == "B"}:
        raise ValueError("Session 5 B mapping mismatch")
    benchmark = json.loads(
        (benchmark_path or SHARED_FROZEN / "benchmark_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    cases = {case["case_id"]: case for case in benchmark["cases"]}
    for spec in specs:
        case = cases.get(spec.case_id)
        if not isinstance(case, Mapping):
            raise ValueError(f"missing benchmark case {spec.case_id}")
        if (
            case.get("engine") != ENGINE
            or case.get("workload") != spec.benchmark
            or case.get("scenario") != spec.scenario
            or case.get("container") != spec.container_key
            or case.get("fixed_profile") != spec.scenario
        ):
            raise ValueError(f"benchmark case mismatch for {spec.run_id}: {case}")
