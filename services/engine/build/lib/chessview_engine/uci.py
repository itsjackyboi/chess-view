"""Async UCI driver for a single long-lived engine process.

Deliberately thin: it speaks UCI and nothing else. Score normalisation, SAN
conversion and pooling live in sibling modules, so this stays testable against any
UCI engine.

The process is started once and reused for the life of the service. Spawning
Stockfish per request would cost process startup plus NNUE load on every position,
and would throw away the transposition table between moves of the same game -- the
table is exactly what makes the second search in a game fast.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

log = logging.getLogger(__name__)

# Depths below this are noise -- Stockfish reaches them in microseconds and the
# evaluations swing wildly. Emitting them would make the bar jitter on every move.
MIN_REPORTED_DEPTH = 8


class UciError(RuntimeError):
    """The engine process failed, died, or answered something unparseable."""


@dataclass(slots=True)
class InfoLine:
    """One `info ... multipv N ...` line: the engine's current view of one move."""

    depth: int
    multipv: int
    # Exactly one of these is set. Both are still from the *side to move's*
    # perspective at this layer; normalisation happens in analysis.py.
    score_cp: int | None = None
    score_mate: int | None = None
    pv: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DepthReport:
    """A complete set of MultiPV lines at one depth, ready to send to a client."""

    depth: int
    lines: list[InfoLine]


def parse_info(line: str) -> InfoLine | None:
    """Parse one `info` line, or return None if it carries no usable evaluation.

    Skips the many `info` variants that are not evaluations (`currmove`, `string`,
    bare `nps` heartbeats) and bound-flagged scores, which are provisional values
    from an aborted window search and would show the user a wrong evaluation.
    """
    tokens = line.split()
    if not tokens or tokens[0] != "info":
        return None
    if "lowerbound" in tokens or "upperbound" in tokens:
        return None

    depth: int | None = None
    multipv = 1
    score_cp: int | None = None
    score_mate: int | None = None
    pv: list[str] = []

    i = 1
    while i < len(tokens):
        token = tokens[i]
        try:
            if token == "depth":
                depth = int(tokens[i + 1])
                i += 2
            elif token == "multipv":
                multipv = int(tokens[i + 1])
                i += 2
            elif token == "score":
                kind = tokens[i + 1]
                value = int(tokens[i + 2])
                if kind == "cp":
                    score_cp = value
                elif kind == "mate":
                    score_mate = value
                i += 3
            elif token == "pv":
                # `pv` is always last; everything after it is the line.
                pv = tokens[i + 1:]
                break
            else:
                i += 1
        except (IndexError, ValueError):
            # A truncated or malformed field -- salvage whatever parsed cleanly
            # rather than dropping an otherwise usable evaluation.
            break

    if depth is None or (score_cp is None and score_mate is None) or not pv:
        return None
    return InfoLine(depth=depth, multipv=multipv, score_cp=score_cp,
                    score_mate=score_mate, pv=pv)


