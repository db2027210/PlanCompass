$ErrorActionPreference = 'Stop'

$Target = 'anonymous@192.0.2.10'
$RemoteRoot = '/home/anonymous/program/resource_adaptive/pg4_frozen_bundle_20260726'
$Bundle = $PSScriptRoot

ssh -o BatchMode=yes -o ConnectTimeout=8 $Target "mkdir -p '$RemoteRoot'"
if ($LASTEXITCODE -ne 0) { throw 'cannot create remote bundle directory' }

scp -o BatchMode=yes -o ConnectTimeout=8 -r "$Bundle/." "${Target}:${RemoteRoot}/"
if ($LASTEXITCODE -ne 0) { throw 'bundle upload failed' }

$Remote = @"
set -euo pipefail
chmod 0755 '$RemoteRoot/launch_four_lanes.sh' '$RemoteRoot/fix_tpcc_and_restart.sh' '$RemoteRoot/monitor_four_lanes_5min.py'
bash -n '$RemoteRoot/launch_four_lanes.sh' '$RemoteRoot/fix_tpcc_and_restart.sh'
python3 -m py_compile '$RemoteRoot/monitor_four_lanes_5min.py'
'$RemoteRoot/fix_tpcc_and_restart.sh'
'$RemoteRoot/launch_four_lanes.sh' all
'$RemoteRoot/monitor_four_lanes_5min.py' --once
"@
ssh -o BatchMode=yes -o ConnectTimeout=8 -t $Target $Remote
if ($LASTEXITCODE -ne 0) { throw 'remote deploy/start failed; inspect the printed lane-specific result' }
