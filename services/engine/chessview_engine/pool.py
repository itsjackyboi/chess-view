"""A pool of warm engine processes, leased one per active session.

One engine serves one session at a time, per the architecture: sharing a process
between sessions would thrash the transposition table and serialise their searches
behind each other. The pool is the horizontal scaling unit -- adding capacity means
more engines here, and eventually more hosts behind a queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterator, Awaitable, Callable

from chessview_protocol import Eval

from chessview_engine.analysis import report_to_protocol
from chessview_engine.config import EngineConfig
from chessview_engine.uci import UciEngine, UciError

log = logging.getLogger(__name__)

EvalCallback = Callable[[Eval], Awaitable[None]]


class EngineUnavailable(RuntimeError):
    """No engine could be leased. Surfaced to the client rather than hidden."""


class SessionEngine:
    """An engine leased to one session, with at most one search in flight.

    When a new position arrives the previous search is abandoned immediately --
    its results are about a position that no longer exists on the board, and
    finishing it would delay the evaluation the user is actually waiting for.
    """

    def __init__(self, engine: UciEngine, config: EngineConfig) -> None:
        self._engine = engine
        self._config = config
        self._task: asyncio.Task[None] | None = None

    @property
    def engine_name(self) -> str:
        return self._engine.name

    async def analyse(self, fen: str, on_update: EvalCallback) -> None:
        """Begin analysing `fen`, cancelling any search already running.

        Returns as soon as the new search is scheduled; updates arrive via
        `on_update` at increasing depth until the final one (`Eval.final`).
        """
        await self.cancel()
        self._task = asyncio.create_task(self._run(fen, on_update))

    async def _run(self, fen: str, on_update: EvalCallback) -> None:
        last: Eval | None = None
        try:
            async for report in self._engine.analyse(
                fen, depth=self._config.depth, movetime_ms=self._config.movetime_ms
            ):
                if last is not None:
                    await on_update(last)
                last = report_to_protocol(fen, report, final=False)
            # The deepest report is only known to be last once the search ends, so
            # each is held back one iteration and re-sent with final=True. The
            # client uses that flag to stop showing a "deepening" indicator.
            if last is not None:
                await on_update(last.model_copy(update={"final": True}))
        except asyncio.CancelledError:
            raise
        except UciError:
            log.exception("engine failed while analysing %s", fen)

    async def cancel(self) -> None:
        if self._task is None or self._task.done():
            self._task = None
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


class EnginePool:
    def __init__(self, config: EngineConfig | None = None) -> None:
        self._config = config or EngineConfig.from_env()
        self._idle: asyncio.Queue[UciEngine] = asyncio.Queue()
        self._all: list[UciEngine] = []
        self._started = False

    @property
    def config(self) -> EngineConfig:
        return self._config

    @property
    def size(self) -> int:
        return len(self._all)

    @property
    def available(self) -> int:
        return self._idle.qsize()

    @property
    def engine_name(self) -> str:
        return self._all[0].name if self._all else "unavailable"

    async def start(self) -> None:
        """Spawn every engine up front so no session pays startup or NNUE load.

        This is why the service runs on persistent hosts rather than scale-to-zero
        serverless: the pool is warm before the first client connects.
        """
        if self._started:
            return
        for _ in range(self._config.pool_size):
            engine = await self._spawn()
            self._all.append(engine)
            self._idle.put_nowait(engine)
        self._started = True
        log.info("engine pool ready: %d x %s", self.size, self.engine_name)

    async def _spawn(self) -> UciEngine:
        engine = UciEngine(
            self._config.binary,
            threads=self._config.threads,
            hash_mb=self._config.hash_mb,
            multipv=self._config.multipv,
        )
        try:
            await engine.start()
        except (OSError, UciError) as exc:
            raise EngineUnavailable(
                f"could not start engine at {self._config.binary!r}: {exc}"
            ) from exc
        return engine

    @contextlib.asynccontextmanager
    async def lease(self, *, timeout: float = 5.0) -> AsyncIterator[SessionEngine]:
        """Lease an engine for the lifetime of a session.

        Times out rather than queueing indefinitely: a client waiting on a busy pool
        should be told the service is at capacity, not left with a silently frozen
        evaluation.
        """
        if not self._started:
            raise EngineUnavailable("engine pool has not been started")
        try:
            engine = await asyncio.wait_for(self._idle.get(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise EngineUnavailable(
                f"all {self.size} engines are busy"
            ) from exc

        session = SessionEngine(engine, self._config)
        try:
            # A lease boundary is a game boundary, which is the one place clearing
            # the transposition table is correct.
            await engine.new_game()
            yield session
        finally:
            await session.cancel()
            await self._return(engine)

    async def _return(self, engine: UciEngine) -> None:
        """Return an engine to the pool, replacing it if it died mid-session."""
        if engine.is_alive:
            self._idle.put_nowait(engine)
            return

        log.warning("engine died during a session; respawning")
        with contextlib.suppress(ValueError):
            self._all.remove(engine)
        try:
            replacement = await self._spawn()
        except EngineUnavailable:
            log.exception("could not respawn engine; pool is now smaller")
            return
        self._all.append(replacement)
        self._idle.put_nowait(replacement)

    async def stop(self) -> None:
        await asyncio.gather(*(e.stop() for e in self._all), return_exceptions=True)
        self._all.clear()
        self._idle = asyncio.Queue()
        self._started = False
