"""Resumable session state.

A dropped connection must not lose the game. But holding an engine lease open for a
client that may never come back would waste a pool slot -- the pool size *is* the
concurrent session ceiling -- so a disconnect releases the engine and retains only
the cheap part: the position.

On resume the client is handed a fresh engine and the stored position, which is why
the client's copy is a render-only mirror. It never replays anything.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class ResumableSession:
    fen: str
    ply: int
    expires_at: float


class SessionRegistry:
    def __init__(self, ttl_seconds: int = 120) -> None:
        self._ttl = ttl_seconds
        self._sessions: dict[str, ResumableSession] = {}

    def suspend(self, session_id: str, fen: str, ply: int) -> None:
        self._sessions[session_id] = ResumableSession(
            fen=fen, ply=ply, expires_at=time.monotonic() + self._ttl
        )

    def resume(self, session_id: str) -> ResumableSession | None:
        """Take a stored session, if it exists and has not expired.

        Removes on read: a session is resumed once. A second connection claiming the
        same ID gets a fresh session rather than a duplicate of a live one.
        """
        self._purge()
        return self._sessions.pop(session_id, None)

    def discard(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._sessions.items() if v.expires_at <= now]:
            del self._sessions[key]

    def __len__(self) -> int:
        self._purge()
        return len(self._sessions)
