"""Request-local, cooperative budgets; no global proxy or credential mutation."""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Callable

from .models import FailureKind


class RequestStopped(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.failure_kind = {
            "cancelled": FailureKind.CANCELLED,
            "deadline_exceeded": FailureKind.TIMEOUT,
            "operation_budget_exhausted": FailureKind.BUDGET_EXHAUSTED,
        }.get(code, FailureKind.UNKNOWN)


@dataclass
class RequestContext:
    """A single request's budget. Adapters must propagate remaining time to I/O.

    This cannot forcibly interrupt a blocking third-party call. Checks before and
    after operations prevent starting more work or accepting a late response.
    account_scope is a local routing label, never a credential or authorization grant.
    """
    timeout_seconds: float = 30.0
    max_operations: int = 10
    account_scope: str = "default"
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _cancelled: Event = field(default_factory=Event, init=False, repr=False)
    _started: float = field(init=False, repr=False)
    operations: int = field(default=0, init=False)
    _budget_lock: object = field(default_factory=Lock, init=False, repr=False, compare=False)
    _host_starts: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _limited_hosts: set = field(default_factory=set, init=False, repr=False, compare=False)

    def __post_init__(self):
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be positive and at most 300")
        if type(self.max_operations) is not int or not 1 <= self.max_operations <= 100:
            raise ValueError("max_operations must be between 1 and 100")
        if not isinstance(self.account_scope, str) or not self.account_scope.strip():
            raise ValueError("account_scope is required")
        self._started = self._clock()

    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - (self._clock() - self._started))

    def check_active(self) -> None:
        if self._cancelled.is_set():
            raise RequestStopped("cancelled")
        if self.remaining_seconds() <= 0:
            raise RequestStopped("deadline_exceeded")

    def begin_operation(self) -> None:
        with self._budget_lock:
            self.check_active()
            if self.operations >= self.max_operations:
                raise RequestStopped("operation_budget_exhausted")
            self.operations += 1

    def _wait_for_public_host(self, host):
        # Request-local start spacing, no retry after a 429 in this request.
        # A wake-up rechecks the clock/budget; cancellation interrupts waiting.
        while True:
            self.check_active()
            with self._budget_lock:
                if self.operations >= self.max_operations:
                    raise RequestStopped('operation_budget_exhausted')
                if host in self._limited_hosts:
                    return False
                delay = self._host_starts.get(host, 0) - self._clock()
                if delay <= 0:
                    self._host_starts[host] = self._clock() + 0.25
                    return True
            self._cancelled.wait(min(delay, self.remaining_seconds(), 0.05))

    def _mark_public_host_limited(self, host):
        with self._budget_lock:
            self._limited_hosts.add(host)

    def cancel(self) -> None:
        self._cancelled.set()


class SourceSlice:
    """One source's share of a request: a nearer deadline over the parent's shared budget.

    Operation counts, cancellation, host pacing and request-local caches stay on the
    parent, so a slow source can run out of its own time without ending the request.
    """

    def __init__(self, parent, seconds: float):
        object.__setattr__(self, "_parent", parent)
        object.__setattr__(self, "_deadline", parent._clock() + max(0.0, seconds))

    def __getattr__(self, name):
        return getattr(self._parent, name)

    def __setattr__(self, name, value):
        setattr(self._parent, name, value)

    def expired(self) -> bool:
        return self._parent._clock() >= self._deadline

    def remaining_seconds(self) -> float:
        return max(0.0, min(self._parent.remaining_seconds(), self._deadline - self._parent._clock()))

    def check_active(self) -> None:
        self._parent.check_active()
        if self.expired():
            raise RequestStopped("deadline_exceeded")

    def begin_operation(self) -> None:
        self.check_active()
        self._parent.begin_operation()

    def _wait_for_public_host(self, host):
        self.check_active()
        ready = self._parent._wait_for_public_host(host)
        self.check_active()
        return ready
