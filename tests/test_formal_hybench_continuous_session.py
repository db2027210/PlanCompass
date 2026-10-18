from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from formal_hybench_continuous_session import (
    FormalHyBenchContinuousSession,
    FormalHyBenchSessionError,
)


SOURCE = Path("formal_clients_20260721/com/hybench/HyBenchFormalMain.java")
HYBENCH_JAR = Path(
    "/disk2/resource_adaptive_mysql/gd_20260718/hybench_client/target/"
    "HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"
)


def protocol():
    return {
        "immutable": True,
        "timing": {"sample_interval_seconds": 1, "stability_window_seconds": 30,
                   "required_consecutive_stability_windows": 6,
                   "minimum_measurement_seconds": 180, "maximum_measurement_seconds": 600,
                   "sample_coverage_min": 0.9},
        "stability": {"calculation": "within_each_window_from_one_second_samples",
                      "all_cpu_memory_io_resources_must_be_stable": True,
                      "cpu_cv_max": 0.1, "memory_cv_max": 0.1, "io_cv_max": 0.1,
                      "normal_throughput_cv_max": 0.1,
                      "xp_xap_qps_cv_max": 0.1, "xp_xtp_tps_cv_max": 0.1,
                      "low_utilization_ratio_threshold": 0.01,
                      "low_utilization_absolute_range_max": 0.01},
    }


@pytest.fixture(scope="module")
def compiled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    classes = tmp_path_factory.mktemp("hybench_formal_classes")
    subprocess.run(
        ["javac", "-encoding", "UTF-8", "-cp", str(HYBENCH_JAR),
         "-d", str(classes), str(SOURCE)], check=True, capture_output=True, text=True,
    )
    return classes


def test_java_stop_protocol_accepts_only_frozen_boundaries(compiled: Path) -> None:
    good = subprocess.run(
        ["java", "-cp", f"{compiled}:{HYBENCH_JAR}", "com.hybench.HyBenchFormalMain",
         "validate-stop-request", "210", "600"],
        check=True, capture_output=True, text=True,
    )
    assert '"measurement_seconds":210' in good.stdout
    bad = subprocess.run(
        ["java", "-cp", f"{compiled}:{HYBENCH_JAR}", "com.hybench.HyBenchFormalMain",
         "validate-stop-request", "211", "600"],
        check=False, capture_output=True, text=True,
    )
    assert bad.returncode != 0


