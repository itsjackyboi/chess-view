"""Test doubles for the vision service.

Shipped with the package rather than hidden in a conftest so every test module can
import them, and so the doubles stay in step with the interfaces they stand in for.

These let session and transport behaviour be tested without a Stockfish binary --
real engine behaviour is covered by the engine service's own integration tests.
"""

from __future__ import annotations

import contextlib
import time
from typing import AsyncIterator, Awaitable, Callable

from chessview_protocol import Eval, EvalLine
from pydantic import BaseModel


class FakeEngine:
    """Stands in for chessview_engine.pool.SessionEngine."""

    engine_name = "fake"

    def __init__(self) -> None:
        self.analysed: list[str] = []
        self.cancellations = 0
        self._emit: Callable[[Eval], Awaitable[None]] | None = None

    async def analyse(self, fen: str, on_update) -> None:
        self.analysed.append(fen)
        self._emit = on_update

    async def cancel(self) -> None:
        self.cancellations += 1

    async def emit(self, fen: str, *, depth: int = 12, final: bool = True) -> None:
        """Deliver an evaluation for any FEN.

        Passing a stale FEN is how tests exercise the session's late-update filter.
        """
        if self._emit is None:
            raise AssertionError("nothing has been analysed yet")
        await self._emit(
            Eval(
                fen=fen,
                depth=depth,
                final=final,
                lines=[EvalLine(multipv=1, scoreCp=20, pv=["e2e4"], san=["e4"])],
                ts=time.time(),
            )
        )


class FakePool:
    """Stands in for chessview_engine.pool.EnginePool, handing out FakeEngine leases."""

    size = 1
    available = 1
    engine_name = "fake"

    def __init__(self) -> None:
        self.leases: list[FakeEngine] = []

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    @contextlib.asynccontextmanager
    async def lease(self, *, timeout: float = 5.0) -> AsyncIterator[FakeEngine]:
        engine = FakeEngine()
        self.leases.append(engine)
        yield engine


class Recorder:
    """Captures everything a session sends, for assertions by message type."""

    def __init__(self) -> None:
        self.sent: list[BaseModel] = []

    async def __call__(self, message: BaseModel) -> None:
        self.sent.append(message)

    def of_type(self, cls: type) -> list:
        return [m for m in self.sent if isinstance(m, cls)]

    def last(self, cls: type):
        found = self.of_type(cls)
        return found[-1] if found else None
