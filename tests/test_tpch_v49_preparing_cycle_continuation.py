from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE = Path(__file__).parents[1] / "tools/tpch_v49_preparing_cycle_continuation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("tpch_v49_test", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def fixture(tmp_path: Path):
    module = load_module()
    loop = tmp_path / "loop"
    previous_run = loop / "cycle_004/resealed"
    builder_selection = previous_run / "selection.json"
    write_json(builder_selection, {"top5": []})
    builder_selection_sha = module_hash(builder_selection)
    builder_summary = previous_run / "diagnostic_summary.json"
    write_json(
        builder_summary,
        {"successful": [], "selection_sha256": builder_selection_sha},
    )
    write_json(previous_run / "diagnostic_state.json", {"accepted_count": 1})
    evidence_selection = loop / "cycle_004/v47_realized_reseal_evidence.json"
    write_json(
        evidence_selection,
        {
            "schema_version": "realized-selection-contract",
            "selection_contract": "realized-selection-contract",
            "source_predicted_selection": str(builder_selection),
            "source_predicted_selection_sha256": builder_selection_sha,
            "source_realized_summary": str(builder_summary),
            "source_realized_summary_sha256": module_hash(builder_summary),
            "manual_candidate_injection": False,
            "new_workload_calls": 0,
        },
    )
    (loop / "cycle_005").mkdir(parents=True)
    formal = tmp_path / "formal.json"
    counter = tmp_path / "counter.txt"
    handoff = tmp_path / "handoff.json"
    reuse = tmp_path / "reuse.json"
    write_json(formal, {"accepted_count": 1})
    counter.write_text("1\n", encoding="utf-8")
    write_json(handoff, {})
    write_json(reuse, {})
    selection_sha = module_hash(evidence_selection)
    state = {
        "schema_version": "tpch_fresh10_target10_loop_v26",
        "status": "preparing_cycle",
        "accepted_count": 1,
        "accepted_target": 10,
        "next_cycle": 5,
        "resume_state": str(formal),
        "cycles": [
            {
                "cycle": 4,
                "accepted_before": 1,
                "accepted_after": 1,
                "formal_started": False,
                "entry_gate_passed": False,
                "next_reexploration_selection_sha256": selection_sha,
                "v47_rank4_probe_absorption": {
                    "contract": "rank-miss-contract",
                    "status": "automatic_bounded_reexploration_ready",
                    "probe_absorbed_as_trusted": False,
                    "manual_candidate_injection": False,
                    "new_workload_calls": 0,
                    "effective_reexploration_selection": str(evidence_selection),
                    "effective_reexploration_selection_sha256": selection_sha,
                    "effective_reexploration_run": str(previous_run),
                },
            }
        ],
    }
    state_path = loop / "controller_state.json"
    write_json(state_path, state)
    paths = {
        "state": state_path,
        "counter": counter,
        "handoff": handoff,
        "reuse_audit": reuse,
    }
    fake_v26 = SimpleNamespace(
        dynamic_config=lambda *_args, **_kwargs: None,
        run=lambda *_args, **_kwargs: None,
    )
    v41 = SimpleNamespace(
        V41_LOOP=loop,
        configure_controller=lambda *_args, **_kwargs: (
            fake_v26,
            SimpleNamespace(),
        ),
    )
    calls = {"counter": 0, "formal": 0, "run": 0}

    def run_loop(*args, **kwargs):
        calls["run"] += 1
        calls["run_args"] = (args, kwargs)
        return 17

    v48 = SimpleNamespace(
        RANK_MISS_ABSORPTION_CONTRACT="rank-miss-contract",
        REALIZED_SELECTION_CONTRACT="realized-selection-contract",
        sha256=module_hash,
        load=lambda path: json.loads(path.read_text(encoding="utf-8")),
        validate_counter=lambda *_args: calls.__setitem__(
            "counter", calls["counter"] + 1
        ),
        validate_formal_authority=lambda *_args: calls.__setitem__(
            "formal", calls["formal"] + 1
        ),
        ensure_no_formal_started=lambda *_args: None,
        run_loop=run_loop,
    )
    return module, v48, v41, paths, formal, counter, calls


