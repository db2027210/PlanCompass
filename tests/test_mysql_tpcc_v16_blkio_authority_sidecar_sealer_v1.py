from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];SOURCE=(ROOT/"control/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py" if (ROOT/"control/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py").is_file() else ROOT/"shell/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py");s=importlib.util.spec_from_file_location("sealer",SOURCE);M=importlib.util.module_from_spec(s);s.loader.exec_module(M)
def test_missing_sidecar_is_created_exactly_once(tmp_path):
 p=tmp_path/"blkio_throttle_authority.json";raw=b'{"x":1}\n';p.write_bytes(raw);side,created=M.write_sidecar(p,raw);assert created and side.read_text()==M.sha_bytes(raw)+"  "+p.name+"\n";assert M.write_sidecar(p,raw)[1] is False
def test_wrong_existing_sidecar_fails_closed(tmp_path):
 p=tmp_path/"blkio_throttle_authority.json";p.write_bytes(b'{}\n');Path(str(p)+".sha256").write_text("bad\n")
 with pytest.raises(M.Error,match="differs"):M.write_sidecar(p,p.read_bytes())
def test_authority_is_never_modified_and_no_restart_path_exists():
 text=SOURCE.read_text();assert 'authority_content_modified":False' in text and 'database_restarted":False' in text and 'workload_started":False' in text;assert 'docker","--host",SOCKET,"inspect"' in text and 'docker","--host",SOCKET,"restart"' not in text
def test_v16_exact_blkio_contract_is_pinned():assert M.MM=="259:2" and M.BPS==1205862400 and M.ATTEMPT.startswith("tpcc-v16-") and M.AUTH_SHA=="6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d"
