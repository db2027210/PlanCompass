#!/usr/bin/env python3
"""Portable byte-copy plus FIEMAP non-sharing audit for JOB datadirs.

This helper deliberately avoids cp, copy_file_range, sendfile, FICLONE, and
filesystem snapshot APIs.  It copies regular files through userspace
read/write buffers, preserves directory/file metadata, and then fails closed
unless every non-empty destination file has auditable extents that neither
carry FIEMAP_EXTENT_SHARED nor overlap the corresponding source extents.
"""

from __future__ import print_function

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys


FS_IOC_FIEMAP = 0xC020660B
FIEMAP_FLAG_SYNC = 0x00000001
FIEMAP_EXTENT_LAST = 0x00000001
FIEMAP_EXTENT_SHARED = 0x00002000
FIEMAP_HEADER = struct.Struct("<QQIIII")
FIEMAP_EXTENT = struct.Struct("<QQQQQIIII")
ROOT_LOGICAL_SOCKET_NAME = "mysql.sock"
ROOT_LOGICAL_SOCKET_TARGET = "/var/run/mysqld/mysqld.sock"
TRANSIENT_NAMES = {"mysql.sock", "mysqld.sock", "mysqld.pid"}
MYSQL_DATADIR_UID = 999
MYSQL_DATADIR_GID = 999
MYSQL_DATADIR_DIRECTORY_MODE = 0o700
MYSQL_DATADIR_FILE_MODE = 0o600


class CopyError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _xattrs(source, destination, follow_symlinks=True):
    try:
        names = os.listxattr(source, follow_symlinks=follow_symlinks)
    except (AttributeError, OSError) as exc:
        if isinstance(exc, OSError) and exc.errno in (
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
        ):
            return
        raise
    for name in names:
        value = os.getxattr(
            source, name, follow_symlinks=follow_symlinks
        )
        os.setxattr(
            destination, name, value, follow_symlinks=follow_symlinks
        )


def _metadata(source, destination, source_stat, follow_symlinks=True):
    if follow_symlinks:
        os.chmod(destination, stat.S_IMODE(source_stat.st_mode))
    try:
        os.chown(
            destination,
            source_stat.st_uid,
            source_stat.st_gid,
            follow_symlinks=follow_symlinks,
        )
    except (NotImplementedError, TypeError):
        if follow_symlinks:
            os.chown(destination, source_stat.st_uid, source_stat.st_gid)
    if follow_symlinks:
        os.utime(
            destination,
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
        )
    _xattrs(source, destination, follow_symlinks=follow_symlinks)


def _copy_regular(source, destination, source_stat):
    source_flags = os.O_RDONLY
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        destination_flags |= os.O_NOFOLLOW
    source_fd = os.open(str(source), source_flags)
    destination_fd = os.open(
        str(destination), destination_flags, stat.S_IMODE(source_stat.st_mode)
    )
    copied = 0
    digest = hashlib.sha256()
    try:
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            offset = 0
            while offset < len(block):
                written = os.write(destination_fd, block[offset:])
                if written <= 0:
                    raise CopyError("destination write made no progress")
                offset += written
                copied += written
        os.fsync(destination_fd)
    finally:
        os.close(destination_fd)
        os.close(source_fd)
    if copied != source_stat.st_size:
        raise CopyError("regular-file byte count differs")
    destination_digest = hashlib.sha256()
    with destination.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            destination_digest.update(block)
    if destination_digest.hexdigest() != digest.hexdigest():
        raise CopyError("destination content digest differs")
    _metadata(source, destination, source_stat)
    return copied, digest.hexdigest()


def _metadata_digest(root):
    """Return an ownership/mode snapshot without modifying the source tree."""
    rows = []
    for current, directories, files in os.walk(str(root), topdown=True, followlinks=False):
        directories.sort()
        names = ["."] + sorted(directories) + sorted(files)
        for name in names:
            path = Path(current) if name == "." else Path(current) / name
            value = os.lstat(str(path))
            rows.append({
                "relative": str(path.relative_to(root)),
                "mode": stat.S_IMODE(value.st_mode),
                "uid": value.st_uid,
                "gid": value.st_gid,
                "kind": stat.S_IFMT(value.st_mode),
            })
    return hashlib.sha256(canonical(rows).encode("utf-8")).hexdigest()


