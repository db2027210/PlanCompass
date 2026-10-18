#!/usr/bin/env python3
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

LOCK = Path('/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock')
JOURNAL = Path('/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_lease_journal.jsonl')
STATE = JOURNAL.with_suffix('.state.json')
OWNER = 'job_strict_raw_explain_20260723'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def read_rows():
    rows = []
    prior = None
    if not JOURNAL.exists():
        return rows
    for number, line in enumerate(JOURNAL.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        unsigned = dict((key, value) for key, value in row.items() if key != 'event_sha256')
        actual = hashlib.sha256(canonical(unsigned).encode('utf-8')).hexdigest()
        if row.get('event_sha256') != actual or row.get('previous_event_sha256') != prior:
            raise RuntimeError('journal SHA chain failed at line %d' % number)
        prior = row['event_sha256']
        rows.append(row)
    return rows


def append(event, payload):
    rows = read_rows()
    unsigned = {
        'schema_version': 1,
        'sequence': len(rows) + 1,
        'event': event,
        'timestamp': datetime.datetime.now().astimezone().isoformat(),
        'owner_id': OWNER,
        'pid': os.getpid(),
        'scenario': 'job_memory',
        'lock_path': str(LOCK),
        'previous_event_sha256': rows[-1]['event_sha256'] if rows else None,
        'payload': payload,
    }
    row = dict(unsigned)
    row['event_sha256'] = hashlib.sha256(canonical(unsigned).encode('utf-8')).hexdigest()
    descriptor = os.open(str(JOURNAL), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
    try:
        os.write(descriptor, (canonical(row) + '\n').encode('utf-8'))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fd, temporary_name = tempfile.mkstemp(prefix='.' + STATE.name + '.', dir=str(STATE.parent))
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(canonical(row) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o640)
        os.replace(temporary_name, str(STATE))
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def probe(phase):
    output = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}']).decode().splitlines()
    pressure = [name for name in output if name != 'ra_pg5_job_node02_v1_a2'
                and ('job' in name.lower() or 'xp' in name.lower())]
    result = {
        'observed_at': datetime.datetime.now().astimezone().isoformat(),
        'probe_id': 'job_strict_explain_' + phase,
        'job_pressure_count': len([name for name in pressure if 'job' in name.lower()]),
        'xp_pressure_count': len([name for name in pressure if 'xp' in name.lower()]),
        'running_non_pg_pressure_containers': pressure,
    }
    if result['job_pressure_count'] or result['xp_pressure_count']:
        raise RuntimeError('zero-pressure probe failed: ' + canonical(result))
    return result


def main():
    run_root = Path(sys.argv[1]).resolve()
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = open(str(LOCK), 'a+')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        handle.close()
        raise RuntimeError('host02 JOB/XP pressure mutex is busy')
    failed = None
    try:
        rows = read_rows()
        if rows and rows[-1].get('event') not in ('lock_released', 'acquisition_rejected', 'postrun_zero_pressure_failed'):
            append('stale_owner_recovered', {'stale_owner_id': rows[-1].get('owner_id'), 'stale_event': rows[-1].get('event'), 'kernel_lock_reacquired': True})
        append('lock_acquired', {'implementation': 'posix_flock_exclusive_nonblocking', 'scope': 'job_strict_raw_explain'})
        before = probe('before_pressure')
        append('preflight_zero_pressure_passed', {'probe': before})
        append('handoff_acquired', {'atomic_under_flock': True, 'pressure_scope': 'before_EXPLAIN_through_recovery_and_checksum', 'r0': False, 'sgd': False})
        try:
            subprocess.check_call([str(run_root / 'run.sh'), str(run_root)])
        except Exception as error:
            failed = error
            append('campaign_aborted', {'exception_type': type(error).__name__, 'reason': str(error)})
        after = probe('after_recovery_checksum')
        if failed is None:
            append('recovery_checksum_scope_complete', {'probe': after, 'pressure_scope_complete': True})
        else:
            append('aborted_scope_zero_pressure_passed', {'probe': after})
        append('lock_released', {'release_reason': 'complete' if failed is None else 'failed', 'kernel_releases_on_close': True})
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    if failed is not None:
        raise failed
    print(json.dumps({'status': 'PASS', 'mutex_released': True, 'r0': False, 'sgd': False}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
