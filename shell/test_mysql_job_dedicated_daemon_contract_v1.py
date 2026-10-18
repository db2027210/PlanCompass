#!/usr/bin/env python3
import importlib.util,unittest
from pathlib import Path
S=Path(__file__).with_name("mysql_job_dedicated_daemon_contract_v1.py");s=importlib.util.spec_from_file_location("m",S);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class T(unittest.TestCase):
 def test_unit_has_socket_root_and_network_isolation(self):
  t=m.unit_text();self.assertIn("--host=unix:"+m.SOCKET,t);self.assertIn("--data-root="+m.ROOT,t);self.assertIn("--iptables=false --bridge=none",t)
 def test_rejects_pg_graphdriver(self):
  row={"network_mode":"host","daemon_socket":m.SOCKET,"mysql_server_port":65102,"container_id":"x","pid":1,"startticks":1,"graphdriver_data":{k:m.ROOT+"/x" for k in ("UpperDir","WorkDir","MergedDir","LowerDir")}}
  with self.assertRaises(m.Error):m.validate({"DockerRootDir":m.ROOT,"Driver":"overlay2"},{"unit":m.UNIT,"socket":m.SOCKET,"cpuset_cpus":m.CPUS,"data_root_device":m.DEVICE,"pid":1,"startticks":1,"cmdline_sha256":"x"},{"database":row,"client":row},{m.ROOT+"/x":"259:2"})
