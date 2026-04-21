"""systemd sd_notify helpers.

Centralizes notification of service state to systemd via the ``NOTIFY_SOCKET``
Unix datagram socket. When a service runs under ``Type=notify`` (or
``Type=notify-reload``) in its unit file, systemd expects a ``READY=1`` message
before treating the service as started. Services that declare ``WatchdogSec=``
must also send periodic ``WATCHDOG=1`` pings. Sending ``STOPPING=1`` before
shutdown hints to systemd that cleanup is in progress.

All helpers are no-ops when ``NOTIFY_SOCKET`` is unset (i.e. when the process
runs outside systemd, or under ``Type=simple``), so they are safe to call
unconditionally from any service.

Reference: https://www.freedesktop.org/software/systemd/man/sd_notify.html
"""

from __future__ import annotations

import logging
import os
import socket

__all__ = [
    "is_under_systemd",
    "notify_ready",
    "notify_status",
    "notify_stopping",
    "notify_watchdog",
    "sd_notify",
]

logger = logging.getLogger(__name__)


def is_under_systemd() -> bool:
    """Return True if running under systemd with ``NOTIFY_SOCKET`` available."""
    return bool(os.environ.get("NOTIFY_SOCKET"))


def sd_notify(state: str) -> bool:
    """Send a notification message to systemd via ``NOTIFY_SOCKET``.

    Args:
        state: Notification string. Common values:

            - ``"READY=1"`` — service finished startup.
            - ``"STOPPING=1"`` — service is about to shut down.
            - ``"WATCHDOG=1"`` — watchdog keepalive ping.
            - ``"STATUS=<text>"`` — free-form status visible via
              ``systemctl status``.

    Returns:
        True if the datagram was sent, False otherwise (including the common
        case where ``NOTIFY_SOCKET`` is unset because we are not running under
        systemd).
    """
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return False

    try:
        # Abstract Linux socket namespace: leading '@' -> NUL byte.
        if notify_socket.startswith("@"):
            notify_socket = "\0" + notify_socket[1:]

        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(notify_socket)
            sock.sendall(state.encode())
        return True
    except Exception as exc:
        logger.warning("Failed to send sd_notify(%s): %s", state, exc)
        return False


def notify_ready() -> bool:
    """Send ``READY=1``. Call once after startup completes."""
    return sd_notify("READY=1")


def notify_stopping() -> bool:
    """Send ``STOPPING=1``. Call once as shutdown begins."""
    return sd_notify("STOPPING=1")


def notify_watchdog() -> bool:
    """Send ``WATCHDOG=1``. Call periodically when ``WatchdogSec=`` is set."""
    return sd_notify("WATCHDOG=1")


def notify_status(status: str) -> bool:
    """Publish a free-form status line visible via ``systemctl status``."""
    return sd_notify(f"STATUS={status}")
