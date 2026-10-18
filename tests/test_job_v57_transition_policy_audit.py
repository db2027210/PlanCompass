from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import audit_job_v57_transition_policy as audit


def _sources() -> dict[str, str]:
    return {
        name: (TOOLS / name).read_text(encoding="utf-8")
        for name in audit.EXPECTED_SHA256
    }


def test_sealed_transition_policy_passes() -> None:
    completed = subprocess.run(
        [sys.executable, str(TOOLS / "audit_job_v57_transition_policy.py")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"decision": "fail_closed_policy_verified"' in completed.stdout


def test_manual_candidate_injection_is_detected() -> None:
    sources = _sources()
    sources["job_fresh10_target10_autonomous_v50.py"] += (
        '\nmanual = {"manual_candidate_injection": True}\n'
    )
    _expect_failure(sources, "manual candidate")


def test_partial_formal_retry_guard_removal_is_detected() -> None:
    sources = _sources()
    name = "job_fresh10_target10_autonomous_v49.py"
    sources[name] = sources[name].replace(
        "partial formal replay cannot be repeated",
        "partial replay policy removed",
    )
    _expect_failure(sources, "partial formal")


def test_probe_neutrality_removal_is_detected() -> None:
    sources = _sources()
    name = "run_job_round8_strict_serial_robust_v10_resource_median.py"
    sources[name] = sources[name].replace(
        'if diagnostic_only or candidate.get("selection_role") == "probe":',
        "if diagnostic_only:",
    )
    _expect_failure(sources, "probe/diagnostic")


def _expect_failure(sources: dict[str, str], fragment: str) -> None:
    try:
        audit.audit_source_texts(sources)
    except audit.AuditFailure as error:
        assert fragment in str(error), str(error)
        return
    raise AssertionError("tampered source unexpectedly passed the fail-closed audit")


def main() -> int:
    test_sealed_transition_policy_passes()
    test_manual_candidate_injection_is_detected()
    test_partial_formal_retry_guard_removal_is_detected()
    test_probe_neutrality_removal_is_detected()
    print("JOB v57 transition policy audit tests passed: 4", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
