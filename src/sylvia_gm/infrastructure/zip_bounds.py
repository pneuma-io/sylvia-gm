"""Bound ZIP directory allocation before handing untrusted bytes to ``ZipFile``."""

from __future__ import annotations

import struct
import zipfile
from typing import BinaryIO

# A finite directory budget also bounds variable-length names, comments and extra fields.
ZIP_DIRECTORY_BYTE_LIMIT = 8 * 1024 * 1024


def validate_zip_compression(member: zipfile.ZipInfo) -> None:
    """Accept codecs whose standard-library reader bounds each decompression allocation.

    Python 3.11's ZIP BZIP2/LZMA readers decompress whole chunks before applying read limits;
    a forged small file size cannot bound that allocation. Sylvia writes stored/Deflate ZIPs.
    """
    if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError("Unsupported ZIP compression; re-save the archive using Store or Deflate")


def preflight_zip_directory(
    source: BinaryIO,
    *,
    maximum_members: int,
    maximum_directory_bytes: int = ZIP_DIRECTORY_BYTE_LIMIT,
) -> None:
    """Check directory bounds and count actual headers without allocating member records.

    The caller owns the input-byte limit. Member content, paths and CRCs remain the ordinary
    archive reader's responsibility. ZIP64 and ordinary ZIP comments remain supported.
    """
    source.seek(0, 2)
    size = source.tell()
    tail_size = min(size, 65_557 + 20)
    source.seek(size - tail_size)
    tail = source.read(tail_size)
    index = tail.rfind(b"PK\x05\x06")
    if index < 0 or index + 22 > len(tail):
        raise ValueError("Archive ZIP directory is invalid")
    _, disk, directory_disk, on_disk, count, length, offset, comment = struct.unpack_from(
        "<4s4H2LH", tail, index
    )
    if disk or directory_disk or on_disk != count or index + 22 + comment != len(tail):
        raise ValueError("Archive ZIP directory is invalid")
    end = size - tail_size + index
    has_zip64_locator = index >= 20 and tail[index - 20 : index - 16] == b"PK\x06\x07"
    if has_zip64_locator or count == 0xFFFF or length == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        if index < 20:
            raise ValueError("Archive ZIP64 directory is invalid")
        signature, disk, zip64_offset, disks = struct.unpack_from("<4sLQL", tail, index - 20)
        if signature != b"PK\x06\x07" or disk or disks != 1 or zip64_offset + 56 > end - 20:
            raise ValueError("Archive ZIP64 directory is invalid")
        source.seek(zip64_offset)
        record = source.read(56)
        if len(record) != 56:
            raise ValueError("Archive ZIP64 directory is truncated")
        (signature, record_size, _, _, disk, directory_disk, on_disk, count, length, offset) = (
            struct.unpack("<4sQ2H2L4Q", record)
        )
        if (
            signature != b"PK\x06\x06"
            or record_size < 44
            or disk
            or directory_disk
            or on_disk != count
            or zip64_offset + 12 + record_size != end - 20
        ):
            raise ValueError("Archive ZIP64 directory is invalid")
        end = zip64_offset
    if count > maximum_members:
        raise ValueError("Archive has too many members")
    if length > maximum_directory_bytes:
        raise ValueError("Archive ZIP directory is too large")
    if offset + length != end:
        raise ValueError("Archive ZIP directory bounds are invalid")

    # ZipFile iterates headers rather than trusting the footer's count. Match that boundary:
    # a forged low footer count must not bypass the pre-allocation member limit.
    source.seek(offset)
    actual = 0
    while source.tell() < end:
        header = source.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise ValueError("Archive ZIP directory member is invalid")
        actual += 1
        if actual > maximum_members:
            raise ValueError("Archive has too many members")
        name_bytes, extra_bytes, comment_bytes = struct.unpack_from("<3H", header, 28)
        next_offset = source.tell() + name_bytes + extra_bytes + comment_bytes
        if next_offset > end:
            raise ValueError("Archive ZIP directory member is truncated")
        source.seek(next_offset)
    if actual != count:
        raise ValueError("Archive ZIP directory count does not match")
    source.seek(0)
