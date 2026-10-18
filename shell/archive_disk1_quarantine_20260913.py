#!/usr/bin/env python3
"""Archive three explicit obsolete quarantine trees, preserving recoverability."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

SOURCES = (
    Path('/disk1/resource_adaptive/mysql_algorithm_c_cpu_a_slots_v2/quarantine'),
    Path('/disk1/resource_adaptive/mysql_algorithm_c_cpu_a_slots_v3/quarantine'),
    Path('/disk1/resource_adaptive/mysql_external_baselines_20260826/independent_workers_v2/quarantine'),
)
DESTINATION = Path('/disk2/resource_adaptive_disk1_archive_20260913')
REPORT = Path('/home/anonymous/program/resource_adaptive/docs/artifacts/disk1_quarantine_archive_20260913.json')


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def inventory(root: Path) -> dict:
    files = 0
    logical_bytes = 0
    allocated_bytes = 0
    latest_mtime = 0.0
    for directory, _dirs, names in os.walk(root, followlinks=False):
        for name in names:
            info = (Path(directory) / name).lstat()
            files += 1
            logical_bytes += info.st_size
            allocated_bytes += info.st_blocks * 512
            latest_mtime = max(latest_mtime, info.st_mtime)
    return dict(files=files, logical_bytes=logical_bytes,
                allocated_bytes=allocated_bytes, latest_mtime=latest_mtime)


def save(report: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(REPORT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    assert all(p.is_dir() and not p.is_symlink() for p in SOURCES)
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True, timeout=120).split()
    mounts = []
    if ids:
        # Do not emit container environments or credentials.
        documents = json.loads(subprocess.check_output(['docker', 'inspect', *ids], timeout=120))
        for document in documents:
            for mount in document.get('Mounts', []):
                source = Path(mount['Source'])
                # Resolve symlink aliases for data paths, but not host-wide
                # read-only monitoring mounts such as cadvisor's /rootfs.
                if str(source).startswith(('/disk1/', '/disk2/')):
                    resolved = source.resolve()
                    if any(inside(resolved, p) or inside(p, resolved) for p in SOURCES):
                        mounts.append({'container': document['Name'], 'source': str(source)})
    assert not mounts, mounts
    open_paths = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        for entry in ('fd', 'map_files'):
            try:
                links = list((process / entry).iterdir())
            except FileNotFoundError:
                continue
            for link in links:
                try:
                    target = Path(os.readlink(link).removesuffix(' (deleted)'))
                except FileNotFoundError:
                    continue
                if any(inside(target, p) for p in SOURCES):
                    open_paths.append({'pid': process.name, 'path': str(target)})
    assert not open_paths, open_paths
    rows = [{'source': str(p), 'destination': str(DESTINATION / p.relative_to('/disk1')),
             'before': inventory(p)} for p in SOURCES]
    assert all(time.time() - r['before']['latest_mtime'] > 7 * 86400 for r in rows)
    required = sum(r['before']['logical_bytes'] for r in rows)
    assert shutil.disk_usage('/disk2').free > required + 50 * 1024**3
    report = {'phase': 'audited', 'created_at_epoch_s': time.time(), 'rows': rows,
              'container_references': mounts, 'open_file_references': open_paths,
              'recoverable': True, 'destination': str(DESTINATION),
              'disk1_free_before': shutil.disk_usage('/disk1').free}
    save(report)
    print(json.dumps(report, indent=2), flush=True)
    if not args.execute:
        return
    DESTINATION.mkdir(mode=0o700, parents=False, exist_ok=False)
    report['phase'] = 'archiving'
    save(report)
    for row in rows:
        source, destination = Path(row['source']), Path(row['destination'])
        destination.mkdir(parents=True, mode=0o700)
        # rsync removes only source files that it has successfully transferred.
        # Sparse DB files stay sparse, and symlinks are copied, never followed.
        subprocess.run(['rsync', '-aH', '--sparse', '--remove-source-files',
                        '--stats', str(source) + '/', str(destination) + '/'], check=True)
        row['remaining'] = inventory(source)
        row['archived'] = inventory(destination)
        assert row['remaining']['files'] == 0
        assert row['archived']['files'] == row['before']['files']
        assert row['archived']['logical_bytes'] == row['before']['logical_bytes']
        row['completed_at_epoch_s'] = time.time()
        save(report)
    report.update(phase='complete', completed_at_epoch_s=time.time(),
                  disk1_free_after=shutil.disk_usage('/disk1').free)
    save(report)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
