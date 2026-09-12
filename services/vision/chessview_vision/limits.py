"""Rate limiting.

Sessions are anonymous by design -- there are no accounts in v1 -- which means the
only thing standing between one client and the whole engine pool is this module.
An engine serves one session at a time, so a handful of connections from one source
can deny service to everyone else.

Two separate limits, because they protect different resources:

* **Concurrent sessions per client** protects the engine pool, which is the scarce
  and expensive resource.
* **Frame rate per session** protects CPU and memory. The client gates its own frame
  rate, but a client is not something the server may rely on: a buggy build or a
  hostile one can send at whatever rate it likes.

Both are in-memory and per-process, which matches how sessions are held. Behind
several containers each enforces its own share, so the effective limit scales with
the fleet -- acceptable while the fleet is small, and worth moving to a shared store
before it is not.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default

log = logging.getLogger(__name__)


@dataclass(slots=True)
class LimitConfig:
    """Tunable per deployment: the right values depend on the pool size and on
    whether clients arrive from distinct addresses or behind shared NAT."""

    # An engine lease each, so this is the per-client share of the pool.
    max_sessions_per_client: int = 3
    # The client bursts to 5 fps on motion; this leaves headroom for retries and
    # clock skew without allowing a flood.
    max_frames_per_second: float = 12.0
    # Frames above the sustained rate tolerated in a burst, so a legitimate motion
    # burst is never punished.
    frame_burst: int = 24

    @classmethod
    def from_env(cls) -> "LimitConfig":
        return cls(
            max_sessions_per_client=_env_int("CHESSVIEW_MAX_SESSIONS_PER_CLIENT", 3),
            max_frames_per_second=_env_float("CHESSVIEW_MAX_FPS", 12.0),
            frame_burst=_env_int("CHESSVIEW_FRAME_BURST", 24),
        )


class TokenBucket:
    """Classic token bucket: a sustained rate plus a burst allowance.

    Chosen over a fixed window because a window boundary lets a client send two
    full windows' worth of frames back to back, which is exactly the spike the
    limit exists to prevent.
    """

    def __init__(self, rate: float, capacity: int, *, now: float | None = None) -> None:
        self._rate = rate
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._updated = now if now is not None else time.monotonic()

    def allow(self, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        elapsed = max(0.0, current - self._updated)
        self._updated = current
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)

        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    @property
    def tokens(self) -> float:
        return self._tokens


@dataclass(slots=True)
class ClientLimiter:
    """Tracks concurrent sessions per client key (normally a source address)."""

    config: LimitConfig = field(default_factory=LimitConfig)
    _sessions: dict[str, set[str]] = field(default_factory=dict)
    _rejections: int = 0

    def try_acquire(self, client: str, session_id: str) -> bool:
        held = self._sessions.setdefault(client, set())
        if len(held) >= self.config.max_sessions_per_client:
            self._rejections += 1
            log.warning(
                "client %s already holds %d sessions; refusing another",
                client, len(held),
            )
            return False
        held.add(session_id)
        return True

    def release(self, client: str, session_id: str) -> None:
        held = self._sessions.get(client)
        if held is None:
            return
        held.discard(session_id)
        # Drop the key entirely so a long-running process does not accumulate an
        # entry for every address that has ever connected.
        if not held:
            del self._sessions[client]

    @property
    def active_clients(self) -> int:
        return len(self._sessions)

    @property
    def active_sessions(self) -> int:
        return sum(len(held) for held in self._sessions.values())

    @property
    def rejections(self) -> int:
        return self._rejections


class FrameLimiter:
    """Per-session frame rate limit.

    Over-rate frames are dropped rather than closing the connection: the common
    cause is a client whose motion gate is misbehaving, and killing its session
    would turn a minor bug into a broken app. Persistent offenders show up in the
    dropped count.
    """

    def __init__(self, config: LimitConfig | None = None) -> None:
        self._config = config or LimitConfig()
        self._bucket = TokenBucket(
            self._config.max_frames_per_second, self._config.frame_burst
        )
        self._dropped = 0
        self._accepted = 0
        # A short window of recent arrivals, for reporting the observed rate.
        self._recent: deque[float] = deque(maxlen=64)

    def allow(self, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        self._recent.append(current)

        if self._bucket.allow(now=current):
            self._accepted += 1
            return True
        self._dropped += 1
        return False

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def accepted(self) -> int:
        return self._accepted

    @property
    def observed_fps(self) -> float:
        if len(self._recent) < 2:
            return 0.0
        span = self._recent[-1] - self._recent[0]
        return (len(self._recent) - 1) / span if span > 0 else 0.0
