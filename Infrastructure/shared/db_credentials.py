"""Load the Postgres password from the safest source available.

Priority order:

1. systemd `LoadCredential=postgres_password:<file>` — i.e. if
   ``$CREDENTIALS_DIRECTORY`` is set and contains ``postgres_password``,
   read the file and use its contents. This is the Phase 3.8 target: the
   secret is never placed in the service's process environment, so it is
   invisible to ``systemctl show -p Environment``, ``/proc/<pid>/environ``,
   and any child process that inherits the parent env.

2. ``POSTGRES_PASSWORD`` environment variable — the pre-Phase-3.8 path.
   Kept as a fallback so the transition is risk-free: if the credential
   is not wired up on a given host (developer machine, CI, stale
   drop-in), the service still starts from ``postgres.env``.

3. If neither source yields a non-empty password, raise ``RuntimeError``
   with a message that lists both candidate sources so an operator can
   see at a glance which knob to turn.

The helper intentionally trims a single trailing newline (the common
shape of a file produced by ``echo "$PW" > postgres_password``) but
preserves any internal whitespace — passwords that legitimately contain
spaces or tabs must survive round-trip.
"""

from __future__ import annotations

import os
from pathlib import Path

_CREDENTIAL_NAME = "postgres_password"
_ENV_VAR = "POSTGRES_PASSWORD"


def _read_credential_file() -> str | None:
    creds_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if not creds_dir:
        return None
    path = Path(creds_dir) / _CREDENTIAL_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        # Unreadable for some other reason (permissions, I/O). Fall back
        # silently to the env var rather than crashing — the env-var
        # path is the documented pre-3.8 behaviour.
        return None
    # Strip at most one trailing newline (common `echo "$PW" > file`
    # artifact) without touching any other whitespace.
    if raw.endswith("\n"):
        raw = raw[:-1]
    return raw or None


def load_postgres_password() -> str:
    """Return the Postgres password, preferring systemd credentials.

    Raises:
        RuntimeError: if neither source provides a non-empty value.
    """
    from_creds = _read_credential_file()
    if from_creds:
        return from_creds

    from_env = os.environ.get(_ENV_VAR)
    if from_env:
        return from_env

    raise RuntimeError(
        "Postgres password not found. Expected either "
        "systemd LoadCredential=postgres_password (checked "
        "$CREDENTIALS_DIRECTORY/postgres_password) or the "
        f"{_ENV_VAR} environment variable (from "
        "/opt/projectcea/shared/env/postgres.env)."
    )
