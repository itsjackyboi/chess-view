"""Session behaviour: what reaches the client, and when.

The rule these tests exist to protect is the product one from the spec -- never
present analysis for a position that might be wrong. Most of the assertions below
are about *not* sending something.
"""

from __future__ import annotations

import chess
import pytest
from chessview_protocol import (
    Calibrate,
    Corner,
    Error,
    Eval,
    FrameHeader,
    Orientation,
    OverrideFen,
    Pause,
    Ping,
    Pong,
    Position,
    PositionSource,
    SessionStatus,
    State,
)

from chessview_vision.config import VisionConfig
from chessview_vision.detector import Detection, ScriptedDetector, StubDetector
from chessview_vision.session import STARTING_FEN, Session

pytestmark = pytest.mark.asyncio

CORNERS = [Corner(x=0, y=0), Corner(x=1, y=0), Corner(x=1, y=1), Corner(x=0, y=1)]
HEADER = FrameHeader(seq=1, captureTs=0.0, motion=True)
JPEG = b"\xff\xd8not-a-real-image"


def make_session(detector, engine, recorder, **overrides) -> Session:
    config = VisionConfig(
        stability_frames=overrides.pop("stability_frames", 2),
        min_confidence=overrides.pop("min_confidence", 0.6),
        resync_frames=overrides.pop("resync_frames", 8),
    )
    return Session("s1", detector=detector, engine=engine, send=recorder, config=config)


async def calibrate(session: Session) -> None:
    await session.handle_message(
        Calibrate(corners=CORNERS, orientation=Orientation.WHITE_BOTTOM)
    )


async def feed(session: Session, times: int) -> None:
    for _ in range(times):
        await session.handle_frame(HEADER, JPEG)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


async def test_frames_before_calibration_are_ignored(engine, recorder):
    session = make_session(StubDetector.from_moves(["e4"]), engine, recorder)
    await feed(session, 5)

    assert session.status is SessionStatus.CALIBRATING
    assert recorder.of_type(Position) == []
    assert engine.analysed == []


async def test_calibration_seeds_the_position_and_starts_analysis(engine, recorder):
    session = make_session(StubDetector.from_moves(["e4"]), engine, recorder)
    await calibrate(session)

    position = recorder.last(Position)
    assert position is not None
    assert position.fen == STARTING_FEN
    assert position.source is PositionSource.INITIAL
    assert session.status is SessionStatus.TRACKING
    assert engine.analysed == [STARTING_FEN]


async def test_calibration_accepts_a_custom_start_position(engine, recorder):
    fen = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
    session = make_session(StubDetector.from_moves([]), engine, recorder)
    await session.handle_message(
        Calibrate(corners=CORNERS, orientation=Orientation.WHITE_BOTTOM, startFen=fen)
    )
    assert session.fen == fen


async def test_an_illegal_start_position_is_reported_not_adopted(engine, recorder):
    session = make_session(StubDetector.from_moves([]), engine, recorder)
    await session.handle_message(
        Calibrate(corners=CORNERS, orientation=Orientation.WHITE_BOTTOM, startFen="nonsense")
    )

    assert recorder.last(Error) is not None
    assert recorder.of_type(Position) == []
    assert session.status is SessionStatus.CALIBRATING


# --------------------------------------------------------------------------
# Tracking a game
# --------------------------------------------------------------------------


async def test_a_scripted_game_produces_positions_in_order(engine, recorder):
    detector = StubDetector.from_moves(["e4", "e5", "Nf3"], frames_per_position=2)
    session = make_session(detector, engine, recorder, stability_frames=2)
    await calibrate(session)
    await feed(session, 8)

    moves = [p.move_san for p in recorder.of_type(Position) if p.move_san]
    assert moves == ["e4", "e5", "Nf3"]
    assert session.fen.startswith("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2")


async def test_each_committed_position_triggers_analysis(engine, recorder):
    detector = StubDetector.from_moves(["e4", "e5"], frames_per_position=2)
    session = make_session(detector, engine, recorder, stability_frames=2)
    await calibrate(session)
    await feed(session, 6)

    # Once for calibration, once per committed move.
    assert len(engine.analysed) == 3
    assert engine.analysed[-1] == session.fen


async def test_a_position_is_never_sent_before_it_is_confirmed(engine, recorder):
    """A pending candidate must not reach the overlay -- it may be retracted."""
    after_e4 = chess.Board(); after_e4.push_san("e4")
    session = make_session(
        ScriptedDetector([Detection(after_e4.board_fen(), 0.95)], loop=True),
        engine, recorder, stability_frames=3,
    )
    await calibrate(session)

    baseline = len(recorder.of_type(Position))
    await feed(session, 2)  # one short of the threshold
    assert len(recorder.of_type(Position)) == baseline

    await feed(session, 1)
    assert len(recorder.of_type(Position)) == baseline + 1


async def test_an_unchanged_board_sends_nothing(engine, recorder):
    """The debounce requirement: no update unless the position actually changed."""
    session = make_session(StubDetector([chess.Board().board_fen()] * 1), engine, recorder)
    await calibrate(session)
    before = len(recorder.sent)

    await feed(session, 10)
    assert len(recorder.sent) == before


