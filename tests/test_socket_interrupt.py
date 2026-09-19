"""A cancelled or expired request must release a blocked read promptly on every OS."""
import socket
import threading
import time

from ir_search.infrastructure._interrupt import wake_blocked_socket


def test_blocked_read_returns_promptly_after_wake():
    reader, writer = socket.socketpair()
    outcome = {}

    def read():
        try:
            outcome["data"] = reader.recv(1)
        except OSError as exc:
            outcome["error"] = type(exc).__name__

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    time.sleep(0.1)  # let the reader block first
    started = time.monotonic()
    try:
        wake_blocked_socket(reader)
        thread.join(timeout=5)
        assert not thread.is_alive() and time.monotonic() - started < 2
        assert outcome.get("data") == b"" or "error" in outcome
    finally:
        reader.close(); writer.close()


def test_wake_never_raises_for_missing_closed_or_partial_objects():
    class ShutdownOnly:
        def shutdown(self, how):
            raise OSError("already closed")

    closed = socket.socket()
    closed.close()
    for value in (None, object(), ShutdownOnly(), closed):
        wake_blocked_socket(value)
