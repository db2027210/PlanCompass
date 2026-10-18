from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "capture/mysql_tpcc_typed_interference_gate_v1.py"
CAPTURE_ENTRY = ROOT / "capture/mysql_tpcc_typed_capture_entry_v1.py"


def _load_owned_numa_isolated():
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in {"EXPECTED_CPUSET", "EXPECTED_MEMS"}
                for target in node.targets
            )
        )
        or (
            isinstance(node, ast.FunctionDef)
            and node.name == "owned_numa_isolated"
        )
    ]
    namespace = {"Any": Any, "Mapping": Mapping}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(GATE), "exec"),
        namespace,
    )
    return namespace["owned_numa_isolated"]


def test_owned_numa_accepts_strict_anonymous_bind_evidence() -> None:
    check = _load_owned_numa_isolated()
    assert check(
        {
            "cpuset_cpus": "208-215,464-471",
            "cpuset_mems": "0-1",
            "anonymous_numa_nodes": [1],
            "anonymous_numa_policies": ["bind:1"],
        }
    )


def test_owned_numa_rejects_remote_node_or_default_anonymous_policy() -> None:
    check = _load_owned_numa_isolated()
    base = {
        "cpuset_cpus": "208-215,464-471",
        "cpuset_mems": "0-1",
        "anonymous_numa_nodes": [1],
        "anonymous_numa_policies": ["bind:1"],
    }
    assert not check({**base, "anonymous_numa_nodes": [0, 1]})
    assert not check({**base, "anonymous_numa_policies": ["default"]})
    assert not check({**base, "cpuset_cpus": "0-7"})


def test_gate_source_excludes_file_backed_cow_from_dynamic_numa() -> None:
    source = GATE.read_text(encoding="utf-8")
    assert '" file=" in f" {line}"' in source
    assert '" file=" not in f" {line}"' in source


def test_gate_restarts_window_without_relaxing_gap_threshold() -> None:
    source = GATE.read_text(encoding="utf-8")
    assert source.count("rolling_window_resets += 1") == 1
    assert '"sample_gap"' in source
    assert '"relevant_identity_churn"' in source
    assert '"triggers"' in source
    assert "> 1.5" in source
    assert "sample_count + 1" in source
    assert "next_sample_start += 1.0" in source
    assert "< 60.0" in source
    assert "relevant_identities(" in source
    assert "proven_disjoint_mysql_client(" in source
    assert "current_relevant != stable_relevant" in source
    assert "sampling_deadline_seconds = 150.0" in source


def test_capture_uses_valid_session_maximum_then_stops_at_capture_boundary() -> None:
    source = CAPTURE_ENTRY.read_text(encoding="utf-8")
    assert "CAPTURE_MEASUREMENT_SECONDS = 180" in source
    assert "SESSION_MAXIMUM_MEASUREMENT_SECONDS = 600" in source
    assert (
        "maximum_measurement_seconds=SESSION_MAXIMUM_MEASUREMENT_SECONDS"
        in source
    )
    assert "measurement_seconds=SESSION_MAXIMUM_MEASUREMENT_SECONDS" in source
    assert "minimum_measurement_seconds=CAPTURE_MEASUREMENT_SECONDS" in source
    assert "self.session.stop_after_measurement(" in source
    assert "CAPTURE_MEASUREMENT_SECONDS," in source
    assert "graceful_drain_seconds=120" in source
