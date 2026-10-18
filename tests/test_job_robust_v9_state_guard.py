from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/run_job_round8_strict_serial_robust_v9_state_guard.py"


def import_module():
    fake = ModuleType("run_job_round8_strict_serial_robust_v8_apply_failure")
    previous = sys.modules.get(fake.__name__)
    sys.modules[fake.__name__] = fake
    try:
        spec = importlib.util.spec_from_file_location("job_v9_guard_test", MODULE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot import JOB v9 guard")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop(fake.__name__, None)
        else:
            sys.modules[fake.__name__] = previous


guard = import_module()


PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}


def state(accepted: int = 0, score: float = 4.0) -> dict:
    return {
        "accepted_count": accepted,
        "incumbent_score": score,
        "incumbent_params": copy.deepcopy(PARAMS),
        "range_by_parameter": {
            name: {"min": value, "max": value}
            for name, value in PARAMS.items()
        },
    }


def rejected_row(rank: int, role: str, before: dict) -> dict:
    return {
        "rank": rank,
        "selection_role": role,
        "technical_failure": False,
        "accepted": False,
        "gradient_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "incumbent_state_before": copy.deepcopy(before),
        "incumbent_state_after": copy.deepcopy(before),
    }


class JobRobustV9StateGuardTest(unittest.TestCase):
    def test_rejected_and_probe_rows_are_state_neutral(self) -> None:
        initial = state()
        rows = [
            rejected_row(1, "trusted", initial),
            rejected_row(2, "probe", initial),
        ]
        result = {
            "successful": rows,
            "technical_failures": [],
            "successful_real_replay_count": 2,
            "technical_failure_count": 0,
            "final_state": copy.deepcopy(initial),
        }
        guard.validate_formal_state_transitions(result, initial)

    def test_rejected_incumbent_or_range_mutation_fails_closed(self) -> None:
        initial = state()
        row = rejected_row(1, "trusted", initial)
        row["incumbent_state_after"]["incumbent_score"] = 9.0
        result = {
            "successful": [row],
            "technical_failures": [],
            "successful_real_replay_count": 1,
            "technical_failure_count": 0,
            "final_state": copy.deepcopy(row["incumbent_state_after"]),
        }
        with self.assertRaisesRegex(RuntimeError, "rejected rank 1 mutated"):
            guard.validate_formal_state_transitions(result, initial)

    def test_accepted_trusted_row_has_exact_incumbent_and_range_transition(self) -> None:
        initial = state()
        candidate = copy.deepcopy(PARAMS)
        candidate["cpu_operator_cost"] = 0.003
        after = state(accepted=1, score=4.5)
        after["incumbent_params"] = copy.deepcopy(candidate)
        after["range_by_parameter"]["cpu_operator_cost"]["max"] = 0.003
        row = {
            "rank": 1,
            "selection_role": "trusted",
            "technical_failure": False,
            "accepted": True,
            "gradient_update_applied": True,
            "incumbent_update_applied": True,
            "range_update_applied": True,
            "incumbent_state_before": copy.deepcopy(initial),
            "incumbent_state_after": copy.deepcopy(after),
            "candidate_binding": {"candidate_params": candidate},
        }
        result = {
            "successful": [row],
            "technical_failures": [],
            "successful_real_replay_count": 1,
            "technical_failure_count": 0,
            "final_state": copy.deepcopy(after),
        }
        guard.validate_formal_state_transitions(result, initial)

    def test_technical_failure_with_update_flag_fails_closed(self) -> None:
        initial = state()
        failure = {
            "rank": 1,
            "technical_failure": True,
            "accepted": False,
            "gradient_update_applied": False,
            "incumbent_update_applied": True,
            "range_update_applied": False,
        }
        result = {
            "successful": [],
            "technical_failures": [failure],
            "successful_real_replay_count": 0,
            "technical_failure_count": 1,
            "final_state": copy.deepcopy(initial),
        }
        with self.assertRaisesRegex(RuntimeError, "technical rank 1"):
            guard.validate_formal_state_transitions(result, initial)


if __name__ == "__main__":
    unittest.main()
