"""Unit tests for UCI line parsing and score normalisation.

These are pure and fast -- no engine process. The integration tests in
test_engine_integration.py cover the parts that need a real Stockfish.
"""

from __future__ import annotations

import chess
import pytest

from chessview_engine.analysis import normalise_cp, normalise_mate, pv_to_san
from chessview_engine.uci import MIN_REPORTED_DEPTH, UciEngine, parse_info

INFO = (
    "info depth 20 seldepth 27 multipv 2 score cp -34 nodes 1234 nps 9999 "
    "hashfull 100 tbhits 0 time 100 pv e2e4 e7e5 g1f3"
)


def test_parses_a_full_info_line():
    info = parse_info(INFO)
    assert info is not None
    assert (info.depth, info.multipv, info.score_cp) == (20, 2, -34)
    assert info.pv == ["e2e4", "e7e5", "g1f3"]


def test_parses_mate_scores():
    info = parse_info("info depth 12 multipv 1 score mate -3 pv e1e2")
    assert info is not None
    assert info.score_mate == -3 and info.score_cp is None


@pytest.mark.parametrize(
    "line",
    [
        "info depth 1 currmove e2e4 currmovenumber 1",  # no score, no pv
        "info string NNUE evaluation using nn-abc.nnue",
        "bestmove e2e4 ponder e7e5",
        "",
        "info depth 20 multipv 1 score cp 12",  # score but no pv
    ],
)
def test_ignores_lines_carrying_no_evaluation(line: str):
    assert parse_info(line) is None


@pytest.mark.parametrize("bound", ["lowerbound", "upperbound"])
def test_ignores_bounded_scores(bound: str):
    """Bounded scores are provisional values from an aborted window search.

    Showing one to the user would mean displaying an evaluation the engine has
    explicitly flagged as not yet established.
    """
    assert parse_info(f"info depth 20 multipv 1 score cp 900 {bound} pv e2e4") is None


def test_salvages_a_truncated_line():
    """A line cut short mid-field should still yield what parsed cleanly."""
    info = parse_info("info depth 14 multipv 1 score cp 25 pv e2e4 e7e5 nodes")
    assert info is not None and info.depth == 14


# --------------------------------------------------------------------------
# Score normalisation -- silently wrong if untested, and very visible if wrong
# --------------------------------------------------------------------------


def test_white_scores_pass_through():
    assert normalise_cp(150, chess.WHITE) == 150
    assert normalise_mate(3, chess.WHITE) == 3


def test_black_to_move_scores_flip_to_white_perspective():
    # Black to move and winning by 1.5 pawns is -150 from white's point of view.
    assert normalise_cp(150, chess.BLACK) == -150
    # Black mating in 3 is -3 to white.
    assert normalise_mate(3, chess.BLACK) == -3
    # Black being mated in 2 is +2 to white.
    assert normalise_mate(-2, chess.BLACK) == 2


# --------------------------------------------------------------------------
# PV -> SAN
# --------------------------------------------------------------------------


def test_converts_a_principal_variation_to_san():
    assert pv_to_san(chess.Board(), ["e2e4", "e7e5", "g1f3"]) == ["e4", "e5", "Nf3"]


def test_truncates_at_the_first_illegal_move():
    """A stale PV from a position we have moved past must not produce a wrong line."""
    assert pv_to_san(chess.Board(), ["e2e4", "e2e4", "g1f3"]) == ["e4"]


def test_tolerates_malformed_uci():
    assert pv_to_san(chess.Board(), ["e2e4", "zzzz"]) == ["e4"]


def test_respects_the_ply_limit():
    board = chess.Board()
    long_pv = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1"]
    assert len(pv_to_san(board, long_pv, limit=4)) == 4


# --------------------------------------------------------------------------
# Depth flushing
# --------------------------------------------------------------------------


def test_shallow_depths_are_suppressed():
    """Depth 1-7 evaluations swing wildly and would make the bar jitter."""
    from chessview_engine.uci import InfoLine

    pending = {1: InfoLine(depth=3, multipv=1, score_cp=0, pv=["e2e4"])}
    assert UciEngine._flush(3, pending) is None


def test_reportable_depth_flushes_all_multipv_lines_in_order():
    from chessview_engine.uci import InfoLine

    depth = MIN_REPORTED_DEPTH
    pending = {
        2: InfoLine(depth=depth, multipv=2, score_cp=10, pv=["d2d4"]),
        1: InfoLine(depth=depth, multipv=1, score_cp=30, pv=["e2e4"]),
    }
    report = UciEngine._flush(depth, pending)
    assert report is not None
    assert [line.multipv for line in report.lines] == [1, 2]


def test_a_depth_missing_its_best_line_is_discarded():
    """Regression: a cut-off iteration can report PV 2 and 3 but not PV 1.

    Stockfish emits PV 1 as `lowerbound` while it is still being resolved, and that
    line is skipped as provisional. If the search is cut off at that moment the
    depth group holds only PV 2 and PV 3 -- and since the client renders lines[0]
    as the best move, sending it would show the second-best move as best.
    """
    from chessview_engine.uci import InfoLine

    depth = MIN_REPORTED_DEPTH + 2
    pending = {
        2: InfoLine(depth=depth, multipv=2, score_cp=10, pv=["d2d4"]),
        3: InfoLine(depth=depth, multipv=3, score_cp=5, pv=["c2c4"]),
    }
    assert UciEngine._flush(depth, pending) is None