# --------------------------------------------------------------------------
# Degraded modes -- the cases where we must not show analysis
# --------------------------------------------------------------------------


async def test_no_board_is_reported_distinctly_from_low_confidence(engine, recorder):
    session = make_session(ScriptedDetector([None]), engine, recorder)
    await calibrate(session)
    await feed(session, 2)

    assert session.status is SessionStatus.NO_BOARD
    assert recorder.of_type(Position)[-1].source is PositionSource.INITIAL


async def test_low_confidence_detections_do_not_move_the_position(engine, recorder):
    after_e4 = chess.Board(); after_e4.push_san("e4")
    session = make_session(
        ScriptedDetector([Detection(after_e4.board_fen(), 0.2)], loop=True),
        engine, recorder, min_confidence=0.6,
    )
    await calibrate(session)
    await feed(session, 5)

    assert session.fen == STARTING_FEN
    assert session.status is SessionStatus.LOW_LIGHT


async def test_an_unexplainable_board_is_reported_unclear(engine, recorder):
    two_ply = chess.Board(); two_ply.push_san("e4"); two_ply.push_san("e5")
    session = make_session(
        ScriptedDetector([Detection(two_ply.board_fen(), 0.95)], loop=True),
        engine, recorder, resync_frames=99,
    )
    await calibrate(session)
    await feed(session, 3)

    assert session.status is SessionStatus.UNCLEAR
    assert session.fen == STARTING_FEN


async def test_a_resynced_position_is_sent_with_reduced_confidence(engine, recorder):
    """We believe the board, but we lost the thread of the game getting here."""
    two_ply = chess.Board(); two_ply.push_san("e4"); two_ply.push_san("e5")
    session = make_session(
        ScriptedDetector([Detection(two_ply.board_fen(), 0.95)], loop=True),
        engine, recorder, resync_frames=3,
    )
    await calibrate(session)
    await feed(session, 4)

    latest = recorder.of_type(Position)[-1]
    assert latest.source is PositionSource.DETECTOR
    assert latest.confidence < 0.6, "resync must not claim full confidence"


async def test_status_changes_are_not_repeated(engine, recorder):
    """Re-sending an unchanged status every frame would be noise and would flicker."""
    session = make_session(ScriptedDetector([None], loop=True), engine, recorder)
    await calibrate(session)
    await feed(session, 10)

    no_board = [s for s in recorder.of_type(State) if s.status is SessionStatus.NO_BOARD]
    assert len(no_board) == 1


# --------------------------------------------------------------------------
# Manual control
# --------------------------------------------------------------------------


async def test_manual_override_is_adopted_and_analysed(engine, recorder):
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
    session = make_session(StubDetector.from_moves(["e4"]), engine, recorder)
    await calibrate(session)
    await session.handle_message(OverrideFen(fen=fen))

    latest = recorder.of_type(Position)[-1]
    assert latest.fen == fen and latest.source is PositionSource.MANUAL
    assert engine.analysed[-1] == fen


async def test_an_illegal_override_is_rejected(engine, recorder):
    session = make_session(StubDetector.from_moves([]), engine, recorder)
    await calibrate(session)
    await session.handle_message(OverrideFen(fen="not a fen"))

    assert recorder.last(Error) is not None
    assert session.fen == STARTING_FEN


async def test_pausing_stops_processing_and_releases_the_engine(engine, recorder):
    detector = StubDetector.from_moves(["e4", "e5"], frames_per_position=2)
    session = make_session(detector, engine, recorder, stability_frames=2)
    await calibrate(session)

    await session.handle_message(Pause(paused=True))
    assert session.status is SessionStatus.PAUSED
    assert engine.cancellations >= 1

    analysed_before = len(engine.analysed)
    await feed(session, 10)
    assert session.frames_seen == 0, "frames must not be processed while paused"
    assert len(engine.analysed) == analysed_before

    await session.handle_message(Pause(paused=False))
    assert session.status is SessionStatus.TRACKING


async def test_ping_is_answered_with_the_clients_own_timestamp(engine, recorder):
    session = make_session(StubDetector.from_moves([]), engine, recorder)
    await session.handle_message(Ping(ts=1234.5))

    pong = recorder.last(Pong)
    assert pong is not None and pong.ts == 1234.5


# --------------------------------------------------------------------------
# Evaluation routing
# --------------------------------------------------------------------------


async def test_evaluations_for_the_current_position_are_forwarded(engine, recorder):
    session = make_session(StubDetector.from_moves([]), engine, recorder)
    await calibrate(session)
    await engine.emit(session.fen)

    assert len(recorder.of_type(Eval)) == 1


async def test_late_evaluations_for_a_stale_position_are_dropped(engine, recorder):
    """A cancelled search can still deliver one update after the board moved on.

    Forwarding it would briefly show the previous position's evaluation over the
    new one.
    """
    after_e4 = chess.Board(); after_e4.push_san("e4")
    session = make_session(
        ScriptedDetector([Detection(after_e4.board_fen(), 0.95)], loop=True),
        engine, recorder, stability_frames=2,
    )
    await calibrate(session)
    stale = session.fen

    await feed(session, 2)
    assert session.fen != stale, "precondition: the board has moved on"

    await engine.emit(stale)
    assert recorder.of_type(Eval) == []
