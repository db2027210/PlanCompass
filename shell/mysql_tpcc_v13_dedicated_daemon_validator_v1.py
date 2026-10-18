import hashlib,json
SOCKET='/run/ra-docker-mysql-tpcc-v1.sock';ROOT='/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/docker_ra_tpcc_v1';DEVICE='259:2';UNIT='ra-docker-mysql-tpcc-v1.service'
class Error(RuntimeError):pass
def validate(info,daemon,containers,mounts):
 if info.get('DockerRootDir')!=ROOT or info.get('Driver')!='overlay2':raise Error('daemon root/driver')
 if daemon.get('unit')!=UNIT or daemon.get('socket')!=SOCKET or daemon.get('ppid')!=1 or daemon.get('docker_ancestry') is not False or UNIT not in str(daemon.get('cgroup')) or not daemon.get('startticks') or set(daemon.get('namespaces') or {})!={'mnt','pid','net','user'}:raise Error('direct host daemon identity')
 for role,row in containers.items():
  if row.get('network_mode')!='host' or row.get('mysql_server_port')!=65106 or row.get('daemon_socket')!=SOCKET:raise Error(role+' network/socket')
  d=row.get('graphdriver_data') or {}
  if set(d)!={'UpperDir','WorkDir','MergedDir','LowerDir'} or any(not isinstance(p,str) or not p.startswith(ROOT+'/') or mounts.get(p)!=DEVICE for p in d.values()):raise Error(role+' graphdriver')
 return {'status':'PASS_TPCC_DEDICATED_DAEMON_NON_PG','accepted_round_authorized':False}
