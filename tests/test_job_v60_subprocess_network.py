from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
V60 = TOOLS / "job_fresh10_target10_autonomous_v60.py"
LAUNCHER = TOOLS / "launch_job_target10_autonomous_v60.sh"
sys.path.insert(0, str(TOOLS))
import job_fresh10_target10_autonomous_v60 as v60  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_real_runner_subprocess_receives_only_sealed_network_allowlist() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        preflight = root / "network-preflight.json"
        write_json(preflight, {"sealed": True})
        child = root / "base_runner.py"
        child.write_text(
            "import json, os\n"
            "names = ('RA_JOB_DOCKER_NETWORK', "
            "'RA_JOB_NETWORK_PREFLIGHT_JSON', "
            "'RA_JOB_NETWORK_PREFLIGHT_SHA256', 'RA_JOB_EVIL')\n"
            "print(json.dumps({name: os.environ.get(name) for name in names}, sort_keys=True))\n",
            encoding="utf-8",
        )
        old_environment = dict(os.environ)
        old_preflight = v60.v59._network_preflight
        old_base = v60.implementation.v49.BASE_RUNNER
        try:
            os.environ["RA_JOB_DOCKER_NETWORK"] = "ra_job_v53_net"
            os.environ["RA_JOB_NETWORK_PREFLIGHT_JSON"] = str(preflight.resolve())
            os.environ["RA_JOB_NETWORK_PREFLIGHT_SHA256"] = sha(preflight)
            os.environ["RA_JOB_EVIL"] = "must-not-cross-boundary"
            v60.v59._network_preflight = lambda: {
                "network_name": "ra_job_v53_net",
                "network_inspect_sha256": "a" * 64,
            }
            v60.implementation.v49.BASE_RUNNER = child
            log = root / "phase/runner.log"
            assert v60._is_runner_boundary([sys.executable, str(child)])
            result = v60.run_logged_v60(
                [sys.executable, str(child)],
                log,
                timeout_seconds=10,
            )
            assert result == 0
            observed = json.loads(log.read_text(encoding="utf-8"))
            assert observed == {
                "RA_JOB_DOCKER_NETWORK": "ra_job_v53_net",
                "RA_JOB_NETWORK_PREFLIGHT_JSON": str(preflight.resolve()),
                "RA_JOB_NETWORK_PREFLIGHT_SHA256": sha(preflight),
                "RA_JOB_EVIL": None,
            }
            audit = json.loads(
                (log.parent / "v60_runner_subprocess_environment_audit.json").read_text(
                    encoding="utf-8"
                )
            )
            assert audit["status"] == "parent_asserted_before_subprocess"
            assert audit["allowed_ra_job_environment_names"] == list(
                v60.NETWORK_ENVIRONMENT_ALLOWLIST
            )
        finally:
            v60.v59._network_preflight = old_preflight
            v60.implementation.v49.BASE_RUNNER = old_base
            os.environ.clear()
            os.environ.update(old_environment)


def _failure_summary() -> dict:
    return {
        "physical_invocation_count": 0,
        "successful_real_replay_count": 0,
        "technical_failure_count": 1,
        "pre_invocation_technical_failure_count": 1,
        "diagnostic_only": True,
        "baseline_params_restored": True,
        "initial_state": {"accepted_count": 1},
        "final_state": {"accepted_count": 1},
        "preclaim_failure_marker": {
            "bridge_invoked": False,
            "preclaim_no_ordinal_claim": True,
            "error": "invalid or prohibited RA_JOB_DOCKER_NETWORK",
        },
        "technical_failures": [
            {"error": "invalid or prohibited RA_JOB_DOCKER_NETWORK"}
        ],
    }


def _selection(attempt: int) -> dict:
    return {
        "diagnostic_only": True,
        "top5": [
            {
                "selection_role": "probe",
                "three_level_fingerprint": {
                    "id": f"three-level-sha256:{attempt:02d}{rank:02d}" + "a" * 60,
                    "valid": True,
                },
            }
            for rank in range(1, 6)
        ],
    }


