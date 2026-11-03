"""Usage caps for paid external services behind the viewer server.

`dynacity serve` spends the operator's money on every scenario compile or map
question (a language-model call) and on every photorealistic-tiles session.
Bound to localhost that is only the operator's own clicking; bound to a
workshop network, anyone who can reach the page can spend it. A cap per server
run and a per-minute rate make the worst case a known number instead of an
open-ended bill — the same idea as a hard spend cap, counted in requests
because the providers do not report cost per call.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class RequestBudget:
    """Allows at most `total` requests per server run and `per_minute` in any 60 s window.

    `None` disables a limit. Thread-safe: FastAPI runs sync endpoints on a
    thread pool.
    """

    def __init__(
        self,
        total: int | None = None,
        per_minute: int | None = None,
        *,
        what: str = "requests",
        clock: Callable[[], float] = time.monotonic,
    ):
        if (total is not None and total < 0) or (per_minute is not None and per_minute < 1):
            raise ValueError("total must be >= 0 and per_minute >= 1 when set")
        self.total = total
        self.per_minute = per_minute
        self.what = what
        self.used = 0
        self._recent: deque[float] = deque()
        self._clock = clock
        self._lock = threading.Lock()

    def take(self) -> str | None:
        """Spend one request, or return why it is refused (spending nothing)."""

        with self._lock:
            now = self._clock()
            while self._recent and now - self._recent[0] >= 60.0:
                self._recent.popleft()
            if self.total is not None and self.used >= self.total:
                return (f"this server's limit of {self.total} {self.what} per run is used up; "
                        "restart it or raise the limit")
            if self.per_minute is not None and len(self._recent) >= self.per_minute:
                wait = 60.0 - (now - self._recent[0])
                return f"more than {self.per_minute} {self.what} a minute; try again in {wait:.0f} s"
            self.used += 1
            self._recent.append(now)
            return None

    def remaining(self) -> int | None:
        with self._lock:
            return None if self.total is None else max(self.total - self.used, 0)