def module_hash(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(args):
    module, v48, v41, paths, formal, counter, calls = args
    boundary = module.validate_boundary(
        v48,
        v41,
        paths,
        expected_state_sha=module_hash(paths["state"]),
        expected_formal_sha=module_hash(formal),
        expected_counter_sha=module_hash(counter),
        expected_cycle=5,
    )
    return boundary, calls


def test_empty_preparing_cycle_resumes_v48_without_candidate_injection(tmp_path):
    args = fixture(tmp_path)
    boundary, calls = validate(args)
    assert boundary["preflight"]["new_workload_calls"] == 0
    assert boundary["preflight"]["manual_candidate_injection"] is False
    args[0].generalized_existing_cycle_run_loop = lambda v48: v48.run_loop
    assert args[0].resume_boundary(args[1], args[2], args[3], boundary) == 17
    _, kwargs = calls["run_args"]
    assert kwargs["resume_existing_cycle004"] is True
    assert kwargs["start_cycle"] == 5
    assert kwargs["previous_selection_override"] == boundary["evidence_selection"]


def test_rejects_any_diagnostic_or_workload_shape(tmp_path):
    args = fixture(tmp_path)
    (args[2].V41_LOOP / "cycle_005/diagnostic_probe").mkdir()
    with pytest.raises(RuntimeError, match="zero-workload preparing boundary"):
        validate(args)


def test_rejects_previous_selection_sha_mismatch(tmp_path):
    args = fixture(tmp_path)
    previous = Path(
        args[1].load(args[3]["state"])["cycles"][-1]
        ["v47_rank4_probe_absorption"]["effective_reexploration_selection"]
    )
    write_json(previous, {"changed": True})
    with pytest.raises(RuntimeError, match="reexploration evidence binding mismatch"):
        validate(args)


def test_accepts_only_identical_optional_preworkload_pairs(tmp_path):
    args = fixture(tmp_path)
    cycle = args[2].V41_LOOP / "cycle_005"
    write_json(cycle / "config.a.json", {"same": True})
    with pytest.raises(RuntimeError, match="partial deterministic config pair"):
        validate(args)
    write_json(cycle / "config.b.json", {"same": False})
    with pytest.raises(RuntimeError, match="deterministic config pair mismatch"):
        validate(args)
    write_json(cycle / "config.b.json", {"same": True})
    boundary, _ = validate(args)
    assert boundary["preflight"]["observed_preworkload_files"] == [
        "config.a.json",
        "config.b.json",
    ]


def test_real_cycle005_shape_reuses_completed_expansion_and_preserves_log(tmp_path):
    args = fixture(tmp_path)
    module, v48, v41, paths, _formal, _counter, calls = args
    cycle = v41.V41_LOOP / "cycle_005"
    write_json(cycle / "config.a.json", {"same": True})
    write_json(cycle / "config.b.json", {"same": True})
    expansion = cycle / "dynamic_expansion_v32"
    write_json(expansion / "expanded_candidates.a.json", {"same": True})
    write_json(expansion / "expanded_candidates.b.json", {"same": True})
    write_json(expansion / "audit.json", {"status": "complete"})
    write_json(expansion / "explain_checkpoint.json", {"completed": 7})
    (expansion / "expand_a.log").write_text("original A\n", encoding="utf-8")
    (expansion / "expand_b.log").write_text("original B\n", encoding="utf-8")
    old_builder_log = cycle / "probe_builder_a.log"
    old_builder_log.write_text("interrupted original\n", encoding="utf-8")
    boundary, _ = validate(args)
    assert boundary["preflight"]["completed_expansion_reused"] is True
    assert boundary["preflight"]["physical_explain_calls_repeated"] == 0

    observed = {}

    def configured(*_args, **_kwargs):
        fake_v26 = SimpleNamespace()
        fake_v26.dynamic_config = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("existing config must be reused")
        )

        def original_run(_common, argv, log, _environment):
            observed["log"] = Path(log)
            observed["argv"] = list(argv)
            Path(log).write_text("continued\n", encoding="utf-8")

        fake_v26.run = original_run
        return fake_v26, SimpleNamespace()

    v41.configure_controller = configured

    def exercising_run_loop(v41_arg, _paths, _state, **_kwargs):
        v26, _common = v41_arg.configure_controller()
        assert v26.dynamic_config(None, cycle, 1) == cycle / "config.a.json"
        v26.run(
            None,
            [
                "builder.py",
                "--previous-selection",
                str(boundary["evidence_selection"]),
                "--previous-summary",
                str(boundary["evidence_run"] / "diagnostic_summary.json"),
                "--additional-exclusion-selection",
                str(boundary["builder_selection"]),
            ],
            old_builder_log,
            {},
        )
        return 23

    v48.run_loop = exercising_run_loop
    module.generalized_existing_cycle_run_loop = lambda candidate: candidate.run_loop
    assert module.resume_boundary(v48, v41, paths, boundary) == 23
    assert old_builder_log.read_text(encoding="utf-8") == "interrupted original\n"
    assert observed["log"].name == (
        "probe_builder_a.v49_preparing_cycle_resume_01.log"
    )
    assert observed["log"].read_text(encoding="utf-8") == "continued\n"
    selection_index = observed["argv"].index("--previous-selection") + 1
    summary_index = observed["argv"].index("--previous-summary") + 1
    assert Path(observed["argv"][selection_index]) == boundary["builder_selection"]
    assert Path(observed["argv"][summary_index]) == boundary["builder_summary"]
    assert str(boundary["evidence_selection"]) in observed["argv"]
    assert str(boundary["builder_selection"]) not in [
        observed["argv"][index + 1]
        for index, value in enumerate(observed["argv"][:-1])
        if value == "--additional-exclusion-selection"
    ]
    assert calls["run"] == 0


