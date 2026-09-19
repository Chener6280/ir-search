"""Wake a read that is blocked in another thread, on every operating system."""
from __future__ import annotations

import os
import socket


def wake_blocked_socket(sock) -> None:
    """Stop a request-owned socket from a watcher thread once the request has ended.

    POSIX wakes a blocked `recv` on `shutdown`. Windows does not: the read only
    returns when the handle is closed (otherwise it waits for the peer, ~120s for a
    half-closed TCP connection). The socket belongs to a single request and is never
    reused afterwards, so closing it here cannot affect another caller.
    """
    # Best effort from a watcher thread: it must never raise, whatever object it is given.
    for method, args in (("shutdown", (socket.SHUT_RDWR,)), ("close", ())):
        if method == "close" and os.name != "nt":
            break
        try:
            getattr(sock, method)(*args)
        except Exception:
            pass
