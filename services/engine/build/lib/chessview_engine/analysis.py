"""Turn raw UCI output into protocol messages.

Two conversions happen here, both of which are wrong-by-default if skipped:

* **Score normalisation.** UCI reports scores from the side to move's point of
  view, so an unchanged position would flip sign every move and the evaluation bar
  would swing wildly back and forth. Everything leaving this module is from
  **white's** point of view.
* **SAN.** The client shows `Nf3`, not `g1f3`. Converting server-side keeps the
  chess rules in one place instead of shipping a move formatter to the client.
"""

from __future__ import annotations

import logging
import time

import chess
from chessview_protocol import Eval, EvalLine

from chessview_engine.uci import DepthReport, InfoLine

log = logging.getLogger(__name__)

# How many plies of the principal variation to convert and send. The overlay shows
# a handful of moves; sending a 30-ply line would be bandwidth spent on text no one
# reads.
PV_PLIES = 8


def normalise_cp(score_cp: int, turn: chess.Color) -> int:
    """Convert a centipawn score to white's point of view."""
    return score_cp if turn == chess.WHITE else -score_cp


def normalise_mate(score_mate: int, turn: chess.Color) -> int:
    """Convert a mate distance to white's point of view.

    `mate 3` from black's perspective means black delivers mate, which is `-3` to
    white. The sign of the number carries who is mating; its magnitude is the
    distance, so negating is correct for both signs.
    """
    return score_mate if turn == chess.WHITE else -score_mate


def pv_to_san(board: chess.Board, pv: list[str], limit: int = PV_PLIES) -> list[str]:
    """Convert a UCI principal variation to SAN, stopping at the first illegal move.

    A PV can contain a move that is illegal in the position we hold if the engine
    was searching a position we have since moved past. Truncating is the right
    response: better a short correct line than a wrong one.
    """
    scratch = board.copy(stack=False)
    san: list[str] = []
    for uci in pv[:limit]:
        try:
            move = chess.Move.from_uci(uci)
            if move not in scratch.legal_moves:
                break
            san.append(scratch.san(move))
            scratch.push(move)
        except (chess.InvalidMoveError, ValueError):
            break
    return san


def line_to_protocol(info: InfoLine, board: chess.Board) -> EvalLine:
    return EvalLine(
        multipv=info.multipv,
        scoreCp=(
            normalise_cp(info.score_cp, board.turn)
            if info.score_cp is not None
            else None
        ),
        scoreMate=(
            normalise_mate(info.score_mate, board.turn)
            if info.score_mate is not None
            else None
        ),
        pv=info.pv[:PV_PLIES],
        san=pv_to_san(board, info.pv),
    )


def report_to_protocol(fen: str, report: DepthReport, *, final: bool) -> Eval:
    """Build the `eval` message for one completed depth.

    Carrying `fen` lets the client discard updates belonging to a position it has
    already moved past -- searches are cancelled asynchronously, so a late update
    for a stale position can still arrive.
    """
    board = chess.Board(fen)
    return Eval(
        fen=fen,
        depth=report.depth,
        final=final,
        lines=[line_to_protocol(line, board) for line in report.lines],
        ts=time.time(),
    )
