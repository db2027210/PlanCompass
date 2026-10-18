#!/usr/bin/env python3
"""Self-test for the post-terminal ABC validation handoff."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import run_pg_algorithm_c_postterminal_abc_validation as handoff  # noqa: E402


FAKE_TOOL = r'''#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path

args = sys.argv[1:]
name = Path(sys.argv[0]).name
def value(flag):
    return args[args.index(flag) + 1]
if name == "auditor.py":
    output = Path(value("--output"))
    matched = "--matched-absolute-qps" in args
    payload = {
        "complete": True,
        "publication_claim_allowed": matched,
        "publication_absolute_qps_and_efficiency_four_scene_target_met": matched,
        "runtime_independence": {
            "external_baseline_independence_passed": True,
            "sealed_runtime_integrity_passed": True,
        },
        "coverage_summary": {"terminal_evidence_integrity_passed": True},
    }
    output.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    raise SystemExit(0 if matched else 1)
if name == "runner.py":
    root = Path(value("--output-dir"))
    root.mkdir(parents=True)
    (root / "runner_args.json").write_text(json.dumps(args) + "\n", encoding="utf-8")
    raise SystemExit(0)
if name == "verifier.py":
    output = Path(value("--output"))
    output.write_text(json.dumps({"passed": True}) + "\n", encoding="utf-8")
    raise SystemExit(0)
if name == "builder.py":
    output = Path(value("--output"))
    output.write_text(json.dumps({
        "contract": "algorithm_abc_pg_common_domain_final_validation_v1"
    }) + "\n", encoding="utf-8")
    raise SystemExit(0)
raise SystemExit(9)
'''


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def make_tool(path: Path) -> None:
    path.write_text(FAKE_TOOL, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="postterminal-abc-") as temporary:
        root = Path(temporary)
        formal_root = root / "formal"
        output_root = root / "output"
        tools = root / "tools"
        tools.mkdir()
        runner = tools / "runner.py"
        verifier = tools / "verifier.py"
        builder = tools / "builder.py"
        auditor = tools / "auditor.py"
        for tool in (runner, verifier, builder, auditor):
            make_tool(tool)

        write_json(formal_root / "launcher_status.json", {"phase": "launched"})
        try:
            handoff.run_handoff(
                formal_root=formal_root,
                output_root=output_root,
                seeds=(11, 12),
                common_runner=runner,
                common_verifier=verifier,
                builder=builder,
                terminal_auditor=auditor,
                python_bin=Path(sys.executable),
            )
        except RuntimeError as exc:
            assert "not successfully terminal" in str(exc)
        else:
            raise AssertionError("nonterminal C must block before any A/B-capable subprocess")
        assert not output_root.exists()

        write_json(formal_root / "launcher_status.json", {
            "phase": "finished",
            "finished_at_epoch_s": 123.0,
            "returncodes": {scene: 0 for scene in handoff.SCENES},
            "failures": [],
            "deadline_signals": [],
        })
        returncode = handoff.run_handoff(
            formal_root=formal_root,
            output_root=output_root,
            seeds=(11, 12),
            common_runner=runner,
            common_verifier=verifier,
            builder=builder,
            terminal_auditor=auditor,
            python_bin=Path(sys.executable),
        )
        assert returncode == 0
        receipt = handoff.read_json(output_root / "handoff_receipt.json")
        assert receipt["phase"] == "complete"
        assert receipt["publication_target_met"] is True
        assert set(receipt["tool_sources"]) == {
            "common_runner",
            "common_verifier",
            "matched_qps_builder",
            "terminal_auditor",
        }
        assert all(
            len(source["sha256"]) == 64
            for source in receipt["tool_sources"].values()
        )
        assert all(step["command"] for step in receipt["steps"])
        assert all(len(step["log_sha256"]) == 64 for step in receipt["steps"])
        first_args = json.loads(
            (output_root / "abc_rep1_seed11" / "runner_args.json").read_text(encoding="utf-8")
        )
        second_args = json.loads(
            (output_root / "abc_rep2_seed12" / "runner_args.json").read_text(encoding="utf-8")
        )
        assert first_args[first_args.index("--seed") + 1] == "11"
        assert second_args[second_args.index("--seed") + 1] == "12"
        assert first_args[first_args.index("--methods") + 1:] == [
            "algorithm_a",
            "algorithm_b",
            "algorithm_c",
            "--algorithm-c-formal-root",
            str(formal_root.resolve()),
        ]

    print("PASS: post-terminal ABC validation handoff", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
