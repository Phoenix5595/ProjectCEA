"""Authenticated, immutable Nextcloud Files artifacts over WebDAV.

All URLs are derived from the already validated shared DAV discovery result;
this module never discovers alternate endpoints and never logs credentials or
instance-specific URLs.

Uploads use Nextcloud chunking v2 (16 MiB chunks, owned transfer collection,
``OC-Total-Length``, final ``.file`` MOVE with ``Overwrite: F``) and reconcile
a lost final acknowledgement by verifying the destination bytes instead of
assuming the write failed. Downloads stream into caller-owned paths with a
1 GiB free-space reserve and every remote artifact is verified by a full GET
plus the caller's ZIP verifier — never by ETag or size alone. Fixed error
codes: ``archive_remote_unavailable`` (transport/auth/collection),
``archive_remote_quota`` (507), ``archive_remote_conflict`` (409/412 and
out-of-root paths), ``archive_integrity_failed`` (byte/schema mismatch) and
``archive_local_space_insufficient``.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from shared.nextcloud_dav import DavDiscovery, DavError, dav_request, validate_dav_url

CHUNK_SIZE = 16 * 1024 * 1024
MAX_CHUNKS = 10_000
BLOCK_SIZE = 1024 * 1024
ROOT_PARTS = ("SJCEA", "Grow Archives")
RESERVE_BYTES = 1024 ** 3
MAX_DOWNLOAD_BYTES = 64 * 1024 ** 3

#: Async ZIP verifier: ``(path, expected_sha256) -> manifest``.
Verifier = Callable[[Path, str], Awaitable[Any]]
#: Optional progress callback; a plain callable or a coroutine function.
Checkpoint = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class ArtifactName:
    dataset_id: uuid.UUID
    revision: int
    sha256: str
    kind: str = "grow"
    year: int | None = None
    quarter: int | None = None

    def path_parts(self) -> tuple[str, ...]:
        digest = self.sha256.lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("archive_integrity_failed")
        if self.revision < 1:
            raise ValueError("archive_integrity_failed")
        if self.kind == "grow":
            return (*ROOT_PARTS, f"{self.dataset_id}-r{self.revision}-{digest}.zip")
        if self.kind == "veg_quarter" and self.year and self.quarter in (1, 2, 3, 4):
            return (*ROOT_PARTS, "Veg", str(self.year), f"Q{self.quarter}-r{self.revision}-{digest}.zip")
        if self.kind == "veg_year" and self.year:
            return (*ROOT_PARTS, "Veg", str(self.year), f"{self.year}-r{self.revision}-{digest}.zip")
        raise ValueError("archive_integrity_failed")

    def remote_path(self) -> str:
        """Root-relative immutable path stored in the archive catalog."""
        return "/".join(self.path_parts())


def _join_segments(root: str, segments: tuple[str, ...]) -> str:
    parsed = urlsplit(root)
    path = parsed.path.rstrip("/") + "/" + "/".join(quote(s, safe="") for s in segments)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _split_remote_path(remote_path: str) -> tuple[str, ...]:
    if (not isinstance(remote_path, str) or not remote_path
            or remote_path.startswith("/") or "\\" in remote_path or "\x00" in remote_path):
        raise DavError("archive_remote_conflict")
    parts = tuple(remote_path.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise DavError("archive_remote_conflict")
    return parts



async def _notify(checkpoint: Checkpoint | None, payload: dict[str, Any]) -> None:
    if checkpoint is None:
        return
    value = checkpoint(payload)
    if asyncio.iscoroutine(value):
        await value


class WebDAVArtifactClient:
    """Files client bound to one validated principal/files root and auth session."""

    def __init__(self, discovery: DavDiscovery, client: httpx.AsyncClient, *, instance_id: uuid.UUID,
                 scratch_root: Path, reserve: int = RESERVE_BYTES):
        if discovery.instance_id != instance_id:
            raise ValueError("archive_remote_unavailable")
        self.discovery = discovery
        self.client = client
        self.instance_id = instance_id
        self.files_root_url = validate_dav_url(discovery.files_root_url)
        self.scratch_root = Path(scratch_root)
        self.reserve = reserve

    def artifact_url(self, name: ArtifactName) -> str:
        return self._validate_artifact_url(_join_segments(self.files_root_url, name.path_parts()))

    def remote_url(self, remote_path: str) -> str:
        """Absolute URL for a catalogued root-relative artifact path."""
        return self._validate_artifact_url(_join_segments(self.files_root_url, _split_remote_path(remote_path)))

    def _validate_artifact_url(self, url: str) -> str:
        validate_dav_url(url, origin=self.files_root_url)
        root_path = urlsplit(self.files_root_url).path.rstrip("/")
        candidate = unquote(urlsplit(url).path)
        allowed = unquote(root_path) + "/" + "/".join(ROOT_PARTS) + "/"
        if not candidate.startswith(allowed) or candidate.endswith("/"):
            raise DavError("archive_remote_conflict")
        return url

    async def ensure_archive_directories(self, name: ArtifactName | None = None) -> None:
        """Create only ``SJCEA/Grow Archives`` and its ``Veg`` year subtree."""
        parts: list[str] = list(ROOT_PARTS)
        if name is not None and name.kind != "grow":
            parts.append("Veg")
            if name.year is not None:
                parts.append(str(name.year))
        for end in range(1, len(parts) + 1):
            url = _join_segments(self.files_root_url, tuple(parts[:end]))
            try:
                reply = await dav_request(self.client, "MKCOL", url)
            except httpx.HTTPError as exc:
                raise DavError("archive_remote_unavailable") from exc
            if reply.status not in (201, 405):
                _raise_status(reply.status)

    async def upload(self, source: Path, name: ArtifactName, *, upload_id: uuid.UUID,
                     verifier: Verifier, checkpoint: Checkpoint | None = None,
                     resume_from: int = 0) -> tuple[str, Any]:
        """Chunked v2 PUT, immutable MOVE, lost-ACK reconciliation and full GET verify."""
        source = Path(source)
        await self.ensure_archive_directories(name)
        size = source.stat().st_size
        chunk_count = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        if (size <= 0 or size > MAX_DOWNLOAD_BYTES or chunk_count > MAX_CHUNKS
                or len(name.sha256) != 64):
            raise DavError("archive_integrity_failed")
        destination = self.artifact_url(name)
        persisted_next = max(0, int(resume_from))
        if persisted_next > MAX_CHUNKS + 1 or persisted_next > chunk_count + 1:
            raise DavError("archive_integrity_failed")
        collection = _join_segments(self.discovery.uploads_root_url, (str(upload_id),))
        next_chunk = max(1, persisted_next)
        exists = await self._collection_exists(collection)
        if persisted_next <= 1:
            # A zero/first-chunk checkpoint cannot prove that an unacknowledged
            # PUT did not reach the server. Abandon any existing collection so
            # stale or changed-source chunks can never be assembled into this ZIP.
            if exists or persisted_next > 0:
                upload_id = uuid.uuid4()
                collection = _join_segments(self.discovery.uploads_root_url, (str(upload_id),))
            next_chunk = 1
            await self._ensure_upload_collection(collection, destination)
        elif not exists:
            # Nextcloud expires inactive collections. Never silently recreate
            # an expired job-owned transfer ID; persist a fresh owned UUID now.
            upload_id = uuid.uuid4()
            collection = _join_segments(self.discovery.uploads_root_url, (str(upload_id),))
            next_chunk = 1
            await self._ensure_upload_collection(collection, destination)
        else:
            # Re-send the last acknowledged chunk: Nextcloud may discard an
            # incomplete final chunk when a transfer is resumed.
            next_chunk = persisted_next - 1
        await _notify(checkpoint, {"phase": "upload", "upload_id": str(upload_id),
                                   "next_chunk": next_chunk, "size": size})
        with source.open("rb") as stream:
            stream.seek((next_chunk - 1) * CHUNK_SIZE)
            while block := stream.read(CHUNK_SIZE):
                if next_chunk > MAX_CHUNKS:
                    raise DavError("archive_integrity_failed")
                part = f"{next_chunk:05d}"
                url = collection.rstrip("/") + "/" + quote(part, safe="")
                try:
                    response = await self.client.put(
                        url, content=block,
                        headers={"Destination": destination, "OC-Total-Length": str(size)})
                except httpx.HTTPError as exc:
                    raise DavError("archive_remote_unavailable") from exc
                if response.status_code not in (200, 201, 204):
                    _raise_status(response.status_code)
                next_chunk += 1
                await _notify(checkpoint, {"phase": "upload", "upload_id": str(upload_id),
                                           "next_chunk": next_chunk, "size": size})
        final = collection.rstrip("/") + "/.file"
        try:
            response = await self.client.request(
                "MOVE", final,
                headers={"Destination": destination, "Overwrite": "F", "OC-Total-Length": str(size)})
        except httpx.TransportError:
            # The server may have accepted the MOVE before the acknowledgement
            # was lost; verify the destination instead of assuming failure.
            return await self._reconcile_destination(destination, name, verifier, expected_size=size)
        if response.status_code in (201, 204):
            path, manifest = await self.verify_remote(destination, name.sha256,
                                                      verifier=verifier, expected_size=size)
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return name.remote_path(), manifest
        if response.status_code in (409, 412):
            return await self._reconcile_destination(destination, name, verifier, expected_size=size)
        _raise_status(response.status_code)
        raise AssertionError("unreachable")
    async def _collection_exists(self, collection: str) -> bool:
        try:
            reply = await dav_request(self.client, "PROPFIND", collection, headers={"Depth": "0"})
        except httpx.HTTPError as exc:
            raise DavError("archive_remote_unavailable") from exc
        if reply.status == 404:
            return False
        if reply.status in (401, 403):
            _raise_status(reply.status)
        if not 200 <= reply.status < 300:
            _raise_status(reply.status)
        return True

    async def _ensure_upload_collection(self, collection: str, destination: str) -> None:
        # Nextcloud chunking v2 binds the final target when creating the
        # collection; a stable job UUID is only reused for a live resumable upload.
        try:
            response = await self.client.request(
                "MKCOL", collection, headers={"Destination": destination})
        except httpx.HTTPError as exc:
            raise DavError("archive_remote_unavailable") from exc
        if response.status_code in (201, 405):
            return
        _raise_status(response.status_code)
    async def _reconcile_destination(self, destination: str, name: ArtifactName,
                                     verifier: Verifier, *, expected_size: int) -> tuple[str, Any]:
        path, manifest = await self.verify_remote(destination, name.sha256, verifier=verifier,
                                                  expected_size=expected_size)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        return name.remote_path(), manifest

    async def verify_remote(self, url: str, expected_sha256: str, *, verifier: Verifier,
                            expected_size: int | None = None) -> tuple[Path, Any]:
        """Full GET plus SHA256 plus member/schema/count verification."""
        self.scratch_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.scratch_root / f"verify-{uuid.uuid4().hex}.zip"
        _path, digest, size = await self.download(url, path, expected_size=expected_size)
        if digest != expected_sha256 or (expected_size is not None and size != expected_size):
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise DavError("archive_integrity_failed")
        try:
            manifest = await verifier(path, expected_sha256)
        except BaseException:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise
        return path, manifest

    async def download(self, url: str, destination: Path, *,
                       expected_size: int | None = None) -> tuple[Path, str, int]:
        """Stream one remote artifact into an owned path with reserve checks."""
        url = self._validate_artifact_url(url)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest = hashlib.sha256()
        size = 0
        try:
            async with self.client.stream("GET", url) as response:
                if response.status_code != 200:
                    _raise_status(response.status_code)
                content_length = response.headers.get("Content-Length")
                declared = int(content_length) if content_length and content_length.isdigit() else None
                if declared is not None and shutil.disk_usage(destination.parent).free <= declared + self.reserve:
                    raise DavError("archive_local_space_insufficient")
                with destination.open("wb") as target:
                    async for block in response.aiter_bytes(BLOCK_SIZE):
                        if not block:
                            continue
                        if shutil.disk_usage(destination.parent).free <= len(block) + self.reserve:
                            raise DavError("archive_local_space_insufficient")
                        if size + len(block) > MAX_DOWNLOAD_BYTES:
                            raise DavError("archive_integrity_failed")
                        target.write(block)
                        digest.update(block)
                        size += len(block)
            if expected_size is not None and size != expected_size:
                raise DavError("archive_integrity_failed")
            os.chmod(destination, 0o600)
            return destination, digest.hexdigest(), size
        except BaseException as exc:
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            if isinstance(exc, httpx.HTTPError):
                raise DavError("archive_remote_unavailable") from exc
            raise

    async def delete(self, remote_path: str) -> None:
        """Delete one known SJCEA-owned artifact path; absent paths are success."""
        url = self.remote_url(remote_path)
        try:
            response = await self.client.delete(url)
        except httpx.HTTPError as exc:
            raise DavError("archive_remote_unavailable") from exc
        if response.status_code not in (200, 204, 404):
            _raise_status(response.status_code)


def _raise_status(status: int) -> None:
    if status in (401, 403):
        raise DavError("archive_remote_unavailable")
    if status == 507:
        raise DavError("archive_remote_quota")
    if status in (409, 412):
        raise DavError("archive_remote_conflict")
    raise DavError("archive_remote_unavailable")
