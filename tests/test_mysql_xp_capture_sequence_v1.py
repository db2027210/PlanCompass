import ast
from pathlib import Path
from typing import Any, Dict, Mapping


ROOT = Path(__file__).resolve().parents[1]
ENTRY = Path(__file__).resolve().parent / "mysql_xp_local_capture_entry.py"
if not ENTRY.is_file():
    ENTRY = ROOT / "shell/mysql_xp_local_capture_entry_v8.py"


def _load_binding_function():
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id in {"CAPTURE_AP_SEQUENCE"}
                for target in node.targets
            )
        )
        or (
            isinstance(node, ast.FunctionDef)
            and node.name == "_bind_capture_ap_sequence"
        )
    ]
    namespace = {
        "Path": Path,
        "Any": Any,
        "Dict": Dict,
        "Mapping": Mapping,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ENTRY), "exec"), namespace)
    return namespace["CAPTURE_AP_SEQUENCE"], namespace["_bind_capture_ap_sequence"]


def _load_prestart_wrapper():
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "_CapturePreStartSession"
    ]
    namespace = {"Any": Any}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(ENTRY), "exec"),
        namespace,
    )
    return namespace["_CapturePreStartSession"]


def test_capture_sequence_is_bound_without_replacing_formal_mix(tmp_path: Path) -> None:
    sequence, bind = _load_binding_function()
    properties = tmp_path / "formal_hybench.properties"
    properties.write_text(
        "random_seed=4508333\n"
        "ap_query_ids=1,2,3,4,5,6,7,8,9,10,11,12,13\n"
        "ap_query_weights=6,6,6,6,6,6,6,6,6,28,6,6,6\n",
        encoding="utf-8",
    )
    frozen = {
        "workload_seed": 4508333,
        "workload": {"capture_ap_query_sequence": list(sequence)},
    }

    bind(properties, frozen)

    text = properties.read_text(encoding="utf-8")
    assert "ap_query_weights=6,6,6,6,6,6,6,6,6,28,6,6,6\n" in text
    assert "ap_query_sequence=1,2,3,4,5,6,7,8,9,10,11,12,13\n" in text
    assert "capture_finite_ap_sequence=1,2,3,4,5,6,7,8,9,10,11,12,13\n" in text
    assert "capture_finite_at_sequence=1,2,3,4,5,6\n" in text
    assert text.count("ap_query_sequence=") == 1


def test_capture_sequence_rejects_changed_seed(tmp_path: Path) -> None:
    sequence, bind = _load_binding_function()
    properties = tmp_path / "formal_hybench.properties"
    properties.write_text(
        "random_seed=9\n"
        "ap_query_ids=1,2,3,4,5,6,7,8,9,10,11,12,13\n"
        "ap_query_weights=6,6,6,6,6,6,6,6,6,28,6,6,6\n",
        encoding="utf-8",
    )
    frozen = {
        "workload_seed": 4508333,
        "workload": {"capture_ap_query_sequence": list(sequence)},
    }

    try:
        bind(properties, frozen)
    except ValueError as exc:
        assert "capture authority" in str(exc)
    else:
        raise AssertionError("changed capture seed was accepted")


def test_prestart_race_gate_runs_before_hybench_start() -> None:
    wrapper_type = _load_prestart_wrapper()
    events = []

    class Campaign:
        def assert_capture_prestart_clear(self):
            events.append("fresh_same_token_assert")
            raise RuntimeError("new overlapping PG process")

        def start_capture_runtime_interference_monitor(self):
            events.append("runtime_monitor")

    class Session:
        def start(self, **_kwargs):
            events.append("hybench_start")

    session = wrapper_type(Campaign(), Session())
    try:
        session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    except RuntimeError as exc:
        assert "overlapping PG" in str(exc)
    else:
        raise AssertionError("pre-start failure did not abort session start")
    assert events == ["fresh_same_token_assert"]


def test_prestart_pass_arms_runtime_monitor_before_hybench() -> None:
    wrapper_type = _load_prestart_wrapper()
    events = []

    class Campaign:
        def assert_capture_prestart_clear(self):
            events.append("fresh_same_token_assert")

        def start_capture_runtime_interference_monitor(self):
            events.append("runtime_first_sample")

    class Session:
        def start(self, **_kwargs):
            events.append("hybench_start")

    wrapper_type(Campaign(), Session()).start(
        warmup_seconds=60, maximum_measurement_seconds=600
    )
    assert events == [
        "fresh_same_token_assert",
        "runtime_first_sample",
        "hybench_start",
    ]


def test_prestart_gate_fail_closes_cpu_device_and_unknown_authority() -> None:
    source = ENTRY.read_text(encoding="utf-8")
    assert '"cpu_overlap": cpu_overlap' in source
    assert '"shared_device": shared_device' in source
    assert '"unknown_relevant_device": unknown_device' in source
    assert "capture pre-start interference gate failed before HyBench" in source
    assert "campaign.execute()\n        return 0\n    finally:\n        campaign.cleanup()" in source
