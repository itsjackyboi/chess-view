"""Per-session state and message handling.

Deliberately transport-agnostic: a session takes a `send` callable rather than a
WebSocket, so the whole pipeline can be tested without a socket and the transport
stays a thin adapter in app.py.

This is where the architecture's "authoritative state lives server-side" decision is
realised. The client holds a render-only mirror; on reconnect it is re-sent the truth
rather than replaying anything.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from pydantic import BaseModel

from chessview_engine.pool import SessionEngine
from chessview_protocol import (
    PROTOCOL_VERSION,
    Calibrate,
    ClientMessage,
    EngineInfo,
    Error,
    ErrorCode,
    Eval,
    FrameHeader,
    Hello,
    OverrideFen,
    Pause,
    Ping,
    Pong,
    Position,
    PositionSource,
    SessionReady,
    SessionStatus,
    State,
)

from chessview_vision.config import VisionConfig
from chessview_vision.detector import Detection, Detector
from chessview_vision.metrics import METRICS
from chessview_vision.tracker import BoardTracker, Outcome

log = logging.getLogger(__name__)

Sender = Callable[[BaseModel], Awaitable[None]]

# Confidence attached to a position we recovered by resynchronising rather than by
# matching a legal move. Deliberately below the client's "show analysis confidently"
# threshold: we believe the board, but we lost the thread of the game to get here.
RESYNC_CONFIDENCE = 0.5

STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class Session:
    def __init__(
        self,
        session_id: str,
        *,
        detector: Detector,
        engine: SessionEngine,
        send: Sender,
        config: VisionConfig | None = None,
    ) -> None:
        self.session_id = session_id
        self._detector = detector
        self._engine = engine
        self._send = send
        self._config = config or VisionConfig()

        self._tracker = BoardTracker(
            stability_frames=self._config.stability_frames,
            min_confidence=self._config.min_confidence,
            resync_frames=self._config.resync_frames,
        )
        self._status = SessionStatus.CALIBRATING
        self._paused = False
        self._calibrated = False
        self._frames_seen = 0

    # ----------------------------------------------------------------- state

    @property
    def status(self) -> SessionStatus:
        return self._status

    @property
    def fen(self) -> str:
        return self._tracker.fen

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    async def _set_status(self, status: SessionStatus, detail: str | None = None) -> None:
        """Emit a status change, and only a change.

        Re-sending the same status every frame would be pure noise on the wire and
        would make the client's UI flicker between identical states.
        """
        if status is self._status:
            return
        self._status = status
        await self._send(State(status=status, detail=detail))

    # -------------------------------------------------------------- lifecycle

    async def greet(self, *, resumed: bool = False) -> None:
        await self._send(
            SessionReady(
                sessionId=self.session_id,
                protocolVersion=PROTOCOL_VERSION,
                engine=EngineInfo(
                    name=self._engine.engine_name,
                    version="",
                    multipv=3,
                ),
                resumed=resumed,
            )
        )
        if resumed:
            # Re-send the authoritative position so a reconnecting client renders
            # the truth rather than whatever it held when the connection dropped.
            await self._emit_position(PositionSource.MANUAL, confidence=1.0)
            await self._send(State(status=self._status))

    # ---------------------------------------------------------------- inbound

    async def handle_message(self, message: ClientMessage) -> None:
        if isinstance(message, Hello):
            # Version is checked at the transport layer, before a session exists.
            return
        if isinstance(message, Ping):
            await self._send(Pong(ts=message.ts, serverTs=time.time()))
            return
        if isinstance(message, Calibrate):
            await self._handle_calibrate(message)
            return
        if isinstance(message, OverrideFen):
            await self._handle_override(message)
            return
        if isinstance(message, Pause):
            await self._handle_pause(message)
            return
        log.warning("unhandled client message: %s", type(message).__name__)

    async def _handle_calibrate(self, message: Calibrate) -> None:
        try:
            self._tracker.set_position(message.start_fen or STARTING_FEN)
        except ValueError as exc:
            await self._send(
                Error(code=ErrorCode.ILLEGAL_POSITION, message=str(exc), fatal=False)
            )
            return

        self._calibrated = True
        await self._set_status(SessionStatus.TRACKING)
        await self._emit_position(PositionSource.INITIAL, confidence=1.0)
        await self._analyse()

    async def _handle_override(self, message: OverrideFen) -> None:
        """Apply a manual correction from the 2D board editor.

        Respected as authoritative: the tracker adopts it wholesale and drops any
        pending candidate, so the correction is not immediately overwritten by the
        evidence that produced the wrong reading.
        """
        try:
            self._tracker.set_position(message.fen)
        except ValueError as exc:
            await self._send(
                Error(code=ErrorCode.ILLEGAL_POSITION, message=str(exc), fatal=False)
            )
            return

        self._calibrated = True
        await self._set_status(SessionStatus.TRACKING)
        await self._emit_position(PositionSource.MANUAL, confidence=1.0)
        await self._analyse()

    async def _handle_pause(self, message: Pause) -> None:
        self._paused = message.paused
        if message.paused:
            # Stop the engine too: a paused session should not hold a core busy.
            await self._engine.cancel()
            await self._set_status(SessionStatus.PAUSED)
        else:
            await self._set_status(
                SessionStatus.TRACKING if self._calibrated else SessionStatus.CALIBRATING
            )

    async def handle_frame(self, header: FrameHeader, jpeg: bytes) -> None:
        """Run one camera frame through detection and tracking."""
        if self._paused:
            return
        if not self._calibrated:
            # Frames before calibration have no board geometry behind them.
            await self._set_status(SessionStatus.CALIBRATING)
            return

        self._frames_seen += 1
        METRICS.frames_received += 1
        detection = await self._detector.detect(jpeg)

        if detection is None:
            METRICS.frames_undecodable += 1
            await self._set_status(
                SessionStatus.NO_BOARD, "point the camera at the board"
            )
            return

        METRICS.record_confidence(detection.confidence)
        result = self._tracker.observe(detection.placement, detection.confidence)

        if result.outcome is Outcome.COMMITTED:
            METRICS.positions_committed += 1
            if result.resynced:
                METRICS.positions_resynced += 1
            await self._set_status(SessionStatus.TRACKING)
            await self._emit_position(
                PositionSource.DETECTOR,
                confidence=RESYNC_CONFIDENCE if result.resynced else detection.confidence,
                move_uci=result.move.uci() if result.move else None,
                move_san=result.san,
                weak_squares=self._weak_squares(detection),
            )
            await self._analyse()
        elif result.outcome is Outcome.LOW_CONFIDENCE:
            METRICS.observations_low_confidence += 1
            await self._set_status(SessionStatus.LOW_LIGHT, result.detail)
        elif result.outcome is Outcome.UNCLEAR:
            METRICS.observations_unclear += 1
            await self._set_status(SessionStatus.UNCLEAR, result.detail)
        else:
            # UNCHANGED and PENDING are both "still tracking". Notably we do *not*
            # emit a position for PENDING: the client only ever sees committed
            # positions, so the overlay never shows a candidate that gets retracted.
            await self._set_status(SessionStatus.TRACKING)

    # --------------------------------------------------------------- outbound

    async def _emit_position(
        self,
        source: PositionSource,
        *,
        confidence: float,
        move_uci: str | None = None,
        move_san: str | None = None,
        weak_squares: list[int] | None = None,
    ) -> None:
        await self._send(
            Position(
                fen=self._tracker.fen,
                confidence=confidence,
                source=source,
                ply=self._tracker.ply,
                moveUci=move_uci,
                moveSan=move_san,
                lowConfidenceSquares=weak_squares or [],
                ts=time.time(),
            )
        )

    @staticmethod
    def _weak_squares(detection: Detection, limit: int = 3) -> list[int]:
        """The squares the detector was least sure about, worst first.

        Only reported when they are actually shaky -- listing the three weakest
        squares of a confident board would send the user chasing nothing.
        """
        if not detection.squares:
            return []
        ranked = sorted(detection.squares.items(), key=lambda item: item[1])
        return [index for index, score in ranked[:limit] if score < 0.9]

    async def _analyse(self) -> None:
        """Start analysing the current position, abandoning any search in flight."""
        fen = self._tracker.fen

        async def on_eval(ev: Eval) -> None:
            # A search cancelled after emitting can still deliver one late update.
            # Dropping updates for a position we have moved past keeps the overlay
            # from briefly showing an evaluation of the previous board.
            if ev.fen != self._tracker.fen:
                return
            METRICS.evaluations_sent += 1
            await self._send(ev)

        await self._engine.analyse(fen, on_eval)

    async def close(self) -> None:
        await self._engine.cancel()

