"""Tests for board tracking.

This is where the system's accuracy actually comes from, so the cases that matter
are the adversarial ones: a hand over the board, a flickering detection, a move we
missed entirely. Getting any of these wrong means confidently showing the user an
evaluation for a position that is not on their board.
"""

from __future__ import annotations

import chess
import pytest

from chessview_vision.tracker import BoardTracker, Outcome

CONFIDENT = 0.95


def placement_after(fen: str, *ucis: str) -> str:
    """The piece-placement field after playing some moves -- what a detector sees."""
    board = chess.Board(fen)
    for uci in ucis:
        board.push_uci(uci)
    return board.board_fen()


def observe_until(tracker: BoardTracker, placement: str, times: int):
    result = None
    for _ in range(times):
        result = tracker.observe(placement, CONFIDENT)
    assert result is not None
    return result


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_an_unchanged_board_is_not_a_move():
    tracker = BoardTracker()
    assert tracker.observe(chess.Board().board_fen(), CONFIDENT).outcome is Outcome.UNCHANGED


def test_a_move_commits_only_after_the_stability_threshold():
    tracker = BoardTracker(stability_frames=3)
    e4 = placement_after(chess.STARTING_FEN, "e2e4")

    assert tracker.observe(e4, CONFIDENT).outcome is Outcome.PENDING
    assert tracker.observe(e4, CONFIDENT).outcome is Outcome.PENDING

    result = tracker.observe(e4, CONFIDENT)
    assert result.outcome is Outcome.COMMITTED
    assert result.san == "e4"
    assert tracker.board.turn is chess.BLACK
    assert tracker.ply == 1


def test_pending_reports_accumulating_agreement():
    tracker = BoardTracker(stability_frames=4)
    e4 = placement_after(chess.STARTING_FEN, "e2e4")
    assert [tracker.observe(e4, CONFIDENT).agreement for _ in range(3)] == [1, 2, 3]


def test_successive_moves_track_a_game():
    tracker = BoardTracker(stability_frames=2)
    fen = chess.STARTING_FEN
    for uci, expected_san in [("e2e4", "e4"), ("e7e5", "e5"), ("g1f3", "Nf3")]:
        result = observe_until(tracker, placement_after(fen, uci), 2)
        assert result.outcome is Outcome.COMMITTED and result.san == expected_san
        fen = tracker.fen
    assert tracker.ply == 3


# --------------------------------------------------------------------------
# Special moves -- each produces a placement no ordinary move could
# --------------------------------------------------------------------------


def test_castling_is_recognised_as_a_single_move():
    fen = "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1"
    tracker = BoardTracker(chess.Board(fen), stability_frames=2)
    result = observe_until(tracker, placement_after(fen, "e1g1"), 2)
    assert result.outcome is Outcome.COMMITTED and result.san == "O-O"


def test_en_passant_is_recognised():
    fen = "4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1"
    tracker = BoardTracker(chess.Board(fen), stability_frames=2)
    result = observe_until(tracker, placement_after(fen, "d4e3"), 2)
    assert result.outcome is Outcome.COMMITTED and result.san == "dxe3"


@pytest.mark.parametrize("uci, san", [("a7a8q", "a8=Q+"), ("a7a8n", "a8=N")])
def test_promotion_pieces_are_distinguished(uci: str, san: str):
    """Promoting to a queen and to a knight give different placements, so the
    matcher can tell them apart without asking the user."""
    fen = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
    tracker = BoardTracker(chess.Board(fen), stability_frames=2)
    result = observe_until(tracker, placement_after(fen, uci), 2)
    assert result.outcome is Outcome.COMMITTED and result.san == san


# --------------------------------------------------------------------------
# Adversarial: the cases that cause wrong evaluations
# --------------------------------------------------------------------------


def test_low_confidence_observations_are_discarded():
    tracker = BoardTracker(min_confidence=0.6)
    e4 = placement_after(chess.STARTING_FEN, "e2e4")
    result = tracker.observe(e4, 0.4)
    assert result.outcome is Outcome.LOW_CONFIDENCE
    assert tracker.ply == 0


def test_a_hand_over_the_board_never_commits():
    """Occlusion produces a different garbled placement every frame.

    Agreement accumulates on the raw observation, so a sequence that never repeats
    can never reach the threshold -- which is the whole point of the stability gate.
    """
    tracker = BoardTracker(stability_frames=3, resync_frames=8)
    board = chess.Board()
    for missing in range(12):
        occluded = board.copy()
        occluded.remove_piece_at(chess.A2 + missing)  # a different square each frame
        result = tracker.observe(occluded.board_fen(), CONFIDENT)
        assert result.outcome is not Outcome.COMMITTED

    assert tracker.ply == 0
    assert tracker.fen == chess.STARTING_FEN