def test_existing_v49_log_uses_next_id_without_overwrite(tmp_path):
    args = fixture(tmp_path)
    module, v48, v41, paths, _formal, _counter, _calls = args
    cycle = v41.V41_LOOP / "cycle_005"
    write_json(cycle / "config.a.json", {"same": True})
    write_json(cycle / "config.b.json", {"same": True})
    old = cycle / "probe_builder_a.log"
    old.write_text("original\n", encoding="utf-8")
    first = cycle / "probe_builder_a.v49_preparing_cycle_resume_01.log"
    first.write_text("first retry\n", encoding="utf-8")
    boundary, _ = validate(args)
    observed = {}

    def configured(*_args, **_kwargs):
        fake_v26 = SimpleNamespace(dynamic_config=lambda *_a, **_k: None)

        def original_run(_common, _argv, log, _environment):
            observed["log"] = Path(log)
            Path(log).write_text("second retry\n", encoding="utf-8")

        fake_v26.run = original_run
        return fake_v26, SimpleNamespace()

    v41.configure_controller = configured

    def exercising(v41_arg, _paths, _state, **_kwargs):
        v26, _common = v41_arg.configure_controller()
        v26.run(None, [], old, {})
        return 0

    v48.run_loop = exercising
    module.generalized_existing_cycle_run_loop = lambda candidate: candidate.run_loop
    assert module.resume_boundary(v48, v41, paths, boundary) == 0
    assert old.read_text(encoding="utf-8") == "original\n"
    assert first.read_text(encoding="utf-8") == "first retry\n"
    assert observed["log"].name.endswith("resume_02.log")


def test_generalized_run_loop_changes_only_hardcoded_cycle_guard(tmp_path):
    module = load_module()
    source = '''\
def run_loop(*, resume_existing_cycle004=False, start_cycle=4):
    cycle = start_cycle
    exist_ok=(resume_existing_cycle004 and cycle == 4)
    return exist_ok
'''
    implementation = tmp_path / "implementation.py"
    implementation.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("guard_fixture", implementation)
    assert spec is not None and spec.loader is not None
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    generalized = module.generalized_existing_cycle_run_loop(candidate)
    assert candidate.run_loop(resume_existing_cycle004=True, start_cycle=5) is False
    assert generalized(resume_existing_cycle004=True, start_cycle=5) is True