def _normalize_target_permissions(destination, directory_metadata, regular):
    """Normalize only the copied target to the frozen MySQL image identity."""
    for _, directory, _ in sorted(
        directory_metadata, key=lambda row: len(row[1].parts), reverse=True
    ):
        if directory.is_symlink() or not directory.is_dir():
            raise CopyError("copied directory is unsafe during normalization")
        os.chown(str(directory), MYSQL_DATADIR_UID, MYSQL_DATADIR_GID)
        os.chmod(str(directory), MYSQL_DATADIR_DIRECTORY_MODE)
    for row in regular:
        target = row["destination"]
        if target.is_symlink() or not target.is_file():
            raise CopyError("copied regular file is unsafe during normalization")
        os.chown(str(target), MYSQL_DATADIR_UID, MYSQL_DATADIR_GID)
        os.chmod(str(target), MYSQL_DATADIR_FILE_MODE)
    for _, directory, _ in directory_metadata:
        value = os.stat(str(directory), follow_symlinks=False)
        if (
            value.st_uid != MYSQL_DATADIR_UID
            or value.st_gid != MYSQL_DATADIR_GID
            or stat.S_IMODE(value.st_mode) != MYSQL_DATADIR_DIRECTORY_MODE
        ):
            raise CopyError("copied directory normalization readback differs")
    for row in regular:
        value = os.stat(str(row["destination"]), follow_symlinks=False)
        if (
            value.st_uid != MYSQL_DATADIR_UID
            or value.st_gid != MYSQL_DATADIR_GID
            or stat.S_IMODE(value.st_mode) != MYSQL_DATADIR_FILE_MODE
        ):
            raise CopyError("copied file normalization readback differs")
    return {
        "schema_version": "mysql_job_target_datadir_permission_normalization_v1",
        "target_only": True,
        "frozen_image_uid": MYSQL_DATADIR_UID,
        "frozen_image_gid": MYSQL_DATADIR_GID,
        "directory_mode": "0700",
        "regular_file_mode": "0600",
        "directory_count": len(directory_metadata),
        "regular_file_count": len(regular),
        "socket_excluded_from_target": True,
    }