def test_flickering_detection_resets_agreement():
    """Alternating between two readings must not accumulate toward either."""
    tracker = BoardTracker(stability_frames=3)
    e4 = placement_after(chess.STARTING_FEN, "e2e4")
    d4 = placement_after(chess.STARTING_FEN, "d2d4")

    for _ in range(6):
        assert tracker.observe(e4, CONFIDENT).outcome is Outcome.PENDING
        assert tracker.observe(d4, CONFIDENT).outcome is Outcome.PENDING
    assert tracker.ply == 0


def test_an_unexplainable_board_is_reported_unclear_not_guessed():
    tracker = BoardTracker(stability_frames=2, resync_frames=99)
    # Two moves at once: no single legal move produces this.
    two_ply = placement_after(chess.STARTING_FEN, "e2e4", "e7e5")
    result = tracker.observe(two_ply, CONFIDENT)
    assert result.outcome is Outcome.UNCLEAR
    assert tracker.ply == 0


@pytest.mark.parametrize(
    "fen",
    [
        chess.STARTING_FEN,
        "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1",  # castling available
        "4k3/P7/8/8/8/8/8/4K3 w - - 0 1",  # promotions available
        "r2q1rk1/pp1nbppp/2p1pn2/3p4/2PP4/2N1PN2/PPQ1BPPP/R1B2RK1 w - - 0 10",
        "4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1",  # en passant available
    ],
)
def test_distinct_legal_moves_yield_distinct_placements(fen: str):
    """The invariant that makes single-ply matching unambiguous.

    Two different moves always vacate different origin squares or fill different
    destinations, so no placement can be explained by more than one legal move.
    This is why a matched observation needs no tie-breaking -- and why the
    multiple-match branch in the tracker is defensive rather than reachable.
    """
    board = chess.Board(fen)
    placements = []
    for move in board.legal_moves:
        board.push(move)
        placements.append(board.board_fen())
        board.pop()

    assert len(placements) == len(set(placements)), "found an ambiguous placement"


# --------------------------------------------------------------------------
# Recovery
# --------------------------------------------------------------------------


def test_resynchronises_after_a_persistently_unexplainable_board():
    """A missed move must not wedge the session permanently.

    Without this, every later observation is also two plies away, matches nothing,
    and the tracker never recovers.
    """
    tracker = BoardTracker(stability_frames=2, resync_frames=5)
    two_ply = placement_after(chess.STARTING_FEN, "e2e4", "e7e5")

    result = observe_until(tracker, two_ply, 5)
    assert result.outcome is Outcome.COMMITTED
    assert result.resynced is True
    assert tracker.board.board_fen() == two_ply


def test_resync_refuses_an_impossible_board():
    tracker = BoardTracker(stability_frames=2, resync_frames=3)
    # Two white kings and no black king is not a legal chess position.
    result = observe_until(tracker, "8/8/8/8/8/8/8/K6K", 3)
    assert result.outcome is Outcome.UNCLEAR
    assert "not a legal chess position" in (result.detail or "")


def test_resync_picks_the_side_to_move_that_makes_the_position_legal():
    """Side to move is unobservable, so legality has to decide it.

    Here black is in check, so it cannot be white's move.
    """
    tracker = BoardTracker(stability_frames=2, resync_frames=3)
    # Black king on e8 in check from the rook on e1; white king safely on a1.
    result = observe_until(tracker, "4k3/8/8/8/8/8/8/K3R3", 3)
    assert result.outcome is Outcome.COMMITTED and result.resynced
    assert tracker.board.turn is chess.BLACK


def test_manual_correction_replaces_the_position_and_clears_pending():
    tracker = BoardTracker(stability_frames=3)
    tracker.observe(placement_after(chess.STARTING_FEN, "e2e4"), CONFIDENT)
    tracker.observe(placement_after(chess.STARTING_FEN, "e2e4"), CONFIDENT)

    corrected = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
    tracker.set_position(corrected)
    assert tracker.fen == corrected

    # The pending candidate must not survive: the user just said it was wrong.
    assert tracker.observe(placement_after(chess.STARTING_FEN, "e2e4"), CONFIDENT).outcome is Outcome.UNCLEAR
