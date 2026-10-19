"""Strict PostgreSQL binary COPY framing and canonical row digests."""
from __future__ import annotations

import hashlib
import struct
from typing import BinaryIO, Iterator

_SIGNATURE = b"PGCOPY\n\xff\r\n\x00"

class BinaryCopyError(ValueError):
    pass

def rows(stream: BinaryIO) -> Iterator[tuple[bytes | None, ...]]:
    if stream.read(len(_SIGNATURE)) != _SIGNATURE:
        raise BinaryCopyError("invalid PostgreSQL binary COPY signature")
    header = stream.read(8)
    if len(header) != 8:
        raise BinaryCopyError("truncated PostgreSQL COPY header")
    flags, extension = struct.unpack(">II", header)
    if flags != 0 or extension > 16 * 1024 * 1024:
        raise BinaryCopyError("unsupported PostgreSQL COPY header")
    if len(stream.read(extension)) != extension:
        raise BinaryCopyError("truncated PostgreSQL COPY extension")
    while True:
        raw = stream.read(2)
        if len(raw) != 2:
            raise BinaryCopyError("missing PostgreSQL COPY trailer")
        count = struct.unpack(">h", raw)[0]
        if count == -1:
            if stream.read(1):
                raise BinaryCopyError("trailing bytes after PostgreSQL COPY trailer")
            return
        if count < 0 or count > 32767:
            raise BinaryCopyError("invalid PostgreSQL COPY field count")
        fields: list[bytes | None] = []
        for _ in range(count):
            raw_len = stream.read(4)
            if len(raw_len) != 4:
                raise BinaryCopyError("truncated PostgreSQL COPY field length")
            size = struct.unpack(">i", raw_len)[0]
            if size == -1:
                fields.append(None)
            elif size < 0 or size > 64 * 1024 * 1024:
                raise BinaryCopyError("invalid PostgreSQL COPY field length")
            else:
                value = stream.read(size)
                if len(value) != size:
                    raise BinaryCopyError("truncated PostgreSQL COPY field")
                fields.append(value)
        yield tuple(fields)

def canonical(fields: tuple[bytes | None, ...]) -> bytes:
    return struct.pack(">h", len(fields)) + b"".join(
        struct.pack(">i", -1 if value is None else len(value)) + (value or b"")
        for value in fields
    )

def digest_rows(rows_iter: Iterator[tuple[bytes | None, ...]]) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for row in rows_iter:
        digest.update(canonical(row))
        count += 1
    return digest.hexdigest(), count

def write_header(stream: BinaryIO) -> None:
    stream.write(_SIGNATURE + struct.pack(">II", 0, 0))

def write_row(stream: BinaryIO, fields: tuple[bytes | None, ...]) -> None:
    stream.write(canonical(fields))

def write_trailer(stream: BinaryIO) -> None:
    stream.write(struct.pack(">h", -1))

def record_fields(payload: bytes, expected_oids: tuple[int, ...] | None = None) -> tuple[bytes | None, ...]:
    """Decode record_send framing without converting any PostgreSQL value."""
    if len(payload) < 4:
        raise BinaryCopyError("truncated PostgreSQL record")
    count = struct.unpack_from(">i", payload)[0]
    if not 0 <= count <= 32767 or (expected_oids is not None and count != len(expected_oids)):
        raise BinaryCopyError("PostgreSQL record field count changed")
    offset = 4
    fields: list[bytes | None] = []
    for index in range(count):
        if offset + 8 > len(payload):
            raise BinaryCopyError("truncated PostgreSQL record field")
        oid, size = struct.unpack_from(">Ii", payload, offset)
        offset += 8
        if expected_oids is not None and oid != expected_oids[index]:
            raise BinaryCopyError("PostgreSQL record field type changed")
        if size == -1:
            fields.append(None)
        elif size < 0 or size > 8 * 1024 * 1024 or offset + size > len(payload):
            raise BinaryCopyError("invalid PostgreSQL record field size")
        else:
            fields.append(payload[offset:offset + size])
            offset += size
    if offset != len(payload):
        raise BinaryCopyError("trailing PostgreSQL record bytes")
    return tuple(fields)

def record_payload(fields: tuple[bytes | None, ...], oids: tuple[int, ...]) -> bytes:
    """Reconstruct exact record_send bytes for source/cold payload comparison."""
    if len(fields) != len(oids):
        raise BinaryCopyError("PostgreSQL record type count changed")
    return struct.pack(">i", len(fields)) + b"".join(
        struct.pack(">Ii", oid, -1 if field is None else len(field)) + (field or b"")
        for oid, field in zip(oids, fields, strict=True)
    )
