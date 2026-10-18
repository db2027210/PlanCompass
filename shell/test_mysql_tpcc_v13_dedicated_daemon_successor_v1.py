import importlib.util,unittest
from pathlib import Path
s=importlib.util.spec_from_file_location('b',Path(__file__).with_name('build_mysql_tpcc_v13_dedicated_daemon_successor_v1.py'));b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
class T(unittest.TestCase):
 def test_socket_and_token(self):
  x=b.patch('x.py','["docker", "inspect", x]\nparser.add_argument("--main-agent-token")')
  self.assertIn('"--host", "unix:///run/ra-docker-mysql-tpcc-v1.sock"',x);self.assertIn('--main-agent-token-file',x)
 def test_contract(self):
  self.assertEqual(b.SOCK,'unix:///run/ra-docker-mysql-tpcc-v1.sock')
if __name__=='__main__':unittest.main()