def copy_tree(source, destination):
    source = Path(source)
    destination = Path(destination)
    if source.is_symlink() or not source.is_dir():
        raise CopyError("source tree is absent or symlinked")
    if destination.exists() or destination.is_symlink():
        raise CopyError("destination must not exist")
    destination.mkdir(mode=0o700)
    source_metadata_before = _metadata_digest(source)
    directory_metadata = []
    regular = []
    file_count = 0
    byte_count = 0
    normalization = {
        "schema_version": "mysql_job_tree_identity_normalization_v1",
        "excluded_root_logical_socket": {
            "path": ROOT_LOGICAL_SOCKET_NAME,
            "target": ROOT_LOGICAL_SOCKET_TARGET,
        },
        "other_transient_entries_rejected": True,
        "other_symlinks_rejected": True,
        "path_type_size_sha256_content_identity": True,
        "ownership_and_mode_excluded_from_content_identity": True,
    }
    stack = [(source, destination, Path("."))]
    while stack:
        current_source, current_destination, relative_root = stack.pop()
        current_stat = os.lstat(str(current_source))
        directory_metadata.append(
            (current_source, current_destination, current_stat)
        )
        entries = sorted(
            os.scandir(str(current_source)), key=lambda row: row.name
        )
        for entry in entries:
            relative = relative_root / entry.name
            source_path = current_source / entry.name
            destination_path = current_destination / entry.name
            metadata = entry.stat(follow_symlinks=False)
            mode = metadata.st_mode
            if entry.name in TRANSIENT_NAMES:
                if (
                    relative == Path(ROOT_LOGICAL_SOCKET_NAME)
                    and stat.S_ISLNK(mode)
                    and os.readlink(str(source_path))
                    == ROOT_LOGICAL_SOCKET_TARGET
                ):
                    # This sole logical runtime socket is intentionally absent
                    # from a copied datadir and from normalized tree identity.
                    continue
                raise CopyError(
                    "unexpected transient entry: %s" % relative
                )
            if stat.S_ISDIR(mode):
                destination_path.mkdir(mode=0o700)
                stack.append((source_path, destination_path, relative))
            elif stat.S_ISREG(mode):
                size, digest = _copy_regular(
                    source_path, destination_path, metadata
                )
                regular.append(
                    {
                        "relative": str(relative),
                        "source": source_path,
                        "destination": destination_path,
                        "size": size,
                        "sha256": digest,
                    }
                )
                file_count += 1
                byte_count += size
            elif stat.S_ISLNK(mode):
                raise CopyError("symlink source entry is prohibited: %s" % relative)
            else:
                raise CopyError(
                    "special source entry is prohibited: %s" % relative
                )
    for source_path, destination_path, metadata in reversed(
        directory_metadata
    ):
        _metadata(source_path, destination_path, metadata)
    descriptor = os.open(str(destination), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    target_permissions = _normalize_target_permissions(
        destination, directory_metadata, regular
    )
    source_metadata_after = _metadata_digest(source)
    if source_metadata_after != source_metadata_before:
        raise CopyError("source metadata changed during target-only normalization")
    target_permissions["source_metadata_sha256_before"] = source_metadata_before
    target_permissions["source_metadata_sha256_after"] = source_metadata_after
    return regular, file_count, byte_count, normalization, target_permissions


def fiemap(path):
    size = os.stat(str(path), follow_symlinks=False).st_size
    if size == 0:
        return []
    result = []
    start = 0
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        while start < size:
            count = 512
            buffer = bytearray(
                FIEMAP_HEADER.size + count * FIEMAP_EXTENT.size
            )
            FIEMAP_HEADER.pack_into(
                buffer,
                0,
                start,
                0xFFFFFFFFFFFFFFFF - start,
                FIEMAP_FLAG_SYNC,
                0,
                count,
                0,
            )
            fcntl.ioctl(descriptor, FS_IOC_FIEMAP, buffer, True)
            header = FIEMAP_HEADER.unpack_from(buffer, 0)
            mapped = int(header[3])
            if mapped <= 0:
                raise CopyError("FIEMAP returned no extents for non-empty file")
            page = []
            for index in range(mapped):
                values = FIEMAP_EXTENT.unpack_from(
                    buffer,
                    FIEMAP_HEADER.size + index * FIEMAP_EXTENT.size,
                )
                extent = {
                    "logical": int(values[0]),
                    "physical": int(values[1]),
                    "length": int(values[2]),
                    "flags": int(values[5]),
                }
                if extent["length"] <= 0 or extent["physical"] <= 0:
                    raise CopyError("FIEMAP extent is unauditable")
                page.append(extent)
                result.append(extent)
            last = page[-1]
            if last["flags"] & FIEMAP_EXTENT_LAST:
                break
            next_start = last["logical"] + last["length"]
            if next_start <= start:
                raise CopyError("FIEMAP pagination made no progress")
            start = next_start
    finally:
        os.close(descriptor)
    return result


def _overlaps(left, right):
    return (
        left["physical"] < right["physical"] + right["length"]
        and right["physical"] < left["physical"] + left["length"]
    )


def audit_nonsharing(regular):
    extent_count = 0
    audited_files = 0
    source_extents = []
    destination_rows = []
    for row in regular:
        if row["size"] == 0:
            continue
        current_source_extents = fiemap(row["source"])
        destination_extents = fiemap(row["destination"])
        if any(
            extent["flags"] & FIEMAP_EXTENT_SHARED
            for extent in destination_extents
        ):
            raise CopyError(
                "destination has shared extent: %s" % row["relative"]
            )
        source_extents.extend(current_source_extents)
        destination_rows.append((row["relative"], destination_extents))
        audited_files += 1
        extent_count += len(destination_extents)
    for relative, destination_extents in destination_rows:
        if any(
            _overlaps(source_extent, destination_extent)
            for source_extent in source_extents
            for destination_extent in destination_extents
        ):
            raise CopyError(
                "source/destination physical extents overlap: %s"
                % relative
            )
    return {
        "nonempty_regular_files_audited": audited_files,
        "destination_extent_count": extent_count,
        "fiemap_extent_shared_absent": True,
        "source_destination_physical_extent_overlap_absent": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        regular, file_count, byte_count, normalization, target_permissions = copy_tree(
            args.source, args.destination
        )
        audit = audit_nonsharing(regular)
        result = {
            "schema_version": "mysql_job_physical_copy_evidence_v1",
            "status": "PASS_BYTE_COPY_AND_FIEMAP_NONSHARING",
            "implementation":
                "userspace_read_write_no_clone_no_copy_file_range_v1",
            "source": str(args.source),
            "destination": str(args.destination),
            "file_count": file_count,
            "byte_count": byte_count,
            "regular_file_content_sha256_verified_during_copy": True,
            "copy_api": "os.read/os.write",
            "cp_used": False,
            "reflink_api_used": False,
            "copy_file_range_used": False,
            "sendfile_used": False,
            "tree_identity_normalization": normalization,
            "target_permission_normalization": target_permissions,
            "fiemap": audit,
            "accepted_round_authorized": False,
        }
        sys.stdout.write(canonical(result))
        return 0
    except (CopyError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
