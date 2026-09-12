"""The detection seam: pixels in, piece placement out.

Everything downstream -- tracking, the engine, the client -- is written against
`Detector`, so the real model arriving at M2 is a drop-in replacement for the stub
used to prove the pipeline at M0.

A detector reports **piece placement only**. It cannot observe whose turn it is,
castling rights, or an en passant square; those are game state and live in
tracker.BoardTracker. Keeping that boundary honest is what lets the tracker use
chess rules to correct the detector's mistakes.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Protocol, Sequence

import chess


@dataclass(slots=True)
class Detection:
    """One frame's reading of the board."""

    # The piece-placement field of a FEN -- the part before the first space.
    placement: str
    # 0..1, the detector's own estimate. For a per-square classifier this is the
    # *minimum* across squares, not the mean: one uncertain square is enough to make
    # the whole board's reading untrustworthy, and averaging would hide it.
    confidence: float
    # Per-square confidence, keyed by chess.Square. Diagnostics and, later, the
    # highlighting that tells a user which square to check.
    squares: dict[int, float] = field(default_factory=dict)


class Detector(Protocol):
    """Pixels to placement. Implementations must be safe to call concurrently."""

    async def detect(self, jpeg: bytes) -> Detection | None:
        """Read one rectified board image.

        Returns None when no board is visible at all, which is distinct from
        reading one with low confidence -- the client shows different things for
        "point me at a board" and "I can't make this out".
        """
        ...


class StubDetector:
    """Replays a scripted sequence of positions, ignoring the image entirely.

    Exists so the whole pipeline -- camera, transport, tracking, engine, overlay --
    can be built and proven end to end before any ML lands, and so the transport and
    latency tests stay deterministic afterwards.

    Each scripted position is returned `frames_per_position` times, mimicking a real
    detector holding a steady reading long enough for the stability gate to commit.
    """

    def __init__(
        self,
        placements: Sequence[str],
        *,
        frames_per_position: int = 3,
        confidence: float = 0.95,
        loop: bool = False,
    ) -> None:
        if not placements:
            raise ValueError("StubDetector needs at least one placement")
        self._placements = list(placements)
        self._frames_per_position = frames_per_position
        self._confidence = confidence
        self._loop = loop
        self._calls = 0

    @classmethod
    def from_moves(
        cls,
        moves: Sequence[str],
        *,
        start_fen: str = chess.STARTING_FEN,
        **kwargs,
    ) -> "StubDetector":
        """Build a script from SAN or UCI moves -- how the tests express a game."""
        board = chess.Board(start_fen)
        placements = [board.board_fen()]
        for move in moves:
            try:
                board.push_san(move)
            except ValueError:
                board.push_uci(move)
            placements.append(board.board_fen())
        return cls(placements, **kwargs)

    @property
    def exhausted(self) -> bool:
        return not self._loop and self._calls >= len(self._placements) * self._frames_per_position

    async def detect(self, jpeg: bytes) -> Detection | None:  # noqa: ARG002 - stub
        index = self._calls // self._frames_per_position
        self._calls += 1
        if index >= len(self._placements):
            if not self._loop:
                # Hold the final position rather than reporting no board: a real
                # detector pointed at a finished game keeps seeing it.
                index = len(self._placements) - 1
            else:
                index %= len(self._placements)
        return Detection(placement=self._placements[index], confidence=self._confidence)


class ScriptedDetector:
    """Returns an explicit sequence of Detection objects, including None.

    Used by tests that need to drive a specific failure -- a dropout, a
    low-confidence run, an impossible board -- rather than a plausible game.
    """

    def __init__(self, script: Sequence[Detection | None], *, loop: bool = False) -> None:
        self._script = list(script)
        self._iter = itertools.cycle(self._script) if loop else iter(self._script)
        self._last: Detection | None = None

    async def detect(self, jpeg: bytes) -> Detection | None:  # noqa: ARG002 - stub
        try:
            self._last = next(self._iter)
        except StopIteration:
            pass  # hold the final entry
        return self._last