def test_java_observes_atomically_published_stop_file(compiled: Path, tmp_path: Path) -> None:
    stop = tmp_path / "hybench.stop"
    process = subprocess.Popen(
        ["java", "-cp", f"{compiled}:{HYBENCH_JAR}", "com.hybench.HyBenchFormalMain",
         "await-stop-request", str(stop), "600", "3000"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert process.stdout.readline().strip() == "HYBENCH_FORMAL_STOP_AWAIT_READY"
    temporary = tmp_path / "hybench.stop.tmp"
    temporary.write_text("240\n", encoding="ascii")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, stop)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert '"measurement_seconds":240' in stdout


class FakeProcess:
    def __init__(self, stdout, stop_file: Path, workload: str, rc: int = 0):
        self.stdout, self.stop_file, self.workload, self.rc = stdout, stop_file, workload, rc
        self.returncode = None
        stdout.write(
            'HYBENCH_FORMAL_SESSION_JSON={"started_epoch_ms":0,'
            '"measurement_start_epoch_ms":60000,"warmup_seconds":60,'
            '"maximum_measurement_seconds":600}\n'
        )
        for second in range(0, 211):
            stdout.write("HYBENCH_FORMAL_SAMPLE_JSON=" + json.dumps({
                "epoch_ms": 60000 + second * 1000,
                "xap_or_ap_total": second,
                "xtp_total": second,
                "measurement_phase": True,
                "at_commit_counts": {},
            }) + "\n")
        stdout.flush()

    def poll(self): return self.returncode

    def wait(self, timeout=None):
        seconds = int(self.stop_file.read_text(encoding="ascii"))
        self.returncode = self.rc
        if self.rc == 0:
            self.stdout.write("HYBENCH_FORMAL_METRICS_JSON=" + json.dumps({
                "workload": self.workload, "qps": 1.0,
                "xap_qps": 1.0, "xtp_tps": 1.0,
                "ap_total": seconds, "xap_total": seconds, "xtp_total": seconds,
                "latency_p95_ms": 10.0, "latency_p99_ms": 12.0,
                "measurement_seconds": seconds, "stop_requested": True,
                "workload_error": None, "progress_error": None,
                "primary_right_censored_count": 0, "xtp_right_censored_count": 0,
            }) + "\n")
            self.stdout.flush()
        return self.returncode

    def terminate(self): self.returncode = -15
    def kill(self): self.returncode = -9


def make_session(tmp_path: Path, workload: str, *, source="hybench_formal_sample_json", rc=0):
    resources = []
    for second in range(1, 211):
        resources.append({
            "epoch_ms": 60000 + second * 1000,
            "cpu_utilization_ratio": 0.7 if second <= 30 and second % 2 else 0.5,
            "memory_utilization_ratio": 0.9,
            "io_utilization_ratio": 0.4,
        })
    state = {}
    def builder(stop_file):
        state["stop"] = stop_file
        return ["fake"]
    def factory(command, **kwargs):
        process = FakeProcess(kwargs["stdout"], state["stop"], workload, rc)
        state["process"] = process
        return process
    session = FormalHyBenchContinuousSession(
        output=tmp_path, workload=workload, command_builder=builder,
        resource_samples=lambda: resources,
        database_qps_samples=lambda: [
            {
                "epoch_ms": 60000 + second * 1000,
                "completed_total": float(second * 3),
                "source": "database_status_counter",
                "attribution_scope": "registered_workload_digest_set",
                "digest_set_sha256": "d" * 64,
                "counter_name": "performance_schema_digest_sum_count_star",
            }
            for second in range(0, 212)
        ],
        measurement_protocol=protocol(),
        process_factory=factory, throughput_source=source,
    )
    if workload == "xp":
        outcomes = []
        for second in range(1, 211):
            outcomes.append({"epoch_ms": 60000 + second * 1000,
                             "at_type": f"AT{(second - 1) % 6 + 1}", "outcome": "commits"})
        session.output.mkdir(parents=True, exist_ok=True)
        session.at_outcomes_path.write_text(
            "".join(json.dumps(row) + "\n" for row in outcomes), encoding="utf-8"
        )
    return session, state


@pytest.mark.parametrize("workload", ["ap", "xp"])
def test_ap_xp_extend_same_session_to_210_and_finish_correctly(tmp_path: Path, workload: str) -> None:
    session, state = make_session(tmp_path, workload)
    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    session.wait_until_measurement(180)
    assert session.observation(180)["stability"]["stable"] is False
    session.wait_until_measurement(210)
    observation = session.observation(210)
    assert observation["stability"]["stable"] is True
    if workload == "xp":
        final_window = observation["stability"]["windows"][-1]["metrics"]
        assert final_window["xap_qps"]["stable"] is True
        assert final_window["xtp_tps"]["stable"] is True
    result = session.stop(210)
    assert result["correctness"]["passed"] is True
    assert result["database_throughput"]["db_qps"] == 3.0
    assert result["client_throughput_evidence"]["source"] == "hybench_formal_sample_json"
    assert state["stop"].read_text(encoding="ascii") == "210\n"


def test_questions_is_rejected_and_failed_exit_is_closed(tmp_path: Path) -> None:
    questions, _ = make_session(tmp_path / "questions", "ap", source="questions")
    with pytest.raises(FormalHyBenchSessionError, match="Questions"):
        questions.start(warmup_seconds=60, maximum_measurement_seconds=600)
    failed, _ = make_session(tmp_path / "failed", "ap", rc=2)
    failed.start(warmup_seconds=60, maximum_measurement_seconds=600)
    failed.wait_until_measurement(180)
    with pytest.raises(FormalHyBenchSessionError, match="rc=2"):
        failed.stop(180)
