"""Board state tracking: turn noisy per-frame observations into committed positions.

This is the accuracy argument from docs/architecture.md made concrete. A detector
sees *piece placement* and nothing else -- a camera cannot observe whose turn it is,
castling rights, or an en passant square. Those live in game state, here.

Rather than trusting a whole-board read (99.5% per-square accuracy still yields a
73% chance of a correct board), we hold the position and ask a much narrower
question of each observation: **which legal move, if any, explains what changed?**
A typical position has ~35 legal moves, so this is a ~35-way choice rather than a
13^64 one.

Nothing is committed until the same answer arrives `stability_frames` times in a
row, which is what stops a hand over the board being read as a move.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import chess

log = logging.getLogger(__name__)


class Outcome(str, Enum):
    UNCHANGED = "unchanged"
    PENDING = "pending"
    COMMITTED = "committed"
    UNCLEAR = "unclear"
    LOW_CONFIDENCE = "low_confidence"


@dataclass(slots=True)
class TrackerResult:
    outcome: Outcome
    # Set only when outcome is COMMITTED.
    move: chess.Move | None = None
    san: str | None = None
    # Why we are not committing, for the client to show the user.
    detail: str | None = None
    # How many consecutive agreeing observations we have for the pending candidate.
    agreement: int = 0
    # True when a commit came from resynchronisation rather than a matched move.
    resynced: bool = False


class BoardTracker:
    def __init__(
        self,
        board: chess.Board | None = None,
        *,
        stability_frames: int = 3,
        min_confidence: float = 0.6,
        resync_frames: int = 8,
    ) -> None:
        """
        Args:
            stability_frames: consecutive agreeing observations before committing.
                Three at a 5 fps burst costs ~600 ms, which is what the latency
                budget allows (docs/architecture.md).
            min_confidence: observations below this are discarded outright. The
                product requirement is to say "unclear" rather than show analysis
                for a position that might be wrong.
            resync_frames: consecutive *stable but unexplainable* observations
                before accepting the board as a fresh position. Without this, one
                missed move would wedge the tracker permanently -- every later
                observation would be two plies away and match nothing.
        """
        self._board = board.copy() if board is not None else chess.Board()
        self._stability_frames = stability_frames
        self._min_confidence = min_confidence
        self._resync_frames = resync_frames

        self._candidate: str | None = None
        self._agreement = 0

    @property
    def board(self) -> chess.Board:
        return self._board

    @property
    def fen(self) -> str:
        return self._board.fen()

    @property
    def ply(self) -> int:
        return len(self._board.move_stack)

    def set_position(self, fen: str) -> None:
        """Adopt a position wholesale -- a manual correction or calibration.

        Clears any pending candidate: whatever we were accumulating evidence for
        describes a board state the user has just told us is wrong.
        """
        self._board = chess.Board(fen)
        self._reset_candidate()

    def _reset_candidate(self) -> None:
        self._candidate = None
        self._agreement = 0

    def observe(self, placement: str, confidence: float) -> TrackerResult:
        """Fold one detection into the tracked state.

        `placement` is the piece-placement field of a FEN (the part before the first
        space) -- all a detector can actually see.
        """
        if confidence < self._min_confidence:
            self._reset_candidate()
            return TrackerResult(
                Outcome.LOW_CONFIDENCE,
                detail=f"detection confidence {confidence:.2f} below threshold",
            )

        if placement == self._board.board_fen():
            self._reset_candidate()
            return TrackerResult(Outcome.UNCHANGED)

        # Accumulate agreement on the raw observation, not on its interpretation.
        # A hand moving across the board produces a different placement each frame,
        # so it never reaches the threshold.
        if placement == self._candidate:
            self._agreement += 1
        else:
            self._candidate = placement
            self._agreement = 1

        matches = self._matching_moves(placement)

        if len(matches) == 1:
            if self._agreement < self._stability_frames:
                return TrackerResult(
                    Outcome.PENDING, agreement=self._agreement,
                    detail="confirming move",
                )
            return self._commit_move(matches[0])

        if len(matches) > 1:
            # Defensive, and believed unreachable: two distinct legal moves always
            # vacate different origin squares or fill different destinations, so no
            # placement can be explained by more than one move. The invariant is
            # asserted in test_distinct_legal_moves_yield_distinct_placements. If it
            # ever does happen, refusing to guess is the safe response.
            self._reset_candidate()
            return TrackerResult(
                Outcome.UNCLEAR,
                detail=f"{len(matches)} legal moves produce this position",
            )

        # Nothing explains the change in one ply. Usually transient -- a hand over
        # the board, a piece mid-air, a misread square.
        if self._agreement >= self._resync_frames:
            return self._resync(placement)
        return TrackerResult(
            Outcome.UNCLEAR, agreement=self._agreement,
            detail="no legal move explains the observed board",
        )

    def _matching_moves(self, placement: str) -> list[chess.Move]:
        """Every legal move whose result matches the observed placement.

        In practice this returns at most one: distinct moves produce distinct
        placements. Promotions stay distinguishable because promoting to a queen and
        to a knight leave different pieces on the board, and castling moves two
        pieces at once so it cannot collide with a plain king move.
        """
        matches: list[chess.Move] = []
        for move in self._board.legal_moves:
            self._board.push(move)
            if self._board.board_fen() == placement:
                matches.append(move)
            self._board.pop()
        return matches

    def _commit_move(self, move: chess.Move) -> TrackerResult:
        san = self._board.san(move)
        self._board.push(move)
        self._reset_candidate()
        return TrackerResult(Outcome.COMMITTED, move=move, san=san)

    def _resync(self, placement: str) -> TrackerResult:
        """Accept a stable placement that no single move explains.

        Reached when the physical board has moved on without us -- two pieces moved
        between frames, or the user reset the board. Holding the stale position
        forever would be worse: every subsequent observation would also fail to
        match and the session would never recover.

        Side to move is unobservable, so both are tried and validity decides: a
        position where the side *not* to move is in check is illegal. If both or
        neither validate, the side to move is left alternating from where we were,
        and the caller sees `resynced=True` so it can flag reduced confidence.
        """
        candidates = []
        for turn in (self._board.turn, not self._board.turn):
            trial = chess.Board(None)
            trial.set_board_fen(placement)
            trial.turn = turn
            # Castling rights are unobservable too. Clearing them is the safe
            # direction: it can only forbid a legal move, never invent one.
            trial.castling_rights = chess.BB_EMPTY
            if trial.is_valid():
                candidates.append(trial)

        resolved = candidates[0] if candidates else None
        if resolved is None:
            self._reset_candidate()
            return TrackerResult(
                Outcome.UNCLEAR,
                detail="observed board is not a legal chess position",
            )

        self._board = resolved
        self._reset_candidate()
        log.info("resynchronised to an unexplained but stable position")
        return TrackerResult(
            Outcome.COMMITTED, resynced=True,
            detail="resynchronised after losing track of the board",
        )
