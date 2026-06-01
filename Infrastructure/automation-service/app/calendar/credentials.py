"""Encrypt/decrypt Nextcloud credentials at rest."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = os.getenv("CALENDAR_SYNC_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "CALENDAR_SYNC_ENCRYPTION_KEY must be set. "
            + 'Generate with: python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
    if len(key) != 44:
        raise ValueError(
            f"CALENDAR_SYNC_ENCRYPTION_KEY must be 44 characters (urlsafe-base64 32-byte key), got {len(key)} chars"
        )
    return Fernet(key.encode())


def encrypt_secret(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode("utf-8"))


def decrypt_secret(blob: bytes) -> str:
    try:
        return _fernet().decrypt(blob).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Failed to decrypt calendar credentials") from e