def test_four_historical_failures_are_sealed_as_twenty_fingerprints() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cycle = Path(directory) / "cycle_002"
        state_sha = "b" * 64
        v49 = v60.implementation.v49
        old = {
            "state_pin": v49.state_pin,
            "counter": v49.counter,
            "validate_top5": v49.validate_top5,
            "fingerprint_set": v49.fingerprint_set,
        }
        v49.state_pin = lambda: (state_sha, {"accepted_count": 1})
        v49.counter = lambda path: {
            str(v49.GLOBAL_COUNTER): 424,
            str(v49.FORMAL_REPLAY_COUNTER): 211,
            str(v49.ACCEPTED_COUNTER): 1,
        }[str(path)]
        v49.validate_top5 = lambda *args, **kwargs: {}
        v49.fingerprint_set = lambda path: {
            item["three_level_fingerprint"]["id"]
            for item in json.loads(path.read_text(encoding="utf-8"))["top5"]
        }
        try:
            for attempt in range(1, 5):
                root = cycle / f"discovery_replay_{attempt:02d}"
                write_json(root / "top5.json", _selection(attempt))
                write_json(root / "replay/summary.json", _failure_summary())
                (root / "replay/events.jsonl").write_text("", encoding="utf-8")
                write_json(
                    root / "diagnostic_audit.json",
                    {
                        "status": "technical_state_neutral",
                        "physical_calls": 0,
                        "accepted_added": 0,
                        "formal_state_sha256": state_sha,
                    },
                )
                write_json(
                    root / "counters_before.json",
                    {"global": 424, "formal_replay": 211, "accepted": 1},
                )
            quarantine = v60.ensure_network_quarantine_v60(cycle)
            assert quarantine["historical_failure_count"] == 4
            assert len(quarantine["quarantined_fingerprints"]) == 20
            assert quarantine["physical_workload_calls"] == 0
            assert quarantine["ordinal_claims"] == 0
            assert quarantine["remediation_epoch"] == v60.NETWORK_REMEDIATION_EPOCH
        finally:
            for name, value in old.items():
                setattr(v49, name, value)


def test_first_post_remediation_network_failure_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        phase = Path(directory) / "cycle_002/discovery_replay_05"
        phase.mkdir(parents=True)
        summary = _failure_summary()
        old_runner = v60._RUN_DIAGNOSTIC_V58
        v60._RUN_DIAGNOSTIC_V58 = lambda *args, **kwargs: (False, summary)
        try:
            try:
                v60.run_diagnostic_v60(
                    phase / "top5.json",
                    phase / "replay",
                    phase,
                )
            except v60.implementation.v49.FailClosed as error:
                assert "first post-remediation network failure" in str(error)
            else:
                raise AssertionError("v60 allowed a second post-remediation attempt")
        finally:
            v60._RUN_DIAGNOSTIC_V58 = old_runner


def test_launcher_binds_v60_and_network_epoch() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert sha(V60) in source
    for required in (
        "JOB_NETWORK=ra_job_v53_net",
        "JOB_BRIDGE_INTERFACE=ra-job-v53",
        "job_v60_explicit_runner_subprocess_environment_v1",
        'export RA_JOB_DOCKER_NETWORK="${JOB_NETWORK}"',
        "RA_JOB_NETWORK_PREFLIGHT_JSON",
        "RA_JOB_NETWORK_PREFLIGHT_SHA256",
        'V60_PREFLIGHT_ONLY:-0',
        '.controller == "job_v60"',
    ):
        assert required in source, required


def main() -> int:
    test_real_runner_subprocess_receives_only_sealed_network_allowlist()
    test_four_historical_failures_are_sealed_as_twenty_fingerprints()
    test_first_post_remediation_network_failure_fails_closed()
    test_launcher_binds_v60_and_network_epoch()
    print("JOB v60 subprocess network tests passed: 4", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