class UciEngine:
    """A single warm engine process."""

    def __init__(
        self,
        binary: str,
        *,
        threads: int = 2,
        hash_mb: int = 256,
        multipv: int = 3,
    ) -> None:
        self._binary = binary
        self._threads = threads
        self._hash_mb = hash_mb
        self._multipv = multipv
        self._proc: asyncio.subprocess.Process | None = None
        self._name = "unknown"
        # Serialises searches: one `go` at a time per process, since UCI has no
        # request IDs and interleaved searches would be indistinguishable.
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def multipv(self) -> int:
        return self._multipv

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            self._binary,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._handshake()
        log.info("engine ready: %s (threads=%d hash=%dMB multipv=%d)",
                 self._name, self._threads, self._hash_mb, self._multipv)

    async def _handshake(self) -> None:
        self._send("uci")
        async for line in self._read_until("uciok"):
            if line.startswith("id name "):
                self._name = line[len("id name "):].strip()
        for option, value in (
            ("Threads", self._threads),
            ("Hash", self._hash_mb),
            ("MultiPV", self._multipv),
        ):
            self._send(f"setoption name {option} value {value}")
        await self._sync()

    async def _sync(self) -> None:
        """Block until the engine has processed everything sent so far."""
        self._send("isready")
        async for _ in self._read_until("readyok"):
            pass

    def _send(self, command: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise UciError("engine process is not running")
        self._proc.stdin.write(f"{command}\n".encode())

    async def _readline(self) -> str:
        if self._proc is None or self._proc.stdout is None:
            raise UciError("engine process is not running")
        raw = await self._proc.stdout.readline()
        if not raw:
            raise UciError("engine process closed its output stream")
        return raw.decode(errors="replace").strip()

    async def _read_until(self, terminator: str) -> AsyncIterator[str]:
        while True:
            line = await self._readline()
            if line == terminator:
                return
            yield line

    async def new_game(self) -> None:
        """Clear the transposition table for an unrelated position.

        `ucinewgame` throws away exactly the search results that make the next move
        in a game fast, so it is never sent between moves of one game. Only the
        owner knows where a game boundary falls, so the pool calls this on lease
        rather than the engine guessing from consecutive FENs.
        """
        self._send("ucinewgame")
        await self._sync()

    async def analyse(
        self,
        fen: str,
        *,
        depth: int,
        movetime_ms: int,
    ) -> AsyncIterator[DepthReport]:
        """Search `fen`, yielding one report per completed depth.

        Yields progressively so the client can render an evaluation immediately and
        refine it, rather than waiting for the final depth. Stops at whichever of
        `depth` or `movetime_ms` is reached first.

        Cancelling the iterator sends `stop` and drains to `bestmove`, leaving the
        process reusable -- which is what makes it safe to abandon a search the
        moment a new position arrives.
        """
        async with self._lock:
            if not self.is_alive:
                raise UciError("engine process is not running")

            self._send(f"position fen {fen}")
            self._send(f"go depth {depth} movetime {movetime_ms}")

            pending: dict[int, InfoLine] = {}
            current_depth: int | None = None
            try:
                while True:
                    line = await self._readline()
                    if line.startswith("bestmove"):
                        report = self._flush(current_depth, pending)
                        if report is not None:
                            yield report
                        return

                    info = parse_info(line)
                    if info is None:
                        continue

                    # A new depth means the previous one is complete. Flushing on
                    # this edge is what makes each yielded report internally
                    # consistent -- all MultiPV lines from the same depth.
                    if current_depth is not None and info.depth != current_depth:
                        report = self._flush(current_depth, pending)
                        pending = {}
                        if report is not None:
                            yield report

                    current_depth = info.depth
                    pending[info.multipv] = info
            except (asyncio.CancelledError, GeneratorExit):
                await self._abort_search()
                raise

    @staticmethod
    def _flush(depth: int | None, pending: dict[int, InfoLine]) -> DepthReport | None:
        """Build a report for one depth, or None if it is not safe to present.

        A group missing multipv 1 is discarded rather than sent. It happens when the
        search is cut off mid-iteration and PV 1 is still bounded: the bounded line
        is skipped as provisional, leaving PV 2 and PV 3 behind. Sending that group
        would put the *second*-best move at the head of the list, and the client
        renders lines[0] as the best move.

        Dropping it is safe -- the previous depth's complete report already stands.
        """
        if depth is None or depth < MIN_REPORTED_DEPTH or not pending:
            return None
        if 1 not in pending:
            log.debug("discarding depth %d: no resolved multipv 1 line", depth)
            return None
        return DepthReport(depth=depth, lines=[pending[k] for k in sorted(pending)])

    async def _abort_search(self) -> None:
        """Stop an in-flight search and drain to `bestmove` so the process is reusable."""
        try:
            self._send("stop")
            while True:
                if (await self._readline()).startswith("bestmove"):
                    return
        except UciError:
            # The process died mid-abort; the pool health check will replace it.
            log.warning("engine died while aborting a search")

    async def stop(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            self._send("quit")
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except (UciError, asyncio.TimeoutError, ConnectionResetError):
            self._proc.kill()
            await self._proc.wait()
        finally:
            self._proc = None
