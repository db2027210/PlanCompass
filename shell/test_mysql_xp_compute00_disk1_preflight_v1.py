from __future__ import annotations
import importlib.util,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
S=Path(__file__).with_name("mysql_xp_compute00_disk1_preflight_v1.py")
if not S.is_file():
 S=Path(__file__).resolve().parents[1]/"preflight/mysql_xp_compute00_disk1_preflight_v2.py"
spec=importlib.util.spec_from_file_location("xp_preflight",S);P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
def good():return dict(hostname="compute00",row={"source":"/dev/nvme1n1","major_minor":"259:6","filesystem":"ext4","mount_options":["rw"],"super_options":[]},device_dev="259:6",device_numa=0,node1=P.cpus("128-255,384-511"),online=P.cpus("0-511"),conflicts=[],runtime_exists=False,template_exists=True,template_identity={"status":"PASS_EXACT_CANONICAL_TEMPLATE"},port_free=True)
class Tests(unittest.TestCase):
 def test_ready_contract(self):
  v=P.validate(**good());self.assertEqual(v["status"],"PASS_READY_NOT_LAUNCHED");self.assertFalse(v["cpu"]["overlap"]);self.assertEqual(v["accepted_rounds_credit"],0)
 def test_read_only_fails_closed(self):
  x=good();x["row"]["mount_options"]=["ro"]
  with self.assertRaisesRegex(P.PreflightError,"disk1_read_only"):P.validate(**x)
 def test_missing_template_fails_closed(self):
  x=good();x["template_exists"]=False;x["template_identity"]=None
  with self.assertRaisesRegex(P.PreflightError,"xp_template_absent"):P.validate(**x)
 def test_wrong_template_identity_fails_closed(self):
  x=good();x["template_identity"]={"status":"FAIL"}
  with self.assertRaisesRegex(P.PreflightError,"canonical_identity"):P.validate(**x)
 def test_process_conflict_fails_closed(self):
  x=good();x["conflicts"]=[{"pid":1}]
  with self.assertRaisesRegex(P.PreflightError,"cpu_conflict"):P.validate(**x)
 def test_inventory_excludes_launcher_ancestry_but_not_real_workload(self):
  with TemporaryDirectory() as td:
   proc=Path(td)
   for pid,cmd in ((10,"python mysql_xp_compute00_disk1_preflight_v1.py"),(11,"java -jar HyBench.jar")):
    d=proc/str(pid);d.mkdir();(d/"status").write_text("Name:\tt\nPPid:\t0\nCpus_allowed_list:\t0-511\n");(d/"cmdline").write_bytes(cmd.replace(" ","\0").encode()+b"\0")
   rows=P.process_inventory(proc,{10})
   self.assertEqual([r["pid"] for r in rows],[11])
if __name__=="__main__":unittest.main()
