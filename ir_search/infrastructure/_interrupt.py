"""Wake a read that is blocked in another thread, on every operating system."""
from __future__ import annotations

import os
import socket


def wake_blocked_socket(sock) -> None:
    """Stop a request-owned socket from a watcher thread once the request has ended.

    Shutdown is sufficient on POSIX. On Windows, transfer ownership with detach()
    before closing the native socket: close() alone defers closing while makefile()
    wrappers exist. Invalidating the Python socket first prevents their finalizers
    from closing a recycled handle. Only request-owned, single-use sockets qualify.
    The read owner must stop on cancellation and must never reuse this connection.
    """
    # Best effort from a watcher thread: it must never raise, whatever object it is given.
    for method, args in (("shutdown", (socket.SHUT_RDWR,)), ("close", ())):
        if method == "close" and os.name != "nt":
            break
        try:
            if method == "close" and isinstance(sock, socket.socket):
                handle = sock.detach()
                if handle != -1:
                    socket.close(handle)
            else:
                getattr(sock, method)(*args)
        except Exception:
            pass
