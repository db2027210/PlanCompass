"""Frozen target mapping for MySQL formal Session 6 (Recost)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SESSION_ID = "mysql_recost"
ENGINE = "mysql"
METHOD = "recost_gradient_descent"
CAMPAIGN_ID = "mysql3alg_20260721_v3"
WORKSPACE = Path("/home/anonymous/program/resource_adaptive_mysql")
SHARED_FROZEN = WORKSPACE / "history/unified_formal/six_session_20260721_v3/shared/frozen_config"
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
class RecostRunSpec:
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
    def formal_run_id(self) -> str:
        return f"formal_20260721_{self.run_id}_r{self.repeat_id}"

    @property
    def output_relative_dir(self) -> str:
        return f"recost_gradient_descent/{self.exp_id}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


RUN_DEFINITIONS: Mapping[str, Mapping[str, Any]] = {
    "A21": {
        "benchmark": "hybench_ap",
        "workload_variant": "HyBench-AP10-sensitive",
        "scenario": "memory",
        "case_id": "mysql_hybench_ap_memory",
        "container_key": "hybench",
    },
    "A22": {
        "benchmark": "job",
        "workload_variant": "JOB-26a-sensitive",
        "scenario": "memory",
        "case_id": "mysql_job_memory",
        "container_key": "job",
    },
    "A23": {
        "benchmark": "tpch",
        "workload_variant": "TPC-H-Q18-sensitive",
        "scenario": "memory",
        "case_id": "mysql_tpch_memory",
        "container_key": "tpch",
    },
    "A24": {
        "benchmark": "hybench_xp",
        "workload_variant": "HyBench-XP-AP10-sensitive",
        "scenario": "io",
        "case_id": "mysql_hybench_xp_io",
        "container_key": "hybench",
    },
    "B19": {
        "benchmark": "hybench_ap",
        "workload_variant": "HyBench-AP10-sensitive",
        "scenario": "memory",
        "case_id": "mysql_hybench_ap_memory",
        "container_key": "hybench",
        "repeat_one_source": "A21",
    },
    "B20": {
        "benchmark": "job",
        "workload_variant": "JOB-26a-sensitive",
        "scenario": "memory",
        "case_id": "mysql_job_memory",
        "container_key": "job",
        "repeat_one_source": "A22",
    },
    "B21": {
        "benchmark": "hybench_xp",
        "workload_variant": "HyBench-XP-AP10-sensitive",
        "scenario": "io",
        "case_id": "mysql_hybench_xp_io",
        "container_key": "hybench",
        "repeat_one_source": "A24",
    },
    "E21": {
        "benchmark": "hybench_ap",
        "workload_variant": "HyBench-AP10-sensitive",
        "scenario": "none",
        "case_id": "mysql_hybench_ap_none",
        "container_key": "hybench",
    },
    "E22": {
        "benchmark": "job",
        "workload_variant": "JOB-26a-sensitive",
        "scenario": "none",
        "case_id": "mysql_job_none",
        "container_key": "job",
    },
    "E23": {
        "benchmark": "tpch",
        "workload_variant": "TPC-H-Q18-sensitive",
        "scenario": "none",
        "case_id": "mysql_tpch_none",
        "container_key": "tpch",
    },
    "E24": {
        "benchmark": "hybench_xp",
        "workload_variant": "HyBench-XP-AP10-sensitive",
        "scenario": "none",
        "case_id": "mysql_hybench_xp_none",
        "container_key": "hybench",
    },
}

EXPECTED_TARGETS = {
    ("A21", 1), ("A22", 1), ("A23", 1), ("A24", 1),
    ("B19", 2), ("B19", 3), ("B20", 2), ("B20", 3),
    ("B21", 2), ("B21", 3),
    ("E21", 1), ("E22", 1), ("E23", 1), ("E24", 1),
}

EXPECTED_ORDINALS = (1, 9, 17, 19, 25, 36, 38, 46, 49, 59, 64, 72, 75, 81)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_authoritative_files() -> Mapping[str, str]:
    observed = {str(path): _sha256(path) for path in AUTHORITATIVE_FILES}
    mismatches = {
        str(path): {"expected": expected, "observed": observed[str(path)]}
        for path, expected in AUTHORITATIVE_FILES.items()
        if observed[str(path)] != expected
    }
    if mismatches:
        raise ValueError(f"authoritative input changed: {mismatches}")
    return observed


def load_recost_schedule(schedule_path: Path | None = None) -> tuple[RecostRunSpec, ...]:
    path = schedule_path or SHARED_FROZEN / "point_schedule.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("total_entries") != 84:
        raise ValueError("invalid frozen point schedule")
    entries = document.get("entries")
    if not isinstance(entries, list) or len(entries) != 84:
        raise ValueError("frozen schedule must contain exactly 84 entries")
    selected = [entry for entry in entries if entry.get("session_id") == SESSION_ID]
    if len(selected) != 14:
        raise ValueError(f"MySQL Recost must have exactly 14 entries, got {len(selected)}")
    if tuple(int(entry["ordinal"]) for entry in selected) != EXPECTED_ORDINALS:
        raise ValueError("MySQL Recost global ordinals differ from the frozen schedule")
    targets = {(str(entry.get("run_id")), int(entry.get("repeat_id"))) for entry in selected}
    if targets != EXPECTED_TARGETS:
        raise ValueError(
            f"MySQL Recost target mismatch: missing={sorted(EXPECTED_TARGETS - targets)} "
            f"extra={sorted(targets - EXPECTED_TARGETS)}"
        )
    result: list[RecostRunSpec] = []
    for entry in selected:
        for key, expected in {"engine": ENGINE, "method": METHOD, "session_id": SESSION_ID}.items():
            if entry.get(key) != expected:
                raise ValueError(f"schedule ordinal {entry.get('ordinal')} has wrong {key}")
        run_id = str(entry["run_id"])
        definition = RUN_DEFINITIONS[run_id]
        phase = str(entry["phase"])
        repeat_id = int(entry["repeat_id"])
        if phase != run_id[0]:
            raise ValueError(f"phase mismatch for {run_id}")
        if phase in {"A", "E"} and repeat_id != 1:
            raise ValueError(f"{run_id} must be repeat 1")
        if phase == "B" and repeat_id not in {2, 3}:
            raise ValueError(f"{run_id} must be repeat 2 or 3")
        result.append(
            RecostRunSpec(
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
    specs: Sequence[RecostRunSpec],
    *,
    experiment_path: Path | None = None,
    benchmark_path: Path | None = None,
) -> None:
    experiment = json.loads(
        (experiment_path or SHARED_FROZEN / "experiment_matrix.json").read_text(encoding="utf-8")
    )
    session = experiment["sessions"][SESSION_ID]
    if session.get("campaign_id") != CAMPAIGN_ID or session.get("method") != METHOD:
        raise ValueError("MySQL Recost campaign/method mismatch")
    if session.get("new_runs") != 14:
        raise ValueError("MySQL Recost frozen run count differs")
    if set(session["A"]) != {spec.run_id for spec in specs if spec.phase == "A"}:
        raise ValueError("MySQL Recost A mapping mismatch")
    if set(session["E"]) != {spec.run_id for spec in specs if spec.phase == "E"}:
        raise ValueError("MySQL Recost E mapping mismatch")
    b_pairs = {
        (run_id, int(repeat_id))
        for run_id, repeats in session["B_new_repeats"].items()
        for repeat_id in repeats
    }
    if b_pairs != {(spec.run_id, spec.repeat_id) for spec in specs if spec.phase == "B"}:
        raise ValueError("MySQL Recost B mapping mismatch")
    repeat_one = session["B_repeat_one_from_A"]
    for spec in specs:
        if spec.phase == "B" and repeat_one.get(spec.run_id) != spec.repeat_one_source:
            raise ValueError(f"repeat-one source mismatch for {spec.run_id}")

    benchmark = json.loads(
        (benchmark_path or SHARED_FROZEN / "benchmark_matrix.json").read_text(encoding="utf-8")
    )
    cases = {case["case_id"]: case for case in benchmark["cases"]}
    for spec in specs:
        case = cases.get(spec.case_id)
        if not isinstance(case, Mapping):
            raise ValueError(f"missing frozen benchmark case {spec.case_id}")
        observed = (
            case.get("engine"), case.get("workload"), case.get("scenario"),
            case.get("container"), case.get("fixed_profile"),
        )
        expected = (ENGINE, spec.benchmark, spec.scenario, spec.container_key, spec.scenario)
        if observed != expected:
            raise ValueError(f"benchmark case mismatch for {spec.run_id}: {observed}")
