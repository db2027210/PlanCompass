import csv
import json
from pathlib import Path
import subprocess

import pytest

from formal_benchbase_continuous_session import FormalBenchBaseContinuousSession, FormalBenchBaseSessionError


JAR=Path("formal_benchbase_runtime_20260723/benchbase-formal.jar")


def protocol():
    return {"immutable":True,"timing":{"sample_interval_seconds":1,"stability_window_seconds":30,
      "required_consecutive_stability_windows":6,"minimum_measurement_seconds":180,
      "maximum_measurement_seconds":600,"sample_coverage_min":.9},"stability":{
      "calculation":"within_each_window_from_one_second_samples","all_cpu_memory_io_resources_must_be_stable":True,
      "cpu_cv_max":.1,"memory_cv_max":.1,"io_cv_max":.1,"normal_throughput_cv_max":.1,
      "xp_xap_qps_cv_max":.1,"xp_xtp_tps_cv_max":.1,"low_utilization_ratio_threshold":.01,
      "low_utilization_absolute_range_max":.01}}


def test_instrumented_jar_stop_parser():
    good=subprocess.run(["java","-cp",str(JAR),"com.oltpbenchmark.FormalControl",
      "validate-stop-request","210","600"],capture_output=True,text=True,check=True)
    assert '"measurement_seconds":210' in good.stdout
    bad=subprocess.run(["java","-cp",str(JAR),"com.oltpbenchmark.FormalControl",
      "validate-stop-request","211","600"],capture_output=True,text=True,check=False)
    assert bad.returncode!=0


class Fake:
    def __init__(self,out,err,stop,root,rc=0):
        self.out,self.stop,self.rc,self.returncode=out,stop,rc,None
        out.write('BENCHBASE_FORMAL_SESSION_JSON={"started_epoch_ms":0,"measurement_start_epoch_ms":60000,"warmup_seconds":60,"maximum_measurement_seconds":600}\n')
        out.write('BENCHBASE_FORMAL_WARMUP_RESET={"epoch_ms":60000,"warmup_seconds":60}\n')
        for s in range(211): out.write('BENCHBASE_FORMAL_SAMPLE_JSON='+json.dumps({"epoch_ms":60000+s*1000,"completed_total":s,"measurement_phase":True})+'\n')
        out.flush();err.write("FORMAL_TPCH_SEED_AGENT=4508333\n");err.flush()
        result=root/"results";result.mkdir()
        with (result/"tpch.raw.csv").open("w",newline="") as h:
            w=csv.writer(h);w.writerow(["Transaction Type Index","Transaction Name","Start Time (microseconds)","Latency (microseconds)","Worker Id (start number)","Phase Id (index in config file)"])
            for s in range(1,211):w.writerow([1,"Q18",(60000+s*1000-1)/1000,1000,1,1])
        (result/"tpch.summary.json").write_text(json.dumps({"Measured Requests":210}))
    def poll(self):return self.returncode
    def wait(self,timeout=None):
        seconds=int(self.stop.read_text());self.returncode=self.rc
        if self.rc==0:
            self.out.write('BENCHBASE_FORMAL_METRICS_JSON='+json.dumps({"measurement_seconds":seconds,"stop_requested":True,
              "completed_total":seconds,"right_censored_count":0,"measured_requests":seconds,
              "post_deadline_completed_count":0,
              "unexpected_errors":0,"aborts":0,"retries":0,"outputs_written":True})+'\n');self.out.flush()
        return self.returncode
    def terminate(self):self.returncode=-15
    def kill(self):self.returncode=-9


def make(tmp,benchmark="tpch",source="benchbase_formal_completed_total",invariant=None):
    resources=[{"epoch_ms":60000+s*1000,"cpu_utilization_ratio":(.7 if s<=30 and s%2 else .5),"memory_utilization_ratio":.9,"io_utilization_ratio":.4} for s in range(1,211)]
    state={}
    def builder(stop):state["stop"]=stop;return ["fake"]
    def factory(command,**kw):state["p"]=Fake(kw["stdout"],kw["stderr"],state["stop"],tmp);return state["p"]
    correct=lambda:{"query_id":"Q18","parameter_int":314,"row_count":1,"sha256":"a"*64,"seed_agent":4508333}
    return FormalBenchBaseContinuousSession(output=tmp,benchmark=benchmark,command_builder=builder,
      resource_samples=lambda:resources,database_qps_samples=lambda:[{
        "epoch_ms":60000+s*1000,"completed_total":float(s*4),
        "source":"database_status_counter","attribution_scope":"dedicated_mysql_instance",
        "counter_name":"registered_statement_completions"} for s in range(0,212)],
      measurement_protocol=protocol(),tpch_correctness=correct if benchmark=="tpch" else None,
      tpcc_invariant=invariant,process_factory=factory,throughput_source=source)


def test_tpch_native_progress_and_raw_final_metrics(tmp_path):
    s=make(tmp_path);s.start(warmup_seconds=60,maximum_measurement_seconds=600)
    s.wait_until_measurement(180)
    assert s.observation(180)["stability"]["stable"] is False
    s.wait_until_measurement(210);obs=s.observation(210)
    assert obs["stability"]["stable"] is True
    result=s.stop(210)
    assert result["database_throughput"]["db_qps"]==4
    assert result["metrics"]["qps"]==1
    assert result["metrics"]["latency_p95_ms"]==1
    assert result["correctness"]["raw_summary_reconciled"] is True


def test_tpcc_without_frozen_invariant_and_artificial_sources_are_not_ready(tmp_path):
    with pytest.raises(FormalBenchBaseSessionError,match="NOT_READY"):
        make(tmp_path/"tpcc",benchmark="tpcc").start(warmup_seconds=60,maximum_measurement_seconds=600)
    with pytest.raises(FormalBenchBaseSessionError,match="artificial Throughput"):
        make(tmp_path/"fake",source="human_throughput_log").start(warmup_seconds=60,maximum_measurement_seconds=600)
